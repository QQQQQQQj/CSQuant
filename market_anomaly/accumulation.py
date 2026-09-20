"""疑似建仓检测：AccumulationScore 0-100（概率性推断，非事实断言）。

典型形态（指示文档 §十一）：BuyCount↑↑ + BuyPrice↑ + SellCount↓
+ Volume↑ + Price 尚未大涨 → "疑似资金提前建仓/吸筹"。
"""
from __future__ import annotations

from market_anomaly.detectors import ItemMetrics

WEIGHTS = {
    "buy_count_surge": 0.25,
    "buy_price_rising": 0.20,
    "sell_count_falling": 0.20,
    "volume_surge": 0.15,
    "price_mild": 0.10,
    "cross_platform": 0.10,
}


def _pos_z(z: float | None, cap: float = 3.0) -> float | None:
    """正向 Z 映射 0-100（z<=0 得 0）。"""
    if z is None:
        return None
    return round(min(max(z, 0.0) / cap, 1.0) * 100, 2)


def _neg_z(z: float | None, cap: float = 3.0) -> float | None:
    """负向 Z 映射 0-100（z>=0 得 0）。"""
    if z is None:
        return None
    return round(min(max(-z, 0.0) / cap, 1.0) * 100, 2)


def _rising(deltas: dict, key: str) -> float | None:
    """价格持续抬升：显著正变化 -> 100；小幅为正 -> 60；否则 0。"""
    d = deltas.get(key)
    if not d or d.pct_change is None:
        return None
    if d.significant and d.pct_change > 0:
        return 100.0
    if d.pct_change > 0:
        return 60.0
    return 0.0


def _price_mild(deltas: dict) -> float | None:
    """价格温和（未大涨）：|pct|<3% -> 100；<8% -> 50；否则 0。"""
    d = deltas.get("sell_price")
    if not d or d.pct_change is None:
        return None
    ap = abs(d.pct_change)
    if ap < 3.0:
        return 100.0
    if ap < 8.0:
        return 50.0
    return 0.0


def accumulation_score(m: ItemMetrics) -> tuple[float, dict]:
    """返回 (0-100 分, 子项明细)。缺失因子降权。"""
    d = m.deltas
    parts = {
        "buy_count_surge": _pos_z((d.get("buy_count") or {}).zscore
                                  if d.get("buy_count") else None),
        "buy_price_rising": _rising(d, "buy_price"),
        "sell_count_falling": _neg_z((d.get("sell_count") or {}).zscore
                                     if d.get("sell_count") else None),
        "volume_surge": _pos_z((d.get("volume") or {}).zscore
                               if d.get("volume") else None),
        "price_mild": _price_mild(d),
        "cross_platform": (m.cross_platform_sync * 100
                           if m.cross_platform_sync is not None else None),
    }
    acc = avail = 0.0
    for key, weight in WEIGHTS.items():
        score = parts.get(key)
        if score is None:
            continue
        acc += score * weight
        avail += weight
    final = round(acc / avail, 2) if avail > 0 else 0.0
    return final, parts
