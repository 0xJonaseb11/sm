"""hyperliquid public REST API client - no auth required."""

import asyncio
import logging

import httpx

from sm.clients.rate_limiter import RateLimiter

log = logging.getLogger("sm.hyperliquid")

BASE       = "https://api.hyperliquid.xyz"
STATS_BASE = "https://stats-data.hyperliquid.xyz"

# conservative: HL has no documented rate limit but we stay polite
hl_limiter = RateLimiter(rate=10, period=1.0)


class HyperliquidClient:
    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url=BASE,
            headers={"Content-Type": "application/json"},
            timeout=20.0,
        )
        self._stats = httpx.AsyncClient(
            base_url=STATS_BASE,
            timeout=20.0,
        )

    async def close(self):
        await self._http.aclose()
        await self._stats.aclose()

    async def _post(self, body: dict) -> dict | list:
        await hl_limiter.acquire()
        r = await self._http.post("/info", json=body)
        r.raise_for_status()
        return r.json()

    async def clearinghouse_state(self, user: str) -> dict:
        """open perp positions + account margin for a wallet (EVM address)."""
        return await self._post({"type": "clearinghouseState", "user": user})

    async def spot_clearinghouse_state(self, user: str) -> dict:
        """spot balances for a wallet."""
        return await self._post({"type": "spotClearinghouseState", "user": user})

    async def user_fills(self, user: str) -> list:
        """fill history for a wallet."""
        result = await self._post({"type": "userFills", "user": user})
        return result if isinstance(result, list) else []

    async def all_mids(self) -> dict:
        """latest mid prices for all assets, keyed by symbol."""
        result = await self._post({"type": "allMids"})
        return result if isinstance(result, dict) else {}

    async def meta(self) -> dict:
        """asset metadata including universe (symbols, szDecimals, maxLeverage)."""
        return await self._post({"type": "meta"})

    async def leaderboard(
        self,
        timeframe: str = "month",
        start_date: str = "2025-01-01",
    ) -> dict:
        """
        public perp trading leaderboard via stats-data endpoint.

        timeframe: 'day' | 'week' | 'month' | 'allTime'
        start_date: ISO date string, e.g. '2025-01-01'

        returns dict with 'leaderboardRows' list.
        each row: {ethAddress, accountValue, windowPerformances: [[window, {pnl, roi, vlm}]]}
        """
        await hl_limiter.acquire()
        r = await self._stats.get(
            "/Mainnet/leaderboard",
            params={"startDate": start_date, "timeframe": timeframe},
        )
        r.raise_for_status()
        return r.json()

    async def batch_clearinghouse_states(
        self,
        users: list[str],
        concurrency: int = 8,
    ) -> dict[str, dict]:
        """
        fetch open perp positions for multiple wallets concurrently.
        returns {address: state_dict} for wallets with valid responses.
        """
        sem = asyncio.Semaphore(concurrency)
        results: dict[str, dict] = {}

        async def _one(user: str) -> None:
            async with sem:
                try:
                    state = await self.clearinghouse_state(user)
                    if isinstance(state, dict):
                        results[user] = state
                except Exception as e:
                    log.debug("hl state error %s: %s", user[:10], e)

        await asyncio.gather(*[_one(u) for u in users])
        return results


# -------------------------------------------------------------------------
# parse helpers
# -------------------------------------------------------------------------

def parse_position(pos: dict) -> dict:
    """
    normalize a hyperliquid position entry.
    input: pos from state["assetPositions"][i]["position"]

    side is LONG when szi > 0, SHORT when szi < 0.
    size_usd = abs(szi) * entryPx (notional at entry).
    """
    szi    = float(pos.get("szi", 0) or 0)
    entry  = float(pos.get("entryPx", 0) or 0)
    upnl   = float(pos.get("unrealizedPnl", 0) or 0)
    marg   = float(pos.get("marginUsed", 0) or 0)
    coin   = pos.get("coin", "")
    liq_px = pos.get("liquidationPx")
    lev    = pos.get("leverage", {}) or {}

    return {
        "token_sym":      coin,
        "token_addr":     coin,  # HL uses symbol as the asset identifier
        "side":           "LONG" if szi > 0 else ("SHORT" if szi < 0 else "NEUTRAL"),
        "size":           abs(szi),
        "entry_price":    entry,
        "size_usd":       abs(szi) * entry,
        "unrealized_pnl": upnl,
        "margin_used":    marg,
        "liq_price":      float(liq_px) if liq_px else None,
        "leverage_type":  lev.get("type", ""),
        "leverage_value": lev.get("value", 0),
    }


def parse_fill(fill: dict) -> dict:
    """
    normalize a hyperliquid fill entry from userFills.

    side field: "B" = buy (long), "A" = ask/sell (short).
    """
    sz   = float(fill.get("sz", 0) or 0)
    px   = float(fill.get("px", 0) or 0)
    side = fill.get("side", "")
    return {
        "ts":          fill.get("time", 0),
        "tx":          str(fill.get("tid", "")),
        "token_sym":   fill.get("coin", ""),
        "side":        "BUY" if side == "B" else "SELL",
        "size":        sz,
        "price":       px,
        "usd":         sz * px,
        "fee":         float(fill.get("fee", 0) or 0),
        "closed_pnl":  float(fill.get("closedPnl", 0) or 0),
        "dir":         fill.get("dir", ""),
        "crossed":     fill.get("crossed", False),
        "hash":        fill.get("hash", ""),
    }


def parse_leaderboard_entry(entry: dict) -> dict:
    """
    normalize a hyperliquid leaderboard row.

    windowPerformances is a list of [windowName, {pnl, roi, vlm}] pairs.
    """
    perfs: dict[str, dict] = {}
    for w in entry.get("windowPerformances", []) or []:
        if isinstance(w, list) and len(w) == 2:
            name, stats = w
            perfs[name] = stats

    best = perfs.get("allTime") or perfs.get("30d") or {}
    return {
        "eth_address": entry.get("ethAddress", ""),
        "display":     entry.get("displayName", "") or entry.get("ethAddress", "")[:10],
        "pnl_alltime": float(best.get("pnl", 0) or 0),
        "roi_alltime": float(best.get("roi", 0) or 0),
        "vlm_alltime": float(best.get("vlm", 0) or 0),
        "perfs":       perfs,
    }
