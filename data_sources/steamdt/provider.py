"""SteamDT Provider（辅源：价格兜底/跨源校验/大盘备份/磨损）。

文档：https://doc.steamdt.com/  服务地址：https://open.steamdt.com
认证：Header `Authorization: Bearer {KEY}`，无签名。
限频：base 1次/日；price/single 60次/分；price/batch 1次/分(≤100)；kline 120次/分。
统一返回：{success, data, errorCode, errorMsg}。K线明确不含成交量。
"""
from __future__ import annotations

from typing import Any

from data_sources.base import (
    ItemRef, MarketDataProvider, ProviderCapabilities, ProviderHealth,
    UnifiedKline, UnifiedMarketIndex, UnifiedQuote,
)
from data_sources.http_client import HttpError, RateLimitedClient
from utils.timeutils import to_iso, parse_iso
from datetime import datetime, timezone

BASE_URL = "https://open.steamdt.com"

_KLINE_PLATFORM_MAP = {
    "BUFF": "BUFF", "UUYP": "YOUPIN", "STEAM": "STEAM",
    "C5": "C5", "ALL": "ALL",
}


class SteamDTProvider(MarketDataProvider):
    name = "steamdt"
    capabilities = ProviderCapabilities(
        quotes=True, batch_quotes=True, kline=True, market_index=True,
        id_mapping=True, wear=True,
    )

    def __init__(self, api_key: str, client: RateLimitedClient | None = None):
        if not api_key:
            raise ValueError("STEAMDT_API_KEY 未配置")
        self.api_key = api_key
        self.client = client or RateLimitedClient(source=self.name, rate_per_sec=1.0)

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        if isinstance(payload, dict):
            if payload.get("success") is False:
                raise HttpError(f"[steamdt] 业务错误: {payload.get('errorCode')} "
                                f"{payload.get('errorMsg')}")
            if "data" in payload:
                return payload["data"]
        return payload

    # ---------------- 单品/批量报价 ----------------
    def get_item_quotes(self, item: ItemRef) -> list[UnifiedQuote]:
        raw = self.client.get(f"{BASE_URL}/open/cs2/v1/price/single",
                              headers=self._headers,
                              params={"marketHashName": item.market_hash_name})
        data = self._unwrap(raw)
        return self._map_price_list(item, data)

    def get_batch_quotes(self, items: list[ItemRef]) -> dict[str, list[UnifiedQuote]]:
        names = [i.market_hash_name for i in items][:100]
        ref_by_name = {i.market_hash_name: i for i in items}
        raw = self.client.post(f"{BASE_URL}/open/cs2/v1/price/batch",
                               headers=self._headers,
                               json_body={"marketHashNames": names})
        data = self._unwrap(raw)
        result: dict[str, list[UnifiedQuote]] = {}
        if isinstance(data, dict):
            for name, price_list in data.items():
                ref = ref_by_name.get(name) or ItemRef(market_hash_name=name)
                result[name] = self._map_price_list(ref, price_list)
        return result

    def _map_price_list(self, item: ItemRef, data: Any) -> list[UnifiedQuote]:
        quotes: list[UnifiedQuote] = []
        entries = data if isinstance(data, list) else (
            data.get("list") if isinstance(data, dict) else None) or []
        for e in entries:
            if not isinstance(e, dict):
                continue
            platform = str(e.get("platform", "")).upper()
            if platform == "YOUPIN":
                platform = "UUYP"
            quotes.append(UnifiedQuote(
                market_hash_name=item.market_hash_name,
                item_uuid=item.item_uuid,
                platform=platform or "UNKNOWN", source=self.name,
                sell_price=_to_float(e.get("sellPrice")),
                sell_count=_to_int(e.get("sellCount")),
                buy_price=_to_float(e.get("biddingPrice")),
                buy_count=_to_int(e.get("biddingCount")),
                source_update_time=_norm_time(e.get("updateTime")),
                extras={"platformItemId": e.get("platformItemId")},
            ))
        return quotes

    # ---------------- K线（无成交量） ----------------
    def get_klines(self, item: ItemRef, period: str = "1d",
                   platform: str | None = None) -> list[UnifiedKline]:
        """type: 1-3 含义待验证（按日/周/月推测，默认1）。返回 [ts, open, close, high, low]。"""
        body = {
            "marketHashName": item.market_hash_name,
            "type": 1,
            "platform": _KLINE_PLATFORM_MAP.get((platform or "ALL").upper(), "ALL"),
        }
        raw = self.client.post(f"{BASE_URL}/open/cs2/item/v1/kline",
                               headers=self._headers, json_body=body)
        data = self._unwrap(raw)
        klines: list[UnifiedKline] = []
        for row in data if isinstance(data, list) else []:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            klines.append(UnifiedKline(
                market_hash_name=item.market_hash_name,
                item_uuid=item.item_uuid,
                platform=(platform or "ALL").upper(),
                period=period, ts=_norm_time(row[0]) or "",
                open=float(row[1]), close=float(row[2]),
                high=float(row[3]), low=float(row[4]),
                volume=None,  # 官方明确不含成交量
                source=self.name,
            ))
        return klines

    # ---------------- 大盘 ----------------
    def get_market_index(self) -> UnifiedMarketIndex | None:
        raw = self.client.get(f"{BASE_URL}/open/cs2/broad/v1/index",
                              headers=self._headers)
        data = self._unwrap(raw)
        if not isinstance(data, dict):
            return None
        value = _to_float(data.get("broadMarketIndex"))
        if value is None:
            return None
        return UnifiedMarketIndex(
            index_name="steamdt_broad",
            index_value=value,
            source=self.name,
            ts=_norm_time(data.get("updateTime")) or "",
            change_1d=_to_float(data.get("diffYesterdayRatio")),
            history=data.get("historyMarketIndexList") or [],
            extras={"diffYesterday": _to_float(data.get("diffYesterday"))},
        )

    # ---------------- 全量基础信息（每日1次，必须落库） ----------------
    def get_base_items(self) -> list[dict]:
        raw = self.client.get(f"{BASE_URL}/open/cs2/v1/base", headers=self._headers)
        data = self._unwrap(raw)
        return data if isinstance(data, list) else []

    def health_check(self) -> ProviderHealth:
        try:
            self.get_market_index()
            return ProviderHealth(source=self.name, status="ok")
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(source=self.name, status="down", error=str(e))


def _norm_time(v: Any) -> str | None:
    """SteamDT 时间可能是秒/毫秒时间戳或字符串，统一为 UTC ISO。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        ts = float(v)
        if ts > 1e12:
            ts /= 1000.0
        return to_iso(datetime.fromtimestamp(ts, tz=timezone.utc))
    s = str(v)
    if s.isdigit():
        return _norm_time(int(s))
    try:
        return to_iso(parse_iso(s))
    except ValueError:
        return s  # 保留原始串，质量管道另行处理


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
