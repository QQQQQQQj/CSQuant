"""告警规则引擎（纯函数，可测）+ 默认规则定义 + 消息模板。

规则类型见 ALERT_AND_QQ_BOT_SPEC §2。所有判定函数只依赖输入数据，不查库。
"""
from __future__ import annotations

DEFAULT_RULES: list[dict] = [
    {"rule_id": "price_change", "rule_type": "price_change", "name": "价格异动",
     "threshold": 5.0, "cooldown_min": 60, "enabled": 1,
     "params": {"platform": "BUFF"}},
    {"rule_id": "signal_trigger", "rule_type": "signal_trigger", "name": "买卖信号",
     "threshold": 0.6, "cooldown_min": 120, "enabled": 1, "params": {}},
    {"rule_id": "market_regime_change", "rule_type": "market_regime_change",
     "name": "大盘情绪切换", "threshold": None, "cooldown_min": 360,
     "enabled": 1, "params": {}},
    {"rule_id": "nav_drawdown", "rule_type": "nav_drawdown", "name": "净值回撤",
     "threshold": 0.10, "cooldown_min": 720, "enabled": 1, "params": {}},
    {"rule_id": "datasource_down", "rule_type": "datasource_down",
     "name": "数据源故障", "threshold": None, "cooldown_min": 60,
     "enabled": 1, "params": {}},
]

LEVEL_MAP = {
    "price_change": "ALERT",
    "signal_trigger": "INFO",
    "market_regime_change": "INFO",
    "nav_drawdown": "ALERT",
    "datasource_down": "ALERT",
}


# ---------------- 判定函数 ----------------

def eval_price_change(old_price: float | None, new_price: float | None,
                      threshold_pct: float) -> tuple[bool, float | None]:
    """相邻两次在售价涨跌幅绝对值 >= threshold_pct（%）。"""
    if not old_price or not new_price or old_price <= 0:
        return False, None
    change_pct = (new_price - old_price) / old_price * 100
    return abs(change_pct) >= threshold_pct, round(change_pct, 2)


def eval_signal_trigger(signal_row: dict, min_confidence: float) -> bool:
    """BUY/SELL 且置信度达标。"""
    return (signal_row.get("signal") in ("BUY", "SELL")
            and (signal_row.get("confidence") or 0) >= min_confidence)


def eval_regime_change(prev_regime: str | None, curr_regime: str | None) -> bool:
    """两连续 regime 均非空且不同。"""
    return bool(prev_regime and curr_regime and prev_regime != curr_regime)


def current_drawdown(nav_values: list[float]) -> float | None:
    """当前回撤 = (历史峰值 - 最新值)/峰值（区别于全周期最大回撤）。"""
    if not nav_values:
        return None
    peak = max(nav_values)
    if peak <= 0:
        return None
    return round((peak - nav_values[-1]) / peak, 4)


def eval_nav_drawdown(nav_values: list[float], threshold: float) -> tuple[bool, float | None]:
    dd = current_drawdown(nav_values)
    if dd is None:
        return False, None
    return dd >= threshold, dd


def eval_datasource_down(health_rows: list[dict]) -> list[str]:
    """返回 down 状态的数据源名列表。"""
    return [h["source"] for h in health_rows if h.get("status") == "down"]


# ---------------- 消息模板 ----------------

def build_alert_message(rule_type: str, payload: dict) -> str:
    level = LEVEL_MAP.get(rule_type, "INFO")
    prefix = f"【CSQuant·{level}】"
    if rule_type == "price_change":
        arrow = "↑" if (payload.get("change_pct") or 0) > 0 else "↓"
        return (f"{prefix}价格异动 {arrow}\n"
                f"{payload.get('item_name')}\n"
                f"{payload.get('platform')} 在售价 {payload.get('old_price'):.2f} → "
                f"{payload.get('new_price'):.2f}（{payload.get('change_pct'):+.2f}%）\n"
                f"{payload.get('time_cn')}")
    if rule_type == "signal_trigger":
        return (f"{prefix}买卖信号 {payload.get('signal')}\n"
                f"{payload.get('item_name')}\n"
                f"总分 {payload.get('final_score')} 置信度 {payload.get('confidence')} "
                f"风险 {payload.get('risk_level')} 建议仓位 "
                f"{(payload.get('suggested_position') or 0) * 100:.0f}%\n"
                f"{payload.get('time_cn')}")
    if rule_type == "market_regime_change":
        return (f"{prefix}大盘情绪切换\n"
                f"{payload.get('prev_regime')} → {payload.get('curr_regime')}\n"
                f"MarketScore {payload.get('market_score')}\n{payload.get('time_cn')}")
    if rule_type == "nav_drawdown":
        return (f"{prefix}净值回撤告警\n"
                f"当前回撤 {payload.get('drawdown') * 100:.2f}%"
                f"（阈值 {payload.get('threshold') * 100:.0f}%）\n"
                f"总资产 {payload.get('total_value')}\n{payload.get('time_cn')}")
    if rule_type == "datasource_down":
        return (f"{prefix}数据源故障\n"
                f"{', '.join(payload.get('sources', []))} 不可用\n"
                f"请检查网络/网关白名单/密钥\n{payload.get('time_cn')}")
    return f"{prefix}{payload.get('text', '未知告警')}"
