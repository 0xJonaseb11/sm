"""
signal engine - compute per-wallet position bias from on-chain data.

for solana spot / pump.fun:
    uses nansen dex trades to compute net buy/sell USD per wallet per token.

for perps (hyperliquid):
    reads clearinghouse state directly for open positions and direction.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sm.clients.nansen import NansenClient, parse_dex_trade
from sm.clients.hyperliquid import HyperliquidClient, parse_position
import sm.db as db

log = logging.getLogger("sm.signals.engine")


def _now_iso(offset_days: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=offset_days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# -------------------------------------------------------------------------
# spot signal (solana / nansen dex trades)
# -------------------------------------------------------------------------

async def compute_spot_positions(
    nansen: NansenClient,
    wallets: list[str],
    token_address: str,
    chain: str = "solana",
    days: int = 7,
) -> dict[str, dict]:
    """
    for a given token, fetch nansen dex trades and compute net position per wallet.

    returns {wallet_address: {side, buy_usd, sell_usd, net_usd, size_usd, trades}}

    side is LONG when net_usd > 100 (net buyer), SHORT when net_usd < -100 (net seller).
    filtering is done locally to avoid the FOMO label filter timeout on busy tokens.
    """
    date_from = _now_iso(days)
    date_to   = _now_iso(0)

    try:
        resp = await nansen.dex_trades(
            chain=chain,
            token_address=token_address,
            date_from=date_from,
            date_to=date_to,
            per_page=500,
        )
    except Exception as e:
        log.warning("nansen dex_trades error for %s: %s", token_address[:12], e)
        return {}

    rows       = resp.get("data", []) or []
    wallet_set = {w.lower() for w in wallets}
    positions: dict[str, dict] = {}

    for row in rows:
        trade  = parse_dex_trade(row)
        wallet = (trade["wallet"] or "").lower()
        if wallet not in wallet_set:
            continue

        if wallet not in positions:
            positions[wallet] = {"buy_usd": 0.0, "sell_usd": 0.0, "trades": 0}

        if trade["action"] == "BUY":
            positions[wallet]["buy_usd"] += trade["usd"]
        elif trade["action"] == "SELL":
            positions[wallet]["sell_usd"] += trade["usd"]
        positions[wallet]["trades"] += 1

    for wallet, pos in positions.items():
        net = pos["buy_usd"] - pos["sell_usd"]
        pos["net_usd"]  = net
        pos["size_usd"] = max(pos["buy_usd"], pos["sell_usd"])
        if net > 100:
            pos["side"] = "LONG"
        elif net < -100:
            pos["side"] = "SHORT"
        else:
            pos["side"] = "NEUTRAL"

    return positions


# -------------------------------------------------------------------------
# perp signal (hyperliquid)
# -------------------------------------------------------------------------

async def compute_perp_positions(
    hl: HyperliquidClient,
    wallets: list[str],
) -> dict[str, list[dict]]:
    """
    for each EVM wallet, fetch open hyperliquid clearinghouse positions.

    returns {wallet: [position_dict, ...]} - only wallets with open non-neutral positions.
    """
    states = await hl.batch_clearinghouse_states(wallets)
    result: dict[str, list[dict]] = {}

    for wallet, state in states.items():
        parsed = []
        for ap in (state.get("assetPositions") or []):
            pos = ap.get("position", {})
            if not pos:
                continue
            p = parse_position(pos)
            if p["side"] != "NEUTRAL":
                parsed.append(p)
        if parsed:
            result[wallet] = parsed

    return result


# -------------------------------------------------------------------------
# full pass
# -------------------------------------------------------------------------

async def run_signal_pass(
    hl: HyperliquidClient,
    db_conn,
    nansen: NansenClient | None = None,
    days: int = 7,
) -> int:
    """
    full signal computation pass:
      1. load all indexed wallets from db
      2. fetch HL perp positions for EVM wallets (free, no limit)
      3. optionally compute spot positions via nansen for SOL wallets
      4. write position records to db
      5. recompute signals table

    returns total position count written.
    """
    async with db_conn.execute(
        "SELECT address, family, handle FROM wallets"
    ) as cur:
        wallet_rows = [dict(r) for r in await cur.fetchall()]

    sol_wallets = [w["address"] for w in wallet_rows if w["family"] == "solana"]
    evm_wallets = [w["address"] for w in wallet_rows if w["family"] in ("evm", "ethereum")]

    log.info("signal pass: %d sol, %d evm wallets", len(sol_wallets), len(evm_wallets))

    now = int(time.time())
    pos_count = 0

    # --- perp positions (hyperliquid) ---
    if evm_wallets:
        perp = await compute_perp_positions(hl, evm_wallets)

        for wallet, positions in perp.items():
            for pos in positions:
                pid = f"{wallet.lower()}:{pos['token_sym']}:hl"
                await db_conn.execute(
                    """
                    INSERT OR REPLACE INTO positions
                        (id, wallet, token_addr, token_sym, chain, venue, side,
                         size_usd, unrealized_pnl, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        pid, wallet.lower(),
                        pos["token_sym"], pos["token_sym"],
                        "evm", "hyperliquid", pos["side"],
                        pos["size_usd"], pos["unrealized_pnl"], now,
                    ),
                )
                pos_count += 1

        await db_conn.commit()
        log.info("wrote %d perp positions", pos_count)

    # aggregate signals from positions table
    await aggregate_signals(db_conn)
    return pos_count


# -------------------------------------------------------------------------
# aggregation
# -------------------------------------------------------------------------

async def aggregate_signals(db_conn) -> int:
    """
    recompute signals table from positions table.
    returns number of signal rows written.
    """
    async with db_conn.execute(
        "SELECT token_sym, token_addr, chain, venue, side, size_usd FROM positions"
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]

    groups: dict[tuple, dict] = {}
    for row in rows:
        key = (row["token_addr"], row["venue"])
        if key not in groups:
            groups[key] = {
                "token_addr":     row["token_addr"],
                "token_sym":      row["token_sym"],
                "chain":          row["chain"],
                "venue":          row["venue"],
                "long_count":     0,
                "short_count":    0,
                "neutral_count":  0,
                "total_long_usd": 0.0,
                "total_short_usd":0.0,
            }
        g   = groups[key]
        usd = float(row["size_usd"] or 0)
        if row["side"] == "LONG":
            g["long_count"]     += 1
            g["total_long_usd"] += usd
        elif row["side"] == "SHORT":
            g["short_count"]      += 1
            g["total_short_usd"]  += usd
        else:
            g["neutral_count"] += 1

    now = int(time.time())
    for g in groups.values():
        total    = g["long_count"] + g["short_count"]
        pct_long = round(g["long_count"] / total * 100, 1) if total > 0 else 50.0
        await db_conn.execute(
            """
            INSERT OR REPLACE INTO signals
                (token_addr, token_sym, chain, venue, pct_long,
                 long_count, short_count, neutral_count,
                 total_long_usd, total_short_usd, computed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                g["token_addr"], g["token_sym"], g["chain"], g["venue"],
                pct_long,
                g["long_count"], g["short_count"], g["neutral_count"],
                g["total_long_usd"], g["total_short_usd"], now,
            ),
        )
    await db_conn.commit()
    return len(groups)
