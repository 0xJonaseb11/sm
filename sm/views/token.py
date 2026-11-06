"""token callout wall and thesis feed for a specific token address."""

import asyncio
import datetime
from rich.console import Console
from rich.table import Table
from rich import box

from sm.clients.fomoscan import FomoscanClient
from sm.config import cfg
from sm import fmt

console = Console()


async def run_token_wall(token_addr: str) -> None:
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    try:
        console.print(f"[dim]fetching theses + callouts for {fmt.short_addr(token_addr)}...[/]")

        # fetch both in parallel
        results = await asyncio.gather(
            _fetch_theses(client, token_addr),
            _fetch_pump_callouts(client, token_addr),
            return_exceptions=True,
        )

        theses   = results[0] if not isinstance(results[0], Exception) else []
        callouts = results[1] if not isinstance(results[1], Exception) else []

        if not theses and not callouts:
            console.print("[dim]no thesis or callout data found for this token[/]")
            return

        if theses:
            _print_thesis_table(theses, token_addr, source="fomoscan theses")

        if callouts:
            console.print()
            _print_callout_table(callouts, token_addr, source="pump.fun callouts")

    except Exception as e:
        console.print(f"[red]error:[/] {e}")
    finally:
        await client.close()


async def _fetch_theses(client: FomoscanClient, token_addr: str) -> list:
    try:
        resp = await client.get_theses_for_token(token_addr)
        raw  = resp if isinstance(resp, list) else resp.get("items", []) or resp.get("data", [])
        return raw or []
    except Exception:
        return []


async def _fetch_pump_callouts(client: FomoscanClient, token_addr: str) -> list:
    try:
        resp = await client.pump_token_callouts(token_addr)
        return resp.get("items", []) or resp.get("data", []) or []
    except Exception:
        return []


def _ts_int(ts) -> int:
    if isinstance(ts, int):
        return ts
    if isinstance(ts, (float,)):
        return int(ts)
    if isinstance(ts, str):
        return fmt.iso_to_ts(ts)
    return 0


def _print_thesis_table(entries: list, token_addr: str, source: str) -> None:
    t = Table(
        title=f"{source} - {fmt.short_addr(token_addr, 8)}",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
    )
    t.add_column("time",    width=11, style="dim")
    t.add_column("trader",  width=20)
    t.add_column("network", width=8,  style="dim")
    t.add_column("body",    ratio=1,  no_wrap=True)

    for entry in entries[:25]:
        handle = (
            entry.get("handle")
            or entry.get("authorHandle")
            or (entry.get("author") or {}).get("handle", "")
            or "-"
        )
        body    = (entry.get("body") or entry.get("text") or "")[:80]
        network = entry.get("tokenNetwork", "")
        ts_raw  = entry.get("createdAt") or entry.get("postedAt") or 0
        ts_int  = _ts_int(ts_raw)

        t.add_row(
            fmt.ts_ago(ts_int),
            f"[bold]{handle}[/]",
            network,
            f"[dim]{body}[/]",
        )
    console.print(t)


def _print_callout_table(entries: list, token_addr: str, source: str) -> None:
    t = Table(
        title=f"{source} - {fmt.short_addr(token_addr, 8)}",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
    )
    t.add_column("time",    width=11, style="dim")
    t.add_column("trader",  width=20)
    t.add_column("wallet",  width=12)
    t.add_column("body",    ratio=1,  no_wrap=True)

    for entry in entries[:25]:
        handle = (
            entry.get("handle")
            or entry.get("username")
            or (entry.get("author") or {}).get("handle", "")
            or "-"
        )
        wallet = entry.get("wallet", "") or ""
        body   = (entry.get("body") or entry.get("text") or entry.get("content") or "")[:80]
        ts_raw = entry.get("createdAt") or entry.get("postedAt") or 0
        ts_int = _ts_int(ts_raw)

        t.add_row(
            fmt.ts_ago(ts_int),
            f"[bold]{handle}[/]",
            fmt.short_addr(str(wallet), 4),
            f"[dim]{body}[/]",
        )
    console.print(t)
