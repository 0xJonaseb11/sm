#!/usr/bin/env python3
"""
sm - stupid money social trading indexer

tracks fomo and pump.fun traders by wallet with their real social identities.
"""

import asyncio
import sys
import click

from rich.console import Console

console = Console()


def _run(coro):
    asyncio.run(coro)


@click.group()
def cli():
    """sm: social trading indexer for fomo and pump.fun wallets."""
    pass


@cli.command("feed")
@click.option("--platform", "-p", default=None, help="filter by platform: fomo | pump")
@click.option("--token", "-t", default=None, help="filter by token id (tok_... or address)")
@click.option("--save", is_flag=True, default=False, help="persist events to local db")
def cmd_feed(platform, token, save):
    """live post and callout feed from fomo + pump.fun via websocket."""
    from sm.views.feed import run_feed

    async def _go():
        db = None
        if save:
            from sm import db as dbmod
            db = await dbmod.init()
        try:
            await run_feed(platform=platform, token=token, db=db)
        finally:
            if db:
                await db.close()

    _run(_go())


@cli.command("board")
@click.argument("source", default="pump", metavar="SOURCE")
@click.option("--window", "-w", default="7d", help="time window: 1d | 7d | 30d")
def cmd_board(source, window):
    """pnl leaderboard. source: pump | fomo"""
    from sm.views.leaderboard import run_pump_board, run_fomo_board

    if source.lower() == "pump":
        _run(run_pump_board(window=window))
    elif source.lower() == "fomo":
        _run(run_fomo_board(window=window))
    else:
        console.print(f"[red]unknown source '{source}'[/] - use 'pump' or 'fomo'")
        sys.exit(1)


@cli.command("trader")
@click.argument("handle")
@click.option("--platform", "-p", default=None, help="narrow to platform: fomo | pump | x")
def cmd_trader(handle, platform):
    """look up a trader by handle and show their wallets + pnl."""
    from sm.views.trader import run_trader
    _run(run_trader(handle=handle, platform=platform))


@cli.command("wallet")
@click.argument("address")
def cmd_wallet(address):
    """resolve a wallet address to its owner."""
    from sm.views.trader import run_wallet
    _run(run_wallet(address=address))


@cli.command("token")
@click.argument("address")
def cmd_token(address):
    """show callout wall for a token address."""
    from sm.views.token import run_token_wall
    _run(run_token_wall(token_addr=address))


@cli.command("index")
@click.option("--once", is_flag=True, default=False, help="run one pass and exit")
@click.option("--interval", default=30, help="poll interval in minutes (default 30)")
def cmd_index(once, interval):
    """index fomoscan rankings into local db."""
    from sm.indexer import run_index, index_once

    if once:
        _run(index_once())
    else:
        _run(run_index(interval_minutes=interval))


@cli.command("whoami")
def cmd_whoami():
    """show api key info and credit balance."""
    async def _go():
        from sm.clients.fomoscan import FomoscanClient
        from sm.config import cfg

        if cfg.has_fomoscan():
            client = FomoscanClient()
            try:
                info = await client.whoami()
                # /v2/me: plan is a string, usage and entitlement are objects
                plan = info.get("plan", "?")
                usage = info.get("usage", {}) or {}
                entitlement = info.get("entitlement", {}) or {}
                key_info = info.get("key", {}) or {}
                console.print(f"[bold]fomoscan[/]")
                console.print(f"  key: [dim]{key_info.get('name', key_info.get('prefix', '?'))}[/]")
                console.print(f"  plan: [cyan]{plan}[/]")
                credits_left = usage.get("unitsRemaining")
                credits_used = usage.get("unitsUsed", 0)
                if credits_left is not None:
                    console.print(f"  credits: [green]{credits_left:,}[/] remaining  [dim]({credits_used:,} used)[/]")
                reveals_left = usage.get("revealsRemaining")
                reveals_used = usage.get("revealsUsed", 0)
                if reveals_left is not None:
                    console.print(f"  reveals: [green]{reveals_left:,}[/] remaining  [dim]({reveals_used} used)[/]")
                rate = entitlement.get("ratePerMinute")
                if rate:
                    console.print(f"  rate limit: [dim]{rate} req/min[/]")
                extra = usage.get("additionalUnits", 0)
                if extra:
                    console.print(f"  credit packs: [dim]+{extra:,}[/]")
            except Exception as e:
                console.print(f"[dim]fomoscan: {e}[/]")
            finally:
                await client.close()
        else:
            console.print("[dim]fomoscan: no key set[/]")

        if cfg.has_pump():
            console.print(f"[bold]pump.fun[/] key set")
        else:
            console.print("[dim]pump.fun: no key set[/]")

    _run(_go())


@cli.command("search")
@click.argument("query")
def cmd_search(query):
    """search the local db for a trader by handle or display name."""
    async def _go():
        from sm import db as dbmod
        import json

        database = await dbmod.init()
        try:
            results = await dbmod.search_traders(database, query)
            if not results:
                console.print(f"[dim]no local matches for '{query}'[/]")
                console.print("[dim]run 'sm index --once' first to populate the db[/]")
                return
            for r in results:
                wallets = json.loads(r.get("wallets") or "[]")
                console.print(
                    f"[bold]{r['handle']}[/] "
                    f"({r.get('display','')}) "
                    f"[dim]{r.get('platform','')}[/]"
                )
                for w in wallets[:3]:
                    console.print(f"  [dim]{w}[/]")
        finally:
            await database.close()

    _run(_go())


@cli.command("history")
@click.option("--kind", "-k", default=None, help="filter by kind: post | trade | callout")
@click.option("--limit", "-n", default=30, help="number of rows")
def cmd_history(kind, limit):
    """show events persisted to local db."""
    async def _go():
        from sm import db as dbmod
        from rich.table import Table
        from rich import box as rbox

        database = await dbmod.init()
        try:
            events = await dbmod.recent_events(database, limit=limit, kind=kind)
            if not events:
                console.print("[dim]no events in db yet - run 'sm feed --save' first[/]")
                return

            t = Table(box=rbox.SIMPLE_HEAD, header_style="bold white", expand=False)
            t.add_column("time", width=10, style="dim")
            t.add_column("platform", width=7)
            t.add_column("trader", width=18)
            t.add_column("token", width=10)
            t.add_column("body", ratio=1, no_wrap=True)

            from sm import fmt
            for ev in events:
                t.add_row(
                    fmt.ts_ago(ev.get("ts", 0)),
                    fmt.platform_tag(ev.get("platform", "")),
                    ev.get("handle", "") or fmt.short_addr(ev.get("wallet", ""), 5),
                    ev.get("token_sym", "") or fmt.short_addr(ev.get("token_addr", ""), 4),
                    f"[dim]{(ev.get('body') or '')[:70]}[/]",
                )
            console.print(t)
        finally:
            await database.close()

    _run(_go())


if __name__ == "__main__":
    cli()
