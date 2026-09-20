"""MarketScore 大盘情绪模型（0-100，五档 regime）。

六因子（初始权重见 config.json，待回测校准）：
  trend 25% / breadth 20% / liquidity 20% / supply_demand 15% /
  cross_platform 10% / event_sentiment 10%
全部为确定性数值计算（LLM 不参与）；缺失因子降权并在结果中登记。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from quant.factors.indicators import ema, momentum


def _tanh_score(x: float, scale: float = 1.0) -> float:
    """把无界输入压缩到 0-100（50 为中性）。"""
    return round(50 + 50 * math.tanh(x * scale), 2)


# ---------------- 六因子 ----------------

def trend_score(index_closes: list[float]) -> float | None:
    """大盘趋势：EMA20/EMA60 排列 + 20日动量。数据不足返回 None。"""
    if len(index_closes) < 26:
        return None
    e_short = ema(index_closes, min(20, len(index_closes)))[-1]
    e_long = ema(index_closes, min(60, len(index_closes)))[-1]
    if e_short is None or e_long is None or e_long == 0:
        return None
    diff_pct = (e_short - e_long) / e_long
    mom_list = momentum(index_closes, 20)
    mom = mom_list[-1] if mom_list else None
    raw = 0.6 * math.tanh(diff_pct * 25) + 0.4 * math.tanh((mom or 0) * 8)
    return round(50 + 50 * raw, 2)


def breadth_score_fn(up: int | None, down: int | None) -> float | None:
    if up is None or down is None or (up + down) == 0:
        return None
    return round(100.0 * up / (up + down), 2)


def liquidity_score_fn(volume_now: float | None,
                       volume_ma20: float | None) -> float | None:
    """成交活跃度：当前量 / 20日均量 的对数比。"""
    if not volume_now or not volume_ma20 or volume_ma20 <= 0:
        return None
    return _tanh_score(math.log(volume_now / volume_ma20), scale=1.0)


def supply_demand_score_fn(bid_total: float | None, sell_total: float | None,
                           spread_median: float | None = None) -> float | None:
    """求购/在售力量比；spread 中位数做惩罚项。"""
    if not bid_total or not sell_total or sell_total <= 0:
        return None
    base = math.tanh(math.log(bid_total / sell_total))
    if spread_median is not None:
        base -= min(spread_median / 0.10, 1.0) * 0.3  # 价差越大流动性越差
    return round(50 + 50 * base, 2)


def cross_platform_score_fn(price_cv: float | None) -> float | None:
    """跨平台一致性：价格变异系数 CV 越小分越高（>10% 得 0）。"""
    if price_cv is None:
        return None
    return round(100 * max(0.0, 1.0 - price_cv / 0.10), 2)


def event_sentiment_score_fn(greedy: float | None) -> float | None:
    """事件/情绪：V1 用 CSQAQ greedy 指标代理；V5 接 Event Agent。"""
    if greedy is None:
        return None
    return round(min(max(greedy, 0.0), 100.0), 2)


# ---------------- 合成 ----------------

@dataclass
class FactorContribution:
    key: str
    score: float | None
    weight: float
    contribution: float
    available: bool


@dataclass
class MarketScoreResult:
    score: float | None
    regime: str | None
    regime_zh: str | None
    factors: list[FactorContribution] = field(default_factory=list)
    missing_factors: list[str] = field(default_factory=list)
    model_version: str = ""


def weighted_score(factor_scores: dict[str, float | None],
                   weights: dict[str, float]) -> tuple[float | None, list[FactorContribution]]:
    """加权合成：缺失因子降权（剩余权重归一化）。全部缺失返回 None。"""
    contribs: list[FactorContribution] = []
    available_weight = 0.0
    acc = 0.0
    for key, weight in weights.items():
        score = factor_scores.get(key)
        if score is None:
            contribs.append(FactorContribution(key, None, weight, 0.0, False))
            continue
        available_weight += weight
        acc += score * weight
        contribs.append(FactorContribution(key, score, weight, 0.0, True))
    if available_weight <= 0:
        return None, contribs
    final = acc / available_weight
    for c in contribs:
        if c.available:
            c.contribution = round(c.score * c.weight / available_weight, 2)
            c.weight = round(c.weight / available_weight, 4)
    return round(final, 2), contribs


def regime_from_score(score: float,
                      bands: list) -> tuple[str, str]:
    for low, high, regime, regime_zh in bands:
        if low <= score < high:
            return regime, regime_zh
    return "neutral", "震荡"


def compute_market_score(factor_scores: dict[str, float | None],
                         weights: dict[str, float],
                         bands: list,
                         model_version: str = "") -> MarketScoreResult:
    score, contribs = weighted_score(factor_scores, weights)
    missing = [c.key for c in contribs if not c.available]
    if score is None:
        return MarketScoreResult(None, None, None, contribs, missing, model_version)
    regime, regime_zh = regime_from_score(score, bands)
    return MarketScoreResult(score, regime, regime_zh, contribs, missing, model_version)
