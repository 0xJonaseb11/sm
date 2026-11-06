#!/usr/bin/env python3
"""
scripts/nansen.py

nansen batch for a token - dex trade history with FOMO label detection,
pnl leaderboard, and optional wallet identity cross-reference via fomoscan.

usage:
    python scripts/nansen.py <token_address>
    python scripts/nansen.py <token_address> --chain base --days 30
    python scripts/nansen.py <token_address> --enrich          # cross-ref with fomoscan
    python scripts/nansen.py --wallet <address>                 # check one wallet
"""

import asyncio
import sys
import datetime
import argparse
import json

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich.columns import Columns
from rich import box

sys.path.insert(0, __import__("os").path.dirname(__file__) + "/..")

from sm.clients.nansen import NansenClient, parse_dex_trade, parse_pnl_row, is_fomo_label
from sm.clients.fomoscan import FomoscanClient
from sm.config import cfg
from sm import fmt

console = Console()


def _date_range(days: int) -> tuple[str, str]:
    now   = datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(days=days)
    return (
        start.strftime("%Y-%m-%dT00:00:00Z"),
        now.strftime("%Y-%m-%dT23:59:59Z"),
    )


def _side_color(action: str) -> str:
    return "green" if action == "BUY" else "red"


async def show_dex_trades(
    ns: NansenClient,
    fs: FomoscanClient | None,
    chain: str,
    token_address: str,
    date_from: str,
    date_to: str,
    enrich: bool = False,
) -> list[str]:
    """show dex trades table, return list of unique wallet addresses seen."""
    console.print("[dim]fetching dex trades (filtering FOMO labels locally)...[/]")
    try:
        resp = await ns.dex_trades(
            chain=chain, token_address=token_address,
            date_from=date_from, date_to=date_to, per_page=100,
        )
    except Exception as e:
        console.print(f"[red]dex-trades error:[/] {e}")
        return []

    raw   = resp.get("data", []) or []
    rows  = [parse_dex_trade(r) for r in raw]
    fomo  = [r for r in rows if r["is_fomo"]]
    token_name = raw[0].get("token_name", fmt.short_addr(token_address, 8)) if raw else fmt.short_addr(token_address, 8)

    # optional: enrich unknown wallets via fomoscan
    fs_identity: dict[str, str] = {}
    if enrich and fs:
        unknowns = [r["wallet"] for r in rows if not r["is_fomo"] and r["wallet"]][:20]
        if unknowns:
            console.print(f"[dim]enriching {len(unknowns)} wallets via fomoscan...[/]")
            tasks = [_safe_fs_lookup(fs, w) for w in unknowns]
            results = await asyncio.gather(*tasks)
            for wallet, identity in zip(unknowns, results):
                if identity:
                    fs_identity[wallet] = identity

    t = Table(
        title=(
            f"dex trades - {token_name}  "
            + (f"[cyan]{len(fomo)} fomo traders[/]" if fomo else "[dim]no fomo labels in batch[/]")
        ),
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
    )
    t.add_column("time",    width=11, style="dim")
    t.add_column("trader",  width=24, no_wrap=True)
    t.add_column("side",    width=5)
    t.add_column("tokens",  width=14, justify="right", style="dim")
    t.add_column("sol",     width=10, justify="right", style="dim")
    t.add_column("usd",     width=10, justify="right")

    for row in rows[:40]:
        wallet  = row["wallet"]
        label   = row["fomo_handle"] or fs_identity.get(wallet, "") or row["label"]
        name_str = (
            f"[bold cyan]{label}[/]  [dim]{fmt.short_addr(wallet, 5)}[/]"
            if label
            else f"[dim]{fmt.short_addr(wallet, 8)}[/]"
        )
        usd_val  = row["usd"]
        colored_usd = (
            f"[{_side_color(row['action'])}]{fmt.usd(usd_val, sign=False)}[/]"
            if usd_val else "-"
        )
        t.add_row(
            fmt.iso_short(row["ts"]),
            name_str,
            fmt.side_tag(row["action"].lower()),
            f"{row['token_amt']:,.0f}" if row["token_amt"] else "-",
            f"{row['sol_amt']:.4f}" if row["sol_amt"] else "-",
            colored_usd,
        )

    console.print(t)
    return list({r["wallet"] for r in rows if r["wallet"]})


async def _safe_fs_lookup(fs: FomoscanClient, wallet: str) -> str:
    try:
        resp = await fs.get_user_by_wallet(wallet)
        return resp.get("handle", "") or resp.get("name", "") or ""
    except Exception:
        return ""


