"""token callout wall and post feed for a specific token."""

import asyncio
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from sm.clients.fomoscan import FomoscanClient
from sm.config import cfg
from sm import fmt

console = Console()


async def run_token_wall(token_addr: str) -> None:
    """show the callout wall for a token address."""
    if not cfg.has_fomoscan():
        console.print("[red]error:[/] FOMOSCAN_KEY not set")
        return

    client = FomoscanClient()
    try:
        console.print(f"[dim]fetching callouts for {fmt.short_addr(token_addr)}...[/]")

        # try v2 theses first (200 CU, works on all plans)
        try:
            resp = await client.get_theses_for_token(token_addr)
            raw = resp if isinstance(resp, list) else resp.get("items", []) or resp.get("data", [])
            entries = raw
            source = "fomoscan theses"
        except Exception:
            entries = []
            source = ""

        # fallback: pump.fun callouts via fomoscan v2
        if not entries:
            try:
                resp2 = await client.pump_token_callouts(token_addr)
                entries = resp2.get("items", []) or resp2.get("data", [])
                source = "pump.fun callouts"
            except Exception:
                pass

        if not entries:
            console.print(
                f"[dim]no callout data for {fmt.short_addr(token_addr)}. "
                f"try a pump.fun token address.[/]"
            )
            return

        t = Table(
            title=f"callout wall - {fmt.short_addr(token_addr)} ({source})",
            box=box.SIMPLE_HEAD,
            header_style="bold white",
            expand=False,
        )
        t.add_column("trader", width=20)
        t.add_column("wallet", width=14)
        t.add_column("body", ratio=1)
        t.add_column("time", width=10, justify="right", style="dim")

        for entry in entries[:25]:
            handle = (
                entry.get("handle")
                or entry.get("username")
                or entry.get("author", {}).get("handle", "")
            )
            wallet = entry.get("wallet", "") or entry.get("author", {}).get("wallet", "")
            body = (entry.get("body") or entry.get("text") or entry.get("content") or "")[:80]
            ts = entry.get("createdAt") or entry.get("postedAt") or entry.get("ts", 0)
            if isinstance(ts, str):
                import datetime
                try:
                    dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    ts_int = int(dt.timestamp())
                except Exception:
                    ts_int = 0
            else:
                ts_int = int(ts or 0)

            t.add_row(
                f"[bold]{handle or '-'}[/]",
                fmt.short_addr(str(wallet), 5),
                f"[dim]{body}[/]",
                fmt.ts_ago(ts_int),
            )

        console.print(t)

    except Exception as e:
        console.print(f"[red]error:[/] {e}")
    finally:
        await client.close()
