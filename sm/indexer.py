"""
background indexer - polls fomoscan rankings and stores wallets + traders
in the local sqlite db so the tool works offline or as a cross-reference.
"""

import asyncio
import json
import logging
import time

from sm.clients.fomoscan import FomoscanClient
from sm.config import cfg
from sm import db as dbmod

log = logging.getLogger("sm.indexer")


async def _index_ranking(client: FomoscanClient, database, ranking_id: str) -> int:
    """pull one ranking and upsert all traders and wallets. returns count indexed."""
    try:
        data = await client.get_ranking(ranking_id)
        entries = data.get("items", []) or data.get("entries", [])
    except Exception as e:
        log.warning("ranking %s failed: %s", ranking_id, e)
        return 0

    count = 0
    for entry in entries:
        trader = entry.get("trader", {}) or {}
        tid = trader.get("id", "")
        if not tid:
            continue

        canonical = (trader.get("canonical") or {})
        handle = canonical.get("handle", "")
        display = canonical.get("displayName", "") or handle
        platforms = canonical.get("platforms", [])

        # upsert trader
        await dbmod.upsert_trader(database, {
            "id": tid,
            "handle": handle,
            "display": display,
            "platform": platforms[0] if platforms else "",
            "wallets": [],
        })

        # try to get wallets
        try:
            w_resp = await client.get_trader_wallets(tid)
            wallets = w_resp.get("items", [])
            addrs = []
            for w in wallets:
                addr = w.get("address", "")
                family = w.get("family", "")
                owners = w.get("owners", []) or []
                tier = owners[0].get("tier", "unverified") if owners else "unverified"
                addrs.append(addr)
                if addr:
                    await dbmod.upsert_wallet(database, {
                        "address": addr,
                        "family": family,
                        "trader_id": tid,
                        "handle": handle,
                        "display": display,
                        "tier": tier,
                    })
            # update trader wallets list
            await dbmod.upsert_trader(database, {
                "id": tid,
                "handle": handle,
                "display": display,
                "platform": platforms[0] if platforms else "",
                "wallets": addrs,
            })
            count += 1
        except Exception as e:
            log.debug("wallets for %s: %s", tid, e)

        # be kind to rate limits
        await asyncio.sleep(0.2)

    return count


async def run_index(interval_minutes: int = 30) -> None:
    """run indexer loop, polling every interval_minutes."""
    if not cfg.has_fomoscan():
        print("error: FOMOSCAN_KEY not set, indexer cannot run")
        return

    database = await dbmod.init()
    client = FomoscanClient()

    print(f"indexer started, polling every {interval_minutes}m")

    while True:
        try:
            rankings = await client.list_rankings()
            items = rankings.get("items", [])
            log.info("found %d rankings to index", len(items))

            total = 0
            for item in items:
                rid = item.get("id", "")
                if rid:
                    n = await _index_ranking(client, database, rid)
                    total += n
                    log.info("indexed %d traders from ranking %s", n, rid)

            log.info("index pass complete, %d traders total", total)
        except Exception as e:
            log.warning("index pass failed: %s", e)

        await asyncio.sleep(interval_minutes * 60)


async def index_once() -> None:
    """run a single index pass and exit."""
    if not cfg.has_fomoscan():
        print("error: FOMOSCAN_KEY not set")
        return

    database = await dbmod.init()
    client = FomoscanClient()

    try:
        print("fetching available rankings...")
        rankings = await client.list_rankings()
        items = rankings.get("items", [])
        print(f"found {len(items)} rankings")

        total = 0
        for item in items:
            rid = item.get("id", "")
            metric = item.get("metric", "")
            window = item.get("window", "")
            if rid:
                print(f"  indexing {metric} {window}...")
                n = await _index_ranking(client, database, rid)
                total += n
                print(f"    -> {n} traders indexed")

        print(f"\ndone. {total} traders in db.")
    except Exception as e:
        print(f"index error: {e}")
    finally:
        await client.close()
        await database.close()
