"""疑似出货/抛压检测：DistributionScore 0-100（概率性推断）。

典型形态（指示文档 §十二）：价格刚经历快速上涨 + 在售暴增
+ 求购减少 + 买价下移 → "疑似集中出货/获利兑现"。
"""
from __future__ import annotations

from market_anomaly.accumulation import _neg_z, _pos_z
from market_anomaly.detectors import ItemMetrics

WEIGHTS = {
    "sell_count_surge": 0.25,
    "buy_count_falling": 0.20,
    "buy_price_dropping": 0.15,
    "volume_surge": 0.15,
    "price_reversal": 0.15,
    "cross_platform_weak": 0.10,
}


def _buy_price_dropping(deltas: dict) -> float | None:
    d = deltas.get("buy_price")
    if not d or d.pct_change is None:
        return None
    if d.significant and d.pct_change < 0:
        return 100.0
    if d.pct_change < 0:
        return 60.0
    return 0.0


def _price_reversal(series: list[dict]) -> float | None:
    """冲高回落：窗口内曾高于现价 5%+，且现价从高点回落 3%+。"""
    prices = [r.get("sell_price") for r in series if r.get("sell_price")]
    if len(prices) < 3:
        return None
    peak = max(prices[:-1])
    last = prices[-1]
    if peak <= 0:
        return None
    run_up = peak / prices[0] - 1.0
    pullback = (peak - last) / peak
    if run_up >= 0.05 and pullback >= 0.03:
        return 100.0
    if pullback >= 0.03:
        return 50.0
    return 0.0


def distribution_score(m: ItemMetrics, series: list[dict]) -> tuple[float, dict]:
    d = m.deltas
    parts = {
        "sell_count_surge": _pos_z((d.get("sell_count") or {}).zscore
                                   if d.get("sell_count") else None),
        "buy_count_falling": _neg_z((d.get("buy_count") or {}).zscore
                                    if d.get("buy_count") else None),
        "buy_price_dropping": _buy_price_dropping(d),
        "volume_surge": _pos_z((d.get("volume") or {}).zscore
                               if d.get("volume") else None),
        "price_reversal": _price_reversal(series),
        "cross_platform_weak": (
            (1 - m.cross_platform_sync) * 100
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
