"""pnl leaderboard views."""

import asyncio
from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel

from sm.clients.fomoscan import FomoscanClient
from sm.clients.pump import PumpClient
from sm.config import cfg
from sm import fmt

console = Console()


def _pnl_table(title: str, rows: list, cols: list[tuple]) -> Table:
    t = Table(
        title=title,
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
        pad_edge=True,
    )
    t.add_column("#", width=4, justify="right", style="dim")
    for label, width, justify in cols:
        t.add_column(label, width=width, justify=justify)

    for i, row in enumerate(rows, 1):
        vals = [str(i)]
        for col_fn in row:
            vals.append(col_fn)
        t.add_row(*vals)
    return t


def _decode_amount(a: dict | None) -> float | None:
    """convert fomoscan amount object {value, decimals, currency} to float."""
    if a is None:
        return None
    if isinstance(a, (int, float)):
        return float(a)
    raw = a.get("value")
    decimals = a.get("decimals", 6)
    if raw is None:
        return None
    return float(str(raw)) / (10 ** decimals)


async def run_fomo_board(window: str = "7d") -> None:
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    try:
        console.print(f"[dim]fetching fomoscan rankings ({window})...[/]")
        rankings = await client.list_rankings()
        items = rankings.get("items", [])

        # match by window; fall back to first entry
        board = next(
            (r for r in items if r.get("window") == window),
            items[0] if items else None,
        )

        if not board:
            console.print("[dim]no rankings available[/]")
            return

        # the key field is used to fetch, not id
        board_key = board.get("key", "")
        data = await client.get_ranking(board_key)
        entries = data.get("entries", []) or data.get("items", [])

        title_str = (
            f"fomoscan - {board.get('title', board.get('metric', 'traders'))} "
            f"(as of {(data.get('dataAsOf') or data.get('computedAt') or '')[:10]})"
        )

        t = Table(
            title=title_str,
            box=box.SIMPLE_HEAD,
            header_style="bold white",
            expand=False,
        )
        t.add_column("#",         width=4,  justify="right", style="dim")
        t.add_column("trader",    width=22)
        t.add_column("platform",  width=8)
        t.add_column("followers", width=10, justify="right", style="dim")
        t.add_column("trades",    width=7,  justify="right", style="dim")
        t.add_column("net flow",  width=13, justify="right")

        for entry in entries[:50]:
            rank    = entry.get("rank", "?")
            subject = entry.get("subject", {}) or {}
            canon   = subject.get("canonical", {}) or {}
            display = canon.get("display", {}) or {}
            handle  = display.get("handle") or display.get("name") or "-"
            platforms = canon.get("platforms", [])

            # pull trading stats from first available source
            sources = subject.get("sources", {}) or {}
            first_src = next(iter(sources.values()), {}) if sources else {}
            follow   = (first_src.get("followGraph") or {})
            stats    = (first_src.get("tradingStats") or {})
            followers   = follow.get("followers")
            trade_count = stats.get("tradeCount") or entry.get("measures", {}).get("trade_count")

            # value = net flow in USD
            value = _decode_amount(entry.get("value"))
            measures = entry.get("measures", {}) or {}
            vol = _decode_amount(measures.get("volume"))

            t.add_row(
                str(rank),
                f"[bold]{handle}[/]",
                fmt.platform_tag(platforms[0]) if platforms else "[dim]-[/]",
                f"{followers:,}" if followers else "-",
                str(trade_count) if trade_count is not None else "-",
                f"[bold {fmt.pnl_color(value)}]{fmt.usd(value)}[/]",
            )

        console.print(t)
        console.print(
            f"[dim]metric: net flow (sold - bought). "
            f"negative = net buyer. use 'trader <handle>' to drill in.[/]"
        )

    except Exception as e:
        console.print(f"[red]fomoscan board error:[/] {e}")
    finally:
        await client.close()


async def run_pump_board(window: str = "7d") -> None:
    # try fomoscan pump leaderboard first (no pump key needed)
    client_fs = FomoscanClient() if cfg.has_fomoscan() else None
    client_p = PumpClient() if cfg.has_pump() else None

    data = None
    source = ""

    if client_fs:
        try:
            console.print("[dim]fetching pump.fun leaderboard via fomoscan...[/]")
            data = await client_fs.pump_leaderboard()
            source = "fomoscan/pump"
        except Exception as e:
            console.print(f"[dim]fomoscan pump board failed: {e}[/]")

    if data is None and client_p:
        try:
            console.print("[dim]fetching pump.fun leaderboard...[/]")
            data = await client_p.pnl_leaderboard(window=window)
            source = "pump.fun"
        except Exception as e:
            console.print(f"[dim]pump.fun board failed: {e}[/]")

    if data is None:
        console.print("[dim]no leaderboard data available[/]")
        if client_fs:
            await client_fs.close()
        if client_p:
            await client_p.close()
        return

    entries = (
        data.get("entries")
        or data.get("items")
        or data.get("data")
        or []
    )
    period = data.get("period", "")

    t = Table(
        title=f"pump.fun pnl leaderboard ({source}"
              + (f" - {period}" if period else "") + ")",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
    )
    t.add_column("#", width=4, justify="right", style="dim")
    t.add_column("trader", width=20)
    t.add_column("wallet", width=14)
    t.add_column("realized", width=12, justify="right")
    t.add_column("unrealized", width=12, justify="right")
    t.add_column("total (usd)", width=12, justify="right")

    for entry in entries[:100]:
        rank     = entry.get("rank", "?")
        handle   = entry.get("username") or entry.get("handle") or entry.get("name") or "-"
        wallet   = entry.get("wallet", "") or entry.get("address", "")
        realized = entry.get("realizedPnlUsd") or entry.get("realizedPnl") or entry.get("realized") or 0
        unrealized = entry.get("unrealizedPnlUsd") or entry.get("unrealizedPnl") or entry.get("unrealized") or 0
        total    = entry.get("pnlUsd") or entry.get("totalPnl") or entry.get("total")
        if total is None:
            total = float(realized or 0) + float(unrealized or 0)
        verified = entry.get("isVerified", False)
        name_str = f"[bold]{handle}[/]" + ("[dim]*[/]" if verified else "")

        t.add_row(
            str(rank),
            name_str,
            fmt.short_addr(str(wallet), 5),
            f"[{fmt.pnl_color(realized)}]{fmt.usd(realized)}[/]",
            f"[{fmt.pnl_color(unrealized)}]{fmt.usd(unrealized)}[/]",
            f"[bold {fmt.pnl_color(total)}]{fmt.usd(total)}[/]",
        )

    console.print(t)

    if client_fs:
        await client_fs.close()
    if client_p:
        await client_p.close()
