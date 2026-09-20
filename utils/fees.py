"""手续费模型。

calculate_after_fee 移植自 SteamTradingSiteTracker（社区标准 Steam 费用近似算法）：
Steam 市场买家支付价 = 卖家所得 + Steam 费(5%) + 游戏费(CS2 10%)，
每部分费用以分为单位向下取整且有最低 0.01。此处按"元"为单位同比例实现。
其他平台费率走 config.fee_table（部分为待验证假设）。
"""
from __future__ import annotations

STEAM_FEE_RATE = 0.05
GAME_FEE_RATE = 0.10
MIN_FEE = 0.01


def calculate_after_fee(paid_price: float) -> float:
    """Steam 市场：给定买家支付价，返回卖家实际所得（扣费后净价）。"""
    if paid_price <= 0:
        return 0.0
    steam_fee = max(paid_price * STEAM_FEE_RATE, MIN_FEE)
    game_fee = max(paid_price * GAME_FEE_RATE, MIN_FEE)
    return round(paid_price - steam_fee - game_fee, 2)


def apply_fee(price: float, platform: str, fee_table: dict) -> float:
    """按平台费率表计算卖出净所得（不含滑点）。STEAM 用精算逆运算。"""
    if platform.upper() == "STEAM":
        return calculate_after_fee(price)
    rate = (fee_table.get(platform.upper()) or {}).get("rate", 0.0)
    return round(price * (1 - rate), 4)


def buy_cost(price: float, platform: str, fee_table: dict) -> float:
    """买入总成本（多数三方平台买家免交易费，Steam 买家价为含税价）。"""
    return round(price, 4)
