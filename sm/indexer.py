"""
background indexer - walks fomoscan rankings and stores traders + wallets
to sqlite. uses v2 endpoints that work on all plans.
"""

import asyncio
import json
import logging
import time

from sm.clients.fomoscan import FomoscanClient
from sm.config import cfg
from sm import db as dbmod

log = logging.getLogger("sm.indexer")


def _decode(a) -> float | None:
    if a is None:
        return None
    if isinstance(a, (int, float)):
        return float(a)
    raw = a.get("value")
    dec = a.get("decimals", 6)
    return float(str(raw)) / (10 ** dec) if raw is not None else None


async def _index_ranking(
    client: FomoscanClient, database, ranking_key: str, label: str
) -> int:
    try:
        data    = await client.get_ranking(ranking_key)
        entries = data.get("entries", []) or []
    except Exception as e:
        log.warning("ranking %s failed: %s", ranking_key, e)
        return 0

    count = 0
    for entry in entries:
        subject  = entry.get("subject", {}) or {}
        canon    = subject.get("canonical", {}) or {}
        display  = canon.get("display", {}) or {}
        handle   = display.get("handle", "") or ""
        name     = display.get("name", "") or handle
        platforms = canon.get("platforms", [])

        sources  = subject.get("sources", {}) or {}
        src      = next(iter(sources.values()), {}) if sources else {}
        follow   = src.get("followGraph", {}) or {}
        followers = follow.get("followers") or 0

        # for v3 ranking entries there's no stable id from list_rankings
        # use the FOMO user id from sources if available
        fomo_src = sources.get("fomo", {}) or {}
        fs_uid   = fomo_src.get("profile", {}).get("id", "") if fomo_src else ""
        # fall back: compose a stable key from handle
        trader_id = fs_uid or f"idx:{handle}"

        if not handle:
            continue

        # look up v2 to get wallet + twitter
        sol_addr = ""
        evm_addr = ""
        twitter  = ""
        try:
            u = await client.get_user_by_handle(handle)
            sol_addr = u.get("solanaAddress", "") or ""
            evm_addr = u.get("evmAddress", "") or ""
            twitter  = u.get("twitter", "") or ""
            trader_id = u.get("id", trader_id)
        except Exception:
            pass

        await dbmod.upsert_trader(database, {
            "id":        trader_id,
            "handle":    handle,
            "display":   name,
            "platform":  platforms[0] if platforms else "",
            "wallets":   [w for w in [sol_addr, evm_addr] if w],
            "sol_addr":  sol_addr,
            "evm_addr":  evm_addr,
            "twitter":   twitter,
            "followers": followers,
        })

        if sol_addr:
            await dbmod.upsert_wallet(database, {
                "address":   sol_addr,
                "family":    "solana",
                "trader_id": trader_id,
                "handle":    handle,
                "display":   name,
                "tier":      "account",
            })
        if evm_addr:
            await dbmod.upsert_wallet(database, {
                "address":   evm_addr,
                "family":    "evm",
                "trader_id": trader_id,
                "handle":    handle,
                "display":   name,
                "tier":      "account",
            })

        count += 1
        # be kind to rate limits
        await asyncio.sleep(0.3)

    return count


async def _index_pump(client: FomoscanClient, database) -> int:
    try:
        data    = await client.pump_leaderboard()
        entries = data.get("entries", []) or []
    except Exception as e:
        log.warning("pump leaderboard failed: %s", e)
        return 0

    count = 0
    for entry in entries:
        handle  = entry.get("username") or entry.get("handle") or ""
        wallet  = entry.get("wallet", "") or ""
        if not wallet:
            continue

        await dbmod.upsert_wallet(database, {
            "address":   wallet,
            "family":    "solana",
            "trader_id": f"pump:{handle}",
            "handle":    handle,
            "display":   handle,
            "tier":      "account" if entry.get("isVerified") else "partial",
        })
        count += 1

    return count


async def index_once(verbose: bool = True) -> None:
    if not cfg.has_fomoscan():
        print("error: FOMOSCAN_KEY not set")
        return

    database = await dbmod.init()
    client   = FomoscanClient()

    def _log(msg: str) -> None:
        if verbose:
            print(msg)

    try:
        _log("fetching available rankings...")
        rankings = await client.list_rankings()
        items    = rankings.get("items", [])
        _log(f"found {len(items)} rankings")

        total = 0
        for item in items:
            key    = item.get("key", "")
            metric = item.get("metric", "")
            window = item.get("window", "")
            if key:
                _log(f"  indexing {metric} {window}...")
                n = await _index_ranking(client, database, key, f"{metric}/{window}")
                total += n
                _log(f"    -> {n} traders indexed")

        _log("indexing pump.fun leaderboard...")
        n = await _index_pump(client, database)
        _log(f"  -> {n} pump wallets indexed")
        total += n

        st = await dbmod.stats(database)
        _log(f"\ndone. db: {st['traders']} traders, {st['wallets']} wallets, {st['events']} events")

    except Exception as e:
        print(f"index error: {e}")
    finally:
        await client.close()
        await database.close()


async def run_index(interval_minutes: int = 30) -> None:
    print(f"indexer started, polling every {interval_minutes}m")
    while True:
        await index_once(verbose=False)
        await asyncio.sleep(interval_minutes * 60)
