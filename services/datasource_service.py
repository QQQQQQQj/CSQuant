"""服务层：数据源健康 + 手动采集 + K线增量 + 元数据同步。"""
from __future__ import annotations

from typing import Callable

from data_sources.base import ItemRef
from data_sources.unified.service import UnifiedMarketService, CollectReport
from database.dao import Dao
from items.metadata import sync_metadata


def make_health_recorder(dao: Dao) -> Callable:
    """RateLimitedClient.on_result 回调：每次 HTTP 结果写 datasource_health。"""
    def record(source: str, endpoint: str, latency_ms: int,
               status: str, error_code: str | None) -> None:
        try:
            dao.insert_health({"source": source, "endpoint": endpoint,
                               "latency_ms": latency_ms, "status": status,
                               "error_code": error_code})
            dao.commit()
        except Exception:  # noqa: BLE001
            pass   # 健康记录失败不得影响业务请求
    return record


class DataSourceService:
    def __init__(self, dao: Dao, unified: UnifiedMarketService | None = None,
                 steamdt=None):
        self.dao = dao
        self.unified = unified
        self.steamdt = steamdt

    def get_health(self) -> list[dict]:
        return self.dao.get_health_latest()

    def get_events(self, limit: int = 50) -> list[dict]:
        return self.dao.get_events(limit)

    def _watchlist_refs(self, tiers=("L1", "L2")) -> list[ItemRef]:
        return [ItemRef(market_hash_name=r["market_hash_name"],
                        item_uuid=r["item_uuid"],
                        csqaq_good_id=r.get("csqaq_good_id"))
                for r in self.dao.get_watchlist(tiers=tiers)]

    def trigger_collect(self, scope: str = "watchlist") -> CollectReport | None:
        if self.unified is None:
            return None
        tiers = ("L1", "L2") if scope == "watchlist" else ("L1", "L2", "L3")
        return self.unified.collect_for_items(self._watchlist_refs(tiers))

    def collect_klines(self, platform: str = "BUFF", period: str = "1d") -> int:
        """自选池 K线增量（SteamDT；无成交量，入库 volume=NULL）。"""
        if self.steamdt is None:
            return 0
        count = 0
        for ref in self._watchlist_refs():
            try:
                klines = self.steamdt.get_klines(ref, period=period,
                                                 platform=platform)
            except Exception:  # noqa: BLE001
                continue
            for k in klines:
                if k.ts:
                    self.dao.upsert_kline(k.to_row())
                    count += 1
            self.dao.commit()
        return count

    def trigger_metadata_sync(self, force: bool = False) -> dict:
        return sync_metadata(self.dao, force=force)
