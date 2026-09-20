"""持仓管理：手工录入、成本补录、卖出登记、状态机（HOLDING/RENTED/SOLD）。

KPI 口径移植自 csgo_investment 聚合公式组（修正其运算符优先级 bug）：
- cost_basis = quantity * buy_price
- 卖出已实现盈亏 = (price - fee) * quantity - cost_basis
"""
from __future__ import annotations

from dataclasses import dataclass

from database.dao import Dao
from utils.timeutils import iso_now


@dataclass
class SellResult:
    realized_pnl: float
    return_rate: float | None
    proceeds: float


def add_manual_position(dao: Dao, market_hash_name: str, quantity: int = 1,
                        buy_price: float | None = None, buy_time: str | None = None,
                        buy_platform: str | None = None,
                        notes: str | None = None) -> str:
    """手工建仓（csgo_investment 式台账升级版）。返回 position_id。"""
    item = dao.get_item_by_hash_name(market_hash_name)
    if not item:
        dao.upsert_item_master({"market_hash_name": market_hash_name})
        dao.commit()
        item = dao.get_item_by_hash_name(market_hash_name)
    cost_basis = round(quantity * buy_price, 2) if buy_price is not None else None
    position_id = dao.add_position({
        "item_uuid": item["item_uuid"],
        "quantity": quantity,
        "buy_price": buy_price,
        "buy_time": buy_time,
        "buy_platform": buy_platform,
        "cost_basis": cost_basis,
        "status": "HOLDING",
        "notes": notes or "manual",
    })
    dao.add_watchlist(item["item_uuid"], tier="L1")
    dao.insert_trade({
        "position_id": position_id, "item_uuid": item["item_uuid"], "side": "BUY",
        "quantity": quantity, "price": buy_price or 0, "fee": 0,
        "platform": buy_platform, "traded_at": buy_time or iso_now(),
    }) if buy_price is not None else None
    dao.commit()
    return position_id


def set_buy_cost(dao: Dao, position_id: str, buy_price: float,
                 buy_time: str | None = None, buy_platform: str | None = None) -> None:
    """补录买入成本（Steam 同步来的持仓初始无成本）。"""
    pos = dao.get_position(position_id)
    if not pos:
        raise KeyError(f"持仓不存在: {position_id}")
    dao.update_position(position_id, {
        "buy_price": buy_price,
        "buy_time": buy_time or pos.get("buy_time"),
        "buy_platform": buy_platform or pos.get("buy_platform"),
        "cost_basis": round(pos["quantity"] * buy_price, 2),
    })
    dao.commit()


def record_sell(dao: Dao, position_id: str, price: float, fee: float = 0.0,
                platform: str | None = None, traded_at: str | None = None) -> SellResult:
    """卖出登记：写流水 + 状态置 SOLD + 计算已实现盈亏。"""
    pos = dao.get_position(position_id)
    if not pos:
        raise KeyError(f"持仓不存在: {position_id}")
    if pos["status"] == "SOLD":
        raise ValueError("该持仓已卖出")
    qty = pos["quantity"]
    proceeds = round((price - fee) * qty, 2)
    cost = pos.get("cost_basis")
    realized = round(proceeds - cost, 2) if cost is not None else None
    dao.insert_trade({
        "position_id": position_id, "item_uuid": pos["item_uuid"], "side": "SELL",
        "quantity": qty, "price": price, "fee": fee, "platform": platform,
        "traded_at": traded_at or iso_now(),
    })
    dao.update_position(position_id, {"status": "SOLD"})
    dao.commit()
    return SellResult(
        realized_pnl=realized if realized is not None else 0.0,
        return_rate=(round(realized / cost, 4) if (realized is not None and cost) else None),
        proceeds=proceeds,
    )


def set_status(dao: Dao, position_id: str, status: str) -> None:
    if status not in ("HOLDING", "RENTED", "SOLD"):
        raise ValueError(f"非法状态: {status}")
    dao.update_position(position_id, {"status": status})
    dao.commit()
