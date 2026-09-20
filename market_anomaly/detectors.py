"""异动检测核心：变化量 / Z-Score / AnomalyScore / OrderFlowScore /
跨平台一致度 / LiquidityScore / DataQualityScore / RelativeStrength。

正确性规则：
- 跨平台一致度只参与 Confidence，不参与 AnomalyScore；
- 小基数不显著变化不计 Z-Score 异常分（分指标阈值）；
- 指标 NULL 恢复时该指标本轮不计分；
- Z-Score 死区：|z|<2 属正常波动，异常分为 0（"相对自身历史是否异常"）；
- 时间依赖统一经注入时钟（now 参数），保证可测可复现。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from market_anomaly.baseline import (
    series_stats, significant_change, zscore_of,
)

# 异常分权重：仅数值异常参与（cross_platform 已移出，只进 confidence）
ANOMALY_WEIGHTS = {
    "buy_count_z": 0.30, "sell_count_z": 0.25, "volume_z": 0.25,
    "price_z": 0.15, "spread_z": 0.05,
}
ORDER_FLOW_WEIGHTS = {
    "demand_supply": 0.40, "buy_count_dir": 0.20, "sell_count_dir": 0.20,
    "price_dir": 0.10, "spread_dir": 0.10,
}

# 分指标显著性阈值（绝对变化下限 / 小基数下限）
METRIC_THRESHOLDS = {
    "buy_count": {"min_abs": 10.0, "min_base": 20.0},
    "sell_count": {"min_abs": 10.0, "min_base": 20.0},
    "buy_price": {"min_abs": 1.0, "min_base": 5.0},
    "sell_price": {"min_abs": 1.0, "min_base": 5.0},
    "volume": {"min_abs": 10.0, "min_base": 20.0},
}

Z_DEAD_ZONE = 2.0     # |z| 低于此值视为正常波动
Z_FULL_SCALE = 4.0    # |z| 达到此值异常分满分


@dataclass
class MetricDelta:
    significant: bool = False
    abs_change: float | None = None
    pct_change: float | None = None     # %
    zscore: float | None = None
    latest: float | None = None
    null_recovered: bool = False        # 前一样本为 NULL（跳变不可信）


@dataclass
class ItemMetrics:
    """单件饰品一轮检测的全部特征。"""
    item_uuid: str
    market_hash_name: str
    name_zh: str | None
    n_samples: int
    latest: dict = field(default_factory=dict)     # 最新快照行
    deltas: dict = field(default_factory=dict)     # metric -> MetricDelta
    cross_platform_sync: float | None = None
    liquidity_score: float = 0.0
    data_quality_score: float = 0.0
    anomaly_score: float = 0.0
    order_flow_score: float = 0.0
    relative_strength: float | None = None
    null_recovery: bool = False


def compute_deltas(series: list[dict]) -> dict[str, MetricDelta]:
    """对五类指标计算变化量与 Z-Score（基线=除最新点外的全部历史）。

    每个指标独立检测 NULL 恢复：前一样本为 NULL 时本轮不计 Z-Score。
    """
    out: dict[str, MetricDelta] = {}
    if len(series) < 2:
        return out
    fields = {"buy_count": "buy_count", "sell_count": "sell_count",
              "buy_price": "buy_price", "sell_price": "sell_price",
              "volume": "volume_24h"}
    for name, col in fields.items():
        values = [row.get(col) for row in series]
        latest = values[-1]
        recovered = latest is not None and values[-2] is None
        baseline = series_stats(values[:-1])
        old = baseline.median
        th = METRIC_THRESHOLDS.get(name, {"min_abs": 10.0, "min_base": 20.0})
        sig, abs_c, pct_c = significant_change(
            old, latest, min_abs=th["min_abs"], min_base=th["min_base"])
        z = None if recovered else zscore_of(latest, baseline)
        out[name] = MetricDelta(sig and not recovered, abs_c, pct_c, z,
                                latest, recovered)
    return out


def z_to_score(z: float | None, dead_zone: float = Z_DEAD_ZONE,
               full_scale: float = Z_FULL_SCALE) -> float | None:
    """|Z| 映射 0-100：死区内（正常波动）为 0，|z|>=full_scale 满分。"""
    if z is None:
        return None
    az = abs(z)
    if az < dead_zone:
        return 0.0
    return round(min((az - dead_zone) / (full_scale - dead_zone), 1.0) * 100, 2)


def cross_platform_sync(platform_prices: list[float | None]) -> float | None:
    """多平台一致度 0-1（仅供 Confidence 使用）。<2 个有效价返回 None。"""
    prices = [p for p in platform_prices if p and p > 0]
    if len(prices) < 2:
        return None
    mean = sum(prices) / len(prices)
    var = sum((p - mean) ** 2 for p in prices) / len(prices)
    cv = math.sqrt(var) / mean
    return round(max(0.0, 1.0 - cv / 0.10), 4)


def spread_of(latest: dict) -> float | None:
    sell, buy = latest.get("sell_price"), latest.get("buy_price")
    if sell and buy and sell > 0:
        return (sell - buy) / sell
    return None


def _scorable_z(delta: MetricDelta | None) -> float | None:
    """仅显著且非 NULL 恢复的指标可计异常分。"""
    if delta is None or delta.null_recovered or not delta.significant:
        return None
    return delta.zscore


def anomaly_score(deltas: dict, weights: dict | None = None) -> float:
    """综合 Z-Score 异常度 0-100（不含跨平台一致度）。

    小基数不显著/NULL 恢复的指标不参与；缺失因子降权归一化。
    """
    w = weights or ANOMALY_WEIGHTS
    parts: dict[str, float | None] = {
        "buy_count_z": z_to_score(_scorable_z(deltas.get("buy_count"))),
        "sell_count_z": z_to_score(_scorable_z(deltas.get("sell_count"))),
        "volume_z": z_to_score(_scorable_z(deltas.get("volume"))),
        "price_z": z_to_score(_scorable_z(deltas.get("sell_price"))),
        "spread_z": None,  # 预留：由调用方注入
    }
    acc = avail = 0.0
    for key, weight in w.items():
        score = parts.get(key)
        if score is None:
            continue
        acc += score * weight
        avail += weight
    return round(acc / avail, 2) if avail > 0 else 0.0


def order_flow_score(latest: dict, deltas: dict,
                     weights: dict | None = None) -> float:
    """买卖力量 -100 ~ +100。"""
    w = weights or ORDER_FLOW_WEIGHTS
    sell_count = latest.get("sell_count") or 0
    buy_count = latest.get("buy_count") or 0
    # 供需比（log 压缩到 ±1）
    if sell_count > 0 and buy_count > 0:
        ds = math.tanh(math.log((buy_count + 1) / (sell_count + 1)))
    elif buy_count > 0 and sell_count == 0:
        ds = 1.0
    else:
        ds = 0.0

    def direction(metric: str, invert: bool = False) -> float:
        d = deltas.get(metric)
        if not d or not d.significant or d.pct_change is None:
            return 0.0
        sign = math.tanh(d.pct_change / 30.0)
        return -sign if invert else sign

    # spread 收窄为买方友好
    spread_now = spread_of(latest)
    spread_dir = 0.0
    if spread_now is not None:
        spread_dir = math.tanh((0.05 - spread_now) / 0.05)

    price_dir = 0.0
    bp = deltas.get("buy_price")
    if bp and bp.significant and bp.pct_change is not None:
        price_dir = math.tanh(bp.pct_change / 10.0)

    score = (ds * w["demand_supply"]
             + direction("buy_count") * w["buy_count_dir"]
             + direction("sell_count", invert=True) * w["sell_count_dir"]
             + price_dir * w["price_dir"]
             + spread_dir * w["spread_dir"])
    return round(max(-100.0, min(100.0, score * 100)), 2)


def liquidity_score(latest: dict) -> float:
    from market_anomaly.universe import liquidity_score_of
    return liquidity_score_of(latest.get("sell_count"), latest.get("buy_count"),
                              latest.get("volume_24h"))


def data_quality_score(series: list[dict], latest: dict,
                       stale_threshold_sec: float = 1800.0,
                       now: str | None = None) -> float:
    """数据质量 0-100：有效样本率 + 新鲜度 + 低质量标记惩罚。

    now：注入时钟（ISO 字符串）；None 时取当前时间（生产路径）。
    """
    from utils.timeutils import iso_now, parse_iso
    n = len(series)
    if n == 0:
        return 0.0
    valid_ratio = sum(1 for r in series if r.get("sell_price") is not None) / n
    score = 100.0 * (0.3 + 0.4 * valid_ratio)
    ts = latest.get("ts")
    if ts:
        now_dt = parse_iso(now or iso_now())
        age = (now_dt - parse_iso(ts)).total_seconds()
        freshness = min(1.0, max(0.0, 1.0 - age / (stale_threshold_sec * 4)))
        score += 30.0 * freshness
    quality = latest.get("data_quality", "A")
    score -= {"A": 0, "B": 5, "C": 20, "D": 40}.get(quality, 20)
    return round(max(0.0, min(100.0, score)), 2)


def is_stale(latest_ts: str | None, stale_sec: float,
             now: str | None = None) -> bool:
    """数据过期硬判定（供服务层门控）。"""
    from utils.timeutils import iso_now, parse_iso
    if not latest_ts:
        return True
    now_dt = parse_iso(now or iso_now())
    return (now_dt - parse_iso(latest_ts)).total_seconds() > stale_sec


def relative_strength(item_momentum: float | None,
                      market_momentum: float | None) -> float | None:
    """相对大盘强弱 = 单品动量 − 大盘动量（逆势走强为重要信号）。"""
    if item_momentum is None or market_momentum is None:
        return None
    return round(item_momentum - market_momentum, 4)


def momentum_of(series: list[dict], field: str = "sell_price",
                window: int = 20) -> float | None:
    """价格序列动量（比率）。不足 window 时用全长。"""
    prices = [r.get(field) for r in series if r.get(field)]
    if len(prices) < 2:
        return None
    back = prices[-min(window, len(prices)) - 1] if len(prices) > window else prices[0]
    if not back:
        return None
    return round(prices[-1] / back - 1.0, 4)


def build_item_metrics(item: dict, series: list[dict],
                       platform_prices: list[float | None],
                       market_momentum: float | None,
                       now: str | None = None) -> ItemMetrics:
    """组装单件饰品一轮检测特征（now：注入时钟）。"""
    latest = series[-1] if series else {}
    deltas = compute_deltas(series)
    sync = cross_platform_sync(platform_prices)
    item_mom = momentum_of(series)
    any_recovered = any(d.null_recovered for d in deltas.values())
    return ItemMetrics(
        item_uuid=item["item_uuid"],
        market_hash_name=item["market_hash_name"],
        name_zh=item.get("name_zh"),
        n_samples=len(series),
        latest=latest,
        deltas=deltas,
        cross_platform_sync=sync,
        liquidity_score=liquidity_score(latest),
        data_quality_score=data_quality_score(series, latest, now=now),
        anomaly_score=anomaly_score(deltas),
        order_flow_score=order_flow_score(latest, deltas),
        relative_strength=relative_strength(item_mom, market_momentum),
        null_recovery=any_recovered,
    )
