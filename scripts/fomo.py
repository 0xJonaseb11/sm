#!/usr/bin/env python3
"""
scripts/fomo.py

fomoscan batch - all ranking windows in parallel, side by side.
optionally drills into the top N traders to fetch profile + recent theses.

usage:
    python scripts/fomo.py
    python scripts/fomo.py --drill 5
    python scripts/fomo.py --window 24h
    python scripts/fomo.py --window 7d --drill 10
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
WINDOWS = ["24h", "7d", "30d", "all"]


def _decode(a) -> float | None:
    if a is None:
        return None
    if isinstance(a, (int, float)):
        return float(a)
    raw = a.get("value")
    dec = a.get("decimals", 6)
    return float(str(raw)) / (10 ** dec) if raw is not None else None


async def fetch_window(client: FomoscanClient, items: list, window: str) -> tuple[str, list, str]:
    board = next((r for r in items if r.get("window") == window), None)
    if not board:
        return window, [], ""
    try:
        data    = await client.get_ranking(board.get("key", ""))
        entries = data.get("entries", []) or []
        date_at = (data.get("dataAsOf") or data.get("computedAt") or "")[:10]
        return window, entries, date_at
    except Exception as e:
        console.print(f"[dim]{window} error: {e}[/]")
        return window, [], ""


def build_window_table(window: str, entries: list, date_at: str, top: int = 20) -> Table:
    t = Table(
        title=f"[bold]{window}[/]" + (f"  [dim]{date_at}[/]" if date_at else ""),
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        min_width=42,
        expand=False,
    )
    t.add_column("#",         width=3,  justify="right", style="dim")
    t.add_column("trader",    width=22, no_wrap=True)
    t.add_column("followers", width=7,  justify="right", style="dim")
    t.add_column("net flow",  width=11, justify="right")

    for entry in entries[:top]:
        rank    = entry.get("rank", "?")
        subject = entry.get("subject", {}) or {}
        canon   = (subject.get("canonical") or {})
        display = (canon.get("display") or {})
        handle  = display.get("handle") or display.get("name") or "-"
        value   = _decode(entry.get("value"))

        sources  = subject.get("sources", {}) or {}
        src      = next(iter(sources.values()), {}) if sources else {}
        follow   = (src.get("followGraph") or {})
        followers = follow.get("followers")

        t.add_row(
            str(rank),
            f"[bold]{handle}[/]",
            fmt.num(followers) if followers else "-",
            f"[bold {fmt.pnl_color(value)}]{fmt.usd(value)}[/]",
        )
    return t


async def drill_trader(client: FomoscanClient, handle: str, rank: int) -> Panel:
    lines = [f"[dim]#{rank}[/]  [bold white]{handle}[/]"]
    try:
        u = await client.get_user_by_handle(handle)
        name    = u.get("name", "") or handle
        twitter = u.get("twitter", "")
        sol     = u.get("solanaAddress") or ""
        uid     = u.get("id", "")

        if name != handle:
            lines[0] = f"[dim]#{rank}[/]  [bold white]{name}[/]  [dim]{handle}[/]"
        if twitter:
            t_handle = twitter.rstrip("/").split("/")[-1]
            lines.append(f"[cyan]@{t_handle}[/]")
        if sol:
            lines.append(f"[dim]{fmt.short_addr(sol, 7)}[/]")

        if uid:
            theses_resp = await client.get_theses_for_user(uid, limit=3)
            items = (
                theses_resp if isinstance(theses_resp, list)
                else theses_resp.get("items", []) or []
            )
            for t in items[:3]:
                sym  = t.get("tokenSymbol") or (t.get("token", {}) or {}).get("symbol", "") or "?"
                body = (t.get("body") or t.get("text") or "")[:55]
                lines.append(f"  [yellow]{sym}[/]  [dim]{body}[/]")
            if not items:
                lines.append("[dim]no theses[/]")

    except Exception as e:
        lines.append(f"[dim red]{str(e)[:50]}[/]")

    return Panel("\n".join(lines), expand=True, border_style="dim")


async def main(drill: int = 0, window: str | None = None, top: int = 20):
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client   = FomoscanClient()
    now      = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    console.print(Rule(f"[bold]fomoscan batch[/]  [dim]{now}[/]"))

    try:
        rankings = await client.list_rankings()
        meta     = rankings.get("items", [])

        windows_to_run = [window] if window else WINDOWS
        results = await asyncio.gather(
            *[fetch_window(client, meta, w) for w in windows_to_run]
        )

        tables       = []
        drill_source = []

        for win, entries, date_at in results:
            tables.append(build_window_table(win, entries, date_at, top=top))
            # collect from the 7d window for drilling
            if drill and win == "7d" and entries:
                for entry in entries[:drill]:
                    subject = entry.get("subject", {}) or {}
                    h = (
                        (subject.get("canonical") or {})
                        .get("display", {}).get("handle", "")
                    )
                    if h:
                        drill_source.append((h, entry.get("rank", 0)))

        # if single window was requested, drill from that
        if drill and window and results:
            _, entries, _ = results[0]
            drill_source  = []
            for entry in entries[:drill]:
                subject = entry.get("subject", {}) or {}
                h = (
                    (subject.get("canonical") or {})
                    .get("display", {}).get("handle", "")
                )
                if h:
                    drill_source.append((h, entry.get("rank", 0)))

        console.print(Columns(tables, padding=(0, 2), equal=False))

        if drill and drill_source:
            console.print(Rule("[dim]top trader drill[/]"))
            panels = await asyncio.gather(
                *[drill_trader(client, h, r) for h, r in drill_source]
            )
            # 2 panels per row
            for i in range(0, len(panels), 2):
                console.print(Columns(list(panels[i:i+2]), padding=(0, 1), equal=True))

    finally:
        await client.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="fomoscan batch - all windows in parallel")
    p.add_argument("--drill",  type=int, default=0, metavar="N",
                   help="drill into top N traders")
    p.add_argument("--window", default=None,
                   help="single window: 24h | 7d | 30d | all")
    p.add_argument("--top",    type=int, default=20,
                   help="rows per window (default 20)")
    args = p.parse_args()
    asyncio.run(main(drill=args.drill, window=args.window, top=args.top))
