import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, per_minute: int, window: float = 60.0):
        self.per_minute = per_minute
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return False
        hits.append(now)
        if len(self._hits) > 5000:
            self._sweep(now)
        return True

    def _sweep(self, now: float) -> None:
        for key in [k for k, hits in self._hits.items() if not hits or now - hits[-1] >= self.window]:
            del self._hits[key]
