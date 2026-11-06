"""nansen token god mode + wallet labeling client."""

import json
import logging
import httpx
from sm.config import cfg
from sm.clients.rate_limiter import nansen_limiter

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
            timeout=25.0,
        )

    async def close(self):
        await self._http.aclose()

    async def _post(self, path: str, body: dict) -> dict:
        await nansen_limiter.acquire()
        r = await self._http.post(path, content=json.dumps(body))
        r.raise_for_status()
        return r.json()

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
        return await self._post("/tgm/dex-trades", body)

    async def who_bought_sold(
        self,
        chain: str,
        token_address: str,
        date_from: str,
        date_to: str,
        action: str = "BUY",
        per_page: int = 100,
    ) -> dict:
        body = {
            "chain": chain,
            "token_address": token_address,
            "date": {"from": date_from, "to": date_to},
            "action": action,
            "pagination": {"page": 1, "per_page": per_page},
        }
        return await self._post("/tgm/who-bought-sold", body)

    async def pnl_leaderboard(
        self,
        chain: str,
        token_address: str,
        date_from: str,
        date_to: str,
        per_page: int = 50,
    ) -> dict:
        """
        note: premium_labels=True requires pro plan.
        this call uses the standard leaderboard which is available on all plans.
        field names: pnl_usd_realised, pnl_usd_unrealised, pnl_usd_total,
                     trader_address, trader_address_label, nof_trades, roi_percent_realised
        """
        body: dict = {
            "chain": chain,
            "token_address": token_address,
            "date": {"from": date_from, "to": date_to},
            "pagination": {"page": 1, "per_page": per_page},
        }
        return await self._post("/tgm/pnl-leaderboard", body)

    async def holders(
        self,
        chain: str,
        token_address: str,
        per_page: int = 50,
    ) -> dict:
        body: dict = {
            "chain": chain,
            "token_address": token_address,
            "pagination": {"page": 1, "per_page": per_page},
        }
        return await self._post("/tgm/holders", body)

    async def premium_labels(self, chain: str, address: str) -> dict:
        """
        check if a wallet is a known FOMO trader.
        free on all plans.  kind field contains 'unique-identifier/fomo-trader' for FOMO wallets.
        """
        body = {"chain": chain, "address": address}
        return await self._post("/profiler/address/premium-labels", body)

    async def batch_premium_labels(
        self, addresses: list[str], chain: str = "solana"
    ) -> dict[str, dict]:
        """
        resolve FOMO labels for multiple wallets concurrently.
        returns {address: label_data} for wallets that have labels.
        """
        import asyncio
        results: dict[str, dict] = {}

        async def _one(addr: str) -> None:
            try:
                resp = await self.premium_labels(chain=chain, address=addr)
                labels = resp.get("data", []) or []
                if labels:
                    results[addr] = {"labels": labels}
            except Exception:
                pass

        await asyncio.gather(*[_one(a) for a in addresses])
        return results


# -------------------------------------------------------------------------
# FOMO label helpers
# -------------------------------------------------------------------------

def is_fomo_label(label: str) -> bool:
    return "on FOMO" in (label or "")


def extract_fomo_handle(label: str) -> str:
    """pull 'iganu' from '👀 \"iganu\" on FOMO [2k3jk299]'"""
    if not is_fomo_label(label):
        return ""
    try:
        start = label.index('"') + 1
        end   = label.index('"', start)
        return label[start:end]
    except ValueError:
        return label


def parse_dex_trade(row: dict) -> dict:
    """
    normalize a nansen dex-trades row into a flat, typed dict.

    raw fields:
        block_timestamp, transaction_hash,
        trader_address, trader_address_label,
        action (BUY|SELL), token_address, token_name,
        token_amount, traded_token_address, traded_token_name,
        traded_token_amount, estimated_swap_price_usd, estimated_value_usd
    """
    label  = row.get("trader_address_label", "") or ""
    action = (row.get("action") or "").upper()
    usd    = row.get("estimated_value_usd") or 0.0
    return {
        "ts":          row.get("block_timestamp", ""),
        "tx":          row.get("transaction_hash", ""),
        "wallet":      row.get("trader_address", ""),
        "label":       label,
        "fomo_handle": extract_fomo_handle(label),
        "is_fomo":     is_fomo_label(label),
        "action":      action,
        "token_name":  row.get("token_name", ""),
        "token_addr":  row.get("token_address", ""),
        "token_amt":   row.get("token_amount") or 0.0,
        "sol_amt":     row.get("traded_token_amount") or 0.0,
        "usd":         usd,
        "price_usd":   row.get("estimated_swap_price_usd") or 0.0,
    }


def parse_pnl_row(row: dict) -> dict:
    """normalize a nansen pnl-leaderboard row."""
    label = row.get("trader_address_label", "") or ""
    return {
        "wallet":      row.get("trader_address", ""),
        "label":       label,
        "fomo_handle": extract_fomo_handle(label),
        "is_fomo":     is_fomo_label(label),
        "realized":    row.get("pnl_usd_realised") or 0.0,
        "unrealized":  row.get("pnl_usd_unrealised") or 0.0,
        "total":       row.get("pnl_usd_total") or 0.0,
        "roi_pct":     row.get("roi_percent_realised"),
        "trades":      row.get("nof_trades") or 0,
        "holding_usd": row.get("holding_usd") or 0.0,
        "netflow_usd": row.get("netflow_amount_usd") or 0.0,
    }
