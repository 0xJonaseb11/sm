#!/usr/bin/env python3
"""
scripts/nansen.py

nansen batch for a token - finds FOMO traders in dex activity,
shows the pnl leaderboard with premium labels, and flags who is still holding.

usage:
    python scripts/nansen.py <token_address>
    python scripts/nansen.py <token_address> --chain base
    python scripts/nansen.py <token_address> --days 14
    python scripts/nansen.py --wallet <address>   # check one wallet for FOMO label
"""

import asyncio
import sys
import datetime
import argparse

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich.columns import Columns
from rich import box

sys.path.insert(0, __import__("os").path.dirname(__file__) + "/..")

from sm.clients.nansen import NansenClient, is_fomo_label, extract_fomo_handle
from sm.config import cfg
from sm import fmt

console = Console()


def _date_range(days: int) -> tuple[str, str]:
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(days=days)
    return (
        start.strftime("%Y-%m-%dT00:00:00Z"),
        now.strftime("%Y-%m-%dT23:59:59Z"),
    )


async def show_fomo_traders(
    client: NansenClient,
    chain: str,
    token_address: str,
    date_from: str,
    date_to: str,
) -> None:
    console.print(f"[dim]fetching dex trades (filtering FOMO labels locally)...[/]")

    # fetch without label filter to avoid timeout, filter locally
    try:
        resp = await client.dex_trades(
            chain=chain,
            token_address=token_address,
            date_from=date_from,
            date_to=date_to,
            per_page=100,
        )
    except Exception as e:
        console.print(f"[red]dex-trades error:[/] {e}")
        return

    rows = resp.get("data", []) or []
    token_name = rows[0].get("token_name", fmt.short_addr(token_address, 6)) if rows else fmt.short_addr(token_address, 6)

    # group by trader, collect any with FOMO labels or just all if none match
    fomo_rows = [r for r in rows if is_fomo_label(r.get("trader_address_label", ""))]
    display_rows = fomo_rows if fomo_rows else rows

    t = Table(
        title=f"dex trades - {token_name} ({chain})"
              + (f"  [{len(fomo_rows)} fomo traders]" if fomo_rows else "  [no fomo labels in this batch]"),
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
    )
    t.add_column("time",    width=10, style="dim")
    t.add_column("trader",  width=22)
    t.add_column("action",  width=6)
    t.add_column("amount",  width=12, justify="right")
    t.add_column("usd",     width=10, justify="right")
    t.add_column("label",   width=28)

    for row in display_rows[:30]:
        ts_str    = (row.get("block_timestamp") or "")[:10]
        trader    = row.get("trader_address", "")
        label     = row.get("trader_address_label", "")
        action    = row.get("action", "")
        amount    = row.get("token_amount")
        usd_val   = row.get("usd_value") or row.get("traded_token_usd_amount")

        fomo_handle = extract_fomo_handle(label) if is_fomo_label(label) else ""
        name_str = (
            f"[bold cyan]{fomo_handle}[/]  [dim]{fmt.short_addr(trader, 5)}[/]"
            if fomo_handle
            else f"[dim]{fmt.short_addr(trader, 7)}[/]"
        )

        t.add_row(
            ts_str,
            name_str,
            fmt.side_tag(action.lower()),
            f"{amount:,.0f}" if amount else "-",
            f"[{fmt.pnl_color(usd_val if action=='BUY' else -(usd_val or 0))}]{fmt.usd(usd_val, sign=False)}[/]" if usd_val else "-",
            f"[dim]{label[:26]}[/]" if label and not fomo_handle else "",
        )

    console.print(t)


