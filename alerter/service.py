"""告警服务：规则评估 → 去抖 → QQ 推送 → 落库（可追溯）。"""
from __future__ import annotations

import json

from alerter.rules import (
    DEFAULT_RULES, LEVEL_MAP, build_alert_message, current_drawdown,
    eval_datasource_down, eval_nav_drawdown, eval_price_change,
    eval_regime_change, eval_signal_trigger,
)
from database.dao import Dao
from utils.logger import get_logger
from utils.timeutils import iso_days_ago, iso_now, seconds_since, to_cn_str


class AlertService:
    def __init__(self, dao: Dao, notifier=None, enabled: bool = True):
        self.dao = dao
        self.notifier = notifier   # InventoryNotifier（库存通道，路由隔离）
        self.enabled = enabled
        self.log = get_logger("csquant.alert")
        self.dao.ensure_default_alert_rules(DEFAULT_RULES)
        self.dao.commit()

    # ---------------- 主入口 ----------------
    def evaluate_all(self) -> dict:
        report = {"evaluated": 0, "triggered": 0, "pushed": 0,
                  "skipped_cooldown": 0, "errors": 0}
        if not self.enabled:
            return report
        for rule in self.dao.get_alert_rules(enabled_only=True):
            report["evaluated"] += 1
            try:
                events = self._eval_rule(rule)
            except Exception as e:  # noqa: BLE001
                report["errors"] += 1
                self.log.warning("规则评估失败 %s: %s", rule["rule_id"], e)
                continue
            for event in events:
                report["triggered"] += 1
                if self._in_cooldown(rule, event["target"]):
                    report["skipped_cooldown"] += 1
                    continue
                ok, err = self._push(event["message"], event["level"])
                self.dao.insert_alert_record({
                    "rule_id": rule["rule_id"], "rule_type": rule["rule_type"],
                    "level": event["level"], "target": event["target"],
                    "message": event["message"], "pushed": 1 if ok else 0,
                    "error": err})
                if ok:
                    report["pushed"] += 1
        self.dao.commit()
        self.log.info("告警评估: %s", report)
        return report

    # ---------------- 规则评估 ----------------
    def _eval_rule(self, rule: dict) -> list[dict]:
        rt = rule["rule_type"]
        threshold = rule.get("threshold")
        if rt == "price_change":
            return self._eval_price_change(rule, threshold or 5.0)
        if rt == "signal_trigger":
            return self._eval_signal_trigger(rule, threshold or 0.6)
        if rt == "market_regime_change":
            return self._eval_regime_change(rule)
        if rt == "nav_drawdown":
            return self._eval_nav_drawdown(rule, threshold or 0.10)
        if rt == "datasource_down":
            return self._eval_datasource_down(rule)
        return []

    def _event(self, rule: dict, target: str | None, payload: dict) -> dict:
        payload.setdefault("time_cn", to_cn_str(iso_now()))
        return {"target": target, "level": LEVEL_MAP.get(rule["rule_type"], "INFO"),
                "message": build_alert_message(rule["rule_type"], payload)}

    def _eval_price_change(self, rule: dict, threshold_pct: float) -> list[dict]:
        platform = (json.loads(rule.get("params_json") or "{}")
                    .get("platform", "BUFF"))
        events = []
        for row in self.dao.get_watchlist(tiers=("L1", "L2")):
            prices = self.dao.get_recent_prices(row["item_uuid"], platform, limit=2)
            if len(prices) < 2:
                continue
            new_price, old_price = prices[0]["sell_price"], prices[1]["sell_price"]
            hit, change_pct = eval_price_change(old_price, new_price, threshold_pct)
            if hit:
                events.append(self._event(rule, row["item_uuid"], {
                    "item_name": row.get("name_zh") or row["market_hash_name"],
                    "platform": platform, "old_price": old_price,
                    "new_price": new_price, "change_pct": change_pct}))
        return events

    def _eval_signal_trigger(self, rule: dict, min_confidence: float) -> list[dict]:
        since = iso_days_ago(1)  # 近24h信号批次（冷却期控制重复）
        events = []
        for sig in self.dao.get_recent_signals(since, ("BUY", "SELL"), min_confidence):
            if eval_signal_trigger(sig, min_confidence):
                events.append(self._event(rule, sig["item_uuid"], {
                    "signal": sig["signal"],
                    "item_name": sig.get("name_zh") or sig["market_hash_name"],
                    "final_score": sig.get("final_score"),
                    "confidence": sig.get("confidence"),
                    "risk_level": sig.get("risk_level"),
                    "suggested_position": sig.get("suggested_position")}))
        return events

    def _eval_regime_change(self, rule: dict) -> list[dict]:
        rows = self.dao.get_recent_regimes(limit=2)
        if len(rows) < 2:
            return []
        curr, prev = rows[0], rows[1]
        if not eval_regime_change(prev.get("market_regime"),
                                  curr.get("market_regime")):
            return []
        latest = self.dao.get_latest_market_index("csquant_market_score") or {}
        return [self._event(rule, "market", {
            "prev_regime": prev["market_regime"],
            "curr_regime": curr["market_regime"],
            "market_score": latest.get("market_score")})]

    def _eval_nav_drawdown(self, rule: dict, threshold: float) -> list[dict]:
        nav = [r["total_value"] for r in self.dao.get_nav_history(limit=90)
               if r.get("total_value")]
        hit, dd = eval_nav_drawdown(nav, threshold)
        if not hit:
            return []
        latest = nav[-1] if nav else None
        return [self._event(rule, "nav", {
            "drawdown": dd, "threshold": threshold,
            "total_value": f"¥{latest:,.2f}" if latest else "-"})]

    def _eval_datasource_down(self, rule: dict) -> list[dict]:
        sources = eval_datasource_down(self.dao.get_health_latest())
        return [self._event(rule, src, {"sources": [src]}) for src in sources]

    # ---------------- 去抖与推送 ----------------
    def _in_cooldown(self, rule: dict, target: str | None) -> bool:
        last = self.dao.get_last_alert_ts(rule["rule_id"], target)
        if not last:
            return False
        return seconds_since(last) < (rule.get("cooldown_min") or 60) * 60

    def _push(self, message: str, level: str) -> tuple[bool, str | None]:
        if self.notifier is None or not self.notifier.configured:
            return False, "qq_not_configured"
        ok, err = self.notifier.send(message, message_type="alert")
        if not ok and level == "ALERT":
            ok, err = self.notifier.send(message, message_type="alert")  # ALERT 重发1次
        return ok, err

    # ---------------- 页面辅助 ----------------
    def qq_status(self) -> dict:
        if self.notifier is None:
            return {"online": False, "reason": "未配置 QQ Bot"}
        return self.notifier.status()

    def send_test_message(self) -> tuple[bool, str | None]:
        if self.notifier is None:
            return False, "未配置 QQ Bot"
        return self.notifier.send(
            "【CSQuant·测试】库存告警通道已连通 ✅\n" + to_cn_str(iso_now()),
            message_type="test")
