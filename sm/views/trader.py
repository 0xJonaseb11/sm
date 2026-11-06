"""single trader lookup view."""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.columns import Columns
from rich import box

from sm.clients.fomoscan import FomoscanClient
from sm.clients.pump import PumpClient
from sm.config import cfg
from sm import fmt

console = Console()


def _amount_usd(a) -> float | None:
    if a is None:
        return None
    if isinstance(a, dict):
        raw = a.get("amount") or a.get("usd") or a.get("value")
        if raw is None:
            return None
        raw = float(str(raw))
        # fomoscan stores amounts as integers in smallest unit (1e6 for usd)
        if abs(raw) > 1_000_000:
            return raw / 1_000_000
        return raw
    return float(a)


async def run_trader(handle: str, platform: str | None = None) -> None:
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    try:
        console.print(f"[dim]looking up trader: {handle}...[/]")
        resp = await client.lookup_trader(handle, platform=platform)
        items = resp.get("items", [])

        if not items:
            suggestions = resp.get("suggestions", [])
            console.print(f"[yellow]no match for '{handle}'[/]")
            if suggestions:
                sug_handles = [
                    (s.get("canonical", {}) or {}).get("handle", "?")
                    for s in suggestions
                ]
                console.print(f"[dim]suggestions: {', '.join(sug_handles)}[/]")
            return

        trader = items[0]
        canonical = trader.get("canonical", {}) or {}
        trader_id = trader.get("id", "")

        name = canonical.get("displayName") or canonical.get("handle", handle)
        platforms = canonical.get("platforms", [])
        followers = canonical.get("followerCount")

        # render profile panel
        lines = [
            f"[bold white]{name}[/]",
            f"id: [dim]{trader_id}[/]",
            f"platforms: {', '.join(fmt.platform_tag(p) for p in platforms) or '-'}",
        ]
        if followers is not None:
            lines.append(f"followers: [cyan]{followers:,}[/]")

        console.print(Panel("\n".join(lines), title="trader", expand=False))

        # wallets
        try:
            wallets_resp = await client.get_trader_wallets(trader_id)
            wallets = wallets_resp.get("items", [])
            if wallets:
                wt = Table(box=box.SIMPLE, show_header=True, header_style="bold white")
                wt.add_column("wallet", width=46)
                wt.add_column("chain", width=8)
                wt.add_column("tier", width=12)
                for w in wallets:
                    addr = w.get("address", "")
                    family = w.get("family", "")
                    owners = w.get("owners", []) or []
                    tier = owners[0].get("tier", "?") if owners else "?"
                    wt.add_row(addr, family, fmt.tier_tag(tier))
                console.print(wt)
        except Exception as e:
            console.print(f"[dim]wallets unavailable: {e}[/]")

        # pnl
        try:
            pnl_resp = await client.get_trader_pnl(trader_id, window="30d")
            status = pnl_resp.get("status", "")
            if status == "ready":
                windows = pnl_resp.get("windows", {}) or {}
                pt = Table(
                    box=box.SIMPLE, show_header=True, header_style="bold white"
                )
                pt.add_column("window", width=8)
                pt.add_column("realized", width=12, justify="right")
                pt.add_column("unrealized", width=12, justify="right")
                pt.add_column("total", width=12, justify="right")
                for win_key, win_data in windows.items():
                    r = _amount_usd(win_data.get("realized"))
                    u = _amount_usd(win_data.get("unrealized"))
                    total_v = _amount_usd(win_data.get("total"))
                    if total_v is None and r is not None and u is not None:
                        total_v = r + u
                    pt.add_row(
                        win_key,
                        f"[{fmt.pnl_color(r)}]{fmt.usd(r)}[/]",
                        f"[{fmt.pnl_color(u)}]{fmt.usd(u)}[/]",
                        f"[bold {fmt.pnl_color(total_v)}]{fmt.usd(total_v)}[/]",
                    )
                console.print(pt)
            elif status == "pending":
                retry = pnl_resp.get("retryAfterSeconds", 30)
                console.print(
                    f"[dim]pnl is being computed, retry in {retry}s[/]"
                )
            elif status == "no_wallet":
                console.print("[dim]no verified wallets for pnl[/]")
            else:
                console.print(f"[dim]pnl status: {status}[/]")
        except Exception as e:
            console.print(f"[dim]pnl unavailable: {e}[/]")

        # recent posts
        try:
            posts_resp = await client.get_trader_posts(trader_id)
            posts = posts_resp.get("items", [])
            if posts:
                console.print("\n[bold]recent posts[/]")
                for post in posts[:5]:
                    platform = post.get("platform", "")
                    body = (post.get("body") or post.get("text") or "")[:120]
                    token = (post.get("token") or {})
                    tok_sym = (
                        token.get("symbol") or token.get("name") or ""
                    )
                    console.print(
                        f"  {fmt.platform_tag(platform)} "
                        + (f"[yellow]{tok_sym}[/] " if tok_sym else "")
                        + f"[dim]{body}[/]"
                    )
        except Exception:
            pass

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
        console.print(f"[dim]resolving wallet: {address}...[/]")
        # detect family
        family = "evm" if address.startswith("0x") else "solana"
        resp = await client.resolve_wallet(family, address.lower())

        owners = resp.get("owners", []) or []
        wallet_addr = resp.get("address", address)

        if not owners:
            console.print(f"[yellow]no known owner for wallet {fmt.short_addr(wallet_addr)}[/]")
            return

        console.print(f"[bold]wallet:[/] {wallet_addr}")
        console.print(f"[bold]chain:[/] {resp.get('family', family)}")

        for owner in owners:
            trader = owner.get("trader", {}) or {}
            canonical = (trader.get("canonical") or {})
            handle = canonical.get("handle", "") or trader.get("id", "")
            display = canonical.get("displayName", "") or handle
            tier = owner.get("tier", "?")
            platforms = canonical.get("platforms", [])
            console.print(
                f"\n  [bold white]{display}[/] "
                f"({fmt.tier_tag(tier)})\n"
                f"  handle: [cyan]{handle}[/]\n"
                f"  platforms: {', '.join(fmt.platform_tag(p) for p in platforms) or '-'}"
            )

    except Exception as e:
        console.print(f"[red]error:[/] {e}")
    finally:
        await client.close()
