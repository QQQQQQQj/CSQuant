"""CSQAQ Provider（主源）。

文档：https://docs.csqaq.com/  服务地址：https://api.csqaq.com/api/v1
认证：Header `ApiToken` + IP 白名单。限频：单 IP 1 次/秒不限总量。
合规：官方声明数据严禁商用，本系统仅个人投研用途。

注意：响应包裹结构按 {code, data} 容错解析；个别字段名以官方文档为准，
不确定处在代码注释标注「待验证」。
"""
from __future__ import annotations

from typing import Any

from data_sources.base import (
    ItemRef, MarketDataProvider, ProviderCapabilities, ProviderHealth,
    UnifiedKline, UnifiedMarketIndex, UnifiedQuote,
)
from data_sources.http_client import HttpError, RateLimitedClient
from utils.timeutils import iso_now

BASE_URL = "https://api.csqaq.com/api/v1"

# CSQAQ 字段前缀 -> 统一平台名
_PLATFORM_PREFIX = [
    ("BUFF", "buff"), ("UUYP", "yyyp"), ("STEAM", "steam"),
    ("C5", "c5"), ("IGXE", "igxe"), ("ECO", "eco"),
]


class CSQAQProvider(MarketDataProvider):
    name = "csqaq"
    capabilities = ProviderCapabilities(
        quotes=True, batch_quotes=True, kline=True, market_index=True,
        breadth=True, id_mapping=True, inventory=True,
    )

    def __init__(self, token: str, client: RateLimitedClient | None = None):
        if not token:
            raise ValueError("CSQAQ_API_TOKEN 未配置")
        self.token = token
        self.client = client or RateLimitedClient(source=self.name, rate_per_sec=1.0)

    @property
    def _headers(self) -> dict:
        return {"ApiToken": self.token}

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        """容错解包：官方响应包裹结构待验证，按常见 {code, data} 处理。"""
        if isinstance(payload, dict):
            for key in ("data", "result"):
                if key in payload and payload[key] is not None:
                    return payload[key]
        return payload

    # ---------------- IP 白名单 ----------------
    def bind_local_ip(self) -> str:
        """绑定本机出口IP到Token白名单（官方接口 /sys/bind_local_ip，30秒/次）。

        适用于非固定IP场景；返回绑定状态信息。
        """
        raw = self.client.post(f"{BASE_URL}/sys/bind_local_ip",
                               headers=self._headers)
        data = self._unwrap(raw)
        return str(data)

    # ---------------- 单品报价 ----------------
    def get_item_quotes(self, item: ItemRef) -> list[UnifiedQuote]:
        if not item.csqaq_good_id:
            return []
        raw = self.client.get(f"{BASE_URL}/info/good",
                              headers=self._headers, params={"id": item.csqaq_good_id})
        data = self._unwrap(raw)
        if not isinstance(data, dict):
            return []
        return self._map_good_detail(item, data)

    def _map_good_detail(self, item: ItemRef, d: dict) -> list[UnifiedQuote]:
        quotes: list[UnifiedQuote] = []
        volume = _to_int(d.get("turnover_number"))
        for platform, prefix in _PLATFORM_PREFIX:
            sell_price = _to_float(d.get(f"{prefix}_sell_price"))
            sell_count = _to_int(d.get(f"{prefix}_sell_num"))
            buy_price = _to_float(d.get(f"{prefix}_buy_price"))
            buy_count = _to_int(d.get(f"{prefix}_buy_num"))
            if all(v is None for v in (sell_price, sell_count, buy_price, buy_count)):
                continue  # 该平台无数据，跳过（不为 0）
            quotes.append(UnifiedQuote(
                market_hash_name=item.market_hash_name,
                item_uuid=item.item_uuid,
                platform=platform, source=self.name,
                sell_price=sell_price, sell_count=sell_count,
                buy_price=buy_price, buy_count=buy_count,
                volume_24h=volume if platform == "STEAM" else None,
                reference_price=_to_float(d.get("turnover_avg_price"))
                if platform == "STEAM" else None,
                extras={
                    "statistic": _to_int(d.get("statistic")),
                    "sell_price_rate_1": _to_float(d.get("sell_price_rate_1")),
                    "sell_price_rate_7": _to_float(d.get("sell_price_rate_7")),
                    "sell_price_rate_30": _to_float(d.get("sell_price_rate_30")),
                    "rank_num": _to_int(d.get("rank_num")),
                } if platform == "BUFF" else {},
            ))
        return quotes

    def get_good_detail_raw(self, good_id: int) -> dict:
        """保留原始响应（租赁/存世量等扩展字段取用）。"""
        raw = self.client.get(f"{BASE_URL}/info/good",
                              headers=self._headers, params={"id": good_id})
        data = self._unwrap(raw)
        return data if isinstance(data, dict) else {}

    # ---------------- ID 映射 ----------------
    def iter_good_ids(self, page_size: int = 500, max_pages: int = 200):
        """分页拉取 good_id <-> market_hash_name 映射（POST /info/get_good_id）。"""
        for page in range(1, max_pages + 1):
            raw = self.client.post(f"{BASE_URL}/info/get_good_id",
                                   headers=self._headers,
                                   json_body={"page": page, "page_size": page_size})
            data = self._unwrap(raw)
            items = data.get("list") if isinstance(data, dict) else data
            if not items:
                break
            yield items

    # ---------------- 大盘 ----------------
    def get_market_index(self) -> UnifiedMarketIndex | None:
        raw = self.client.get(f"{BASE_URL}/current_data",
                              headers=self._headers, params={"type": "init"})
        data = self._unwrap(raw)
        if not isinstance(data, dict):
            return None
        # 字段名以官方 api-187131779 为准；此处防御性提取（待验证键名）
        index_value = _to_float(_dig(data, "index_data", "index")
                                or _dig(data, "index_data", "current_index")
                                or data.get("index"))
        if index_value is None:
            return None
        breadth = data.get("rise_fall_distribution") or data.get("distribution") or {}
        sub = data.get("sub_index_data") or []
        return UnifiedMarketIndex(
            index_name="csqaq_main",
            index_value=index_value,
            source=self.name,
            change_1d=_to_float(_dig(data, "index_data", "change_rate")
                                or _dig(data, "index_data", "rise")),
            breadth_up=_to_int(breadth.get("rise_count") or breadth.get("up")),
            breadth_down=_to_int(breadth.get("fall_count") or breadth.get("down")),
            sentiment_greedy=_to_float(data.get("greedy") or data.get("sentiment")),
            sub_indices=[{
                "name": _to_str(s.get("name")),
                "value": _to_float(s.get("index") or s.get("value")),
                "change": _to_float(s.get("rise") or s.get("change_rate")),
            } for s in sub if isinstance(s, dict)],
            extras={"raw_keys": list(data.keys())},
        )

    # ---------------- K线（免费档为图表数据；含量K线仅企业档） ----------------
    def get_klines(self, item: ItemRef, period: str = "1d",
                   platform: str | None = None) -> list[UnifiedKline]:
        """图表接口（api-187131781）粒度与字段待验证；优先用企业K线（若已授权）。"""
        return []  # V1 免费档不保证稳定结构，由 SteamDT/Steam 补；企业档开通后实现

    def health_check(self) -> ProviderHealth:
        try:
            self.client.get(f"{BASE_URL}/current_data",
                            headers=self._headers, params={"type": "init"})
            return ProviderHealth(source=self.name, status="ok")
        except HttpError as e:
            if e.status_code in (400, 401, 403):
                # 可能是IP白名单未绑定：自动绑定本机IP后重试一次
                try:
                    self.bind_local_ip()
                    self.client.get(f"{BASE_URL}/current_data",
                                    headers=self._headers,
                                    params={"type": "init"})
                    return ProviderHealth(source=self.name, status="ok")
                except Exception as e2:  # noqa: BLE001
                    return ProviderHealth(source=self.name, status="down",
                                          error=f"认证失败且自动绑IP无效: {e2}")
            return ProviderHealth(source=self.name, status="down", error=str(e))
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(source=self.name, status="down", error=str(e))


def _dig(d: dict, *path) -> Any:
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _to_float(v: Any) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_str(v: Any) -> str | None:
    return str(v) if v is not None else None
