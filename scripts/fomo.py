#!/usr/bin/env python3
"""
scripts/fomo.py

fomoscan batch - runs all ranking windows in parallel and prints them
side by side. optionally drills into the top N traders.

usage:
    python scripts/fomo.py
    python scripts/fomo.py --drill 5      # show top 5 traders' theses
    python scripts/fomo.py --window 24h   # single window only
"""

import asyncio
import sys
import datetime

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

WINDOWS = ["24h", "7d", "30d", "all"]


def _decode(a) -> float | None:
    if a is None:
        return None
    if isinstance(a, (int, float)):
        return float(a)
    raw = a.get("value")
    dec = a.get("decimals", 6)
    if raw is None:
        return None
    return float(str(raw)) / (10 ** dec)


async def fetch_window(client: FomoscanClient, window: str) -> tuple[str, list]:
    """fetch ranking for one window, return (window, entries)."""
    try:
        rankings = await client.list_rankings()
        items = rankings.get("items", [])
        board = next((r for r in items if r.get("window") == window), None)
        if not board:
            return window, []
        data = await client.get_ranking(board.get("key", ""))
        entries = data.get("entries", []) or data.get("items", [])
        return window, entries
    except Exception as e:
        console.print(f"[dim]{window} error: {e}[/]")
        return window, []


def build_window_table(window: str, entries: list, top: int = 20) -> Table:
    t = Table(
        title=f"[bold]{window}[/]",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        show_footer=False,
        expand=False,
        min_width=38,
    )
    t.add_column("#",        width=3,  justify="right", style="dim")
    t.add_column("trader",   width=20, no_wrap=True)
    t.add_column("net flow", width=11, justify="right")

    for entry in entries[:top]:
        rank    = entry.get("rank", "?")
        subject = entry.get("subject", {}) or {}
        canon   = subject.get("canonical", {}) or {}
        display = canon.get("display", {}) or {}
        handle  = display.get("handle") or display.get("name") or "-"
        value   = _decode(entry.get("value"))

        t.add_row(
            str(rank),
            f"[bold]{handle}[/]",
            f"[{fmt.pnl_color(value)}]{fmt.usd(value)}[/]",
        )
    return t


async def drill_trader(client: FomoscanClient, handle: str) -> Panel:
    """fetch a trader's profile and recent theses."""
    lines = [f"[bold white]{handle}[/]"]
    try:
        u = await client.get_user_by_handle(handle)
        if u.get("twitter"):
            lines.append(f"[dim]x: {u['twitter']}[/]")
        sol = u.get("solanaAddress")
        if sol:
            lines.append(f"[dim]{fmt.short_addr(sol, 6)}[/]")

        uid = u.get("id", "")
        if uid:
            theses = await client.get_theses_for_user(uid, limit=3)
            items = theses if isinstance(theses, list) else theses.get("items", [])
            for t in items[:3]:
                sym  = t.get("tokenSymbol") or t.get("token", {}).get("symbol", "") or "?"
                body = (t.get("body") or t.get("text") or "")[:60]
                lines.append(f"[yellow]{sym}[/] [dim]{body}[/]")
    except Exception as e:
        lines.append(f"[dim]unavailable: {e}[/]")

    return Panel("\n".join(lines), expand=False, border_style="dim")


async def main(drill: int = 0, window: str | None = None):
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    console.print(Rule(f"[bold]fomoscan batch[/]  [dim]{now}[/]"))

    try:
        windows_to_run = [window] if window else WINDOWS
        tasks = [fetch_window(client, w) for w in windows_to_run]
        results = await asyncio.gather(*tasks)

        tables = []
        all_top_handles = []

        for win, entries in results:
            tables.append(build_window_table(win, entries, top=20))
            if not all_top_handles and entries:
                # collect top handles from 7d for drill
                all_top_handles = [
                    (e.get("subject", {}).get("canonical", {}).get("display", {}).get("handle") or "")
                    for e in entries[:drill]
                    if e.get("subject", {}).get("canonical", {}).get("display", {}).get("handle")
                ]

        console.print(Columns(tables, padding=(0, 2), equal=False))

        if drill and all_top_handles:
            console.print(Rule("[dim]top trader drill[/]"))
            drill_tasks = [drill_trader(client, h) for h in all_top_handles]
            panels = await asyncio.gather(*drill_tasks)
            console.print(Columns(list(panels), padding=(0, 1), equal=True))

    finally:
        await client.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="fomoscan batch runner")
    p.add_argument("--drill", type=int, default=0, metavar="N",
                   help="drill into top N traders from 7d window")
    p.add_argument("--window", default=None,
                   help="single window only: 24h | 7d | 30d | all")
    args = p.parse_args()
    asyncio.run(main(drill=args.drill, window=args.window))
