"""pump.fun API client (api.pump.fun/v1)."""

import logging
import httpx
from sm.config import cfg

log = logging.getLogger("sm.pump")

BASE = cfg.pump_rest


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {cfg.pump_key}",
        "Content-Type": "application/json",
    }


class PumpClient:
    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url=BASE,
            headers=_headers(),
            timeout=15.0,
        )

    async def close(self):
        await self._http.aclose()

    # --- profiles ---

    async def get_user(self, user_id: str) -> dict:
        """user_id can be a pump.fun user id or a wallet address."""
        r = await self._http.get(f"/users/{user_id}")
        r.raise_for_status()
        return r.json()

    async def search_users(self, q: str) -> dict:
        r = await self._http.get("/users/search", params={"q": q})
        r.raise_for_status()
        return r.json()

    # --- leaderboards ---

    async def pnl_leaderboard(self, window: str = "7d") -> dict:
        """window: 1d | 7d | 30d"""
        r = await self._http.get("/leaderboards/pnl", params={"window": window})
        r.raise_for_status()
        return r.json()

    async def creator_payouts(self) -> dict:
        r = await self._http.get("/leaderboards/creator-payouts")
        r.raise_for_status()
        return r.json()

    # --- positions ---

    async def get_positions(self, wallet: str) -> dict:
        r = await self._http.get(f"/positions/{wallet}")
        r.raise_for_status()
        return r.json()

    async def get_trades(
        self, wallet: str, cursor: str | None = None
    ) -> dict:
        params: dict = {}
        if cursor:
            params["cursor"] = cursor
        r = await self._http.get(f"/positions/{wallet}/trades", params=params)
        r.raise_for_status()
        return r.json()

    # --- content ---

    async def get_feed(self, cursor: str | None = None) -> dict:
        params: dict = {}
        if cursor:
            params["cursor"] = cursor
        r = await self._http.get("/content/feed", params=params)
        r.raise_for_status()
        return r.json()

    async def get_callouts(
        self, wallet: str, cursor: str | None = None
    ) -> dict:
        params: dict = {}
        if cursor:
            params["cursor"] = cursor
        r = await self._http.get(f"/content/{wallet}/callouts", params=params)
        r.raise_for_status()
        return r.json()

    # --- graph ---

    async def get_followers(self, user_id: str) -> dict:
        r = await self._http.get(f"/graph/{user_id}/followers")
        r.raise_for_status()
        return r.json()

    async def get_following(self, user_id: str) -> dict:
        r = await self._http.get(f"/graph/{user_id}/following")
        r.raise_for_status()
        return r.json()