async def show_pnl_board(
    ns: NansenClient,
    chain: str,
    token_address: str,
    date_from: str,
    date_to: str,
) -> None:
    console.print("[dim]fetching pnl leaderboard...[/]")
    try:
        resp = await ns.pnl_leaderboard(
            chain=chain, token_address=token_address,
            date_from=date_from, date_to=date_to, per_page=30,
        )
    except Exception as e:
        console.print(f"[dim]pnl leaderboard: {e}[/]")
        return

    rows = [parse_pnl_row(r) for r in (resp.get("data", []) or [])]
    if not rows:
        console.print("[dim]no pnl data returned[/]")
        return

    t = Table(
        title="pnl leaderboard (nansen)",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
    )
    t.add_column("#",         width=4,  justify="right", style="dim")
    t.add_column("trader",    width=26, no_wrap=True)
    t.add_column("trades",    width=7,  justify="right", style="dim")
    t.add_column("realized",  width=12, justify="right")
    t.add_column("open",      width=12, justify="right")
    t.add_column("roi",       width=8,  justify="right")

    for i, row in enumerate(rows[:30], 1):
        label    = row["fomo_handle"] or row["label"]
        name_str = (
            f"[bold cyan]{label}[/]  [dim]{fmt.short_addr(row['wallet'], 5)}[/]"
            if label
            else f"[dim]{fmt.short_addr(row['wallet'], 8)}[/]"
        )
        t.add_row(
            str(i),
            name_str,
            str(row["trades"]) if row["trades"] else "-",
            f"[{fmt.pnl_color(row['realized'])}]{fmt.usd(row['realized'])}[/]",
            f"[{fmt.pnl_color(row['unrealized'])}]{fmt.usd(row['unrealized'])}[/]",
            f"[{fmt.pnl_color(row['roi_pct'])}]{fmt.pct(row['roi_pct'])}[/]"
            if row["roi_pct"] is not None else "-",
        )

    console.print(t)


async def check_wallet(ns: NansenClient, fs: FomoscanClient | None, address: str) -> None:
    chain = "evm" if address.startswith("0x") else "solana"
    tasks = [ns.premium_labels(chain=chain, address=address)]
    if fs:
        tasks.append(fs.get_user_by_wallet(address))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    ns_resp = results[0] if not isinstance(results[0], Exception) else {}
    fs_resp = results[1] if len(results) > 1 and not isinstance(results[1], Exception) else {}

    console.print(f"\n[bold]wallet:[/] {address}")

    # nansen labels
    labels = (ns_resp.get("data", []) or []) if isinstance(ns_resp, dict) else []
    if labels:
        for lbl in labels:
            is_fomo = "unique-identifier/fomo-trader" in (lbl.get("kind") or [])
            tag     = "[bold cyan]fomo trader[/]" if is_fomo else "[dim]label[/]"
            console.print(f"  {tag}  {lbl.get('label', '')}  [dim]{lbl.get('category', '')}[/]")
    else:
        console.print("  [dim]nansen: no premium labels found[/]")

    # fomoscan identity
    if isinstance(fs_resp, dict) and fs_resp:
        handle  = fs_resp.get("handle", "")
        name    = fs_resp.get("name", "")
        twitter = fs_resp.get("twitter", "")
        console.print(
            f"  fomoscan: [bold]{name or handle}[/]"
            + (f"  [cyan]{twitter}[/]" if twitter else "")
        )
    elif fs:
        console.print("  [dim]fomoscan: not in sample set or no known owner[/]")


async def main(
    token_address: str | None,
    chain: str,
    days: int,
    wallet: str | None,
    enrich: bool,
):
    if not cfg.has_nansen():
        console.print("[red]error:[/] NANSEN_KEY not set in .env")
        return

    ns  = NansenClient()
    fs  = FomoscanClient() if cfg.has_fomoscan() else None
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    console.print(Rule(f"[bold]nansen batch[/]  [dim]{now}[/]"))

    try:
        if wallet:
            await check_wallet(ns, fs, wallet)
            return

        if not token_address:
            console.print("[red]error:[/] provide a token address or --wallet")
            return

        date_from, date_to = _date_range(days)
        console.print(
            f"[dim]token: {fmt.short_addr(token_address, 10)}  "
            f"chain: {chain}  "
            f"range: {date_from[:10]} - {date_to[:10]}[/]"
        )

        wallets = await show_dex_trades(ns, fs, chain, token_address, date_from, date_to, enrich=enrich)
        console.print()
        await show_pnl_board(ns, chain, token_address, date_from, date_to)

        console.print(
            f"\n[dim]{len(wallets)} unique wallets in this batch. "
            f"use --wallet <addr> to check a specific wallet.[/]"
        )

    finally:
        await ns.close()
        if fs:
            await fs.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="nansen batch - FOMO trader discovery for a token")
    p.add_argument("token",    nargs="?", default=None, help="token address")
    p.add_argument("--chain",  default="solana", help="solana | base | bsc")
    p.add_argument("--days",   type=int,  default=7, help="lookback window in days (default 7)")
    p.add_argument("--wallet", default=None, help="check a single wallet for FOMO label + fomoscan identity")
    p.add_argument("--enrich", action="store_true", help="cross-ref unknown wallets with fomoscan")
    args = p.parse_args()
    asyncio.run(main(
        token_address=args.token,
        chain=args.chain,
        days=args.days,
        wallet=args.wallet,
        enrich=args.enrich,
    ))
