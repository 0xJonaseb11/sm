#!/usr/bin/env python3
"""
scripts/pump.py

pump.fun batch - leaderboard + trending tokens + optional callout wall.

usage:
    python scripts/pump.py
    python scripts/pump.py --top 20          # top 20 traders
    python scripts/pump.py --token <addr>    # callout wall for a token
"""

import asyncio
import sys
import datetime
import argparse

from rich.console import Console
from rich.table import Table
from rich.columns import Columns
from rich.rule import Rule
from rich import box

sys.path.insert(0, __import__("os").path.dirname(__file__) + "/..")

from sm.clients.fomoscan import FomoscanClient
from sm.config import cfg
from sm import fmt

console = Console()


async def fetch_leaderboard(client: FomoscanClient) -> list:
    try:
        r = await client.pump_leaderboard()
        return r.get("entries", []) or r.get("items", []) or []
    except Exception as e:
        console.print(f"[dim]leaderboard error: {e}[/]")
        return []


async def fetch_trending(client: FomoscanClient) -> list:
    try:
        r = await client.get_trending_tokens()
        return r.get("entries", []) or r.get("items", []) or r.get("tokens", []) or []
    except Exception as e:
        console.print(f"[dim]trending error: {e}[/]")
        return []


async def fetch_callouts(client: FomoscanClient, token_address: str) -> list:
    try:
        r = await client.pump_token_callouts(token_address)
        return r.get("items", []) or r.get("data", []) or []
    except Exception as e:
        console.print(f"[dim]callouts error: {e}[/]")
        return []


def build_leaderboard_table(entries: list, top: int = 25) -> Table:
    period = ""
    t = Table(
        title="pump.fun leaderboard",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
        min_width=52,
    )
    t.add_column("#",         width=4,  justify="right", style="dim")
    t.add_column("trader",    width=20, no_wrap=True)
    t.add_column("v",         width=2,  justify="center")  # verified
    t.add_column("realized",  width=10, justify="right")
    t.add_column("open",      width=10, justify="right")
    t.add_column("total",     width=10, justify="right")

    for entry in entries[:top]:
        rank       = entry.get("rank", "?")
        handle     = entry.get("username") or entry.get("handle") or "-"
        wallet     = entry.get("wallet", "")
        realized   = entry.get("realizedPnlUsd") or entry.get("realizedPnl") or 0
        unrealized = entry.get("unrealizedPnlUsd") or entry.get("unrealizedPnl") or 0
        total      = entry.get("pnlUsd") or entry.get("totalPnl")
        if total is None:
            total = float(realized or 0) + float(unrealized or 0)
        verified   = entry.get("isVerified", False)

        t.add_row(
            str(rank),
            f"[bold]{handle}[/]",
            "[green]*[/]" if verified else "",
            f"[{fmt.pnl_color(realized)}]{fmt.usd(realized)}[/]",
            f"[{fmt.pnl_color(unrealized)}]{fmt.usd(unrealized)}[/]",
            f"[bold {fmt.pnl_color(total)}]{fmt.usd(total)}[/]",
        )
    return t


def build_trending_table(entries: list) -> Table:
    t = Table(
        title="trending tokens (fomoscan)",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
        min_width=38,
    )
    t.add_column("#",     width=4,  justify="right", style="dim")
    t.add_column("token", width=14, no_wrap=True)
    t.add_column("mcap",  width=10, justify="right")
    t.add_column("vol",   width=10, justify="right")

    for i, entry in enumerate(entries[:20], 1):
        token  = entry.get("handle") or entry.get("label") or entry.get("symbol") or "?"
        mcap   = entry.get("marketCap")
        vol    = entry.get("volume")
        t.add_row(
            str(i),
            f"[bold yellow]{token}[/]",
            fmt.usd(mcap, sign=False) if mcap else "-",
            fmt.usd(vol, sign=False) if vol else "-",
        )
    return t


def build_callout_table(entries: list, token_address: str) -> Table:
    t = Table(
        title=f"callouts - {fmt.short_addr(token_address, 8)}",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
    )
    t.add_column("trader",  width=20)
    t.add_column("wallet",  width=12)
    t.add_column("body",    ratio=1, no_wrap=True)
    t.add_column("time",    width=10, style="dim", justify="right")

    for entry in entries[:25]:
        handle = entry.get("handle") or entry.get("username") or "-"
        wallet = entry.get("wallet", "")
        body   = (entry.get("body") or entry.get("text") or entry.get("content") or "")[:70]
        ts     = entry.get("createdAt") or entry.get("postedAt") or 0
        if isinstance(ts, str):
            import datetime as _dt
            try:
                ts = int(_dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
            except Exception:
                ts = 0
        t.add_row(
            f"[bold]{handle}[/]",
            fmt.short_addr(str(wallet), 4),
            f"[dim]{body}[/]",
            fmt.ts_ago(int(ts or 0)),
        )
    return t


async def main(top: int = 25, token_address: str | None = None):
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    console.print(Rule(f"[bold]pump batch[/]  [dim]{now}[/]"))

    try:
        tasks = [fetch_leaderboard(client), fetch_trending(client)]
        if token_address:
            tasks.append(fetch_callouts(client, token_address))

        results = await asyncio.gather(*tasks)
        leaderboard = results[0]
        trending    = results[1]
        callouts    = results[2] if token_address else []

        lb_table = build_leaderboard_table(leaderboard, top=top)
        tr_table = build_trending_table(trending)

        console.print(Columns([lb_table, tr_table], padding=(0, 3)))

        if callouts and token_address:
            console.print()
            console.print(build_callout_table(callouts, token_address))

    finally:
        await client.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="pump.fun batch runner")
    p.add_argument("--top",   type=int, default=25, help="top N traders (default 25)")
    p.add_argument("--token", default=None, help="show callout wall for this token address")
    args = p.parse_args()
    asyncio.run(main(top=args.top, token_address=args.token))
