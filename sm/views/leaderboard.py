"""pnl and ranking leaderboard views."""

import asyncio
from rich.console import Console
from rich.table import Table
from rich.columns import Columns
from rich import box

from sm.clients.fomoscan import FomoscanClient
from sm.config import cfg
from sm import fmt

console = Console()


def _decode(a) -> float | None:
    """decode fomoscan amount object or plain number to float."""
    if a is None:
        return None
    if isinstance(a, (int, float)):
        return float(a)
    raw = a.get("value")
    dec = a.get("decimals", 6)
    if raw is None:
        return None
    return float(str(raw)) / (10 ** dec)


async def run_fomo_board(window: str = "7d") -> None:
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    try:
        console.print(f"[dim]fetching fomoscan rankings ({window})...[/]")
        rankings = await client.list_rankings()
        items    = rankings.get("items", [])

        board = next(
            (r for r in items if r.get("window") == window),
            items[0] if items else None,
        )
        if not board:
            console.print("[dim]no rankings available[/]")
            return

        data    = await client.get_ranking(board.get("key", ""))
        entries = data.get("entries", []) or []

        date_str = (data.get("dataAsOf") or data.get("computedAt") or "")[:10]
        t = Table(
            title=f"fomoscan - {board.get('title', 'traders')} [{date_str}]",
            box=box.SIMPLE_HEAD,
            header_style="bold white",
            expand=False,
        )
        t.add_column("#",         width=4,  justify="right", style="dim")
        t.add_column("trader",    width=22)
        t.add_column("platform",  width=7)
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

            sources   = subject.get("sources", {}) or {}
            src       = next(iter(sources.values()), {}) if sources else {}
            follow    = src.get("followGraph", {}) or {}
            stats     = src.get("tradingStats", {}) or {}
            followers = follow.get("followers")
            trades    = stats.get("tradeCount") or entry.get("measures", {}).get("trade_count")

            value = _decode(entry.get("value"))

            t.add_row(
                str(rank),
                f"[bold]{handle}[/]",
                fmt.platform_tag(platforms[0]) if platforms else "[dim]-[/]",
                fmt.num(followers) if followers else "-",
                str(trades) if trades is not None else "-",
                f"[bold {fmt.pnl_color(value)}]{fmt.usd(value)}[/]",
            )

        console.print(t)
        console.print(
            "[dim]net flow = sold - bought. "
            "positive = net seller. use 'sm.py trader <handle>' to drill in.[/]"
        )

    except Exception as e:
        console.print(f"[red]error:[/] {e}")
    finally:
        await client.close()


async def run_pump_board(window: str = "7d") -> None:
    client = FomoscanClient() if cfg.has_fomoscan() else None
    if not client:
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    try:
        console.print("[dim]fetching pump.fun leaderboard...[/]")
        data    = await client.pump_leaderboard()
        entries = data.get("entries", []) or data.get("items", []) or []
        period  = data.get("period", "")

        t = Table(
            title=f"pump.fun pnl leaderboard" + (f" ({period})" if period else ""),
            box=box.SIMPLE_HEAD,
            header_style="bold white",
            expand=False,
        )
        t.add_column("#",         width=4,  justify="right", style="dim")
        t.add_column("trader",    width=20)
        t.add_column("v",         width=2,  justify="center")
        t.add_column("realized",  width=12, justify="right")
        t.add_column("open",      width=12, justify="right")
        t.add_column("total",     width=12, justify="right")

        for entry in entries[:100]:
            rank       = entry.get("rank", "?")
            handle     = entry.get("username") or entry.get("handle") or "-"
            realized   = entry.get("realizedPnlUsd") or entry.get("realizedPnl") or 0
            unrealized = entry.get("unrealizedPnlUsd") or entry.get("unrealizedPnl") or 0
            total      = entry.get("pnlUsd") or entry.get("totalPnl")
            if total is None:
                total = float(realized or 0) + float(unrealized or 0)
            verified   = entry.get("isVerified", False)

            t.add_row(
                str(rank),
                f"[bold]{handle}[/]",
                fmt.verified_star(verified),
                f"[{fmt.pnl_color(realized)}]{fmt.usd(realized)}[/]",
                f"[{fmt.pnl_color(unrealized)}]{fmt.usd(unrealized)}[/]",
                f"[bold {fmt.pnl_color(total)}]{fmt.usd(total)}[/]",
            )

        console.print(t)

    except Exception as e:
        console.print(f"[red]error:[/] {e}")
    finally:
        await client.close()
