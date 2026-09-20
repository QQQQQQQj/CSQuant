"""令牌桶限流（线程安全）。按 source:endpoint 维度各持一桶。"""
from __future__ import annotations

import random
import threading
import time


class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: float | None = None,
                 jitter: float = 0.0):
        self.rate = rate_per_sec
        self.capacity = burst if burst is not None else max(1.0, rate_per_sec)
        self.tokens = self.capacity
        self.jitter = jitter
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity,
                                  self.tokens + (now - self._last) * self.rate)
                self._last = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    break
                sleep_for = (1.0 - self.tokens) / self.rate
                # 锁外睡眠，避免阻塞其他桶的使用者（此处简化为持锁短睡）
                time.sleep(min(sleep_for, 1.0))
        if self.jitter > 0:
            time.sleep(random.uniform(0, self.jitter))
