"""trending tokens - what's hot on fomoscan with who's calling it."""

import asyncio
from rich.console import Console
from rich.table import Table
from rich.columns import Columns
from rich import box

from sm.clients.fomoscan import FomoscanClient
from sm.config import cfg
from sm import fmt

console = Console()


async def run_trends(drill: bool = False) -> None:
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    try:
        console.print("[dim]fetching trending tokens (10 CU)...[/]")
        resp    = await client.get_trending_tokens()
        entries = resp.get("entries", []) or []
        window  = resp.get("window", "")
        captured = resp.get("capturedAt") or 0
        at = fmt.ts_ago(int(captured)) if isinstance(captured, (int, float)) else str(captured)[:16].replace("T", " ")

        t = Table(
            title=f"trending tokens [{window}]  {at}",
            box=box.SIMPLE_HEAD,
            header_style="bold white",
            expand=False,
        )
        t.add_column("#",      width=4,  justify="right", style="dim")
        t.add_column("token",  width=16, no_wrap=True)
        t.add_column("mcap",   width=12, justify="right")
        t.add_column("vol",    width=12, justify="right")
        t.add_column("price",  width=12, justify="right", style="dim")
        t.add_column("liq",    width=10, justify="right", style="dim")

        token_addrs = []
        for i, entry in enumerate(entries[:20], 1):
            handle  = entry.get("handle") or entry.get("label") or "?"
            mcap    = entry.get("marketCap")
            vol     = entry.get("volume")
            price   = entry.get("price")
            liq     = entry.get("liquidity")
            addr    = entry.get("id", "")
            token_addrs.append(addr)

            t.add_row(
                str(i),
                f"[bold yellow]{handle}[/]",
                fmt.usd(mcap,  sign=False) if mcap  else "-",
                fmt.usd(vol,   sign=False) if vol   else "-",
                f"${price:.6f}" if price and price < 0.01 else fmt.usd(price, sign=False) if price else "-",
                fmt.usd(liq,   sign=False) if liq   else "-",
            )

        console.print(t)

        if drill and token_addrs:
            console.print("[dim]fetching theses for top 5 tokens...[/]")
            top5 = token_addrs[:5]
            tasks = [client.get_theses_for_token(addr, limit=3) for addr in top5]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, (addr, res) in enumerate(zip(top5, results), 1):
                if isinstance(res, Exception):
                    continue
                items = res if isinstance(res, list) else res.get("items", []) or []
                if not items:
                    continue

                token_name = entries[i - 1].get("handle", fmt.short_addr(addr, 6))
                ct = Table(
                    title=f"[yellow]{token_name}[/] callers",
                    box=box.SIMPLE,
                    header_style="bold white",
                    expand=False,
                    min_width=50,
                )
                ct.add_column("trader", width=18)
                ct.add_column("body",   ratio=1, no_wrap=True)

                for item in items[:3]:
                    handle = (
                        item.get("handle")
                        or item.get("authorHandle")
                        or "-"
                    )
                    body = (item.get("body") or item.get("text") or "")[:60]
                    ct.add_row(f"[bold]{handle}[/]", f"[dim]{body}[/]")

                console.print(ct)

    except Exception as e:
        console.print(f"[red]error:[/] {e}")
    finally:
        await client.close()
