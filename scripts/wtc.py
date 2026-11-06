#!/usr/bin/env python3
"""
scripts/wtc.py  (watch the charts)

polls a list of traders every N seconds and prints any new theses
or rank changes. runs until ctrl+c.

usage:
    python scripts/wtc.py the_nutgod cryptolyxe kyle
    python scripts/wtc.py --handles-file handles.txt
    python scripts/wtc.py --board fomo                # watch fomo #1-10 from live board
    python scripts/wtc.py --board pump                # watch pump #1-10
    python scripts/wtc.py the_nutgod --interval 60    # poll every 60s (default 90)
"""

import asyncio
import sys
import datetime
import argparse
import time

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from rich import box

sys.path.insert(0, __import__("os").path.dirname(__file__) + "/..")

from sm.clients.fomoscan import FomoscanClient
from sm.config import cfg
from sm import fmt

console = Console()

# track last seen thesis id per trader so we only alert on new ones
_seen_theses: dict[str, set] = {}
# store trader state for the live table
_state: dict[str, dict] = {}


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


async def load_board_handles(client: FomoscanClient, board: str, top: int = 10) -> list[str]:
    """pull top N handles from fomo or pump leaderboard."""
    handles = []
    if board == "fomo":
        try:
            rankings = await client.list_rankings()
            items = rankings.get("items", [])
            meta = next((r for r in items if r.get("window") == "7d"), items[0] if items else None)
            if meta:
                data = await client.get_ranking(meta.get("key", ""))
                for entry in data.get("entries", [])[:top]:
                    h = (entry.get("subject", {}).get("canonical", {}).get("display", {}).get("handle") or "")
                    if h:
                        handles.append(h)
        except Exception as e:
            console.print(f"[dim]board load error: {e}[/]")
    elif board == "pump":
        try:
            r = await client.pump_leaderboard()
            for entry in (r.get("entries", []) or r.get("items", []))[:top]:
                h = entry.get("username") or entry.get("handle") or ""
                if h:
                    handles.append(h)
        except Exception as e:
            console.print(f"[dim]pump board error: {e}[/]")
    return handles


async def poll_trader(client: FomoscanClient, handle: str) -> dict:
    """pull profile + recent theses for one trader. returns state dict."""
    state = _state.get(handle, {"handle": handle, "thesis_count": 0, "new": []})
    try:
        u = await client.get_user_by_handle(handle)
        uid = u.get("id", "")
        state["name"]    = u.get("name") or handle
        state["sol"]     = u.get("solanaAddress") or ""
        state["twitter"] = u.get("twitter") or ""
        state["uid"]     = uid

        if uid:
            theses_resp = await client.get_theses_for_user(uid, limit=5)
            theses = theses_resp if isinstance(theses_resp, list) else theses_resp.get("items", [])
            seen = _seen_theses.setdefault(handle, set())
            new = []
            for t in theses:
                tid = t.get("id", "")
                if tid and tid not in seen:
                    seen.add(tid)
                    sym  = t.get("tokenSymbol") or "?"
                    body = (t.get("body") or t.get("text") or "")[:80]
                    new.append({"sym": sym, "body": body, "ts": time.time()})
            state["new"] = new
            state["thesis_count"] = len(theses)
        state["updated"] = int(time.time())
        state["error"]   = ""
    except Exception as e:
        state["error"] = str(e)[:60]
        state["updated"] = int(time.time())
    _state[handle] = state
    return state


