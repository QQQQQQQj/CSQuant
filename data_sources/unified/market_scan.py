"""全市场分层批量采集（不依赖 watchlist）。

调度策略（指示文档性能要求）：
- Tier A（高流动/被提升的异动件）：每 tick 采集；
- Tier B：每 tier_b_every 个 tick 采集一次；
- Tier C + 未入库饰品：按 item_master rowid 游标轮换慢采（universe 持续扩张）；
- 单批 ≤ scan_batch_size（默认 100，对齐 SteamDT batch 限制），
  单 tick ≤ scan_max_batches 批（对齐 1 批/分钟限频）；
- 异动标的 promote() 升频（Tier A），promoted_until 到期自动降级。
"""
from __future__ import annotations

import json

from data_sources.base import ItemRef
from data_sources.unified.service import UnifiedMarketService
from database.dao import Dao
from market_anomaly.universe import build_universe
from utils.logger import get_logger
from utils.timeutils import iso_now, parse_iso

_PROMOTED_KEY = "market_scan_promoted"     # {item_uuid: until_iso}
_CURSOR_KEY = "market_scan_cursor"
_ROUND_KEY = "market_scan_round"

DEFAULTS = {
    "scan_batch_size": 100,
    "scan_max_batches": 3,
    "tier_b_every": 3,
    "rotation_size": 100,
    "promote_minutes": 120,
}


class MarketScanService:
    def __init__(self, dao: Dao, unified: UnifiedMarketService,
                 batch_provider, cfg: dict):
        self.dao = dao
        self.unified = unified
        self.batch_provider = batch_provider   # 需实现 get_batch_quotes
        ma = cfg.get("market_anomaly", {})
        self.cfg = {**DEFAULTS, **{k: ma[k] for k in DEFAULTS if k in ma}}
        self.universe_cfg = ma.get("universe")
        self.log = get_logger("csquant.market_scan")

    # ---------------- 升降频 ----------------
    def _promoted(self, now: str) -> dict[str, str]:
        raw = json.loads(self.dao.get_meta(_PROMOTED_KEY) or "{}")
        now_dt = parse_iso(now)
        alive = {k: v for k, v in raw.items() if parse_iso(v) > now_dt}
        if len(alive) != len(raw):
            self.dao.set_meta(_PROMOTED_KEY, json.dumps(alive))
            self.dao.commit()
        return alive

    def promote(self, item_uuids: list[str], now: str | None = None) -> None:
        """异动标的升频至 Tier A，promote_minutes 后自动降级。"""
        from datetime import timedelta
        now = now or iso_now()
        until = (parse_iso(now)
                 + timedelta(minutes=self.cfg["promote_minutes"])).isoformat(
                     timespec="seconds")
        promoted = self._promoted(now)
        for uid in item_uuids:
            promoted[uid] = until
        self.dao.set_meta(_PROMOTED_KEY, json.dumps(promoted))
        self.dao.commit()

    # ---------------- 采集 tick ----------------
    def scan_tick(self, now: str | None = None) -> dict:
        now = now or iso_now()
        report = {"batches": 0, "collected": 0, "tier_a": 0, "tier_b": 0,
                  "rotation": 0, "skipped_no_provider": False}
        if self.batch_provider is None:
            report["skipped_no_provider"] = True
            return report

        round_no = int(self.dao.get_meta(_ROUND_KEY) or "0") + 1
        self.dao.set_meta(_ROUND_KEY, str(round_no))

        refs: dict[str, ItemRef] = {}
        # Tier A/B：来自现有快照的 universe 分层 + 升频名单
        candidates = self.dao.get_universe_candidates()
        universe = build_universe(candidates, self.universe_cfg)
        promoted = set(self._promoted(now))
        for entry in universe:
            tier = "A" if (entry.tier == "A" or entry.item_uuid in promoted) \
                else entry.tier
            if tier == "A":
                report["tier_a"] += 1
            elif tier == "B" and round_no % self.cfg["tier_b_every"] == 0:
                report["tier_b"] += 1
            else:
                continue
            refs[entry.item_uuid] = ItemRef(
                market_hash_name=entry.market_hash_name,
                item_uuid=entry.item_uuid)
        # Tier C/未入库：item_master rowid 游标轮换（universe 持续扩张的来源）
        cursor = int(self.dao.get_meta(_CURSOR_KEY) or "0")
        page = self.dao.get_items_page(after_rowid=cursor,
                                       limit=self.cfg["rotation_size"])
        if not page:   # 轮换到末尾，从头再来
            cursor = 0
            page = self.dao.get_items_page(after_rowid=0,
                                           limit=self.cfg["rotation_size"])
        for row in page:
            if row["item_uuid"] not in refs:
                refs[row["item_uuid"]] = ItemRef(
                    market_hash_name=row["market_hash_name"],
                    item_uuid=row["item_uuid"],
                    csqaq_good_id=row.get("csqaq_good_id"))
                report["rotation"] += 1
        if page:
            self.dao.set_meta(_CURSOR_KEY, str(page[-1]["rid"]))

        # 分批采集（单批 ≤ 限制、单 tick ≤ max_batches）
        all_refs = list(refs.values())
        batch_size = self.cfg["scan_batch_size"]
        for i in range(0, len(all_refs), batch_size):
            if report["batches"] >= self.cfg["scan_max_batches"]:
                break
            chunk = all_refs[i:i + batch_size]
            try:
                quotes_by_name = self.batch_provider.get_batch_quotes(chunk)
            except Exception as e:  # noqa: BLE001
                self.dao.log_event("WARN", "market_scan",
                                   f"批量采集失败: {e}")
                break
            report["batches"] += 1
            for quotes in quotes_by_name.values():
                if not quotes:
                    continue
                quotes = self.unified.quality_pipeline(list(quotes))
                for q in quotes:
                    if q.item_uuid is None:
                        continue
                    self.dao.insert_tick(q.to_tick())
                    self.dao.upsert_snapshot(q.to_tick())
                report["collected"] += 1
        self.dao.commit()
        self.log.info("全市场扫描: %s", report)
        return report
