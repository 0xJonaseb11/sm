"""fomoscan API client - v2 (all plans) and v3 (paid plan scope)."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from sm.clients.rate_limiter import fomoscan_limiter
from sm.config import cfg

log = logging.getLogger("sm.fomoscan")

BASE   = cfg.fomoscan_rest
WS_URL = cfg.fomoscan_ws


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {cfg.fomoscan_key}",
        "Content-Type": "application/json",
    }


class FomoscanClient:
    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url=BASE,
            headers=_headers(),
            timeout=20.0,
        )

    async def close(self):
        await self._http.aclose()

    async def _get(self, path: str, **kwargs) -> dict:
        await fomoscan_limiter.acquire()
        r = await self._http.get(path, **kwargs)
        r.raise_for_status()
        return r.json()

    async def _post(self, path: str, **kwargs) -> dict:
        await fomoscan_limiter.acquire()
        r = await self._http.post(path, **kwargs)
        r.raise_for_status()
        return r.json()

    # -------------------------------------------------------------------------
    # v2 identity  (available on all plans)
    # -------------------------------------------------------------------------

    async def get_user_by_handle(self, handle: str) -> dict:
        """100 CU on hit. returns wallet when plan allows reveals."""
        return await self._get(f"/v2/user/handle/{handle}")

    async def get_user_by_id(self, user_id: str) -> dict:
        """100 CU on hit."""
        return await self._get(f"/v2/user/id/{user_id}")

    async def get_user_by_wallet(self, address: str) -> dict:
        """30,000 CU on hit - costs a reveal."""
        return await self._get(f"/v2/user/wallet/{address}")

    async def get_pnl_by_handle(self, handle: str) -> dict:
        """30,000 CU on a ready answer - costs a reveal."""
        return await self._get(f"/v2/user/handle/{handle}/pnl")

    async def batch_pnl_handles(self, handles: list[str]) -> dict:
        """30,000 CU per ready entry."""
        return await self._post("/v2/user/handles/pnl", json={"handles": handles})

    # -------------------------------------------------------------------------
    # v2 thesis / posts  (available on all plans)
    # -------------------------------------------------------------------------

    async def get_theses(self, cursor: str | None = None, limit: int = 20) -> dict:
        """200 CU per 20 rows."""
        params: dict = {"limit": limit}
        if cursor:
            params["before"] = cursor
        return await self._get("/v2/thesis", params=params)

    async def get_theses_for_token(
        self, token_address: str, cursor: str | None = None, limit: int = 20
    ) -> dict:
        """200 CU per 20 rows."""
        params: dict = {"limit": limit}
        if cursor:
            params["before"] = cursor
        return await self._get(f"/v2/thesis/token/{token_address}", params=params)

    async def get_theses_for_user(
        self, user_id: str, cursor: str | None = None, limit: int = 20
    ) -> dict:
        """200 CU per 20 rows."""
        params: dict = {"limit": limit}
        if cursor:
            params["before"] = cursor
        return await self._get(f"/v2/thesis/user/{user_id}", params=params)

    async def get_trending_tokens(self) -> dict:
        """10 CU per call."""
        return await self._get("/v2/leaderboard/tokens/trending")

    # -------------------------------------------------------------------------
    # v2 pump.fun  (available on all plans, 10 CU per call)
    # -------------------------------------------------------------------------

    async def pump_leaderboard(self) -> dict:
        return await self._get("/v2/pump/leaderboard/traders")

    async def pump_user_by_wallet(self, wallet: str) -> dict:
        return await self._get(f"/v2/pump/user/wallet/{wallet}")

    async def pump_pnl(self, wallet: str) -> dict:
        """500 CU per call."""
        return await self._get(f"/v2/pump/user/wallet/{wallet}/pnl")

    async def pump_callouts(self, cursor: str | None = None) -> dict:
        params = {}
        if cursor:
            params["cursor"] = cursor
        return await self._get("/v2/pump/thesis", params=params)

    async def pump_token_callouts(
        self, token_address: str, cursor: str | None = None
    ) -> dict:
        params = {}
        if cursor:
            params["cursor"] = cursor
        return await self._get(f"/v2/pump/thesis/token/{token_address}", params=params)

    async def pump_wallet_callouts(
        self, wallet: str, cursor: str | None = None
    ) -> dict:
        params = {}
        if cursor:
            params["cursor"] = cursor
        return await self._get(f"/v2/pump/thesis/wallet/{wallet}", params=params)

    # -------------------------------------------------------------------------
    # v3 identity / traders  (paid plan scope - 403 on explore)
    # -------------------------------------------------------------------------

    async def lookup_trader(self, handle: str, platform: str | None = None) -> dict:
        params = {"handle": handle}
        if platform:
            params["platform"] = platform
        return await self._get("/v3/traders/lookup", params=params)

    async def get_trader_wallets(self, trader_id: str) -> dict:
        return await self._get(f"/v3/traders/{trader_id}/wallets")

    async def get_trader_pnl(self, trader_id: str, window: str = "7d") -> dict:
        return await self._get(f"/v3/traders/{trader_id}/pnl", params={"window": window})

    async def resolve_wallet(self, family: str, address: str) -> dict:
        return await self._get(f"/v3/wallets/{family}/{address}")

    # -------------------------------------------------------------------------
    # v3 rankings / leaderboards  (works on explore)
    # -------------------------------------------------------------------------

    async def list_rankings(self) -> dict:
        return await self._get("/v3/rankings")

    async def get_ranking(self, ranking_key: str) -> dict:
        """ranking_key is the 'key' field from list_rankings."""
        return await self._get(f"/v3/rankings/{ranking_key}")

    async def list_leaderboards(self) -> dict:
        return await self._get("/v3/leaderboards")

    async def get_leaderboard(self, board_id: str) -> dict:
        return await self._get(f"/v3/leaderboards/{board_id}")

    # -------------------------------------------------------------------------
    # v3 trades  (paid plan scope)
    # -------------------------------------------------------------------------

    async def get_trades(self, cursor: str | None = None) -> dict:
        params: dict = {}
        if cursor:
            params["cursor"] = cursor
        return await self._get("/v3/trades", params=params)

    async def get_trader_trades(self, trader_id: str, cursor: str | None = None) -> dict:
        params: dict = {}
        if cursor:
            params["cursor"] = cursor
        return await self._get(f"/v3/traders/{trader_id}/trades", params=params)

    # -------------------------------------------------------------------------
    # v3 posts  (paid plan scope)
    # -------------------------------------------------------------------------

    async def get_trader_posts(self, trader_id: str) -> dict:
        return await self._get(f"/v3/posts/trader/{trader_id}")

    # -------------------------------------------------------------------------
    # account
    # -------------------------------------------------------------------------

    async def whoami(self) -> dict:
        return await self._get("/v2/me")


class FomoscanStream:
    """
    websocket stream for live posts (requires Growth plan or higher).
    reconnects automatically with exponential backoff and cursor replay.
    """

    def __init__(self):
        self._cursor: str | None = None

    def _subscribe_msg(
        self,
        platforms: list[str] | None,
        tokens: list[str] | None,
    ) -> str:
        sub: dict = {"type": "subscribe", "topic": "posts"}
        if platforms:
            sub["platforms"] = platforms
        if tokens:
            sub["tokens"] = tokens
        if self._cursor:
            sub["since"] = self._cursor
        return json.dumps(sub)

    async def _handle_frame(self, ws, frame: dict, on_reconnect, platforms, tokens):
        ftype = frame.get("type")
        if ftype == "ready":
            await ws.send(self._subscribe_msg(platforms, tokens))
            if on_reconnect:
                on_reconnect()
        elif ftype == "post":
            self._cursor = frame.get("cursor")
            return frame
        elif ftype == "error":
            log.warning("ws error frame: %s", frame.get("error"))
        elif ftype == "ping":
            await ws.send(json.dumps({"type": "pong"}))
        return None

    async def listen(
        self,
        platforms: list[str] | None = None,
        tokens: list[str] | None = None,
        on_reconnect: Callable | None = None,
    ) -> AsyncIterator[dict]:
        backoff = 1
        while True:
            try:
                async with websockets.connect(
                    WS_URL,
                    extra_headers={"Authorization": f"Bearer {cfg.fomoscan_key}"},
                    ping_interval=30,
                    ping_timeout=75,
                ) as ws:
                    backoff = 1
                    async for raw in ws:
                        result = await self._handle_frame(
                            ws, json.loads(raw), on_reconnect, platforms, tokens
                        )
                        if result is not None:
                            yield result
            except ConnectionClosed as e:
                log.warning("ws closed (%s), reconnecting in %ds", e.code, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except (OSError, TimeoutError) as e:
                log.warning("ws error: %s, reconnecting in %ds", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
