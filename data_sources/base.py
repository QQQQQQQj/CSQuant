"""数据源统一模型与 Provider 协议（stdlib dataclass，核心零依赖）。

铁律：平台特有字段不得穿出 Provider；统一结构之外的字段进 extras。
缺失一律 None，禁止置 0。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from utils.timeutils import iso_now

# 平台枚举
PLATFORMS = ("BUFF", "UUYP", "STEAM", "C5", "IGXE", "ECO")
# 数据源枚举
SOURCES = ("csqaq", "steamdt", "steam")
# 质量等级
QUALITY_A, QUALITY_B, QUALITY_C, QUALITY_D = "A", "B", "C", "D"


@dataclass
class ItemRef:
    """跨平台饰品身份引用。"""
    market_hash_name: str
    item_uuid: str | None = None
    csqaq_good_id: int | None = None
    steam_name_id: int | None = None
    buff_id: int | None = None
    uuyp_id: int | None = None
    c5_id: str | None = None
    igxe_id: int | None = None


@dataclass
class UnifiedQuote:
    market_hash_name: str
    platform: str
    source: str
    item_uuid: str | None = None
    sell_price: float | None = None
    sell_count: int | None = None
    buy_price: float | None = None       # 最高求购价
    buy_count: int | None = None
    volume_24h: int | None = None
    reference_price: float | None = None
    currency: str = "CNY"
    source_update_time: str | None = None
    fetched_at: str = field(default_factory=iso_now)
    data_quality: str = QUALITY_A
    quality_flags: list[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    def to_tick(self) -> dict:
        return {
            "ts": self.fetched_at, "item_uuid": self.item_uuid,
            "platform": self.platform, "source": self.source,
            "sell_price": self.sell_price, "sell_count": self.sell_count,
            "buy_price": self.buy_price, "buy_count": self.buy_count,
            "volume_24h": self.volume_24h, "reference_price": self.reference_price,
            "currency": self.currency, "source_update_time": self.source_update_time,
            "data_quality": self.data_quality, "quality_flags": self.quality_flags,
        }


@dataclass
class UnifiedKline:
    market_hash_name: str
    platform: str
    period: str
    ts: str
    open: float
    close: float
    high: float
    low: float
    volume: int | None = None
    source: str = ""
    item_uuid: str | None = None

    def to_row(self) -> dict:
        return {
            "item_uuid": self.item_uuid, "platform": self.platform,
            "period": self.period, "ts": self.ts, "open": self.open,
            "close": self.close, "high": self.high, "low": self.low,
            "volume": self.volume, "source": self.source,
        }


@dataclass
class UnifiedMarketIndex:
    index_name: str
    index_value: float
    source: str
    ts: str = field(default_factory=iso_now)
    change_1d: float | None = None
    change_7d: float | None = None
    change_30d: float | None = None
    breadth_up: int | None = None
    breadth_down: int | None = None
    sentiment_greedy: float | None = None
    sub_indices: list[dict] = field(default_factory=list)
    history: list[list] = field(default_factory=list)
    extras: dict = field(default_factory=dict)


@dataclass
class ProviderHealth:
    source: str
    status: str = "ok"          # ok / degraded / down
    latency_ms: int | None = None
    error: str | None = None


@dataclass
class ProviderCapabilities:
    quotes: bool = False
    batch_quotes: bool = False
    kline: bool = False
    kline_with_volume: bool = False
    market_index: bool = False
    breadth: bool = False
    id_mapping: bool = False
    inventory: bool = False
    wear: bool = False


class MarketDataProvider(ABC):
    """Provider 协议：上层（采集器/因子引擎）只依赖本接口。"""

    name: str = "base"
    capabilities: ProviderCapabilities = ProviderCapabilities()

    @abstractmethod
    def get_item_quotes(self, item: ItemRef) -> list[UnifiedQuote]:
        """单件饰品全平台报价。"""

    def get_batch_quotes(self, items: list[ItemRef]) -> dict[str, list[UnifiedQuote]]:
        """默认逐条实现；有批量能力的 Provider 覆写。"""
        return {i.market_hash_name: self.get_item_quotes(i) for i in items}

    def get_klines(self, item: ItemRef, period: str = "1d",
                   platform: str | None = None) -> list[UnifiedKline]:
        return []

    def get_market_index(self) -> UnifiedMarketIndex | None:
        return None

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(source=self.name)
