"""统一行情服务：主备降级 + 数据质量管道 + 落库。

降级矩阵（DATA_SOURCE_SPEC §9）：单品多平台价格 CSQAQ 主 → SteamDT 备 → 上次快照。
质量规则（§10）：跨平台偏离>15% / 买卖倒挂 / 时序突变>30% / 缺失降级 / 备源降 C。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from data_sources.base import (
    QUALITY_A, QUALITY_B, QUALITY_C, ItemRef, MarketDataProvider, UnifiedQuote,
)
from data_sources.http_client import CircuitOpen, HttpError
from database.dao import Dao
from utils.logger import get_logger

DIVERGENCE_THRESHOLD = 0.15   # 跨平台偏离阈值
SPIKE_THRESHOLD = 0.30        # 时序突变阈值


@dataclass
class CollectReport:
    total: int = 0
    success: int = 0
    fallback_used: int = 0
    failed: int = 0
    failed_items: list[str] = field(default_factory=list)


class UnifiedMarketService:
    def __init__(self, dao: Dao, primary: MarketDataProvider | None,
                 fallback: MarketDataProvider | None = None,
                 steam: MarketDataProvider | None = None):
        self.dao = dao
        self.primary = primary       # CSQAQ
        self.fallback = fallback     # SteamDT
        self.steam = steam           # Steam 官方（低频）
        self.log = get_logger("csquant.unified")

    # ---------------- 采集主流程 ----------------
    def collect_for_items(self, items: list[ItemRef]) -> CollectReport:
        report = CollectReport(total=len(items))
        for item in items:
            try:
                quotes, used_fallback = self._fetch_with_fallback(item)
            except (HttpError, CircuitOpen) as e:
                report.failed += 1
                report.failed_items.append(item.market_hash_name)
                self.dao.log_event("WARN", "unified", f"采集失败: {item.market_hash_name}",
                                   {"error": str(e)})
                continue
            if not quotes:
                report.failed += 1
                report.failed_items.append(item.market_hash_name)
                continue
            if used_fallback:
                report.fallback_used += 1
            quotes = self.quality_pipeline(quotes)
            for q in quotes:
                self.dao.insert_tick(q.to_tick())
                self.dao.upsert_snapshot(q.to_tick())
            report.success += 1
        self.dao.commit()
        self.log.info("采集完成: %s/%s 成功, %s 降级, %s 失败",
                      report.success, report.total, report.fallback_used, report.failed)
        return report

    def _fetch_with_fallback(self, item: ItemRef) -> tuple[list[UnifiedQuote], bool]:
        """返回 (quotes, 是否使用了备源)。"""
        if self.primary is not None:
            try:
                quotes = self.primary.get_item_quotes(item)
                if quotes:
                    return quotes, False
            except (HttpError, CircuitOpen) as e:
                self.dao.log_event("WARN", "unified",
                                   f"主源 {self.primary.name} 失败，尝试备源",
                                   {"item": item.market_hash_name, "error": str(e)})
        if self.fallback is not None:
            quotes = self.fallback.get_item_quotes(item)
            for q in quotes:
                q.data_quality = QUALITY_C
                q.quality_flags.append("SOURCE_FALLBACK")
            return quotes, True
        return [], False

    # ---------------- 数据质量管道 ----------------
    def quality_pipeline(self, quotes: list[UnifiedQuote]) -> list[UnifiedQuote]:
        quotes = list(quotes)
        self._check_missing(quotes)
        self._check_inverted_book(quotes)
        self._check_cross_platform_divergence(quotes)
        self._check_spike(quotes)
        return quotes

    @staticmethod
    def _check_missing(quotes: list[UnifiedQuote]) -> None:
        for q in quotes:
            if q.sell_price is None and q.data_quality == QUALITY_A:
                q.data_quality = QUALITY_B
                q.quality_flags.append("MISSING_SELL_PRICE")

    @staticmethod
    def _check_inverted_book(quotes: list[UnifiedQuote]) -> None:
        for q in quotes:
            if q.sell_price is not None and q.buy_price is not None \
                    and q.buy_price > q.sell_price:
                q.quality_flags.append("INVERTED_BOOK")

    @staticmethod
    def _check_cross_platform_divergence(quotes: list[UnifiedQuote]) -> None:
        prices = [q.sell_price for q in quotes if q.sell_price]
        if len(prices) < 2:
            return
        lo, hi = min(prices), max(prices)
        if lo > 0 and (hi - lo) / lo > DIVERGENCE_THRESHOLD:
            for q in quotes:
                if q.sell_price is not None:
                    q.quality_flags.append("CROSS_PLATFORM_DIVERGENCE")
                    if q.data_quality == QUALITY_A:
                        q.data_quality = QUALITY_B

    def _check_spike(self, quotes: list[UnifiedQuote]) -> None:
        """与上一快照对比，|Δ|>30% 且量未放大 → 标记。"""
        for q in quotes:
            if q.sell_price is None or q.item_uuid is None:
                continue
            prev = self.dao.get_snapshot(q.item_uuid, q.platform)
            if not prev or not prev.get("sell_price"):
                continue
            old = prev["sell_price"]
            if old <= 0:
                continue
            change = abs(q.sell_price - old) / old
            if change > SPIKE_THRESHOLD:
                vol_old = prev.get("volume_24h")
                vol_new = q.volume_24h
                volume_surge = (vol_old and vol_new and vol_new > vol_old * 2)
                if not volume_surge:
                    q.quality_flags.append("PRICE_SPIKE_SUSPECT")
