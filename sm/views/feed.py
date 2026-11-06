"""live websocket feed view (requires fomoscan Growth plan or higher)."""

import asyncio
import time
from collections import deque

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich import box

from sm.clients.fomoscan import FomoscanStream
from sm.config import cfg
from sm import fmt

console = Console()
_ROWS: deque = deque(maxlen=cfg.feed_rows)


def _build_table(rows: list) -> Table:
    t = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold white",
        expand=True,
        pad_edge=False,
    )
    t.add_column("time",     width=9,  style="dim")
    t.add_column("platform", width=6)
    t.add_column("trader",   width=20, no_wrap=True)
    t.add_column("token",    width=10, no_wrap=True)
    t.add_column("side",     width=5)
    t.add_column("value",    width=10, justify="right")
    t.add_column("note",     ratio=1,  no_wrap=True)

    for row in rows:
        platform  = row.get("platform", "")
        handle    = row.get("handle", "") or fmt.short_addr(row.get("wallet", ""), 6)
        token_sym = row.get("token_sym", "") or ""
        token_addr = row.get("token_addr", "") or ""
        token_label = token_sym or fmt.short_addr(token_addr, 4) or "-"
        side      = row.get("side") or ""
        val       = row.get("usd_value")
        body      = (row.get("body") or "")[:60]
        ts        = row.get("ts", 0)

        t.add_row(
            fmt.ts_ago(ts),
            fmt.platform_tag(platform),
            f"[bold]{handle[:18]}[/]",
            f"[yellow]{token_label}[/]",
            fmt.side_tag(side),
            f"[{fmt.pnl_color(val)}]{fmt.usd(val)}[/]" if val else "",
            f"[dim]{body}[/]" if body else "",
        )
    return t


async def run_feed(
    platform: str | None = None,
    token: str | None = None,
    db=None,
) -> None:
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    platforms = [platform] if platform else None
    tokens    = [token] if token else None
    stream    = FomoscanStream()

    console.print(
        "[dim]connecting to fomoscan stream...[/]"
        + (f" platform=[cyan]{platform}[/]" if platform else "")
        + (f" token=[yellow]{token}[/]" if token else "")
        + " [dim](ctrl+c to quit)[/]"
    )

    with Live(console=console, refresh_per_second=4, screen=False) as live:
        async for frame in stream.listen(platforms=platforms, tokens=tokens):
            parts = fmt.extract_post_parts(frame)
            _ROWS.appendleft(parts)

            if db:
                try:
                    from sm import db as dbmod
                    await dbmod.insert_event(db, {
                        "id":        parts.get("id", ""),
                        "kind":      "post",
                        "platform":  parts.get("platform", ""),
                        "trader_id": parts.get("trader_id", ""),
                        "handle":    parts.get("handle", ""),
                        "wallet":    parts.get("wallet", ""),
                        "token_addr":parts.get("token_addr", ""),
                        "token_sym": parts.get("token_sym", ""),
                        "side":      parts.get("side") or "",
                        "usd_value": parts.get("usd_value"),
                        "body":      parts.get("body", ""),
                        "raw":       parts.get("raw", {}),
                        "ts":        parts.get("ts", int(time.time())),
                    })
                except Exception:
                    pass

            live.update(_build_table(list(_ROWS)))
