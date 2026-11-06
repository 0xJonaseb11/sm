#!/usr/bin/env python3
"""
sm - stupid money social trading indexer

tracks fomo and pump.fun traders by wallet with their real social identities.
uses fomoscan v2/v3 and nansen for identity resolution and on-chain labeling.
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
    """sm: fomo + pump.fun social trading indexer."""
    pass


# -------------------------------------------------------------------------
# live feed
# -------------------------------------------------------------------------

@cli.command("feed")
@click.option("--platform", "-p", default=None, help="fomo | pump")
@click.option("--token",    "-t", default=None, help="token id or address")
@click.option("--save",     is_flag=True, default=False, help="persist events to db")
def cmd_feed(platform, token, save):
    """live post and callout feed via websocket (requires growth plan)."""
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


# -------------------------------------------------------------------------
# leaderboards
# -------------------------------------------------------------------------

@cli.command("board")
@click.argument("source", default="pump", metavar="SOURCE")
@click.option("--window", "-w", default="7d", help="1d | 7d | 30d | all")
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


# -------------------------------------------------------------------------
# trader + wallet lookup
# -------------------------------------------------------------------------

@cli.command("trader")
@click.argument("handle")
@click.option("--platform", "-p", default=None, help="fomo | pump | x")
@click.option("--pnl", is_flag=True, default=False, help="fetch pnl (costs 30,000 CU)")
def cmd_trader(handle, platform, pnl):
    """look up a trader by handle - profile, wallets, theses."""
    from sm.views.trader import run_trader
    _run(run_trader(handle=handle, platform=platform, pnl=pnl))


@cli.command("wallet")
@click.argument("address")
def cmd_wallet(address):
    """resolve a wallet address to its owner (30,000 CU - sample set only on free plan)."""
    from sm.views.trader import run_wallet
    _run(run_wallet(address=address))


# -------------------------------------------------------------------------
# token views
# -------------------------------------------------------------------------

@cli.command("token")
@click.argument("address")
def cmd_token(address):
    """callout wall and thesis feed for a token address."""
    from sm.views.token import run_token_wall
    _run(run_token_wall(token_addr=address))


@cli.command("trends")
@click.option("--drill", is_flag=True, default=False,
              help="fetch theses for top 5 trending tokens")
def cmd_trends(drill):
    """trending tokens with market cap and volume."""
    from sm.views.trends import run_trends
    _run(run_trends(drill=drill))


# -------------------------------------------------------------------------
# local db
# -------------------------------------------------------------------------

@cli.command("index")
@click.option("--once",     is_flag=True, default=False, help="single pass and exit")
@click.option("--interval", default=30, help="poll interval in minutes (default 30)")
def cmd_index(once, interval):
    """index fomoscan rankings + pump leaderboard into local sqlite db."""
    from sm.indexer import run_index, index_once

    if once:
        _run(index_once())
    else:
        _run(run_index(interval_minutes=interval))


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
                console.print("[dim]run 'sm.py index --once' to populate[/]")
                return
            for r in results:
                wallets = json.loads(r.get("wallets") or "[]")
                console.print(
                    f"[bold]{r['handle']}[/]  "
                    f"[dim]{r.get('display','')}[/]  "
                    f"{r.get('platform','')}"
                )
                for w in wallets[:2]:
                    console.print(f"  [dim]{w}[/]")
        finally:
            await database.close()

    _run(_go())


@cli.command("history")
@click.option("--kind",  "-k", default=None, help="post | trade | callout")
@click.option("--limit", "-n", default=30, help="number of rows")
def cmd_history(kind, limit):
    """show events persisted to local db."""
    async def _go():
        from sm import db as dbmod
        from rich.table import Table
        from rich import box as rbox
        from sm import fmt

        database = await dbmod.init()
        try:
            events = await dbmod.recent_events(database, limit=limit, kind=kind)
            if not events:
                console.print("[dim]no events yet - run 'sm.py feed --save' first[/]")
                return

            t = Table(box=rbox.SIMPLE_HEAD, header_style="bold white", expand=False)
            t.add_column("time",     width=10, style="dim")
            t.add_column("platform", width=7)
            t.add_column("trader",   width=18)
            t.add_column("token",    width=10)
            t.add_column("body",     ratio=1,  no_wrap=True)

            for ev in events:
                t.add_row(
                    fmt.ts_ago(ev.get("ts", 0)),
                    fmt.platform_tag(ev.get("platform", "")),
                    ev.get("handle") or fmt.short_addr(ev.get("wallet", ""), 5),
                    ev.get("token_sym") or fmt.short_addr(ev.get("token_addr", ""), 4),
                    f"[dim]{(ev.get('body') or '')[:70]}[/]",
                )
            console.print(t)

            st = await dbmod.stats(database)
            console.print(
                f"[dim]db: {st['traders']} traders  {st['wallets']} wallets  {st['events']} events[/]"
            )
        finally:
            await database.close()

    _run(_go())


# -------------------------------------------------------------------------
# account
# -------------------------------------------------------------------------

@cli.command("whoami")
def cmd_whoami():
    """show api key info and credit balance."""
    async def _go():
        from sm.clients.fomoscan import FomoscanClient
        from sm.config import cfg

        if cfg.has_fomoscan():
            client = FomoscanClient()
            try:
                info        = await client.whoami()
                plan        = info.get("plan", "?")
                usage       = info.get("usage", {}) or {}
                entitlement = info.get("entitlement", {}) or {}
                key_info    = info.get("key", {}) or {}

                console.print("[bold]fomoscan[/]")
                console.print(f"  key:    [dim]{key_info.get('name', key_info.get('prefix', '?'))}[/]")
                console.print(f"  plan:   [cyan]{plan}[/]")

                credits_left = usage.get("unitsRemaining")
                credits_used = usage.get("unitsUsed", 0)
                extra        = usage.get("additionalUnits", 0)
                if credits_left is not None:
                    console.print(f"  credits: [green]{credits_left:,}[/] remaining  [dim]({credits_used:,} used)[/]")
                if extra:
                    console.print(f"  packs:   [dim]+{extra:,}[/]")

                reveals_left = usage.get("revealsRemaining")
                reveals_used = usage.get("revealsUsed", 0)
                if reveals_left is not None:
                    console.print(f"  reveals: [green]{reveals_left:,}[/] remaining  [dim]({reveals_used} used)[/]")

                rate = entitlement.get("ratePerMinute")
                if rate:
                    console.print(f"  rate:    [dim]{rate} req/min[/]")
            except Exception as e:
                console.print(f"[dim]fomoscan: {e}[/]")
            finally:
                await client.close()
        else:
            console.print("[dim]fomoscan: no key[/]")

        from sm.config import cfg as _cfg
        console.print("[bold]pump.fun[/]  " + ("[dim]key set[/]" if _cfg.has_pump() else "[dim]no key[/]"))
        console.print("[bold]nansen[/]    " + ("[dim]key set[/]" if _cfg.has_nansen() else "[dim]no key[/]"))

    _run(_go())


# -------------------------------------------------------------------------
# signals  (hyperliquid perp positions + smart money bias)
# -------------------------------------------------------------------------

@cli.command("signals")
@click.option("--hl-top", default=0, type=int, help="index top N HL leaderboard traders")
@click.option("--token",  default="",          help="show signal breakdown for a token")
@click.option("--venue",  default="hyperliquid")
@click.option("--wallet", default="",          help="show open HL positions for an EVM wallet")
@click.option("--watch",  is_flag=True,        help="watch for position changes (polls every 60s)")
@click.option("--interval", default=60, type=int)
@click.option("--api",    is_flag=True,        help="start REST API server")
@click.option("--port",   default=8787, type=int)
def cmd_signals(hl_top, token, venue, wallet, watch, interval, api, port):
    """
    smart money position signals with real trader identities.

      sm signals                        show stored signals
      sm signals --hl-top 200          index top 200 HL traders (free, no key)
      sm signals --token HYPE          who is long/short HYPE
      sm signals --wallet 0x...        open positions for one wallet
      sm signals --watch               poll for position changes
      sm signals --api                 start REST API on port 8787
    """
    import subprocess, sys
    args = ["python", "scripts/signals.py"]
    if hl_top:
        args += ["--hl-top", str(hl_top)]
    if token:
        args += ["--token", token, "--venue", venue]
    if wallet:
        args += ["--wallet", wallet]
    if watch:
        args += ["--watch", "--interval", str(interval)]
    if api:
        args += ["--api", "--port", str(port)]
    subprocess.run(args)


# -------------------------------------------------------------------------
# api  (alias for signals --api)
# -------------------------------------------------------------------------

@cli.command("api")
@click.option("--port", default=8787, type=int)
def cmd_api(port):
    """start REST API server for stupidmoney.io consumption."""
    from sm.api.server import run
    console.print(f"[bold]starting SM API on port {port}[/]")
    run(port=port)


if __name__ == "__main__":
    cli()
