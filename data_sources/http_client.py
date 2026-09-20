"""统一 HTTP 客户端：限流 + 超时 + 指数退避重试 + 429/5xx 处理 + 熔断 + 健康回调。

所有 Provider 必须经本客户端发请求（DATA_SOURCE_SPEC §5/§7）。
"""
from __future__ import annotations

import time
from typing import Any, Callable

# 优先使用系统证书库：解决加速器/杀软/企业网关 TLS 拦截导致的
# "self-signed certificate in certificate chain" 错误（无 truststore 时回退 certifi）
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import requests

from data_sources.ratelimit import TokenBucket


class HttpError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class RateLimited(HttpError):
    pass


class CircuitOpen(Exception):
    pass


class RateLimitedClient:
    def __init__(
        self,
        source: str,
        rate_per_sec: float = 1.0,
        jitter: float = 0.0,
        timeout: tuple[float, float] = (5.0, 15.0),
        max_retries: int = 3,
        circuit_threshold: int = 5,
        circuit_cooldown_sec: int = 1800,
        on_result: Callable[[str, str, int, str, str | None], None] | None = None,
        session: requests.Session | None = None,
        endpoint_rates: dict[str, float] | None = None,
    ):
        self.source = source
        self.default_rate = rate_per_sec
        self.jitter = jitter
        # 端点级令牌桶：同一源不同接口独立限频（如 SteamDT batch 1/分 vs single 60/分）
        self.endpoint_rates = endpoint_rates or {}
        self._buckets: dict[str, TokenBucket] = {}
        self.timeout = timeout
        self.max_retries = max_retries
        self.circuit_threshold = circuit_threshold
        self.circuit_cooldown_sec = circuit_cooldown_sec
        # on_result(source, endpoint, latency_ms, status, error_code)
        self.on_result = on_result
        self.session = session or requests.Session()
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def _bucket_for(self, endpoint: str) -> TokenBucket:
        rate = self.default_rate
        for suffix, r in self.endpoint_rates.items():
            if endpoint.endswith(suffix):
                rate = r
                break
        bucket = self._buckets.get(endpoint)
        if bucket is None or bucket.rate != rate:
            bucket = TokenBucket(rate, jitter=self.jitter)
            self._buckets[endpoint] = bucket
        return bucket

    @property
    def circuit_open(self) -> bool:
        return time.monotonic() < self._circuit_open_until

    def get(self, url: str, headers: dict | None = None,
            params: dict | None = None, cookies: dict | None = None) -> Any:
        return self._request("GET", url, headers=headers, params=params,
                             cookies=cookies)

    def post(self, url: str, headers: dict | None = None,
             json_body: Any = None, cookies: dict | None = None) -> Any:
        return self._request("POST", url, headers=headers, json_body=json_body,
                             cookies=cookies)

    def _request(self, method: str, url: str, headers: dict | None = None,
                 params: dict | None = None, json_body: Any = None,
                 cookies: dict | None = None) -> Any:
        if self.circuit_open:
            raise CircuitOpen(f"[{self.source}] 熔断中，{url} 未发送")
        endpoint = url.split("?")[0]
        bucket = self._bucket_for(endpoint)
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            bucket.wait()
            start = time.monotonic()
            try:
                resp = self.session.request(
                    method, url, headers=headers, params=params,
                    json=json_body, cookies=cookies, timeout=self.timeout)
                latency_ms = int((time.monotonic() - start) * 1000)
                if resp.status_code == 429:
                    self._report(endpoint, latency_ms, "degraded", "429")
                    last_error = RateLimited(f"[{self.source}] 429 限频", 429)
                    time.sleep(self._backoff(attempt, base=5.0))
                    continue
                if resp.status_code in (400, 401, 403):
                    self._report(endpoint, latency_ms, "down", str(resp.status_code))
                    self._record_failure()
                    raise HttpError(
                        f"[{self.source}] 认证/参数错误 {resp.status_code}",
                        resp.status_code, resp.text[:500])
                if resp.status_code >= 500:
                    self._report(endpoint, latency_ms, "degraded", str(resp.status_code))
                    last_error = HttpError(f"[{self.source}] {resp.status_code}",
                                           resp.status_code)
                    time.sleep(self._backoff(attempt))
                    continue
                resp.raise_for_status()
                self._report(endpoint, latency_ms, "ok", None)
                self._record_success()
                return resp.json()
            except (requests.Timeout, requests.ConnectionError) as e:
                latency_ms = int((time.monotonic() - start) * 1000)
                self._report(endpoint, latency_ms, "degraded", type(e).__name__)
                last_error = e
                time.sleep(self._backoff(attempt))
        self._record_failure()
        raise HttpError(f"[{self.source}] 请求失败（{self.max_retries} 次重试后）: "
                        f"{last_error}") from last_error

    @staticmethod
    def _backoff(attempt: int, base: float = 1.0) -> float:
        return base * (4 ** attempt) + min(1.0, attempt * 0.3)

    def _report(self, endpoint: str, latency_ms: int, status: str,
                error_code: str | None) -> None:
        if self.on_result:
            try:
                self.on_result(self.source, endpoint, latency_ms, status, error_code)
            except Exception:
                pass

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.circuit_threshold:
            self._circuit_open_until = time.monotonic() + self.circuit_cooldown_sec
            self._consecutive_failures = 0

    def _record_success(self) -> None:
        self._consecutive_failures = 0
