"""
cross-API enrichment layer.

takes raw wallet addresses and resolves social identities from
fomoscan (handle, x account) and nansen (FOMO trader label) concurrently.
results are cached and optionally persisted to sqlite.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from sm.cache import handle_cache
from sm.clients.fomoscan import FomoscanClient
from sm.clients.nansen import NansenClient, is_fomo_label, extract_fomo_handle

log = logging.getLogger("sm.enricher")


@dataclass
class WalletIdentity:
    address: str
    chain: str = "solana"

    # fomoscan
    fs_handle:    str = ""
    fs_name:      str = ""
    fs_twitter:   str = ""
    fs_user_id:   str = ""
    fs_sol_addr:  str = ""
    fs_evm_addr:  str = ""
    fs_tier:      str = ""
    fs_error:     str = ""

    # nansen
    ns_label:     str = ""
    ns_fomo_handle: str = ""
    ns_is_fomo:   bool = False
    ns_error:     str = ""

    # merged display name (best available)
    @property
    def display(self) -> str:
        if self.fs_handle:
            return self.fs_handle
        if self.ns_fomo_handle:
            return self.ns_fomo_handle
        if self.ns_label:
            return self.ns_label
        return ""

    @property
    def twitter_handle(self) -> str:
        if not self.fs_twitter:
            return ""
        return self.fs_twitter.rstrip("/").split("/")[-1]

    def to_dict(self) -> dict:
        return {
            "address":      self.address,
            "chain":        self.chain,
            "fs_handle":    self.fs_handle,
            "fs_name":      self.fs_name,
            "fs_twitter":   self.fs_twitter,
            "fs_user_id":   self.fs_user_id,
            "ns_label":     self.ns_label,
            "ns_fomo_handle": self.ns_fomo_handle,
            "ns_is_fomo":   self.ns_is_fomo,
            "display":      self.display,
        }


class Enricher:
    def __init__(
        self,
        fomoscan: FomoscanClient | None = None,
        nansen: NansenClient | None = None,
    ):
        self._fs = fomoscan
        self._ns = nansen

    async def enrich_wallet(self, address: str, chain: str = "solana") -> WalletIdentity:
        identity = WalletIdentity(address=address, chain=chain)
        tasks = []

        if self._fs:
            tasks.append(self._enrich_fomoscan(identity))
        if self._ns:
            tasks.append(self._enrich_nansen(identity))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return identity

    async def enrich_batch(
        self,
        addresses: list[str],
        chain: str = "solana",
        concurrency: int = 5,
    ) -> list[WalletIdentity]:
        sem = asyncio.Semaphore(concurrency)

        async def _bounded(addr: str) -> WalletIdentity:
            async with sem:
                return await self.enrich_wallet(addr, chain=chain)

        return list(await asyncio.gather(*[_bounded(a) for a in addresses]))

    async def _enrich_fomoscan(self, identity: WalletIdentity) -> None:
        if not self._fs:
            return
        cache_key = f"fs_wallet:{identity.address}"
        cached = await handle_cache.get(cache_key)
        if cached:
            _apply_fs(identity, cached)
            return
        try:
            resp = await self._fs.get_user_by_wallet(identity.address)
            await handle_cache.set(cache_key, resp)
            _apply_fs(identity, resp)
        except Exception as e:
            code = str(e)
            if "403" in code:
                identity.fs_error = "not in sample set (upgrade to reveal)"
            elif "404" in code:
                identity.fs_error = "no known owner"
            else:
                identity.fs_error = str(e)[:60]

    async def _enrich_nansen(self, identity: WalletIdentity) -> None:
        if not self._ns:
            return
        cache_key = f"ns_wallet:{identity.address}"
        cached = await handle_cache.get(cache_key)
        if cached:
            _apply_ns(identity, cached)
            return
        try:
            resp = await self._ns.premium_labels(
                chain=identity.chain, address=identity.address
            )
            await handle_cache.set(cache_key, resp)
            _apply_ns(identity, resp)
        except Exception as e:
            identity.ns_error = str(e)[:60]


def _apply_fs(identity: WalletIdentity, resp: dict) -> None:
    identity.fs_handle   = resp.get("handle", "") or ""
    identity.fs_name     = resp.get("name", "") or ""
    identity.fs_twitter  = resp.get("twitter", "") or ""
    identity.fs_user_id  = resp.get("id", "") or ""
    identity.fs_sol_addr = resp.get("solanaAddress", "") or ""
    identity.fs_evm_addr = resp.get("evmAddress", "") or ""


def _apply_ns(identity: WalletIdentity, resp: dict) -> None:
    labels = resp.get("data", []) or []
    for lbl in labels:
        label_str = lbl.get("label", "") or ""
        kinds     = lbl.get("kind", []) or []
        if "unique-identifier/fomo-trader" in kinds:
            identity.ns_is_fomo    = True
            identity.ns_fomo_handle = extract_fomo_handle(label_str)
        if label_str and not identity.ns_label:
            identity.ns_label = label_str
