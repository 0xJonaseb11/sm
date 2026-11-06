"""
SM REST API - serves identity-aware signal data for stupidmoney.io consumption.

endpoints:
    GET /stats                      index health + counts
    GET /signals                    all tokens with smart money bias
    GET /signals/{token}            single token breakdown with named traders
    GET /traders                    all indexed traders with social identities
    GET /positions/live             latest position change events
    GET /positions/{wallet}         open positions for a specific wallet

start:
    python scripts/signals.py --api [--port 8787]
"""

import asyncio
import json
import logging
import time

from aiohttp import web

import sm.db as db
from sm.signals.aggregator import (
    all_signals,
    token_signal,
    wallet_positions,
    recent_position_events,
)

log = logging.getLogger("sm.api")


# -------------------------------------------------------------------------
# handlers
# -------------------------------------------------------------------------

async def handle_stats(req: web.Request) -> web.Response:
    db_conn = req.app["db"]
    s = await db.stats(db_conn)
    async with db_conn.execute("SELECT COUNT(*) FROM positions") as c:
        s["positions"] = (await c.fetchone())[0]
    async with db_conn.execute("SELECT COUNT(*) FROM signals") as c:
        s["signals"] = (await c.fetchone())[0]
    return _json({"stats": s, "ts": int(time.time())})


async def handle_signals(req: web.Request) -> web.Response:
    db_conn = req.app["db"]
    limit   = int(req.query.get("limit", 50))
    rows    = await all_signals(db_conn, limit=limit)
    return _json({"signals": rows, "count": len(rows), "ts": int(time.time())})


async def handle_signal_token(req: web.Request) -> web.Response:
    db_conn = req.app["db"]
    token   = req.match_info["token"]
    venue   = req.query.get("venue", "hyperliquid")
    sig     = await token_signal(db_conn, token, venue)
    if not sig:
        raise web.HTTPNotFound(
            text=json.dumps({"error": f"no signal data for {token} on {venue}"}),
            content_type="application/json",
        )
    return _json(sig)


async def handle_traders(req: web.Request) -> web.Response:
    db_conn = req.app["db"]
    limit   = int(req.query.get("limit", 100))
    async with db_conn.execute(
        "SELECT * FROM traders ORDER BY followers DESC LIMIT ?", (limit,)
    ) as cur:
        traders = [dict(r) for r in await cur.fetchall()]
    return _json({"traders": traders, "count": len(traders)})


async def handle_positions_live(req: web.Request) -> web.Response:
    db_conn = req.app["db"]
    limit   = int(req.query.get("limit", 50))
    events  = await recent_position_events(db_conn, limit=limit)
    return _json({"events": events, "count": len(events)})


async def handle_wallet_positions(req: web.Request) -> web.Response:
    db_conn  = req.app["db"]
    wallet   = req.match_info["wallet"].lower()
    positions = await wallet_positions(db_conn, wallet)
    identity  = await db.resolve_wallet(db_conn, wallet)
    return _json({
        "wallet":    wallet,
        "identity":  identity,
        "positions": positions,
        "count":     len(positions),
    })


async def handle_search(req: web.Request) -> web.Response:
    db_conn = req.app["db"]
    q       = req.query.get("q", "").strip()
    if not q:
        raise web.HTTPBadRequest(text=json.dumps({"error": "q is required"}))
    traders = await db.search_traders(db_conn, q)
    return _json({"traders": traders, "count": len(traders), "query": q})


# -------------------------------------------------------------------------
# CORS middleware
# -------------------------------------------------------------------------

@web.middleware
async def cors_middleware(req: web.Request, handler) -> web.Response:
    if req.method == "OPTIONS":
        return web.Response(
            headers={
                "Access-Control-Allow-Origin":  "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            }
        )
    resp = await handler(req)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


# -------------------------------------------------------------------------
# app factory
# -------------------------------------------------------------------------

async def make_app(db_path: str | None = None) -> web.Application:
    from sm.config import cfg
    db_conn = await db.init(db_path or cfg.db_path)

    app = web.Application(middlewares=[cors_middleware])
    app["db"] = db_conn

    app.router.add_get("/stats",              handle_stats)
    app.router.add_get("/signals",            handle_signals)
    app.router.add_get("/signals/{token}",    handle_signal_token)
    app.router.add_get("/traders",            handle_traders)
    app.router.add_get("/positions/live",     handle_positions_live)
    app.router.add_get("/positions/{wallet}", handle_wallet_positions)
    app.router.add_get("/search",             handle_search)

    log.info("SM API app created")
    return app


def run(host: str = "0.0.0.0", port: int = 8787) -> None:
    logging.basicConfig(level=logging.INFO)

    async def _start() -> None:
        app    = await make_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        log.info("SM API running on http://%s:%d", host, port)
        # block forever
        await asyncio.Event().wait()

    asyncio.run(_start())


def _json(data: dict | list) -> web.Response:
    return web.Response(
        text=json.dumps(data, default=str),
        content_type="application/json",
    )
