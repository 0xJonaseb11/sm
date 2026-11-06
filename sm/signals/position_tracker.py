"""
position tracker - detects when indexed wallets open, close, or flip positions.

compares current on-chain state against the last known state stored in sqlite.
emits change events to the events table for alerting and downstream consumption.
"""

import logging
import time
import uuid

import sm.db as db

log = logging.getLogger("sm.signals.tracker")

# minimum size in USD to generate an alert (noise filter)
MIN_ALERT_USD = 50.0
# minimum delta to generate an increase/decrease event
MIN_DELTA_USD = 500.0


async def detect_and_write_changes(
    db_conn,
    new_positions: list[dict],
) -> list[dict]:
    """
    compare new_positions against stored positions and write change events.

    new_positions: list of dicts with at minimum:
        wallet, token_addr, token_sym, side, size_usd, venue

    returns list of change event dicts that were written to db.
    """
    changes = []

    for pos in new_positions:
        wallet   = (pos.get("wallet") or "").lower()
        token    = pos.get("token_addr", "")
        venue    = pos.get("venue", "")
        new_side = pos.get("side", "NEUTRAL")
        new_usd  = float(pos.get("size_usd") or 0)

        pos_id = f"{wallet}:{token}:{venue}"

        async with db_conn.execute(
            "SELECT side, size_usd FROM positions WHERE id = ?", (pos_id,)
        ) as cur:
            old = await cur.fetchone()

        if old is None:
            # new position - only alert if meaningful size
            if new_side != "NEUTRAL" and new_usd >= MIN_ALERT_USD:
                changes.append(_make_change(pos, "opened", new_side, new_usd))
        else:
            old_side = old["side"]
            old_usd  = float(old["size_usd"] or 0)

            if new_side == "NEUTRAL" and old_side != "NEUTRAL":
                changes.append(_make_change(pos, "closed", old_side, old_usd))

            elif new_side != old_side and old_side != "NEUTRAL":
                changes.append(_make_change(pos, "flipped", new_side, new_usd))

            elif new_side == old_side:
                delta = new_usd - old_usd
                if delta >= MIN_DELTA_USD:
                    changes.append(_make_change(pos, "increased", new_side, delta))
                elif delta <= -MIN_DELTA_USD:
                    changes.append(_make_change(pos, "decreased", new_side, abs(delta)))

    if changes:
        await _write_events(db_conn, changes)

    return changes


def _make_change(pos: dict, kind: str, side: str, usd: float) -> dict:
    return {
        "kind":       kind,
        "wallet":     (pos.get("wallet") or "").lower(),
        "token_addr": pos.get("token_addr", ""),
        "token_sym":  pos.get("token_sym", ""),
        "side":       side,
        "usd_value":  usd,
        "venue":      pos.get("venue", ""),
    }


async def _write_events(db_conn, changes: list[dict]) -> None:
    now = int(time.time())

    for ch in changes:
        # resolve identity from wallets table
        async with db_conn.execute(
            "SELECT handle, display FROM wallets WHERE address = ?", (ch["wallet"],)
        ) as cur:
            w = await cur.fetchone()

        handle  = w["handle"]  if w else ""
        display = w["display"] if w else ""
        label   = handle or display or ch["wallet"][:10]

        sym     = ch["token_sym"] or ch["token_addr"][:8]
        body    = f"{label} {ch['kind']} {ch['side'].lower()} on {sym}"

        ev = {
            "id":         str(uuid.uuid4()),
            "kind":       f"position_{ch['kind']}",
            "platform":   ch.get("venue", ""),
            "handle":     handle,
            "wallet":     ch["wallet"],
            "token_addr": ch["token_addr"],
            "token_sym":  ch["token_sym"],
            "side":       ch["side"],
            "usd_value":  ch["usd_value"],
            "body":       body,
            "ts":         now,
        }

        await db.insert_event(db_conn, ev)
        log.info("position change: %s $%.0f", body, ch["usd_value"])
