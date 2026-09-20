"""风控：风险等级（L/M/H）+ 建议仓位矩阵 + 敞口与回撤监控。

两段式思想借鉴 CSGOTrading：规则定界（本模块）→ 执行兜底（服务层 clamp）。
"""
from __future__ import annotations

from dataclasses import dataclass

from quant.factors.indicators import max_drawdown


def _band_score(value: float | None, low: float, high: float) -> float:
    """0（低风险）/ 0.5（中）/ 1（高风险）；缺失按 0.5 中性。"""
    if value is None:
        return 0.5
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    return 0.5


def assess_risk_level(volatility: float | None, spread: float | None,
                      price: float | None, risk_bands: dict) -> str:
    """三维风险打分 -> L/M/H。高价、低流动（大spread）、高波动 -> 高风险。"""
    vb = risk_bands.get("volatility", {"low": 0.03, "high": 0.10})
    sb = risk_bands.get("spread", {"low": 0.02, "high": 0.08})
    pb = risk_bands.get("price", {"low": 50.0, "high": 2000.0})
    total = (_band_score(volatility, vb["low"], vb["high"])
             + _band_score(spread, sb["low"], sb["high"])
             + _band_score(price, pb["low"], pb["high"]))
    if total >= 2.0:
        return "H"
    if total >= 1.0:
        return "M"
    return "L"


def suggested_position(risk_level: str, confidence: float,
                       position_matrix: dict) -> float:
    band = position_matrix.get(risk_level, position_matrix.get("M", {}))
    if confidence >= 0.7:
        return band.get("high_conf", 0.0)
    if confidence >= 0.5:
        return band.get("mid_conf", 0.0)
    return band.get("low_conf", 0.0)


@dataclass
class ExposureWarning:
    item_uuid: str
    weight: float
    limit: float


def check_exposure(position_values: list[dict], max_per_item: float = 0.2) -> list[ExposureWarning]:
    """单标的敞口超限检查。position_values: [{item_uuid, value}]"""
    total = sum(p.get("value") or 0 for p in position_values)
    warnings: list[ExposureWarning] = []
    if total <= 0:
        return warnings
    by_item: dict[str, float] = {}
    for p in position_values:
        by_item[p["item_uuid"]] = by_item.get(p["item_uuid"], 0) + (p.get("value") or 0)
    for item_uuid, value in by_item.items():
        weight = value / total
        if weight > max_per_item:
            warnings.append(ExposureWarning(item_uuid, round(weight, 4), max_per_item))
    return warnings


def drawdown_breach(nav_values: list[float], threshold: float = 0.15) -> tuple[bool, float | None]:
    """当前回撤是否超过阈值。nav_values 为净值时间序列（升序）。"""
    mdd = max_drawdown(nav_values)
    if mdd is None:
        return False, None
    return mdd >= threshold, mdd
