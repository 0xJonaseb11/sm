"""nansen API client - token god mode and wallet labeling."""

import json
import logging
import httpx
from sm.config import cfg

log = logging.getLogger("sm.nansen")

BASE = "https://api.nansen.ai/api/v1"


def _headers() -> dict:
    return {
        "apikey": cfg.nansen_key,
        "Content-Type": "application/json",
    }


class NansenClient:
    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url=BASE,
            headers=_headers(),
            timeout=30.0,
        )

    async def close(self):
        await self._http.aclose()

    async def dex_trades(
        self,
        chain: str,
        token_address: str,
        date_from: str,
        date_to: str,
        page: int = 1,
        per_page: int = 100,
        filters: dict | None = None,
    ) -> dict:
        body: dict = {
            "chain": chain,
            "token_address": token_address,
            "date": {"from": date_from, "to": date_to},
            "pagination": {"page": page, "per_page": per_page},
        }
        if filters:
            body["filters"] = filters
        r = await self._http.post("/tgm/dex-trades", content=json.dumps(body))
        r.raise_for_status()
        return r.json()

    async def who_bought_sold(
        self,
        chain: str,
        token_address: str,
        date_from: str,
        date_to: str,
        action: str = "BUY",
        page: int = 1,
        per_page: int = 100,
    ) -> dict:
        body = {
            "chain": chain,
            "token_address": token_address,
            "date": {"from": date_from, "to": date_to},
            "action": action,
            "pagination": {"page": page, "per_page": per_page},
        }
        r = await self._http.post("/tgm/who-bought-sold", content=json.dumps(body))
        r.raise_for_status()
        return r.json()

    async def pnl_leaderboard(
        self,
        chain: str,
        token_address: str,
        date_from: str,
        date_to: str,
        premium_labels: bool = True,
        page: int = 1,
        per_page: int = 50,
        label_filter: str | None = None,
    ) -> dict:
        body: dict = {
            "chain": chain,
            "token_address": token_address,
            "date": {"from": date_from, "to": date_to},
            "premium_labels": premium_labels,
            "pagination": {"page": page, "per_page": per_page},
        }
        if label_filter:
            body["filters"] = {"trader_address_label": label_filter}
        r = await self._http.post("/tgm/pnl-leaderboard", content=json.dumps(body))
        r.raise_for_status()
        return r.json()

    async def holders(
        self,
        chain: str,
        token_address: str,
        premium_labels: bool = True,
        label_filter: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        body: dict = {
            "chain": chain,
            "token_address": token_address,
            "premium_labels": premium_labels,
            "pagination": {"page": page, "per_page": per_page},
        }
        if label_filter:
            body["filters"] = {"address_label": label_filter}
        r = await self._http.post("/tgm/holders", content=json.dumps(body))
        r.raise_for_status()
        return r.json()

    async def premium_labels(self, chain: str, address: str) -> dict:
        """check if a wallet is a known FOMO trader. free on all plans."""
        body = {"chain": chain, "address": address}
        r = await self._http.post(
            "/profiler/address/premium-labels", content=json.dumps(body)
        )
        r.raise_for_status()
        return r.json()


def is_fomo_label(label: str) -> bool:
    return "on FOMO" in label


def extract_fomo_handle(label: str) -> str:
    """pull 'iganu' from '👀 \"iganu\" on FOMO [2k3jk299]'"""
    if "on FOMO" not in label:
        return ""
    try:
        start = label.index('"') + 1
        end = label.index('"', start)
        return label[start:end]
    except ValueError:
        return label
