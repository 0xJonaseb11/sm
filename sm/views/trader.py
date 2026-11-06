"""single trader and wallet lookup views."""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from sm.clients.fomoscan import FomoscanClient
from sm.config import cfg
from sm import fmt

console = Console()


def _net_usd(w: dict) -> float | None:
    val = w.get("netUsd") or w.get("net") or w.get("total")
    return float(val) if val is not None else None


async def run_trader(handle: str, platform: str | None = None, pnl: bool = False) -> None:
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    try:
        console.print(f"[dim]looking up: {handle} (100 CU)...[/]")
        resp = await client.get_user_by_handle(handle)

        uid      = resp.get("id", "")
        name     = resp.get("name", "") or handle
        bio      = (resp.get("bio", "") or "")[:120]
        twitter  = resp.get("twitter", "") or ""
        sol_addr = resp.get("solanaAddress")
        evm_addr = resp.get("evmAddress")

        lines = [f"[bold white]{name}[/]"]
        if bio:
            lines.append(f"[dim]{bio}[/]")
        if twitter:
            lines.append(f"x: [cyan]{twitter}[/]")
        lines.append(f"id: [dim]{uid}[/]")
        console.print(Panel("\n".join(lines), title="trader", expand=False))

        wt = Table(box=box.SIMPLE, show_header=True, header_style="bold white", expand=False)
        wt.add_column("chain",   width=8)
        wt.add_column("address", width=46)
        if sol_addr:
            wt.add_row("solana", sol_addr)
        if evm_addr:
            wt.add_row("evm",    evm_addr)
        if not sol_addr and not evm_addr:
            wt.add_row("[dim]-[/]", "[dim]no verified wallet on record[/]")
        console.print(wt)

        if pnl:
            console.print("[dim]fetching pnl (30,000 CU)...[/]")
            try:
                p       = await client.get_pnl_by_handle(handle)
                status  = p.get("status", "")
                if status == "ready":
                    windows   = p.get("windows", {}) or {}
                    followers = p.get("followers")
                    if followers:
                        console.print(f"followers: [cyan]{followers:,}[/]")
                    pt = Table(
                        box=box.SIMPLE, show_header=True,
                        header_style="bold white", expand=False
                    )
                    pt.add_column("window",  width=6)
                    pt.add_column("net pnl", width=12, justify="right")
                    pt.add_column("return",  width=8,  justify="right")
                    pt.add_column("volume",  width=12, justify="right")
                    pt.add_column("trades",  width=7,  justify="right")
                    for key in ("24h", "7d", "30d", "all"):
                        w = windows.get(key, {}) or {}
                        if not w:
                            continue
                        net    = _net_usd(w)
                        ret    = w.get("returnPct")
                        vol    = w.get("volumeUsd")
                        trades = w.get("trades")
                        pt.add_row(
                            key,
                            f"[bold {fmt.pnl_color(net)}]{fmt.usd(net)}[/]",
                            f"[{fmt.pnl_color(ret)}]{fmt.pct(ret)}[/]" if ret is not None else "-",
                            fmt.usd(vol, sign=False) if vol else "-",
                            str(trades) if trades is not None else "-",
                        )
                    unr = p.get("unrealized", {}) or {}
                    u_pnl = unr.get("unrealizedPnlUsd")
                    if u_pnl is not None:
                        pt.add_row(
                            "open",
                            f"[{fmt.pnl_color(u_pnl)}]{fmt.usd(u_pnl)}[/]",
                            "-", "-", "-",
                        )
                    console.print(pt)
                elif status in ("pending", "failed"):
                    console.print(f"[dim]pnl {status}, retry in {p.get('retryAfterSeconds', 30)}s[/]")
                elif status == "no-wallet":
                    console.print("[dim]no verified wallet for pnl[/]")
                else:
                    console.print(f"[dim]pnl: {status}[/]")
            except Exception as e:
                console.print(f"[dim]pnl: {e}[/]")
        else:
            console.print("[dim]tip: add --pnl to fetch realized profit (costs 30,000 CU)[/]")

        if uid:
            try:
                console.print("[dim]fetching theses (200 CU)...[/]")
                theses_resp = await client.get_theses_for_user(uid, limit=5)
                items = theses_resp if isinstance(theses_resp, list) else theses_resp.get("items", []) or []
                if items:
                    console.print("\n[bold]recent theses[/]")
                    for t in items[:5]:
                        sym  = t.get("tokenSymbol") or (t.get("token", {}) or {}).get("symbol", "") or "?"
                        net  = t.get("tokenNetwork", "")
                        body = (t.get("body") or t.get("text") or "")[:100]
                        console.print(
                            f"  [yellow]{sym}[/] [dim]{net}[/]  [dim]{body}[/]"
                        )
                else:
                    console.print("[dim]no theses found[/]")
            except Exception as e:
                console.print(f"[dim]theses: {e}[/]")

    except Exception as e:
        console.print(f"[red]error:[/] {e}")
    finally:
        await client.close()


async def run_wallet(address: str) -> None:
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    try:
        console.print(f"[dim]resolving {fmt.short_addr(address)} (30,000 CU on hit)...[/]")
        resp = await client.get_user_by_wallet(address)

        uid      = resp.get("id", "")
        name     = resp.get("name", "") or resp.get("handle", "")
        handle   = resp.get("handle", "")
        twitter  = resp.get("twitter", "") or ""
        sol_addr = resp.get("solanaAddress")
        evm_addr = resp.get("evmAddress")

        console.print(f"\n[bold white]{name}[/]  [dim]{handle}[/]")
        if twitter:
            console.print(f"x: [cyan]{twitter}[/]")
        console.print(f"id: [dim]{uid}[/]")
        if sol_addr:
            console.print(f"solana: [dim]{sol_addr}[/]")
        if evm_addr:
            console.print(f"evm:    [dim]{evm_addr}[/]")

    except Exception as e:
        if "403" in str(e):
            console.print("[yellow]wallet not in free-plan sample set - upgrade to reveal[/]")
        elif "404" in str(e):
            console.print(f"[dim]no verified owner for {fmt.short_addr(address)}[/]")
        else:
            console.print(f"[red]error:[/] {e}")
    finally:
        await client.close()
