"""服务层：库存与资产（页面/CLI 唯一入口，页面禁止直查库）。"""
from __future__ import annotations

from dataclasses import asdict

from database.dao import Dao
from inventory import portfolio as portfolio_mod
from inventory.steam_inventory import sync_steam_inventory, SyncReport
from inventory.valuation import (
    PortfolioValuation, snapshot_nav, valuate_portfolio,
)


class InventoryService:
    def __init__(self, dao: Dao, steam_provider=None):
        self.dao = dao
        self.steam = steam_provider

    def sync_steam_inventory(self) -> SyncReport:
        if self.steam is None:
            raise RuntimeError("Steam Provider 未配置（缺 STEAM_ID_64）")
        return sync_steam_inventory(self.dao, self.steam)

    def add_manual_position(self, market_hash_name: str, quantity: int = 1,
                            buy_price: float | None = None,
                            buy_time: str | None = None,
                            buy_platform: str | None = None,
                            notes: str | None = None) -> str:
        return portfolio_mod.add_manual_position(
            self.dao, market_hash_name, quantity, buy_price,
            buy_time, buy_platform, notes)

    def record_sell(self, position_id: str, price: float, fee: float = 0.0,
                    platform: str | None = None, traded_at: str | None = None):
        result = portfolio_mod.record_sell(self.dao, position_id, price, fee,
                                           platform, traded_at)
        self.dao.log_event("INFO", "portfolio",
                           f"卖出登记 {position_id}: pnl={result.realized_pnl}")
        self.dao.commit()
        return result

    def set_buy_cost(self, position_id: str, buy_price: float,
                     buy_time: str | None = None,
                     buy_platform: str | None = None) -> None:
        portfolio_mod.set_buy_cost(self.dao, position_id, buy_price,
                                   buy_time, buy_platform)

    def record_deposit(self, amount: float, note: str | None = None) -> None:
        """注资登记（净注资与收益率口径的事实来源）。"""
        self.dao.insert_cash_flow("DEPOSIT", amount, note)
        self.dao.commit()

    def record_withdrawal(self, amount: float, note: str | None = None) -> None:
        """提现登记。"""
        self.dao.insert_cash_flow("WITHDRAWAL", amount, note)
        self.dao.commit()

    def get_portfolio(self) -> dict:
        v: PortfolioValuation = valuate_portfolio(self.dao)
        return {
            "positions": [asdict(p) for p in v.positions],
            "total_value": v.total_value,
            "total_cost": v.total_cost_known,
            "unrealized_pnl": v.unrealized_pnl,
            "realized_pnl": v.realized_pnl,
            "net_deposit": v.net_deposit,
            "return_rate": v.return_rate,
            "value_by_platform": v.value_by_platform,
            "as_of": v.as_of,
        }

    def get_valuation(self) -> dict:
        p = self.get_portfolio()
        return {k: p[k] for k in ("total_value", "total_cost", "unrealized_pnl",
                                  "realized_pnl", "return_rate", "net_deposit",
                                  "value_by_platform", "as_of")}

    def snapshot_nav(self) -> dict:
        return snapshot_nav(self.dao)

    def get_nav_history(self, days: int = 90) -> list[dict]:
        return self.dao.get_nav_history(limit=days)
