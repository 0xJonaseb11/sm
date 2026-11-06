#!/usr/bin/env python3
"""
signals - smart money position signals with real identities.

usage:
    # show stored signals from db
    python scripts/signals.py

    # index top 200 hyperliquid traders and compute signals (free, no key)
    python scripts/signals.py --hl-top 200

    # watch a wallet's open HL positions
    python scripts/signals.py --wallet 0x...

    # single token breakdown: who is long/short and by name
    python scripts/signals.py --token HYPE

    # start REST API for stupidmoney.io consumption
    python scripts/signals.py --api --port 8787

    # watch for position changes (polls every N seconds)
    python scripts/signals.py --watch --interval 60
"""

import argparse
import asyncio
import sys
import time

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, ".")

import sm.db as db
import sm.fmt as fmt
from sm.clients.hyperliquid import (
    HyperliquidClient,
    parse_fill,
    parse_leaderboard_entry,
    parse_position,
)
from sm.signals.aggregator import (
    all_signals,
    recent_position_events,
    token_signal,
    wallet_positions,
)
from sm.signals.engine import aggregate_signals, run_signal_pass
from sm.signals.position_tracker import detect_and_write_changes

console = Console()


# -------------------------------------------------------------------------
# display helpers
# -------------------------------------------------------------------------

def _signal_table(rows: list[dict]) -> Table:
    t = Table(
        "token", "venue", "% long", "longs", "shorts",
        "long $", "short $", "computed",
        box=box.SIMPLE_HEAD, header_style="bold",
    )
    for r in rows:
        pct = r.get("pct_long") or 50.0
        pct_str = (
            f"[bold green]{pct:.0f}%[/]"
            if pct >= 55
            else (f"[bold red]{pct:.0f}%[/]" if pct <= 45 else f"[dim]{pct:.0f}%[/]")
        )
        t.add_row(
            r.get("token_sym") or r.get("token_addr", "")[:8],
            r.get("venue", ""),
            pct_str,
            str(r.get("long_count", 0)),
            str(r.get("short_count", 0)),
            fmt.usd(r.get("total_long_usd"), sign=False),
            fmt.usd(r.get("total_short_usd"), sign=False),
            fmt.ts_ago(r.get("computed_at") or 0),
        )
    return t


def _position_table(positions: list[dict], title: str, color: str) -> Table:
    t = Table(title, "identity", "$ size", "upnl", box=box.SIMPLE_HEAD)
    for p in positions[:25]:
        handle = (
            p.get("identity")
            or p.get("w_handle")
            or p.get("w_ns_fomo")
            or p.get("w_display")
            or ""
        )
        addr   = fmt.short_addr(p["wallet"])
        label  = f"[bold]{handle}[/]" if handle else f"[dim]{addr}[/]"
        upnl   = p.get("unrealized_pnl") or 0.0
        t.add_row(
            f"[bold {color}]{p['side']}[/]",
            label,
            fmt.usd(p.get("size_usd"), sign=False),
            f"[{fmt.pnl_color(upnl)}]{fmt.usd(upnl)}[/]",
        )
    return t


# -------------------------------------------------------------------------
# commands
# -------------------------------------------------------------------------

async def cmd_show(db_conn) -> None:
    rows = await all_signals(db_conn, limit=50)
    if not rows:
        console.print("[dim]no signals yet. run with --hl-top 200 to index hyperliquid.[/]")
        return
    t = _signal_table(rows)
    console.print(Panel(t, title="[bold]smart money signals[/]", border_style="dim"))


async def cmd_token(db_conn, token: str, venue: str = "hyperliquid") -> None:
    sig = await token_signal(db_conn, token, venue)
    if not sig:
        console.print(f"[dim]no signal data for {token} on {venue}.[/]")
        return

    pct = sig.get("pct_long") or 50.0
    border = "green" if pct >= 50 else "red"
    bias   = "[green]long bias[/]" if pct >= 50 else "[red]short bias[/]"

    console.print(Panel(
        f"{bias}  [bold]{'[green]' if pct >= 50 else '[red]'}{pct:.1f}%[/][/bold] long\n"
        f"[dim]{sig['long_count']} long / {sig['short_count']} short  |  "
        f"long {fmt.usd(sig['total_long_usd'], sign=False)}  "
        f"short {fmt.usd(sig['total_short_usd'], sign=False)}[/]",
        title=f"[bold]{token.upper()}[/] | {venue}",
        border_style=border,
    ))

    longs  = sig.get("longs", [])
    shorts = sig.get("shorts", [])
    cols = []
    if longs:
        cols.append(_position_table(longs, "longs", "green"))
    if shorts:
        cols.append(_position_table(shorts, "shorts", "red"))
    if cols:
        console.print(Columns(cols))


