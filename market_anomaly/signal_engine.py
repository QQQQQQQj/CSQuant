"""FullMarketSignalEngine：综合评分 -> 7 档全市场信号 + 门控 + 置信度。

信号：STRONG BUY / BUY / WATCH / HOLD / REDUCE / SELL / RISK ALERT
门控（防误报）：低流动性或低数据质量禁止 BUY/STRONG BUY（降级 WATCH）；
跨平台一致度差降 Confidence。
"""
from __future__ import annotations

SIGNALS = ("STRONG BUY", "BUY", "WATCH", "HOLD", "REDUCE", "SELL", "RISK ALERT")

DEFAULT_THRESHOLDS = {
    "risk_alert_anomaly": 85.0, "risk_alert_distribution": 70.0,
    "strong_buy_anomaly": 75.0, "strong_buy_accumulation": 75.0,
    "strong_buy_orderflow": 40.0, "strong_buy_confidence": 0.70,
    "buy_accumulation": 65.0, "buy_orderflow": 20.0,
    "watch_anomaly": 60.0, "watch_accumulation": 55.0,
    "reduce_distribution": 60.0, "reduce_orderflow": -20.0,
    "sell_distribution": 75.0, "sell_orderflow": -40.0,
    "min_liquidity_for_buy": 60.0, "min_data_quality_for_buy": 60.0,
}


def confidence_of(cross_platform_sync: float | None,
                  data_quality_score: float,
                  n_samples: int,
                  full_samples: int = 28) -> float:
    """Confidence 0-1：跨平台一致度 0.35 + 数据质量 0.35 + 样本充分性 0.30。"""
    sync = cross_platform_sync if cross_platform_sync is not None else 0.3
    sample = min(n_samples / full_samples, 1.0)
    return round(sync * 0.35 + (data_quality_score / 100.0) * 0.35 + sample * 0.30, 3)


def decide(scores: dict, thresholds: dict | None = None) -> tuple[str, list[str]]:
    """返回 (信号, 判定理由列表)。scores 键：
    anomaly/accumulation/distribution/order_flow/liquidity/data_quality/confidence。
    """
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    a = scores.get("anomaly", 0.0) or 0.0
    acc = scores.get("accumulation", 0.0) or 0.0
    dist = scores.get("distribution", 0.0) or 0.0
    of = scores.get("order_flow", 0.0) or 0.0
    liq = scores.get("liquidity", 0.0) or 0.0
    dq = scores.get("data_quality", 0.0) or 0.0
    conf = scores.get("confidence", 0.0) or 0.0
    reasons: list[str] = []

    # 风控优先
    if a >= t["risk_alert_anomaly"] and dist >= t["risk_alert_distribution"]:
        reasons.append(f"异常度{a:.0f}≥{t['risk_alert_anomaly']:.0f} 且 "
                       f"出货评分{dist:.0f}≥{t['risk_alert_distribution']:.0f}")
        return "RISK ALERT", reasons

    # 买入侧
    buy_gate_ok = liq >= t["min_liquidity_for_buy"] and dq >= t["min_data_quality_for_buy"]
    if (a >= t["strong_buy_anomaly"] and acc >= t["strong_buy_accumulation"]
            and of >= t["strong_buy_orderflow"] and conf >= t["strong_buy_confidence"]):
        if buy_gate_ok:
            reasons.append(f"异常{a:.0f}+建仓{acc:.0f}+订单流{of:+.0f} 共振，"
                           f"置信度{conf:.2f}")
            return "STRONG BUY", reasons
        reasons.append("买入共振但流动性/数据质量不足，降级观察")
        return "WATCH", reasons
    if acc >= t["buy_accumulation"] and of >= t["buy_orderflow"]:
        if buy_gate_ok:
            reasons.append(f"建仓评分{acc:.0f}≥{t['buy_accumulation']:.0f} 且 "
                           f"订单流{of:+.0f}≥{t['buy_orderflow']:+.0f}")
            return "BUY", reasons
        reasons.append("建仓特征成立但流动性/数据质量不足，降级观察")
        return "WATCH", reasons

    # 卖出侧
    if dist >= t["sell_distribution"] and of <= t["sell_orderflow"]:
        reasons.append(f"出货评分{dist:.0f}≥{t['sell_distribution']:.0f} 且 "
                       f"订单流{of:+.0f}≤{t['sell_orderflow']:+.0f}")
        return "SELL", reasons
    if dist >= t["reduce_distribution"] and of <= t["reduce_orderflow"]:
        reasons.append(f"出货评分{dist:.0f}≥{t['reduce_distribution']:.0f} 且 "
                       f"订单流{of:+.0f}≤{t['reduce_orderflow']:+.0f}")
        return "REDUCE", reasons

    # 观察
    if a >= t["watch_anomaly"] and acc >= t["watch_accumulation"]:
        reasons.append(f"异常{a:.0f}+建仓{acc:.0f} 进入观察")
        return "WATCH", reasons

    return "HOLD", reasons


# ---------------- 播报门控（刷屏防护，指示文档 §十九） ----------------

BROADCAST_GATE = {
    "min_anomaly_score": 75.0,
    "min_confidence": 0.70,
    "min_liquidity_score": 60.0,
    "major_anomaly_score": 90.0,   # 重大异动直发
}


def passes_broadcast_gate(scores: dict, gate: dict | None = None) -> tuple[bool, str]:
    g = {**BROADCAST_GATE, **(gate or {})}
    a = scores.get("anomaly", 0.0) or 0.0
    if a >= g["major_anomaly_score"]:
        return True, "重大异动直发"
    if (a >= g["min_anomaly_score"]
            and (scores.get("confidence") or 0) >= g["min_confidence"]
            and (scores.get("liquidity") or 0) >= g["min_liquidity_score"]):
        return True, "门控通过"
    return False, "未过门控"
