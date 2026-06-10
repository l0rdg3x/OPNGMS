"""In-process sliding-window limiter (per worker). Redis-backed is the multi-worker upgrade (debt)."""
import time
from collections import defaultdict, deque
from threading import Lock


class SlidingWindowLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max = max_attempts
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, *, now: float | None = None) -> tuple[bool, int]:
        """(allowed, retry_after_seconds). Does not record; call record_failure on a failed attempt."""
        now = time.monotonic() if now is None else now
        with self._lock:
            dq = self._hits[key]
            while dq and dq[0] <= now - self.window:
                dq.popleft()
            if len(dq) >= self.max:
                return False, max(int(self.window - (now - dq[0])) + 1, 1)
            return True, 0

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._hits[key].append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
