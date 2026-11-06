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


async def run_fomo_board(window: str = "7d") -> None:
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    try:
        console.print(f"[dim]fetching fomoscan rankings ({window})...[/]")
        rankings = await client.list_rankings()
        items = rankings.get("items", [])

        # find a pnl ranking matching the window
        board = None
        for item in items:
            if window in item.get("window", "") or window in item.get("id", ""):
                board = item
                break

        if not board and items:
            board = items[0]

        if not board:
            console.print("[dim]no rankings available from fomoscan[/]")
            await client.close()
            return

        board_id = board.get("id", "")
        data = await client.get_ranking(board_id)
        entries = data.get("items", []) or data.get("entries", [])

        t = Table(
            title=f"fomoscan - {board.get('metric','pnl')} ({board.get('window', window)})",
            box=box.SIMPLE_HEAD,
            header_style="bold white",
            expand=False,
        )
        t.add_column("#", width=4, justify="right", style="dim")
        t.add_column("trader", width=20)
        t.add_column("platform", width=8)
        t.add_column("realized", width=12, justify="right")
        t.add_column("unrealized", width=12, justify="right")
        t.add_column("total", width=12, justify="right")

        for i, entry in enumerate(entries[:50], 1):
            trader = entry.get("trader", {}) or {}
            canonical = (trader.get("canonical") or {})
            handle = canonical.get("handle", "") or entry.get("handle", "")
            platforms = canonical.get("platforms", [])
            platform_str = ", ".join(platforms) if platforms else "-"

            pnl = entry.get("pnl", {}) or {}
            realized = pnl.get("realized", {}) or {}
            unrealized = pnl.get("unrealized", {}) or {}
            total = pnl.get("total", {}) or {}

            def _amount(a: dict | float | None) -> float | None:
                if a is None:
                    return None
                if isinstance(a, dict):
                    raw = a.get("amount") or a.get("usd") or a.get("value")
                    if raw is not None:
                        return float(str(raw)) / 1e6 if int(str(raw).replace("-","")) > 1_000_000 else float(str(raw))
                return float(a)

            r_val = _amount(realized) or _amount(entry.get("realizedPnl")) or _amount(entry.get("realized"))
            u_val = _amount(unrealized) or _amount(entry.get("unrealizedPnl")) or _amount(entry.get("unrealized"))
            t_val = _amount(total) or _amount(entry.get("totalPnl")) or _amount(entry.get("pnlTotal"))

            if t_val is None and r_val is not None and u_val is not None:
                t_val = r_val + u_val

            t.add_row(
                str(i),
                f"[bold]{handle or '-'}[/]",
                fmt.platform_tag(platforms[0] if platforms else ""),
                f"[{fmt.pnl_color(r_val)}]{fmt.usd(r_val)}[/]",
                f"[{fmt.pnl_color(u_val)}]{fmt.usd(u_val)}[/]",
                f"[bold {fmt.pnl_color(t_val)}]{fmt.usd(t_val)}[/]",
            )

        console.print(t)

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

    entries = data.get("items", []) or data.get("data", []) or []

    t = Table(
        title=f"pump.fun pnl leaderboard ({source})",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
    )
    t.add_column("#", width=4, justify="right", style="dim")
    t.add_column("trader", width=20)
    t.add_column("wallet", width=14)
    t.add_column("realized", width=12, justify="right")
    t.add_column("unrealized", width=12, justify="right")
    t.add_column("total", width=12, justify="right")

    for i, entry in enumerate(entries[:100], 1):
        handle = (
            entry.get("handle")
            or entry.get("username")
            or entry.get("name")
            or entry.get("trader", {}).get("handle", "")
        )
        wallet = entry.get("wallet", "") or entry.get("address", "")
        realized = entry.get("realizedPnl") or entry.get("realized") or 0
        unrealized = entry.get("unrealizedPnl") or entry.get("unrealized") or 0
        total = entry.get("totalPnl") or entry.get("total")
        if total is None:
            total = float(realized or 0) + float(unrealized or 0)

        t.add_row(
            str(i),
            f"[bold]{handle or '-'}[/]",
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