async def show_pnl_board(
    client: NansenClient,
    chain: str,
    token_address: str,
    date_from: str,
    date_to: str,
) -> None:
    console.print("[dim]fetching pnl leaderboard (premium labels)...[/]")
    try:
        resp = await client.pnl_leaderboard(
            chain=chain,
            token_address=token_address,
            date_from=date_from,
            date_to=date_to,
            premium_labels=True,
            per_page=30,
        )
    except Exception as e:
        console.print(f"[dim]pnl leaderboard: {e}[/]")
        return

    rows = resp.get("data", []) or []
    if not rows:
        console.print("[dim]no pnl data returned[/]")
        return

    t = Table(
        title="pnl leaderboard (nansen, premium labels)",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
    )
    t.add_column("#",         width=4,  justify="right", style="dim")
    t.add_column("trader",    width=24)
    t.add_column("realized",  width=12, justify="right")
    t.add_column("unrealized",width=12, justify="right")
    t.add_column("label",     width=30)

    for i, row in enumerate(rows[:30], 1):
        label     = row.get("trader_address_label", "")
        address   = row.get("trader_address", "")
        realized  = row.get("realized_pnl_usd") or row.get("pnl_usd") or row.get("net_usd")
        unrealized = row.get("unrealized_pnl_usd")

        fomo_handle = extract_fomo_handle(label) if is_fomo_label(label) else ""
        name_str = (
            f"[bold cyan]{fomo_handle}[/]  [dim]{fmt.short_addr(address, 5)}[/]"
            if fomo_handle
            else f"[dim]{fmt.short_addr(address, 7)}[/]"
        )

        t.add_row(
            str(i),
            name_str,
            f"[{fmt.pnl_color(realized)}]{fmt.usd(realized)}[/]" if realized is not None else "-",
            f"[{fmt.pnl_color(unrealized)}]{fmt.usd(unrealized)}[/]" if unrealized is not None else "-",
            f"[dim]{label[:28]}[/]" if label and not fomo_handle else "",
        )

    console.print(t)


async def check_wallet(client: NansenClient, address: str) -> None:
    chain = "evm" if address.startswith("0x") else "solana"
    console.print(f"[dim]checking premium labels for {fmt.short_addr(address)}...[/]")
    try:
        resp = await client.premium_labels(chain=chain, address=address)
        labels = resp.get("data", []) or []
        if not labels:
            console.print("[dim]no premium labels found[/]")
            return
        console.print(f"\n[bold]wallet:[/] {address}")
        for lbl in labels:
            is_fomo = "unique-identifier/fomo-trader" in (lbl.get("kind") or [])
            tag = "[bold cyan]fomo trader[/]" if is_fomo else "[dim]other[/]"
            console.print(
                f"  {tag}  {lbl.get('label', '')}  "
                f"[dim]{lbl.get('category', '')}[/]"
            )
    except Exception as e:
        console.print(f"[red]premium labels error:[/] {e}")


async def main(token_address: str | None, chain: str, days: int, wallet: str | None):
    if not cfg.has_nansen():
        console.print("[red]error:[/] NANSEN_KEY not set in .env")
        return

    client = NansenClient()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    console.print(Rule(f"[bold]nansen batch[/]  [dim]{now}[/]"))

    try:
        if wallet:
            await check_wallet(client, wallet)
            return

        if not token_address:
            console.print("[red]error:[/] provide a token address or --wallet")
            return

        date_from, date_to = _date_range(days)
        console.print(
            f"[dim]token: {fmt.short_addr(token_address, 8)}  "
            f"chain: {chain}  "
            f"range: last {days}d[/]"
        )

        await show_fomo_traders(client, chain, token_address, date_from, date_to)
        console.print()
        await show_pnl_board(client, chain, token_address, date_from, date_to)

    finally:
        await client.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="nansen batch - FOMO trader discovery for a token")
    p.add_argument("token", nargs="?", default=None, help="token address")
    p.add_argument("--chain",  default="solana", help="solana | base | bsc (default: solana)")
    p.add_argument("--days",   type=int, default=7, help="lookback window in days (default: 7)")
    p.add_argument("--wallet", default=None, help="check a single wallet for FOMO labels")
    args = p.parse_args()
    asyncio.run(main(
        token_address=args.token,
        chain=args.chain,
        days=args.days,
        wallet=args.wallet,
    ))
