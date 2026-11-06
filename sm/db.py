"""sqlite index - traders, wallets, events, and a simple key-value cache."""

import asyncio
import json
import time
import aiosqlite
from sm.config import cfg

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS traders (
    id          TEXT PRIMARY KEY,
    handle      TEXT,
    display     TEXT,
    platform    TEXT,
    wallets     TEXT DEFAULT '[]',
    sol_addr    TEXT DEFAULT '',
    evm_addr    TEXT DEFAULT '',
    twitter     TEXT DEFAULT '',
    followers   INTEGER DEFAULT 0,
    pnl_24h     REAL,
    pnl_7d      REAL,
    pnl_30d     REAL,
    updated_at  INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_traders_handle ON traders (handle);

CREATE TABLE IF NOT EXISTS wallets (
    address     TEXT PRIMARY KEY,
    family      TEXT DEFAULT 'solana',
    trader_id   TEXT DEFAULT '',
    handle      TEXT DEFAULT '',
    display     TEXT DEFAULT '',
    tier        TEXT DEFAULT 'unverified',
    ns_label    TEXT DEFAULT '',
    ns_fomo     TEXT DEFAULT '',
    updated_at  INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_wallets_trader ON wallets (trader_id);

CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    kind        TEXT,
    platform    TEXT,
    trader_id   TEXT DEFAULT '',
    handle      TEXT DEFAULT '',
    wallet      TEXT DEFAULT '',
    token_addr  TEXT DEFAULT '',
    token_sym   TEXT DEFAULT '',
    side        TEXT DEFAULT '',
    usd_value   REAL DEFAULT 0,
    body        TEXT DEFAULT '',
    raw         TEXT DEFAULT '{}',
    ts          INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_events_ts   ON events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events (kind, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_wall ON events (wallet, ts DESC);

CREATE TABLE IF NOT EXISTS kv (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    expires_at  INTEGER DEFAULT 0
);
"""


async def init(path: str = cfg.db_path) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    await db.commit()
    return db


# -------------------------------------------------------------------------
# traders
# -------------------------------------------------------------------------

async def upsert_trader(db: aiosqlite.Connection, t: dict) -> None:
    await db.execute(
        """
        INSERT INTO traders
            (id, handle, display, platform, wallets, sol_addr, evm_addr, twitter, followers, updated_at)
        VALUES
            (:id, :handle, :display, :platform, :wallets, :sol_addr, :evm_addr, :twitter, :followers, :ts)
        ON CONFLICT(id) DO UPDATE SET
            handle=excluded.handle, display=excluded.display, platform=excluded.platform,
            wallets=excluded.wallets, sol_addr=excluded.sol_addr, evm_addr=excluded.evm_addr,
            twitter=excluded.twitter, followers=excluded.followers, updated_at=excluded.updated_at
        """,
        {
            "id":        t.get("id", ""),
            "handle":    t.get("handle", ""),
            "display":   t.get("display", ""),
            "platform":  t.get("platform", ""),
            "wallets":   json.dumps(t.get("wallets", [])),
            "sol_addr":  t.get("sol_addr", ""),
            "evm_addr":  t.get("evm_addr", ""),
            "twitter":   t.get("twitter", ""),
            "followers": t.get("followers", 0) or 0,
            "ts":        int(time.time()),
        },
    )
    await db.commit()


async def upsert_wallet(db: aiosqlite.Connection, w: dict) -> None:
    await db.execute(
        """
        INSERT INTO wallets
            (address, family, trader_id, handle, display, tier, ns_label, ns_fomo, updated_at)
        VALUES
            (:address, :family, :trader_id, :handle, :display, :tier, :ns_label, :ns_fomo, :ts)
        ON CONFLICT(address) DO UPDATE SET
            family=excluded.family, trader_id=excluded.trader_id, handle=excluded.handle,
            display=excluded.display, tier=excluded.tier, ns_label=excluded.ns_label,
            ns_fomo=excluded.ns_fomo, updated_at=excluded.updated_at
        """,
        {
            "address":   w.get("address", ""),
            "family":    w.get("family", "solana"),
            "trader_id": w.get("trader_id", ""),
            "handle":    w.get("handle", ""),
            "display":   w.get("display", ""),
            "tier":      w.get("tier", "unverified"),
            "ns_label":  w.get("ns_label", ""),
            "ns_fomo":   w.get("ns_fomo", ""),
            "ts":        int(time.time()),
        },
    )
    await db.commit()


# -------------------------------------------------------------------------
# events
# -------------------------------------------------------------------------

async def insert_event(db: aiosqlite.Connection, ev: dict) -> None:
    eid = ev.get("id") or ev.get("tx") or f"{ev.get('wallet','')}-{ev.get('ts','')}"
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
            "id":        eid,
            "kind":      ev.get("kind", ""),
            "platform":  ev.get("platform", ""),
            "trader_id": ev.get("trader_id", ""),
            "handle":    ev.get("handle", ""),
            "wallet":    ev.get("wallet", ""),
            "token_addr":ev.get("token_addr", ""),
            "token_sym": ev.get("token_sym", ""),
            "side":      ev.get("side", ""),
            "usd_value": float(ev.get("usd_value") or 0),
            "body":      ev.get("body", ""),
            "raw":       json.dumps(ev.get("raw", {})),
            "ts":        int(ev.get("ts", time.time())),
        },
    )
    await db.commit()


# -------------------------------------------------------------------------
# queries
# -------------------------------------------------------------------------

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
        async with db.execute(
            "SELECT * FROM events WHERE kind=? ORDER BY ts DESC LIMIT ?", (kind, limit)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]
    async with db.execute(
        "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def search_traders(db: aiosqlite.Connection, q: str) -> list[dict]:
    pat = f"%{q}%"
    async with db.execute(
        "SELECT * FROM traders WHERE handle LIKE ? OR display LIKE ? LIMIT 20",
        (pat, pat),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def wallet_history(
    db: aiosqlite.Connection, wallet: str, limit: int = 30
) -> list[dict]:
    async with db.execute(
        "SELECT * FROM events WHERE wallet=? ORDER BY ts DESC LIMIT ?",
        (wallet, limit),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def stats(db: aiosqlite.Connection) -> dict:
    async with db.execute("SELECT COUNT(*) FROM traders") as c:
        traders = (await c.fetchone())[0]
    async with db.execute("SELECT COUNT(*) FROM wallets") as c:
        wallets = (await c.fetchone())[0]
    async with db.execute("SELECT COUNT(*) FROM events") as c:
        events = (await c.fetchone())[0]
    return {"traders": traders, "wallets": wallets, "events": events}


# -------------------------------------------------------------------------
# kv cache (simple persistence layer)
# -------------------------------------------------------------------------

async def kv_set(db: aiosqlite.Connection, key: str, value: str, ttl: int = 300) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO kv (key, value, expires_at) VALUES (?,?,?)",
        (key, value, int(time.time()) + ttl),
    )
    await db.commit()


async def kv_get(db: aiosqlite.Connection, key: str) -> str | None:
    async with db.execute(
        "SELECT value, expires_at FROM kv WHERE key=?", (key,)
    ) as cur:
        row = await cur.fetchone()
        if not row:
            return None
        if int(time.time()) > row[1]:
            await db.execute("DELETE FROM kv WHERE key=?", (key,))
            await db.commit()
            return None
        return row[0]
