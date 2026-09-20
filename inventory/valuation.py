"""估值与净值：多平台现价估值 + 盈亏 KPI + 每日净值快照。

口径（修正自 csgo_investment）：
- 现价优先级：BUFF -> UUYP -> STEAM（sell_price），缺失记 None 不计 0；
- 浮盈 = Σ(value - cost_basis)（仅成本已知的持仓计入收益率分母）；
- 总收益率（资金流调整简化版）= (total_value + realized_pnl - net_deposit) / net_deposit。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from database.dao import Dao
from utils.timeutils import iso_now

PRICE_PLATFORM_PRIORITY = ("BUFF", "UUYP", "STEAM")


@dataclass
class PositionValue:
    position_id: str
    item_uuid: str
    market_hash_name: str = ""
    name_zh: str | None = None
    quantity: int = 1
    status: str = "HOLDING"
    cost_basis: float | None = None
    current_price: float | None = None
    price_platform: str | None = None
    current_value: float | None = None
    unrealized_pnl: float | None = None
    return_rate: float | None = None
    data_quality: str = "A"
    as_of: str | None = None


@dataclass
class PortfolioValuation:
    positions: list[PositionValue] = field(default_factory=list)
    total_value: float = 0.0
    total_cost_known: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    net_deposit: float = 0.0
    return_rate: float | None = None
    value_by_platform: dict = field(default_factory=dict)
    as_of: str = ""


def pick_current_price(dao: Dao, item_uuid: str,
                       priority: tuple = PRICE_PLATFORM_PRIORITY) -> tuple[float | None, str | None, dict | None]:
    """按平台优先级取现价。返回 (price, platform, snapshot_row)。"""
    snapshots = {s["platform"]: s for s in dao.get_snapshots_for_item(item_uuid)}
    for platform in priority:
        snap = snapshots.get(platform)
        if snap and snap.get("sell_price"):
            return snap["sell_price"], platform, snap
    return None, None, None


def compute_position_value(position: dict, current_price: float | None,
                           platform: str | None, quality: str,
                           as_of: str | None) -> PositionValue:
    """单持仓估值（纯函数，便于测试）。"""
    qty = position.get("quantity", 1)
    cost = position.get("cost_basis")
    value = round(current_price * qty, 2) if current_price is not None else None
    pnl = round(value - cost, 2) if (value is not None and cost is not None) else None
    rate = round(pnl / cost, 4) if (pnl is not None and cost) else None
    return PositionValue(
        position_id=position["position_id"],
        item_uuid=position["item_uuid"],
        quantity=qty,
        status=position.get("status", "HOLDING"),
        cost_basis=cost,
        current_price=current_price,
        price_platform=platform,
        current_value=value,
        unrealized_pnl=pnl,
        return_rate=rate,
        data_quality=quality,
        as_of=as_of,
    )


def aggregate(positions: list[PositionValue], realized_pnl: float,
              net_deposit: float | None = None) -> PortfolioValuation:
    """组合汇总（纯函数）。

    net_deposit 口径（优先级）：显式传入（资金流水/买入总投入）>
    降级为当前持仓成本。注意：当前持仓成本会在卖出后减小，
    不能作为真实净注资，仅作无任何流水数据时的最后退路。
    """
    total_value = sum(p.current_value or 0 for p in positions)
    total_cost = sum(p.cost_basis or 0 for p in positions)
    unrealized = sum(p.unrealized_pnl or 0 for p in positions)
    by_platform: dict[str, float] = {}
    for p in positions:
        if p.current_value and p.price_platform:
            by_platform[p.price_platform] = round(
                by_platform.get(p.price_platform, 0) + p.current_value, 2)
    if net_deposit is None:
        net_deposit = total_cost
    return_rate = (round((total_value + realized_pnl - net_deposit) / net_deposit, 4)
                   if net_deposit > 0 else None)
    return PortfolioValuation(
        positions=positions,
        total_value=round(total_value, 2),
        total_cost_known=round(total_cost, 2),
        unrealized_pnl=round(unrealized, 2),
        realized_pnl=round(realized_pnl, 2),
        net_deposit=round(net_deposit, 2),
        return_rate=return_rate,
        value_by_platform=by_platform,
        as_of=iso_now(),
    )


def compute_net_deposit(dao: Dao) -> float:
    """净注资：显式资金流水（cash_flow）优先；无流水时用
    历史全部买入投入（卖出不冲减，避免卖出后错误归零）。"""
    explicit = dao.get_net_deposit()
    if explicit is not None:
        return explicit
    return dao.get_total_buy_cost()


def valuate_portfolio(dao: Dao) -> PortfolioValuation:
    pvs: list[PositionValue] = []
    for pos in dao.get_positions(status=None):
        if pos["status"] == "SOLD":
            continue
        item = dao.get_item_by_uuid(pos["item_uuid"]) or {}
        price, platform, snap = pick_current_price(dao, pos["item_uuid"])
        pv = compute_position_value(
            pos, price, platform,
            (snap or {}).get("data_quality", "D" if price is None else "B"),
            (snap or {}).get("ts"))
        pv.market_hash_name = item.get("market_hash_name", "")
        pv.name_zh = item.get("name_zh")
        pvs.append(pv)
    return aggregate(pvs, dao.get_realized_pnl(),
                     net_deposit=compute_net_deposit(dao))


def snapshot_nav(dao: Dao) -> dict:
    """每日净值快照（portfolio_snapshot）。"""
    v = valuate_portfolio(dao)
    import json as _json
    row = {
        "ts": iso_now()[:10] + "T00:00:00+00:00",  # 每日一条（UTC 日期）
        "total_value": v.total_value,
        "total_cost": v.total_cost_known,
        "cash": 0,
        "unrealized_pnl": v.unrealized_pnl,
        "realized_pnl": v.realized_pnl,
        "return_rate": v.return_rate,
        "net_deposit": v.net_deposit,
        "value_by_platform": _json.dumps(v.value_by_platform, ensure_ascii=False),
    }
    dao.upsert_nav_snapshot(row)
    dao.commit()
    return row
