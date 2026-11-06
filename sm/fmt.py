"""formatting helpers shared across all views and scripts."""

import datetime
import time as _time


# -------------------------------------------------------------------------
# numbers
# -------------------------------------------------------------------------

def usd(amount: float | int | None, sign: bool = True) -> str:
    if amount is None:
        return "-"
    val = float(amount)
    prefix = "+" if sign and val > 0 else ""
    if abs(val) >= 1_000_000:
        return f"{prefix}${val/1_000_000:.2f}m"
    if abs(val) >= 1_000:
        return f"{prefix}${val/1_000:.1f}k"
    return f"{prefix}${val:.2f}"


def pct(val: float | None) -> str:
    if val is None:
        return "-"
    prefix = "+" if val > 0 else ""
    return f"{prefix}{val:.1f}%"


def pnl_color(val: float | None) -> str:
    if val is None:
        return "dim"
    return "green" if val >= 0 else "red"


def num(val: float | int | None, decimals: int = 0) -> str:
    if val is None:
        return "-"
    if decimals == 0:
        return f"{int(val):,}"
    return f"{float(val):,.{decimals}f}"


# -------------------------------------------------------------------------
# addresses
# -------------------------------------------------------------------------

def short_addr(addr: str, n: int = 6) -> str:
    if not addr:
        return ""
    if len(addr) <= n * 2 + 3:
        return addr
    return f"{addr[:n]}...{addr[-n:]}"


# -------------------------------------------------------------------------
# time
# -------------------------------------------------------------------------

def ts_ago(ts: int | float) -> str:
    if not ts:
        return ""
    now  = int(_time.time())
    diff = now - int(ts)
    if diff < 0:
        return "just now"
    if diff < 60:
        return f"{diff}s ago"
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    return f"{diff // 86400}d ago"


def iso_to_ts(s: str) -> int:
    if not s:
        return 0
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return 0


def iso_short(s: str) -> str:
    """'2026-11-07T04:49:12Z' -> '11-07 04:49'"""
    if not s:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return s[:16]


# -------------------------------------------------------------------------
# rich tags
# -------------------------------------------------------------------------

def platform_tag(platform: str) -> str:
    tags = {
        "fomo":        "[bold cyan]fomo[/]",
        "pump":        "[bold magenta]pump[/]",
        "x":           "[bold blue]x[/]",
        "hyperliquid": "[bold yellow]hl[/]",
    }
    return tags.get((platform or "").lower(), f"[dim]{platform}[/]")


def side_tag(side: str | None) -> str:
    if not side:
        return ""
    s = (side or "").lower()
    if s == "buy":
        return "[bold green]buy[/]"
    if s == "sell":
        return "[bold red]sell[/]"
    return f"[dim]{side}[/]"


def tier_tag(tier: str) -> str:
    colors = {
        "proven":      "green",
        "account":     "cyan",
        "partial":     "yellow",
        "weak":        "dim",
        "unverified":  "dim red",
    }
    color = colors.get((tier or "").lower(), "dim")
    return f"[{color}]{tier}[/]"


def verified_star(verified: bool) -> str:
    return "[bold green]*[/]" if verified else " "


# -------------------------------------------------------------------------
# post/frame parsing
# -------------------------------------------------------------------------

def extract_post_parts(frame: dict) -> dict:
    """
    normalize a fomoscan v3 websocket post frame to a flat display dict.
    """
    item    = frame.get("item", {}) or {}
    trader  = item.get("trader", {}) or {}
    canon   = (trader.get("canonical") or {})
    display = (canon.get("display") or {})
    token   = item.get("token", {}) or {}
    platform = item.get("platform", "")

    handle  = display.get("handle", "") or ""
    name    = display.get("name", "") or handle

    tok_sym  = token.get("symbol", "") or token.get("name", "")
    tok_addr = ""
    canon_market = (token.get("canonical") or {}).get("market") or {}
    tok_addr = canon_market.get("address", "") or token.get("address", "")

    wallet  = item.get("wallet", "") or ""
    body    = item.get("body", "") or item.get("text", "") or ""

    position = item.get("position") or {}
    side     = None
    usd_val  = None
    if position:
        side    = position.get("side") or position.get("type")
        usd_val = position.get("usdValue") or position.get("usd")

    raw_ts  = item.get("postedAt", "") or item.get("createdAt", "") or ""
    ts_int  = iso_to_ts(raw_ts) if isinstance(raw_ts, str) else int(raw_ts or _time.time())

    return {
        "id":        item.get("id", ""),
        "platform":  platform,
        "trader_id": trader.get("id", ""),
        "handle":    handle,
        "display":   name,
        "wallet":    wallet,
        "token_sym": tok_sym,
        "token_addr":tok_addr,
        "side":      side,
        "usd_value": usd_val,
        "body":      body,
        "ts":        ts_int,
        "raw":       item,
    }
