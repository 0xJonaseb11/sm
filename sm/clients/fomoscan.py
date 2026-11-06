"""fomoscan v3 REST and WebSocket client."""

import asyncio
import json
import logging
from typing import AsyncIterator, Callable

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from sm.config import cfg

log = logging.getLogger("sm.fomoscan")

BASE = cfg.fomoscan_rest
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
            timeout=15.0,
        )

    async def close(self):
        await self._http.aclose()

    # --- identity ---

    async def lookup_trader(
        self, handle: str, platform: str | None = None
    ) -> dict:
        params = {"handle": handle}
        if platform:
            params["platform"] = platform
        r = await self._http.get("/v3/traders/lookup", params=params)
        r.raise_for_status()
        return r.json()

    async def get_trader(self, trader_id: str) -> dict:
        r = await self._http.get(f"/v3/traders/{trader_id}")
        r.raise_for_status()
        return r.json()

    async def get_trader_wallets(self, trader_id: str) -> dict:
        r = await self._http.get(f"/v3/traders/{trader_id}/wallets")
        r.raise_for_status()
        return r.json()

    async def get_trader_pnl(
        self, trader_id: str, window: str = "7d"
    ) -> dict:
        r = await self._http.get(
            f"/v3/traders/{trader_id}/pnl", params={"window": window}
        )
        r.raise_for_status()
        return r.json()

    async def batch_pnl(self, trader_ids: list[str]) -> dict:
        r = await self._http.post(
            "/v3/traders/pnl", json={"traderIds": trader_ids}
        )
        r.raise_for_status()
        return r.json()

    async def resolve_wallet(self, family: str, address: str) -> dict:
        r = await self._http.get(f"/v3/wallets/{family}/{address}")
        r.raise_for_status()
        return r.json()

    # --- posts and callouts ---

    async def get_feed(
        self,
        platform: str | None = None,
        token_id: str | None = None,
        cursor: str | None = None,
    ) -> dict:
        params: dict = {}
        if platform:
            params["platform"] = platform
        if token_id:
            params["token"] = token_id
        if cursor:
            params["cursor"] = cursor
        r = await self._http.get("/v3/posts", params=params)
        r.raise_for_status()
        return r.json()

    async def get_token_posts(
        self, token_id: str, cursor: str | None = None
    ) -> dict:
        params: dict = {"token": token_id}
        if cursor:
            params["cursor"] = cursor
        r = await self._http.get(f"/v3/posts/token/{token_id}", params=params)
        r.raise_for_status()
        return r.json()

    async def get_trader_posts(self, trader_id: str) -> dict:
        r = await self._http.get(f"/v3/posts/trader/{trader_id}")
        r.raise_for_status()
        return r.json()

    # --- pump.fun via fomoscan ---

    async def pump_leaderboard(self) -> dict:
        r = await self._http.get("/v2/pump/pnl-leaderboard")
        r.raise_for_status()
        return r.json()

    async def pump_user_by_wallet(self, wallet: str) -> dict:
        r = await self._http.get(f"/v2/pump/user/wallet/{wallet}")
        r.raise_for_status()
        return r.json()

    async def pump_pnl(self, wallet: str) -> dict:
        r = await self._http.get(f"/v2/pump/user/wallet/{wallet}/pnl")
        r.raise_for_status()
        return r.json()

    async def pump_callouts(self, cursor: str | None = None) -> dict:
        params = {}
        if cursor:
            params["cursor"] = cursor
        r = await self._http.get("/v2/pump/thesis", params=params)
        r.raise_for_status()
        return r.json()

    async def pump_token_callouts(
        self, token_address: str, cursor: str | None = None
    ) -> dict:
        params = {}
        if cursor:
            params["cursor"] = cursor
        r = await self._http.get(
            f"/v2/pump/thesis/token/{token_address}", params=params
        )
        r.raise_for_status()
        return r.json()

    # --- leaderboards ---

    async def list_leaderboards(self) -> dict:
        r = await self._http.get("/v3/leaderboards")
        r.raise_for_status()
        return r.json()

    async def get_leaderboard(self, board_id: str) -> dict:
        r = await self._http.get(f"/v3/leaderboards/{board_id}")
        r.raise_for_status()
        return r.json()

    async def list_rankings(self) -> dict:
        r = await self._http.get("/v3/rankings")
        r.raise_for_status()
        return r.json()

    async def get_ranking(self, ranking_id: str) -> dict:
        r = await self._http.get(f"/v3/rankings/{ranking_id}")
        r.raise_for_status()
        return r.json()

    # --- token lookup ---

    async def get_token(self, token_id: str) -> dict:
        r = await self._http.get(f"/v3/tokens/{token_id}")
        r.raise_for_status()
        return r.json()

    # --- account / key info ---

    async def whoami(self) -> dict:
        r = await self._http.get("/v3/account")
        r.raise_for_status()
        return r.json()

    # --- live trades ---

    async def get_trades(
        self, cursor: str | None = None, trader_id: str | None = None
    ) -> dict:
        params: dict = {}
        if cursor:
            params["cursor"] = cursor
        if trader_id:
            params["traderId"] = trader_id
        r = await self._http.get("/v3/trades", params=params)
        r.raise_for_status()
        return r.json()

    async def get_trader_trades(
        self, trader_id: str, cursor: str | None = None
    ) -> dict:
        params: dict = {}
        if cursor:
            params["cursor"] = cursor
        r = await self._http.get(
            f"/v3/traders/{trader_id}/trades", params=params
        )
        r.raise_for_status()
        return r.json()


class FomoscanStream:
    """
    websocket stream for live posts from fomoscan v3.

    usage:
        stream = FomoscanStream()
        async for frame in stream.listen(platforms=["fomo","pump"]):
            print(frame)
    """

    def __init__(self):
        self._cursor: str | None = None

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
                        frame = json.loads(raw)
                        ftype = frame.get("type")

                        if ftype == "ready":
                            sub: dict = {"type": "subscribe", "topic": "posts"}
                            if platforms:
                                sub["platforms"] = platforms
                            if tokens:
                                sub["tokens"] = tokens
                            if self._cursor:
                                sub["since"] = self._cursor
                            await ws.send(json.dumps(sub))
                            if on_reconnect:
                                on_reconnect()

                        elif ftype == "post":
                            self._cursor = frame.get("cursor")
                            yield frame

                        elif ftype == "error":
                            log.warning("ws error: %s", frame.get("error"))

                        elif ftype == "ping":
                            await ws.send(json.dumps({"type": "pong"}))

            except ConnectionClosed as e:
                log.warning("ws closed (%s), reconnecting in %ds", e.code, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except Exception as e:
                log.warning("ws error: %s, reconnecting in %ds", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
