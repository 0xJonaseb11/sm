"""token-bucket rate limiter, shared across async clients."""

import asyncio
import time


class RateLimiter:
    """
    token bucket - allows up to `rate` calls per `period` seconds.
    awaiting `acquire()` blocks until a token is available.
    """

    def __init__(self, rate: int, period: float = 60.0):
        self._rate   = rate
        self._period = period
        self._tokens = float(rate)
        self._last   = time.monotonic()
        self._lock   = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(
                float(self._rate),
                self._tokens + elapsed * (self._rate / self._period),
            )
            self._last = now
            if self._tokens < 1:
                wait = (1 - self._tokens) * (self._period / self._rate)
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


# shared limiters - stay inside plan limits
fomoscan_limiter = RateLimiter(rate=55,  period=60.0)   # explore: 60/min, leave buffer
nansen_limiter   = RateLimiter(rate=8,   period=60.0)   # conservative
pump_limiter     = RateLimiter(rate=2,   period=1.0)    # 2 rps per pump.fun docs
