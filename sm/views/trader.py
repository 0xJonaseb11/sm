"""single trader lookup and wallet resolution."""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from sm.clients.fomoscan import FomoscanClient
from sm.config import cfg
from sm import fmt

console = Console()


def _net_usd(window: dict) -> float | None:
    val = window.get("netUsd") or window.get("net") or window.get("total")
    return float(val) if val is not None else None


async def run_trader(handle: str, platform: str | None = None, pnl: bool = False) -> None:
    """
    look up a trader by handle using v2 endpoints (available on all plans).
    pnl=True fetches realized pnl - costs 30,000 CU (one reveal).
    """
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    try:
        console.print(f"[dim]looking up: {handle} (100 CU)...[/]")
        resp = await client.get_user_by_handle(handle)

        user_id  = resp.get("id", "")
        name     = resp.get("name", "") or handle
        bio      = resp.get("bio", "") or ""
        twitter  = resp.get("twitter", "") or ""
        sol_addr = resp.get("solanaAddress")
        evm_addr = resp.get("evmAddress")

        # profile panel
        lines = [f"[bold white]{name}[/]"]
        if bio:
            lines.append(f"[dim]{bio[:120]}[/]")
        if twitter:
            lines.append(f"x: [cyan]{twitter}[/]")
        lines.append(f"id: [dim]{user_id}[/]")
        console.print(Panel("\n".join(lines), title="trader", expand=False))

        # wallets
        wt = Table(box=box.SIMPLE, show_header=True, header_style="bold white", expand=False)
        wt.add_column("chain",  width=8)
        wt.add_column("address", width=46)
        if sol_addr:
            wt.add_row("solana", sol_addr)
        if evm_addr:
            wt.add_row("evm",    evm_addr)
        if not sol_addr and not evm_addr:
            wt.add_row("[dim]-[/]", "[dim]no verified wallet on record[/]")
        console.print(wt)

        # pnl - only if explicitly requested (costs 30,000 CU)
        if pnl:
            console.print("[dim]fetching pnl (30,000 CU - counts as a reveal)...[/]")
            try:
                p = await client.get_pnl_by_handle(handle)
                status = p.get("status", "")
                if status == "ready":
                    windows = p.get("windows", {}) or {}
                    followers = p.get("followers")
                    if followers:
                        console.print(f"followers: [cyan]{followers:,}[/]")

                    pt = Table(
                        box=box.SIMPLE, show_header=True,
                        header_style="bold white", expand=False
                    )
                    pt.add_column("window", width=6)
                    pt.add_column("net pnl", width=12, justify="right")
                    pt.add_column("return", width=8, justify="right")
                    pt.add_column("volume", width=12, justify="right")
                    pt.add_column("trades", width=7, justify="right")

                    for win_key in ("24h", "7d", "30d", "all"):
                        w = windows.get(win_key, {})
                        if not w:
                            continue
                        net = _net_usd(w)
                        ret = w.get("returnPct")
                        vol = w.get("volumeUsd")
                        trades = w.get("trades")
                        pt.add_row(
                            win_key,
                            f"[bold {fmt.pnl_color(net)}]{fmt.usd(net)}[/]",
                            f"[{fmt.pnl_color(ret)}]{ret:.1f}%[/]" if ret is not None else "-",
                            fmt.usd(vol, sign=False) if vol else "-",
                            str(trades) if trades is not None else "-",
                        )

                    unrealized = p.get("unrealized", {}) or {}
                    u_pnl = unrealized.get("unrealizedPnlUsd")
                    if u_pnl is not None:
                        pt.add_row(
                            "open",
                            f"[{fmt.pnl_color(u_pnl)}]{fmt.usd(u_pnl)}[/]",
                            "-", "-", "-",
                        )
                    console.print(pt)

                elif status in ("pending", "failed"):
                    retry = p.get("retryAfterSeconds", 30)
                    console.print(f"[dim]pnl {status}, retry in {retry}s[/]")
                elif status == "no-wallet":
                    console.print("[dim]no verified wallet for pnl[/]")
                else:
                    console.print(f"[dim]pnl: {status}[/]")
            except Exception as e:
                console.print(f"[dim]pnl: {e}[/]")
        else:
            console.print(
                "[dim]tip: add --pnl to fetch realized profit (costs 30,000 CU)[/]"
            )

        # recent theses/posts (200 CU)
        if user_id:
            try:
                console.print("[dim]fetching theses (200 CU)...[/]")
                theses = await client.get_theses_for_user(user_id, limit=5)
                items = theses if isinstance(theses, list) else theses.get("items", []) or []
                if items:
                    console.print("\n[bold]recent theses[/]")
                    for t in items[:5]:
                        token_sym  = t.get("tokenSymbol") or t.get("token", {}).get("symbol", "")
                        token_net  = t.get("tokenNetwork", "")
                        body       = (t.get("body") or t.get("text") or "")[:120]
                        created    = t.get("createdAt") or t.get("postedAt") or 0
                        console.print(
                            f"  [yellow]{token_sym or '?'}[/] "
                            f"[dim]{token_net}[/]  "
                            f"[dim]{body}[/]"
                        )
                else:
                    console.print("[dim]no theses found[/]")
            except Exception as e:
                console.print(f"[dim]theses unavailable: {e}[/]")

    except Exception as e:
        console.print(f"[red]error:[/] {e}")
    finally:
        await client.close()


async def run_wallet(address: str) -> None:
    """
    resolve a wallet to its owner.
    costs 30,000 CU on a hit (a reveal). free-plan keys only cover the sample set.
    """
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    try:
        console.print(
            f"[dim]resolving {fmt.short_addr(address)} "
            f"(30,000 CU on hit)...[/]"
        )
        resp = await client.get_user_by_wallet(address)

        user_id  = resp.get("id", "")
        name     = resp.get("name", "") or resp.get("handle", "")
        handle   = resp.get("handle", "")
        twitter  = resp.get("twitter", "") or ""
        sol_addr = resp.get("solanaAddress")
        evm_addr = resp.get("evmAddress")

        console.print(f"\n[bold white]{name}[/]  [dim]{handle}[/]")
        if twitter:
            console.print(f"x: [cyan]{twitter}[/]")
        console.print(f"id: [dim]{user_id}[/]")
        if sol_addr:
            console.print(f"solana: [dim]{sol_addr}[/]")
        if evm_addr:
            console.print(f"evm:    [dim]{evm_addr}[/]")

    except Exception as e:
        # 403 SAMPLE_SET_ONLY means the wallet is real but outside the free set
        if "403" in str(e):
            console.print(
                f"[yellow]wallet not in free-plan sample set.[/] "
                f"upgrade to reveal it."
            )
        elif "404" in str(e):
            console.print(
                f"[dim]no verified owner found for {fmt.short_addr(address)}[/]"
            )
        else:
            console.print(f"[red]error:[/] {e}")
    finally:
        await client.close()
