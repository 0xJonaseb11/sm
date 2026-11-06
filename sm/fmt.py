"""formatting helpers shared across views."""

import datetime
import time as _time


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


def pnl_color(val: float | None) -> str:
    if val is None:
        return "dim"
    return "green" if val >= 0 else "red"


def short_addr(addr: str, n: int = 6) -> str:
    if not addr:
        return ""
    if len(addr) <= n * 2 + 2:
        return addr
    return f"{addr[:n]}...{addr[-n:]}"


def ts_ago(ts: int) -> str:
    if not ts:
        return ""
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    diff = now - ts
    if diff < 60:
        return f"{diff}s ago"
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    return f"{diff // 86400}d ago"


def platform_tag(platform: str) -> str:
    tags = {
        "fomo": "[bold cyan]fomo[/]",
        "pump": "[bold magenta]pump[/]",
        "x": "[bold blue]x[/]",
    }
    return tags.get(platform.lower(), f"[dim]{platform}[/]")


def side_tag(side: str | None) -> str:
    if not side:
        return ""
    if side.lower() == "buy":
        return "[bold green]buy[/]"
    if side.lower() == "sell":
        return "[bold red]sell[/]"
    return f"[dim]{side}[/]"


def tier_tag(tier: str) -> str:
    colors = {
        "proven": "green",
        "account": "cyan",
        "partial": "yellow",
        "weak": "dim",
        "unverified": "dim red",
    }
    color = colors.get(tier, "dim")
    return f"[{color}]{tier}[/]"


def extract_post_parts(frame: dict) -> dict:
    """
    pull the useful bits out of a fomoscan v3 post frame.
    returns a flat dict ready for display.
    """
    item = frame.get("item", {})
    trader = item.get("trader", {}) or {}
    canonical = trader.get("canonical", {}) or {}
    token = item.get("token", {}) or {}
    platform = item.get("platform", "")

    # get handle from canonical or first source
    handle = canonical.get("handle", "")
    display = canonical.get("displayName", "") or handle

    # token info
    tok_sym = ""
    tok_addr = ""
    if token:
        tok_sym = token.get("symbol", "") or token.get("name", "")
        market = (token.get("canonical") or {}).get("market") or {}
        tok_addr = market.get("address", "") or token.get("address", "")

    # wallet
    wallet = item.get("wallet", "") or ""

    # post body
    body = item.get("body", "") or item.get("text", "") or ""

    # side from position attached to post
    position = item.get("position") or {}
    side = None
    usd_value = None
    if position:
        side = position.get("side") or position.get("type")
        usd_value = position.get("usdValue") or position.get("usd")

    # normalize timestamp to unix int
    raw_ts = item.get("postedAt", "") or item.get("createdAt", "") or ""
    if raw_ts and isinstance(raw_ts, str):
        try:
            dt = datetime.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            ts_int = int(dt.timestamp())
        except Exception:
            ts_int = int(_time.time())
    elif isinstance(raw_ts, (int, float)):
        ts_int = int(raw_ts)
    else:
        ts_int = int(_time.time())

    return {
        "id": item.get("id", ""),
        "platform": platform,
        "trader_id": trader.get("id", ""),
        "handle": handle,
        "display": display,
        "wallet": wallet,
        "token_sym": tok_sym,
        "token_addr": tok_addr,
        "side": side,
        "usd_value": usd_value,
        "body": body,
        "ts": ts_int,
        "raw": item,
    }