async def cmd_wallet(hl: HyperliquidClient, wallet: str) -> None:
    state = await hl.clearinghouse_state(wallet)
    mids  = await hl.all_mids()

    asset_positions = state.get("assetPositions", []) or []
    if not asset_positions:
        console.print(f"[dim]no open positions for {fmt.short_addr(wallet)}[/]")
        return

    t = Table(
        "asset", "side", "size", "entry", "mark", "upnl", "margin", "liq",
        box=box.SIMPLE_HEAD, header_style="bold",
    )
    for ap in asset_positions:
        pos = ap.get("position", {})
        if not pos:
            continue
        p    = parse_position(pos)
        mark = float(mids.get(p["token_sym"], 0) or 0)
        t.add_row(
            p["token_sym"],
            "[green]long[/]"  if p["side"] == "LONG"  else "[red]short[/]",
            fmt.num(p["size"], 4),
            fmt.usd(p["entry_price"], sign=False),
            fmt.usd(mark, sign=False) if mark else "[dim]-[/]",
            f"[{fmt.pnl_color(p['unrealized_pnl'])}]{fmt.usd(p['unrealized_pnl'])}[/]",
            fmt.usd(p["margin_used"], sign=False),
            fmt.usd(p["liq_price"], sign=False) if p["liq_price"] else "[dim]-[/]",
        )

    ms = state.get("marginSummary", {}) or {}
    console.print(Panel(
        t,
        title=(
            f"[bold]hl positions[/]  [dim]{fmt.short_addr(wallet)}[/]  "
            f"equity {fmt.usd(float(ms.get('accountValue') or 0), sign=False)}  "
            f"margin {fmt.usd(float(ms.get('totalMarginUsed') or 0), sign=False)}"
        ),
        border_style="dim",
    ))