def build_watch_table(handles: list[str]) -> Table:
    t = Table(
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=True,
        pad_edge=False,
    )
    t.add_column("trader",    width=20, no_wrap=True)
    t.add_column("wallet",    width=14)
    t.add_column("theses",    width=7,  justify="right", style="dim")
    t.add_column("updated",   width=10, style="dim")
    t.add_column("latest",    ratio=1,  no_wrap=True)

    for handle in handles:
        st = _state.get(handle, {})
        name    = st.get("name", handle)
        sol     = st.get("sol", "")
        tc      = st.get("thesis_count", "-")
        updated = st.get("updated", 0)
        error   = st.get("error", "")
        new     = st.get("new", [])

        if error:
            latest = f"[dim red]{error}[/]"
        elif new:
            latest_item = new[-1]
            latest = f"[bold yellow]{latest_item['sym']}[/]  [dim]{latest_item['body'][:60]}[/]"
        else:
            latest = "[dim]-[/]"

        t.add_row(
            f"[bold]{name}[/]",
            fmt.short_addr(sol, 5),
            str(tc),
            fmt.ts_ago(updated),
            latest,
        )
    return t


async def watch_loop(client: FomoscanClient, handles: list[str], interval: int):
    """poll all handles in a loop, alert on new theses, refresh table."""
    console.print(f"[dim]watching {len(handles)} traders, polling every {interval}s[/]")
    console.print(f"[dim]ctrl+c to stop[/]\n")

    # initial load
    await asyncio.gather(*[poll_trader(client, h) for h in handles])

    alert_lines: list[str] = []

    with Live(console=console, refresh_per_second=1, screen=False) as live:
        while True:
            now = datetime.datetime.now().strftime("%H:%M:%S")

            # run all polls in parallel
            results = await asyncio.gather(
                *[poll_trader(client, h) for h in handles],
                return_exceptions=True,
            )

            # collect new thesis alerts
            for st in results:
                if isinstance(st, dict) and st.get("new"):
                    for item in st["new"]:
                        line = (
                            f"[dim]{now}[/]  "
                            f"[bold cyan]{st.get('name', st.get('handle','?'))}[/]  "
                            f"[yellow]{item['sym']}[/]  "
                            f"[dim]{item['body'][:70]}[/]"
                        )
                        alert_lines.append(line)
                        # keep last 10 alerts
                        if len(alert_lines) > 10:
                            alert_lines.pop(0)

            # rebuild display
            watch_tbl = build_watch_table(handles)
            alert_text = "\n".join(alert_lines) if alert_lines else "[dim]no new theses yet[/]"
            alert_panel = Panel(alert_text, title=f"[dim]alerts[/]  [bold]{now}[/]", expand=True)

            from rich.console import Group
            live.update(Group(watch_tbl, alert_panel))

            await asyncio.sleep(interval)


async def main(handles: list[str], interval: int, board: str | None, top: int):
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    console.print(Rule(f"[bold]wtc - watch the charts[/]  [dim]{now}[/]"))

    try:
        if board:
            console.print(f"[dim]loading top {top} from {board} board...[/]")
            board_handles = await load_board_handles(client, board, top=top)
            if not board_handles:
                console.print("[red]no handles loaded from board[/]")
                return
            handles = board_handles
            console.print(f"[dim]watching: {', '.join(handles)}[/]")

        if not handles:
            console.print("[red]no traders to watch. pass handles as args or use --board[/]")
            return

        await watch_loop(client, handles, interval=interval)

    except KeyboardInterrupt:
        console.print("\n[dim]stopped.[/]")
    finally:
        await client.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="watch traders for new theses")
    p.add_argument("handles", nargs="*", help="fomoscan handles to watch")
    p.add_argument("--board",    default=None, help="auto-load top handles: fomo | pump")
    p.add_argument("--top",      type=int, default=10, help="how many from board (default 10)")
    p.add_argument("--interval", type=int, default=90,
                   help="poll interval in seconds (default 90, min 60 to stay in rate limit)")
    p.add_argument("--handles-file", default=None,
                   help="read one handle per line from this file")
    args = p.parse_args()

    all_handles = list(args.handles)
    if args.handles_file:
        try:
            with open(args.handles_file) as f:
                all_handles += [l.strip() for l in f if l.strip() and not l.startswith("#")]
        except FileNotFoundError:
            console.print(f"[red]file not found:[/] {args.handles_file}")
            sys.exit(1)

    asyncio.run(main(
        handles=all_handles,
        interval=max(args.interval, 60),
        board=args.board,
        top=args.top,
    ))
