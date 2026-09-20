"""Steam 库存同步：/inventory/{id}/730/2 -> portfolio_position。

策略（V1 保守）：
- 新出现的 asset → 新增持仓（buy_price=None 待补录，禁止默认为 0）；
- 消失的 asset → 不自动删（可能转出/上架未成交），记 event 提示人工确认；
- 匹配不上 item_master 的 → 自动建占位 item 并记 unmatched。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from data_sources.steam.provider import SteamMarketProvider
from database.dao import Dao
from utils.logger import get_logger

log = get_logger("csquant.inventory")


@dataclass
class SyncReport:
    added: int = 0
    existing: int = 0
    missing_from_steam: int = 0
    unmatched: list[str] = field(default_factory=list)


def sync_steam_inventory(dao: Dao, steam: SteamMarketProvider) -> SyncReport:
    report = SyncReport()
    remote_items = steam.fetch_inventory()
    seen_asset_ids: set[str] = set()

    for entry in remote_items:
        name = entry["market_hash_name"]
        asset_id = entry["asset_id"]
        seen_asset_ids.add(asset_id)

        item = dao.get_item_by_hash_name(name)
        if not item:
            dao.upsert_item_master({"market_hash_name": name})
            dao.commit()
            item = dao.get_item_by_hash_name(name)
            report.unmatched.append(name)
        item_uuid = item["item_uuid"]

        existing = dao.find_position_by_asset(item_uuid, asset_id)
        if existing:
            report.existing += 1
            continue
        dao.add_position({
            "item_uuid": item_uuid,
            "asset_id": asset_id,
            "quantity": entry.get("amount", 1),
            "buy_price": None,          # 成本待人工补录
            "buy_platform": None,
            "status": "HOLDING",
            "notes": "steam_sync",
        })
        report.added += 1
        # 持仓自动进自选池 L1（最高采集优先级）
        dao.add_watchlist(item_uuid, tier="L1")

    # 库存在 Steam 侧消失的持仓（仅同步来源的）
    for pos in dao.get_positions(status="HOLDING"):
        if pos.get("asset_id") and pos["asset_id"] not in seen_asset_ids \
                and pos.get("notes") == "steam_sync":
            report.missing_from_steam += 1
            dao.log_event("WARN", "inventory",
                          f"持仓在Steam库存中消失（转出/上架？）: {pos['position_id']}",
                          {"item_uuid": pos["item_uuid"], "asset_id": pos["asset_id"]})

    dao.commit()
    log.info("库存同步完成: 新增 %s, 已有 %s, 消失 %s, 未匹配 %s",
             report.added, report.existing, report.missing_from_steam,
             len(report.unmatched))
    return report
