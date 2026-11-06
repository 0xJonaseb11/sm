"""
signal aggregator - cross-wallet smart money bias per asset.

the core output: for token X, what % of tracked wallets are LONG vs SHORT,
and critically -- who exactly are they by name (fomoscan handle, twitter, pump username).
"""

import logging

log = logging.getLogger("sm.signals.aggregator")


async def all_signals(db_conn, limit: int = 50) -> list[dict]:
    """all tokens with computed signals, ordered by trader count."""
    async with db_conn.execute(
        """
        SELECT token_sym, token_addr, venue, pct_long,
               long_count, short_count, neutral_count,
               total_long_usd, total_short_usd, computed_at
        FROM signals
        ORDER BY (long_count + short_count) DESC
        LIMIT ?
        """,
        (limit,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def token_signal(
    db_conn,
    token: str,
    venue: str = "hyperliquid",
) -> dict | None:
    """
    signal for a single token with named wallet breakdown.

    token can be symbol (e.g. HYPE) or address.
    """
    async with db_conn.execute(
        """
        SELECT * FROM signals
        WHERE (token_sym = ? OR token_addr = ?) AND venue = ?
        """,
        (token.upper(), token.lower(), venue),
    ) as cur:
        row = await cur.fetchone()

    if not row:
        return None

    signal = dict(row)

    # named position breakdown with identity
    async with db_conn.execute(
        """
        SELECT
            p.wallet, p.side, p.size_usd, p.unrealized_pnl, p.venue, p.updated_at,
            w.handle    AS w_handle,
            w.display   AS w_display,
            w.ns_fomo   AS w_ns_fomo,
            t.twitter   AS t_twitter,
            t.followers AS t_followers,
            t.pnl_30d   AS t_pnl_30d
        FROM positions p
        LEFT JOIN wallets w ON w.address = p.wallet
        LEFT JOIN traders t ON (t.sol_addr = p.wallet OR t.evm_addr = p.wallet)
        WHERE (p.token_sym = ? OR p.token_addr = ?) AND p.venue = ?
        ORDER BY p.size_usd DESC
        """,
        (token.upper(), token.lower(), venue),
    ) as cur:
        positions = [dict(r) for r in await cur.fetchall()]

    for p in positions:
        # merge best available display name
        p["identity"] = (
            p.get("w_handle")
            or p.get("w_ns_fomo")
            or p.get("w_display")
            or p["wallet"][:10]
        )

    signal["longs"]  = [p for p in positions if p["side"] == "LONG"]
    signal["shorts"] = [p for p in positions if p["side"] == "SHORT"]
    return signal


async def wallet_positions(
    db_conn,
    wallet: str,
) -> list[dict]:
    """all open positions for a specific wallet with token info."""
    async with db_conn.execute(
        """
        SELECT p.*, w.handle, w.display, w.ns_fomo
        FROM positions p
        LEFT JOIN wallets w ON w.address = p.wallet
        WHERE p.wallet = ?
        ORDER BY p.size_usd DESC
        """,
        (wallet.lower(),),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def recent_position_events(
    db_conn,
    limit: int = 50,
) -> list[dict]:
    """latest position change events (opened, closed, flipped, increased, decreased)."""
    async with db_conn.execute(
        """
        SELECT e.*, w.handle, w.display
        FROM events e
        LEFT JOIN wallets w ON w.address = e.wallet
        WHERE e.kind LIKE 'position_%'
        ORDER BY e.ts DESC
        LIMIT ?
        """,
        (limit,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]
