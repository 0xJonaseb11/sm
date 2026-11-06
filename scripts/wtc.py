#!/usr/bin/env python3
"""
scripts/wtc.py  -  watch the charts

polls a list of fomoscan traders every N seconds.
shows a live table with their latest theses and alerts on new ones.
auto-loads the top N from the fomo or pump leaderboard if --board is set.

usage:
    python scripts/wtc.py the_nutgod kyle cryptolyxe
    python scripts/wtc.py --board fomo              # auto top 10 fomo traders
    python scripts/wtc.py --board pump              # auto top 10 pump traders
    python scripts/wtc.py --board fomo --top 20
    python scripts/wtc.py the_nutgod --interval 60
    python scripts/wtc.py --handles-file handles.txt
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
from rich.console import Group
from rich import box

sys.path.insert(0, __import__("os").path.dirname(__file__) + "/..")

from sm.clients.fomoscan import FomoscanClient
from sm.cache import handle_cache, thesis_cache
from sm.config import cfg
from sm import fmt

console = Console()

# state per handle
_state:  dict[str, dict] = {}
_seen:   dict[str, set]  = {}   # seen thesis ids per handle
_alerts: list[str]       = []


def _decode(a) -> float | None:
    if a is None:
        return None
    if isinstance(a, (int, float)):
        return float(a)
    raw = a.get("value")
    dec = a.get("decimals", 6)
    return float(str(raw)) / (10 ** dec) if raw is not None else None


async def load_board_handles(client: FomoscanClient, board: str, top: int) -> list[str]:
    handles = []
    try:
        if board == "fomo":
            rankings = await client.list_rankings()
            meta     = rankings.get("items", [])
            item     = next((r for r in meta if r.get("window") == "7d"), meta[0] if meta else None)
            if item:
                data = await client.get_ranking(item.get("key", ""))
                for entry in data.get("entries", [])[:top]:
                    h = (entry.get("subject", {}).get("canonical", {}).get("display", {}) or {}).get("handle", "")
                    if h:
                        handles.append(h)
        elif board == "pump":
            r = await client.pump_leaderboard()
            for entry in (r.get("entries", []) or [])[:top]:
                h = entry.get("username") or entry.get("handle") or ""
                if h:
                    handles.append(h)
    except Exception as e:
        console.print(f"[dim]board load error: {e}[/]")
    return handles


async def poll_one(client: FomoscanClient, handle: str) -> dict:
    st = _state.get(handle, {
        "handle":        handle,
        "name":          handle,
        "sol":           "",
        "twitter":       "",
        "thesis_count":  0,
        "new_theses":    [],
        "last_sym":      "",
        "last_body":     "",
        "updated":       0,
        "error":         "",
        "first_run":     True,
    })

    try:
        # only fetch profile on first run or every 10m
        if st.get("first_run") or (time.time() - st.get("prof_updated", 0)) > 600:
            u   = await client.get_user_by_handle(handle)
            uid = u.get("id", "")
            st["name"]        = u.get("name", "") or handle
            st["sol"]         = u.get("solanaAddress", "") or ""
            st["twitter"]     = u.get("twitter", "") or ""
            st["uid"]         = uid
            st["prof_updated"] = time.time()

        uid = st.get("uid", "")
        if uid:
            theses_resp = await client.get_theses_for_user(uid, limit=5)
            theses = (
                theses_resp if isinstance(theses_resp, list)
                else theses_resp.get("items", []) or []
            )
            seen     = _seen.setdefault(handle, set())
            new_this = []

            for t in theses:
                tid  = t.get("id", "")
                sym  = t.get("tokenSymbol") or (t.get("token", {}) or {}).get("symbol", "") or "?"
                body = (t.get("body") or t.get("text") or "")[:80]
                if tid:
                    if tid not in seen:
                        seen.add(tid)
                        if not st.get("first_run"):
                            new_this.append({"sym": sym, "body": body, "ts": time.time()})
                    if not st["last_sym"]:
                        st["last_sym"]  = sym
                        st["last_body"] = body

            # update last seen thesis
            if theses:
                t0 = theses[0]
                st["last_sym"]  = t0.get("tokenSymbol") or (t0.get("token", {}) or {}).get("symbol", "") or "?"
                st["last_body"] = (t0.get("body") or t0.get("text") or "")[:60]

            st["thesis_count"] = len(theses)
            st["new_theses"]   = new_this

            if new_this:
                now_str = datetime.datetime.now().strftime("%H:%M:%S")
                for item in new_this:
                    _alerts.append(
                        f"[dim]{now_str}[/]  "
                        f"[bold cyan]{st['name']}[/]  "
                        f"[yellow]{item['sym']}[/]  "
                        f"[dim]{item['body'][:70]}[/]"
                    )
                while len(_alerts) > 15:
                    _alerts.pop(0)

        st["first_run"] = False
        st["error"]     = ""
        st["updated"]   = int(time.time())

    except Exception as e:
        st["error"]   = str(e)[:60]
        st["updated"] = int(time.time())

    _state[handle] = st
    return st


def build_watch_table(handles: list[str]) -> Table:
    t = Table(
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=True,
        pad_edge=False,
        show_footer=False,
    )
    t.add_column("trader",      width=22, no_wrap=True)
    t.add_column("wallet",      width=14)
    t.add_column("theses",      width=7,  justify="right", style="dim")
    t.add_column("updated",     width=9,  style="dim")
    t.add_column("latest call", ratio=1,  no_wrap=True)

    for handle in handles:
        st     = _state.get(handle, {})
        name   = st.get("name", handle)
        sol    = st.get("sol", "")
        tc     = st.get("thesis_count", "-")
        upd    = st.get("updated", 0)
        error  = st.get("error", "")
        sym    = st.get("last_sym", "")
        body   = st.get("last_body", "")
        new    = st.get("new_theses", [])

        if error:
            latest = f"[dim red]{error}[/]"
        elif sym:
            highlight = "[bold yellow]" if new else "[yellow]"
            latest = f"{highlight}{sym}[/]  [dim]{body[:55]}[/]"
        else:
            latest = "[dim]-[/]"

        t.add_row(
            f"[bold]{name}[/]",
            fmt.short_addr(sol, 5),
            str(tc),
            fmt.ts_ago(upd),
            latest,
        )
    return t


async def watch_loop(client: FomoscanClient, handles: list[str], interval: int) -> None:
    console.print(
        f"[dim]watching {len(handles)} traders  |  "
        f"polling every {interval}s  |  ctrl+c to stop[/]\n"
    )

    # initial silent load
    await asyncio.gather(*[poll_one(client, h) for h in handles])

    with Live(console=console, refresh_per_second=2, screen=False) as live:
        while True:
            await asyncio.gather(*[poll_one(client, h) for h in handles])

            now_str    = datetime.datetime.now().strftime("%H:%M:%S")
            watch_tbl  = build_watch_table(handles)
            alert_body = "\n".join(_alerts) if _alerts else "[dim]listening for new theses...[/]"
            alert_panel = Panel(
                alert_body,
                title=f"[dim]alerts[/]  [bold]{now_str}[/]",
                expand=True,
            )
            live.update(Group(watch_tbl, alert_panel))
            await asyncio.sleep(interval)


async def main(handles: list[str], interval: int, board: str | None, top: int) -> None:
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    now    = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    console.print(Rule(f"[bold]wtc - watch the charts[/]  [dim]{now}[/]"))

    try:
        if board:
            console.print(f"[dim]loading top {top} from {board} board...[/]")
            board_handles = await load_board_handles(client, board, top=top)
            if not board_handles:
                console.print("[red]no handles loaded from board[/]")
                return
            handles = board_handles
            console.print("[dim]watching:[/] " + "  ".join(f"[cyan]{h}[/]" for h in handles))

        if not handles:
            console.print("[red]no traders specified. pass handles as args or use --board[/]")
            return

        await watch_loop(client, handles, interval=interval)

    except KeyboardInterrupt:
        console.print("\n[dim]stopped.[/]")
    finally:
        await client.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="watch fomoscan traders for new theses")
    p.add_argument("handles",        nargs="*", help="fomoscan handles to watch")
    p.add_argument("--board",        default=None, help="auto-load handles: fomo | pump")
    p.add_argument("--top",          type=int, default=10, help="how many from board (default 10)")
    p.add_argument("--interval",     type=int, default=90,
                   help="poll interval in seconds (default 90)")
    p.add_argument("--handles-file", default=None,
                   help="file with one handle per line")
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
        interval=max(args.interval, 30),
        board=args.board,
        top=args.top,
    ))
