"""信号引擎：MarketScore + ItemScore -> BUY/HOLD/SELL。

规则（全部可配置，阈值待回测校准）：
  FinalScore = blend.item * ItemScore + blend.market * MarketScore（缺失降权）
  FinalScore >= thresholds.buy  -> BUY 候选
  FinalScore <  thresholds.sell -> SELL/回避候选
  其余 -> HOLD
confidence = agreement*0.45 + data_quality*0.30 + sample*0.25（权重可配）；
关键因子缺失或置信度过低时信号降级（BUY 降 HOLD）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from market.market_score import FactorContribution, MarketScoreResult
from quant.factors.item_score import ItemScoreResult

_QUALITY_MAP = {"A": 1.0, "B": 0.8, "C": 0.5, "D": 0.2}


@dataclass
class SignalResult:
    signal: str                    # BUY / HOLD / SELL
    final_score: float | None
    item_score: float | None
    market_score: float | None
    confidence: float
    reason: dict = field(default_factory=dict)
    model_version: str = ""


def blend_scores(item_score: float | None, market_score: float | None,
                 blend: dict) -> float | None:
    wi, wm = blend.get("item", 0.6), blend.get("market", 0.4)
    if item_score is not None and market_score is not None:
        return round(item_score * wi + market_score * wm, 2)
    if item_score is not None:
        return item_score
    return market_score


def decide(final_score: float, thresholds: dict) -> str:
    if final_score >= thresholds.get("buy", 75.0):
        return "BUY"
    if final_score < thresholds.get("sell", 45.0):
        return "SELL"
    return "HOLD"


def _factor_agreement(item_factors: list[FactorContribution],
                      final_score: float) -> float:
    """因子方向一致性：与最终分同向（>=50 视为多）的可用因子占比。"""
    usable = [f for f in item_factors if f.available and f.score is not None]
    if not usable:
        return 0.0
    bullish_final = final_score >= 50
    same = sum(1 for f in usable if (f.score >= 50) == bullish_final)
    return same / len(usable)


def worst_quality(data_qualities: list[str]) -> str:
    """最差质量等级（A最优 D最差，字典序 max 即最差）。缺失按 D 处理。"""
    valid = [q for q in data_qualities if q in _QUALITY_MAP]
    return max(valid) if valid else "D"


def compute_confidence(item_factors: list[FactorContribution],
                       final_score: float,
                       data_qualities: list[str],
                       sample_days: int,
                       conf_weights: dict,
                       critical_missing: int) -> float:
    agreement = _factor_agreement(item_factors, final_score)
    # 数据质量必须取最差一档（["A","D"] 按 D 计），防止单一好源掩盖坏源
    dq = _QUALITY_MAP[worst_quality(data_qualities)]
    sample = min(sample_days / 180.0, 1.0)
    conf = (agreement * conf_weights.get("agreement", 0.45)
            + dq * conf_weights.get("data_quality", 0.30)
            + sample * conf_weights.get("sample", 0.25))
    if critical_missing > 0:
        conf = min(conf, 0.4)   # 关键因子缺失封顶
    return round(min(max(conf, 0.0), 1.0), 3)


def build_reason(item: ItemScoreResult, market: MarketScoreResult | None,
                 final_score: float, blend: dict, thresholds: dict) -> dict:
    return {
        "final_score": final_score,
        "blend": blend,
        "thresholds": thresholds,
        "item_factors": [
            {"key": c.key, "score": c.score, "weight": c.weight,
             "contribution": c.contribution, "available": c.available}
            for c in item.factors
        ],
        "market_factors": [
            {"key": c.key, "score": c.score, "weight": c.weight,
             "contribution": c.contribution, "available": c.available}
            for c in (market.factors if market else [])
        ],
        "missing_item_factors": item.missing_factors,
        "missing_market_factors": (market.missing_factors if market else []),
    }


def generate_signal(item: ItemScoreResult,
                    market: MarketScoreResult | None,
                    strategy_cfg: dict,
                    data_qualities: list[str],
                    sample_days: int) -> SignalResult:
    blend = strategy_cfg["blend"]
    thresholds = strategy_cfg["signal_thresholds"]
    final = blend_scores(item.score, market.score if market else None, blend)
    if final is None:
        return SignalResult("HOLD", None, item.score,
                            market.score if market else None, 0.0,
                            {"error": "insufficient_data"}, strategy_cfg["model_version"])
    signal = decide(final, thresholds)
    critical_missing = len([k for k in ("trend", "supply_demand")
                            if k in item.missing_factors])
    confidence = compute_confidence(
        item.factors, final, data_qualities, sample_days,
        strategy_cfg["confidence_weights"], critical_missing)
    # 硬门控：D 级数据或关键因子缺失时禁止 BUY（不依赖置信度浮点比较）
    quality_gate_blocked = (worst_quality(data_qualities) == "D"
                            or critical_missing > 0)
    if signal == "BUY" and quality_gate_blocked:
        signal = "HOLD"
    # 置信度过低：BUY 降级 HOLD（防拍脑袋）
    if signal == "BUY" and confidence < thresholds.get("min_confidence", 0.4):
        signal = "HOLD"
    reason = build_reason(item, market, final, blend, thresholds)
    reason["confidence_breakdown"] = {"data_qualities": data_qualities,
                                      "worst_quality": worst_quality(data_qualities),
                                      "quality_gate_blocked": quality_gate_blocked,
                                      "sample_days": sample_days}
    return SignalResult(signal, final, item.score,
                        market.score if market else None,
                        confidence, reason, strategy_cfg["model_version"])
