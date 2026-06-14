"""In-process sliding-window limiter (per worker). Redis-backed is the multi-worker upgrade (debt).

Memory is bounded with LRU eviction so an attacker spraying unique keys (e.g. many distinct emails)
cannot grow the key map without bound and exhaust memory. The authentication endpoint fails CLOSED on
any limiter fault (see `app/api/auth.py`): for credential validation, brief unavailability is preferable
to silently bypassing brute-force protection.
"""
import time
from collections import OrderedDict, deque
from threading import Lock

# Cap on the number of tracked (email|ip) keys. Beyond this, the least-recently-used keys are evicted.
DEFAULT_MAX_KEYS = 50_000


class SlidingWindowLimiter:
    def __init__(
        self, max_attempts: int, window_seconds: int, *, max_keys: int = DEFAULT_MAX_KEYS
    ) -> None:
        self.max = max_attempts
        self.window = window_seconds
        self.max_keys = max_keys
        # OrderedDict gives O(1) LRU: most-recently-touched key moves to the end; eviction pops the front.
        self._hits: OrderedDict[str, deque] = OrderedDict()
        self._lock = Lock()

    def _touch(self, key: str) -> deque:
        """Return the deque for `key` (creating it), mark it most-recently-used, and bound memory."""
        dq = self._hits.get(key)
        if dq is None:
            dq = deque()
            self._hits[key] = dq
        self._hits.move_to_end(key)
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)  # evict the least-recently-used (never the key we just touched)
        return dq

    def check(
        self,
        key: str,
        *,
        now: float | None = None,
        max_attempts: int | None = None,
        window_seconds: float | None = None,
    ) -> tuple[bool, int]:
        """(allowed, retry_after_seconds). Does not record; call record_failure on a failed attempt.

        `max_attempts`/`window_seconds` override the construction-time thresholds for this call (the
        in-process window state is shared and preserved). The login path passes them from the runtime
        config so the brute-force policy is tunable without rebuilding this singleton.
        """
        now = time.monotonic() if now is None else now
        limit = self.max if max_attempts is None else max_attempts
        window = self.window if window_seconds is None else window_seconds
        with self._lock:
            dq = self._touch(key)
            while dq and dq[0] <= now - window:
                dq.popleft()
            if len(dq) >= limit:
                return False, max(int(window - (now - dq[0])) + 1, 1)
            return True, 0

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._touch(key).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
