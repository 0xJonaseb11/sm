#!/usr/bin/env python3
"""
scripts/pump.py

pump.fun batch - leaderboard + trending tokens + optional callout wall.
optionally drills into top N traders for their pump callout history.

usage:
    python scripts/pump.py
    python scripts/pump.py --top 50
    python scripts/pump.py --token <mint_address>
    python scripts/pump.py --drill 5
"""

import asyncio
import sys
import datetime
import argparse

from rich.console import Console
from rich.table import Table
from rich.columns import Columns
from rich.panel import Panel
from rich.rule import Rule
from rich import box

sys.path.insert(0, __import__("os").path.dirname(__file__) + "/..")

from sm.clients.fomoscan import FomoscanClient
from sm.config import cfg
from sm import fmt

console = Console()


async def fetch_all(client: FomoscanClient) -> tuple[list, list]:
    lb, trending = await asyncio.gather(
        client.pump_leaderboard(),
        client.get_trending_tokens(),
        return_exceptions=True,
    )
    entries  = (lb.get("entries", []) or []) if isinstance(lb, dict) else []
    trending_entries = (trending.get("entries", []) or []) if isinstance(trending, dict) else []
    return entries, trending_entries


def build_leaderboard(entries: list, top: int) -> Table:
    t = Table(
        title="pump.fun leaderboard",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        min_width=58,
        expand=False,
    )
    t.add_column("#",         width=4,  justify="right", style="dim")
    t.add_column("trader",    width=20, no_wrap=True)
    t.add_column("v",         width=2,  justify="center")
    t.add_column("realized",  width=11, justify="right")
    t.add_column("open",      width=11, justify="right")
    t.add_column("total",     width=11, justify="right")

    for entry in entries[:top]:
        rank       = entry.get("rank", "?")
        handle     = entry.get("username") or entry.get("handle") or "-"
        realized   = entry.get("realizedPnlUsd") or entry.get("realizedPnl") or 0
        unrealized = entry.get("unrealizedPnlUsd") or entry.get("unrealizedPnl") or 0
        total      = entry.get("pnlUsd") or entry.get("totalPnl")
        if total is None:
            total = float(realized or 0) + float(unrealized or 0)
        verified   = entry.get("isVerified", False)

        t.add_row(
            str(rank),
            f"[bold]{handle}[/]",
            fmt.verified_star(verified),
            f"[{fmt.pnl_color(realized)}]{fmt.usd(realized)}[/]",
            f"[{fmt.pnl_color(unrealized)}]{fmt.usd(unrealized)}[/]",
            f"[bold {fmt.pnl_color(total)}]{fmt.usd(total)}[/]",
        )
    return t


def build_trending(entries: list) -> Table:
    t = Table(
        title="trending (fomoscan)",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        min_width=42,
        expand=False,
    )
    t.add_column("#",     width=4,  justify="right", style="dim")
    t.add_column("token", width=16, no_wrap=True)
    t.add_column("mcap",  width=10, justify="right")
    t.add_column("vol",   width=10, justify="right")

    for i, entry in enumerate(entries[:20], 1):
        token = entry.get("handle") or entry.get("label") or "?"
        mcap  = entry.get("marketCap")
        vol   = entry.get("volume")
        t.add_row(
            str(i),
            f"[bold yellow]{token}[/]",
            fmt.usd(mcap, sign=False) if mcap else "-",
            fmt.usd(vol,  sign=False) if vol  else "-",
        )
    return t


async def drill_trader(client: FomoscanClient, entry: dict) -> Panel:
    handle   = entry.get("username") or entry.get("handle") or "-"
    wallet   = entry.get("wallet", "")
    rank     = entry.get("rank", "?")
    realized = entry.get("realizedPnlUsd") or 0
    total    = entry.get("pnlUsd") or entry.get("totalPnl") or 0
    verified = entry.get("isVerified", False)

    lines = [
        f"[dim]#{rank}[/]  [bold white]{handle}[/]"
        + (" [green]*[/]" if verified else ""),
        f"total: [bold {fmt.pnl_color(total)}]{fmt.usd(total)}[/]",
        f"[dim]{fmt.short_addr(wallet, 7)}[/]",
    ]

    if wallet:
        try:
            callouts_resp = await client.pump_wallet_callouts(wallet)
            callouts = callouts_resp.get("items", []) or callouts_resp.get("data", []) or []
            if callouts:
                lines.append("")
                for c in callouts[:3]:
                    sym  = c.get("tokenSymbol") or c.get("token", {}).get("symbol", "") or "?"
                    body = (c.get("body") or c.get("text") or "")[:55]
                    lines.append(f"  [yellow]{sym}[/]  [dim]{body}[/]")
            else:
                lines.append("[dim]no callouts found[/]")
        except Exception as e:
            lines.append(f"[dim]{str(e)[:40]}[/]")

    return Panel("\n".join(lines), expand=True, border_style="dim")


async def show_callout_wall(client: FomoscanClient, token_address: str) -> None:
    console.print(f"[dim]fetching callout wall for {fmt.short_addr(token_address)}...[/]")
    try:
        resp     = await client.pump_token_callouts(token_address)
        callouts = resp.get("items", []) or resp.get("data", []) or []
    except Exception as e:
        console.print(f"[red]callout wall error:[/] {e}")
        return

    if not callouts:
        console.print("[dim]no callouts for this token[/]")
        return

    t = Table(
        title=f"callout wall - {fmt.short_addr(token_address, 8)}",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
    )
    t.add_column("time",    width=11, style="dim")
    t.add_column("trader",  width=20, no_wrap=True)
    t.add_column("wallet",  width=12)
    t.add_column("body",    ratio=1,  no_wrap=True)

    for entry in callouts[:25]:
        handle = entry.get("handle") or entry.get("username") or "-"
        wallet = entry.get("wallet", "")
        body   = (entry.get("body") or entry.get("text") or "")[:80]
        ts_raw = entry.get("createdAt") or entry.get("postedAt") or 0
        ts_int = fmt.iso_to_ts(ts_raw) if isinstance(ts_raw, str) else int(ts_raw or 0)

        t.add_row(
            fmt.ts_ago(ts_int),
            f"[bold]{handle}[/]",
            fmt.short_addr(str(wallet), 4),
            f"[dim]{body}[/]",
        )
    console.print(t)


async def main(top: int, token_address: str | None, drill: int):
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    now    = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    console.print(Rule(f"[bold]pump batch[/]  [dim]{now}[/]"))

    try:
        entries, trending = await fetch_all(client)

        lb_table = build_leaderboard(entries, top=top)
        tr_table = build_trending(trending)
        console.print(Columns([lb_table, tr_table], padding=(0, 3)))

        if token_address:
            console.print()
            await show_callout_wall(client, token_address)

        if drill and entries:
            console.print(Rule("[dim]top trader drill[/]"))
            top_entries = entries[:drill]
            panels = await asyncio.gather(
                *[drill_trader(client, e) for e in top_entries]
            )
            for i in range(0, len(panels), 2):
                console.print(Columns(list(panels[i:i+2]), padding=(0, 1), equal=True))

    finally:
        await client.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="pump.fun batch runner")
    p.add_argument("--top",   type=int, default=25, help="top N from leaderboard (default 25)")
    p.add_argument("--token", default=None, help="show callout wall for this token address")
    p.add_argument("--drill", type=int, default=0, metavar="N",
                   help="drill into top N traders for their callout history")
    args = p.parse_args()
    asyncio.run(main(top=args.top, token_address=args.token, drill=args.drill))