async def cmd_hl_top(hl: HyperliquidClient, db_conn, top: int) -> None:
    """
    index top N hyperliquid traders:
      1. fetch leaderboard from stats-data endpoint
      2. upsert EVM wallets to db
      3. batch-fetch clearinghouse states
      4. write positions to db
      5. aggregate signals
    """
    console.print(f"[dim]fetching hyperliquid leaderboard (top {top})...[/]")

    try:
        board = await hl.leaderboard(timeframe="month")
    except Exception as e:
        console.print(f"[red]leaderboard error: {e}[/]")
        return

    entries = board.get("leaderboardRows", []) or []
    console.print(f"[dim]got {len(entries)} entries, taking top {top}[/]")

    addrs: list[str] = []
    for entry in entries[:top]:
        parsed = parse_leaderboard_entry(entry)
        addr   = parsed["eth_address"]
        if not addr:
            continue
        addrs.append(addr)
        await db.upsert_wallet(db_conn, {
            "address":   addr.lower(),
            "family":    "evm",
            "trader_id": "",
            "handle":    "",
            "display":   parsed["display"] or f"hl:{fmt.short_addr(addr, 6)}",
            "tier":      "unverified",
            "ns_label":  "",
            "ns_fomo":   "",
        })

    console.print(f"[dim]indexed {len(addrs)} wallets. fetching positions...[/]")

    states    = await hl.batch_clearinghouse_states(addrs, concurrency=10)
    now       = int(time.time())
    pos_count = 0

    for wallet, state in states.items():
        for ap in (state.get("assetPositions") or []):
            pos = ap.get("position", {})
            if not pos:
                continue
            p = parse_position(pos)
            if p["side"] == "NEUTRAL":
                continue
            pid = f"{wallet.lower()}:{p['token_sym']}:hl"
            await db_conn.execute(
                """
                INSERT OR REPLACE INTO positions
                    (id, wallet, token_addr, token_sym, chain, venue, side,
                     size_usd, unrealized_pnl, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    pid, wallet.lower(),
                    p["token_sym"], p["token_sym"],
                    "evm", "hyperliquid", p["side"],
                    p["size_usd"], p["unrealized_pnl"], now,
                ),
            )
            pos_count += 1

    await db_conn.commit()

    n_signals = await aggregate_signals(db_conn)
    console.print(
        f"[green]indexed {len(addrs)} wallets | "
        f"{pos_count} positions | "
        f"{n_signals} signal tokens[/]"
    )

    # show results
    await cmd_show(db_conn)


async def cmd_watch(hl: HyperliquidClient, db_conn, interval: int) -> None:
    """poll for position changes on a fixed interval."""
    console.print(f"[dim]watching for position changes every {interval}s. ctrl+c to stop.[/]")

    while True:
        try:
            async with db_conn.execute(
                "SELECT address FROM wallets WHERE family IN ('evm', 'ethereum')"
            ) as cur:
                rows = await cur.fetchall()
            addrs = [r["address"] for r in rows]

            if not addrs:
                console.print("[dim]no EVM wallets indexed. run --hl-top first.[/]")
                await asyncio.sleep(interval)
                continue

            states = await hl.batch_clearinghouse_states(addrs, concurrency=10)
            now    = int(time.time())
            new_positions = []

            for wallet, state in states.items():
                for ap in (state.get("assetPositions") or []):
                    pos = ap.get("position", {})
                    if not pos:
                        continue
                    p = parse_position(pos)
                    new_positions.append({
                        "wallet":     wallet.lower(),
                        "token_addr": p["token_sym"],
                        "token_sym":  p["token_sym"],
                        "side":       p["side"],
                        "size_usd":   p["size_usd"],
                        "venue":      "hyperliquid",
                    })

            changes = await detect_and_write_changes(db_conn, new_positions)

            if changes:
                for ch in changes:
                    sym  = ch["token_sym"] or ch["token_addr"][:8]
                    side = "[green]long[/]" if ch["side"] == "LONG" else "[red]short[/]"
                    console.print(
                        f"[bold]{ch['kind'].upper()}[/]  "
                        f"{fmt.short_addr(ch['wallet'])}  {sym}  {side}  "
                        f"[dim]{fmt.usd(ch['usd_value'])}[/]"
                    )
            else:
                console.print(f"[dim]{fmt.ts_ago(now)} - no changes in {len(addrs)} wallets[/]")

            await aggregate_signals(db_conn)
            await asyncio.sleep(interval)

        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[red]watch error: {e}[/]")
            await asyncio.sleep(interval)


# -------------------------------------------------------------------------
# entry
# -------------------------------------------------------------------------

def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="signals")
    p.add_argument("--hl-top",   type=int, default=0,       help="index top N HL leaderboard traders")
    p.add_argument("--token",    type=str, default="",       help="show signal for a token (symbol or addr)")
    p.add_argument("--venue",    type=str, default="hyperliquid")
    p.add_argument("--wallet",   type=str, default="",       help="show open HL positions for wallet")
    p.add_argument("--watch",    action="store_true",        help="watch for position changes")
    p.add_argument("--interval", type=int, default=60,       help="watch poll interval in seconds")
    p.add_argument("--api",      action="store_true",        help="start REST API server")
    p.add_argument("--port",     type=int, default=8787)
    return p.parse_args()


async def main() -> None:
    args    = _args()

    if args.api:
        from sm.api.server import run as run_api
        console.print(f"[bold]starting SM API on port {args.port}...[/]")
        run_api(port=args.port)
        return

    hl      = HyperliquidClient()
    db_conn = await db.init()

    try:
        if args.wallet:
            await cmd_wallet(hl, args.wallet)

        elif args.hl_top > 0:
            await cmd_hl_top(hl, db_conn, top=args.hl_top)

        elif args.token:
            await cmd_token(db_conn, args.token, venue=args.venue)

        elif args.watch:
            await cmd_watch(hl, db_conn, interval=args.interval)

        else:
            await cmd_show(db_conn)

    finally:
        await hl.close()
        await db_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
