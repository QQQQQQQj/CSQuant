"""ItemScore 单品评分（0-100）。

八因子（初始权重见 config.json，待回测校准）：
  trend 20% / relative_strength 15% / supply_demand 20% / volume 15% /
  liquidity 10% / cross_platform 10% / valuation 5% / event 5%
输入为 ItemContext（调用方从 DAO/Provider 组装）；全部为确定性计算。
缺失因子降权；event 为 V1 中性占位（V5 接 Event Agent）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from market.market_score import FactorContribution, weighted_score
from quant.factors.indicators import (
    ema, macd, momentum, percentile_rank, rsi, volatility, daily_returns, zscore,
)

NEUTRAL = 50.0


@dataclass
class ItemContext:
    """单品因子计算的输入数据包（t 时刻及之前，严禁未来数据）。"""
    market_hash_name: str
    closes: list[float] = field(default_factory=list)       # 日线收盘（升序）
    volumes: list[float] = field(default_factory=list)      # 日成交量（可空）
    sell_price: float | None = None
    buy_price: float | None = None                          # 最高求购
    sell_count: int | None = None
    buy_count: int | None = None
    volume_24h: int | None = None
    platform_prices: list[float] = field(default_factory=list)  # 各平台在售价格
    market_closes: list[float] = field(default_factory=list)    # 大盘指数（对齐）
    history_prices_180d: list[float] = field(default_factory=list)  # 估值分位用
    event_score: float | None = None                        # V1 占位 None -> 50


# ---------------- 八因子 ----------------

def f_trend(ctx: ItemContext) -> float | None:
    closes = ctx.closes
    if len(closes) < 14:
        return None
    score = NEUTRAL
    parts = 0
    # EMA 排列
    if len(closes) >= 26:
        e20 = ema(closes, 20)[-1]
        e60 = ema(closes, min(60, len(closes)))[-1]
        if e20 and e60 and e60 > 0:
            score += 30 * math.tanh((e20 - e60) / e60 * 25)
            parts += 1
    # RSI
    r = rsi(closes, 14)[-1]
    if r is not None:
        score += (r - 50) * 0.4
        parts += 1
    # MACD hist
    _, _, hist = macd(closes)
    h = hist[-1] if hist else None
    if h is not None and closes[-1] > 0:
        score += 20 * math.tanh(h / closes[-1] * 100)
        parts += 1
    return round(min(max(score, 0.0), 100.0), 2) if parts else None


def f_relative_strength(ctx: ItemContext) -> float | None:
    if len(ctx.closes) < 21 or len(ctx.market_closes) < 21:
        return None
    item_ret = momentum(ctx.closes, 20)[-1]
    market_ret = momentum(ctx.market_closes, 20)[-1]
    if item_ret is None or market_ret is None:
        return None
    return round(50 + 50 * math.tanh((item_ret - market_ret) * 8), 2)


def f_supply_demand(ctx: ItemContext) -> float | None:
    score = NEUTRAL
    parts = 0
    if ctx.sell_price and ctx.buy_price and ctx.sell_price > 0:
        # 求购价/在售价：越接近 1 买方越强
        ratio = ctx.buy_price / ctx.sell_price
        score += 40 * math.tanh((ratio - 0.85) * 10)
        parts += 1
    if ctx.buy_count is not None and ctx.sell_count:
        score += 30 * math.tanh(math.log((ctx.buy_count + 1) / (ctx.sell_count + 1)))
        parts += 1
    return round(min(max(score, 0.0), 100.0), 2) if parts else None


def f_volume(ctx: ItemContext) -> float | None:
    if len(ctx.volumes) >= 20:
        z = zscore(ctx.volumes, 20)[-1]
        if z is not None:
            return round(50 + 50 * math.tanh(z / 2), 2)
    if ctx.volume_24h is not None:
        # 无历史时退化：有成交即中性偏上（粗粒度，置信度侧会惩罚）
        return 60.0 if ctx.volume_24h > 0 else 40.0
    return None


def f_liquidity(ctx: ItemContext) -> float | None:
    score = NEUTRAL
    parts = 0
    if ctx.sell_price and ctx.buy_price and ctx.sell_price > 0:
        spread = (ctx.sell_price - ctx.buy_price) / ctx.sell_price
        score += 30 * (1 - min(spread / 0.10, 1.0) * 2 - 1)  # spread 0->+30, >=10%->-30
        parts += 1
    if ctx.sell_count is not None:
        # 在售深度：过少（易被扫货操纵）与过多（抛压）都扣分
        depth = min(ctx.sell_count / 200.0, 1.0)
        score += 20 * (depth - 0.5)
        parts += 1
    return round(min(max(score, 0.0), 100.0), 2) if parts else None


def f_cross_platform(ctx: ItemContext) -> float | None:
    prices = [p for p in ctx.platform_prices if p and p > 0]
    if len(prices) < 2:
        return None
    mean = sum(prices) / len(prices)
    var = sum((p - mean) ** 2 for p in prices) / len(prices)
    cv = math.sqrt(var) / mean
    return round(100 * max(0.0, 1.0 - cv / 0.10), 2)


def f_valuation(ctx: ItemContext) -> float | None:
    """估值位置：当前价在 180 日历史分位。V1 假说：低位高分（待回测验证方向）。"""
    if not ctx.history_prices_180d or not ctx.sell_price:
        return None
    pct = percentile_rank(ctx.history_prices_180d, ctx.sell_price)
    if pct is None:
        return None
    return round(100 * (1 - pct), 2)


def f_event(ctx: ItemContext) -> float:
    return ctx.event_score if ctx.event_score is not None else NEUTRAL


FACTOR_FUNCS = {
    "trend": f_trend,
    "relative_strength": f_relative_strength,
    "supply_demand": f_supply_demand,
    "volume": f_volume,
    "liquidity": f_liquidity,
    "cross_platform": f_cross_platform,
    "valuation": f_valuation,
    "event": f_event,
}


@dataclass
class ItemScoreResult:
    score: float | None
    factors: list[FactorContribution]
    missing_factors: list[str]
    raw: dict = field(default_factory=dict)


def compute_item_score(ctx: ItemContext, weights: dict[str, float]) -> ItemScoreResult:
    factor_scores = {key: func(ctx) for key, func in FACTOR_FUNCS.items()}
    score, contribs = weighted_score(factor_scores, weights)
    missing = [c.key for c in contribs if not c.available]
    raw = {k: v for k, v in factor_scores.items()}
    return ItemScoreResult(score, contribs, missing, raw)


def item_volatility(ctx: ItemContext) -> float | None:
    return volatility(daily_returns(ctx.closes)) if len(ctx.closes) >= 10 else None
