"""local sqlite index for wallets, traders and trade events."""

import asyncio
import json
import time
import aiosqlite
from sm.config import cfg

SCHEMA = """
CREATE TABLE IF NOT EXISTS traders (
    id          TEXT PRIMARY KEY,       -- fomoscan tdr_ id
    handle      TEXT,
    display     TEXT,
    platform    TEXT,                   -- fomo | pump | x
    wallets     TEXT,                   -- json list of addresses
    pnl_24h     REAL,
    pnl_7d      REAL,
    pnl_30d     REAL,
    updated_at  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS wallets (
    address     TEXT PRIMARY KEY,
    family      TEXT,                   -- solana | evm | bnb
    trader_id   TEXT,
    handle      TEXT,
    display     TEXT,
    tier        TEXT,                   -- proven | account | partial | weak | unverified
    updated_at  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    kind        TEXT,                   -- post | trade | callout
    platform    TEXT,
    trader_id   TEXT,
    handle      TEXT,
    wallet      TEXT,
    token_addr  TEXT,
    token_sym   TEXT,
    side        TEXT,                   -- buy | sell | null
    usd_value   REAL,
    body        TEXT,
    raw         TEXT,
    ts          INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_events_ts   ON events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events (kind, ts DESC);
CREATE INDEX IF NOT EXISTS idx_wallets_tid ON wallets (trader_id);
"""


async def init(path: str = cfg.db_path) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    await db.commit()
    return db


async def upsert_trader(db: aiosqlite.Connection, t: dict) -> None:
    await db.execute(
        """
        INSERT INTO traders (id, handle, display, platform, wallets, updated_at)
        VALUES (:id, :handle, :display, :platform, :wallets, :ts)
        ON CONFLICT(id) DO UPDATE SET
            handle=excluded.handle,
            display=excluded.display,
            platform=excluded.platform,
            wallets=excluded.wallets,
            updated_at=excluded.updated_at
        """,
        {
            "id": t.get("id", ""),
            "handle": t.get("handle", ""),
            "display": t.get("display", ""),
            "platform": t.get("platform", ""),
            "wallets": json.dumps(t.get("wallets", [])),
            "ts": int(time.time()),
        },
    )
    await db.commit()


async def upsert_wallet(db: aiosqlite.Connection, w: dict) -> None:
    await db.execute(
        """
        INSERT INTO wallets (address, family, trader_id, handle, display, tier, updated_at)
        VALUES (:address, :family, :trader_id, :handle, :display, :tier, :ts)
        ON CONFLICT(address) DO UPDATE SET
            family=excluded.family,
            trader_id=excluded.trader_id,
            handle=excluded.handle,
            display=excluded.display,
            tier=excluded.tier,
            updated_at=excluded.updated_at
        """,
        {
            "address": w.get("address", ""),
            "family": w.get("family", ""),
            "trader_id": w.get("trader_id", ""),
            "handle": w.get("handle", ""),
            "display": w.get("display", ""),
            "tier": w.get("tier", "unverified"),
            "ts": int(time.time()),
        },
    )
    await db.commit()


async def insert_event(db: aiosqlite.Connection, ev: dict) -> None:
    await db.execute(
        """
        INSERT OR IGNORE INTO events
            (id, kind, platform, trader_id, handle, wallet, token_addr, token_sym,
             side, usd_value, body, raw, ts)
        VALUES
            (:id, :kind, :platform, :trader_id, :handle, :wallet, :token_addr, :token_sym,
             :side, :usd_value, :body, :raw, :ts)
        """,
        {
            "id": ev.get("id", ""),
            "kind": ev.get("kind", ""),
            "platform": ev.get("platform", ""),
            "trader_id": ev.get("trader_id", ""),
            "handle": ev.get("handle", ""),
            "wallet": ev.get("wallet", ""),
            "token_addr": ev.get("token_addr", ""),
            "token_sym": ev.get("token_sym", ""),
            "side": ev.get("side", ""),
            "usd_value": ev.get("usd_value", 0.0),
            "body": ev.get("body", ""),
            "raw": json.dumps(ev.get("raw", {})),
            "ts": ev.get("ts", int(time.time())),
        },
    )
    await db.commit()


async def resolve_wallet(db: aiosqlite.Connection, address: str) -> dict | None:
    async with db.execute(
        "SELECT * FROM wallets WHERE address = ?", (address.lower(),)
    ) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def recent_events(
    db: aiosqlite.Connection, limit: int = 50, kind: str | None = None
) -> list[dict]:
    if kind:
        q = "SELECT * FROM events WHERE kind = ? ORDER BY ts DESC LIMIT ?"
        params = (kind, limit)
    else:
        q = "SELECT * FROM events ORDER BY ts DESC LIMIT ?"
        params = (limit,)
    async with db.execute(q, params) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def search_traders(db: aiosqlite.Connection, q: str) -> list[dict]:
    pat = f"%{q}%"
    async with db.execute(
        "SELECT * FROM traders WHERE handle LIKE ? OR display LIKE ? LIMIT 20",
        (pat, pat),
    ) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
