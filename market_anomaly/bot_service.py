"""MarketSignalBot 播报服务：门控 → 冷却 → 生命周期消息 → MarketNotifier。

路由纪律：只持有 MarketNotifier，绝无库存群信息（fail-closed）。
"""
from __future__ import annotations

from alerter.notifiers import MarketNotifier
from database.dao import Dao
from market_anomaly.signal_engine import passes_broadcast_gate
from utils.logger import get_logger
from utils.timeutils import iso_now, seconds_since, to_cn_str

SIGNAL_EMOJI = {"STRONG BUY": "🟢🟢", "BUY": "🟢", "WATCH": "🟡",
                "HOLD": "⚪", "REDUCE": "🟠", "SELL": "🔴",
                "RISK ALERT": "🚨"}


def build_market_message(ev: dict) -> str:
    """异动播报消息（概率性推断表述纪律）。"""
    entry, m, scores = ev["entry"], ev["metrics"], ev["scores"]
    event_type = ev["event_type"]
    title = ("疑似建仓" if event_type == "accumulation" else "疑似出货/抛压")
    emoji = "🟢" if event_type == "accumulation" else "🔴"
    reason_tag = ev["emit_reason"]
    name = entry.name_zh or entry.market_hash_name
    d = ev["deltas"]

    def fmt_delta(key, label):
        delta = d.get(key)
        if not delta or delta.pct_change is None:
            return None
        old = (delta.latest or 0) - (delta.abs_change or 0)
        return (f"{label}: {old:,.0f} → {delta.latest:,.0f}"
                f"（{delta.pct_change:+.1f}%）")

    lines = [
        f"{emoji}【全市场异动｜{title}】({reason_tag})",
        f"饰品：{name}",
        f"量化信号：{SIGNAL_EMOJI.get(ev['signal'], '')} {ev['signal']} ｜ "
        f"异常评分 {scores['anomaly']:.0f}/100 ｜ "
        f"{'建仓' if event_type == 'accumulation' else '出货'}评分 "
        f"{scores['accumulation' if event_type == 'accumulation' else 'distribution']:.0f}/100",
    ]
    for key, label in (("buy_count", "求购数量"), ("sell_count", "在售数量"),
                       ("buy_price", "最高求购价"), ("sell_price", "最低在售价")):
        text = fmt_delta(key, label)
        if text:
            lines.append(text)
    extras = []
    if m.relative_strength is not None:
        extras.append(f"相对大盘强度 {m.relative_strength * 100:+.1f}%")
    if m.cross_platform_sync is not None:
        extras.append(f"多平台一致度 {m.cross_platform_sync:.2f}")
    extras.append(f"置信度 {scores['confidence']:.2f}")
    lines.append(" ｜ ".join(extras))
    if event_type == "accumulation":
        lines.append("判断：检测到求购增加、在售减少、买价抬升与成交放大，"
                     "呈现需求增强与供给收缩特征，"
                     "存在「疑似资金建仓/吸筹」的可能（概率性量化推断）。")
    else:
        lines.append("判断：检测到在售异常增加、求购减少、买价下移，"
                     "市场抛压显著增强，"
                     "存在「疑似集中出货/获利兑现」的风险（概率性量化推断）。")
    lines.append("⚠️ 仅为量化信号，不代表确认存在人为控盘，不构成投资建议。")
    lines.append(f"时间：{to_cn_str(iso_now())}")
    return "\n".join(lines)


def build_top_message(top_lists: dict, market_score: float | None,
                      regime_zh: str | None) -> str:
    lines = ["📊【CS2 市场异动 TOP 榜】"]
    for title, rows in top_lists.items():
        if not rows:
            continue
        lines.append(f"\n【{title}】")
        for i, r in enumerate(rows, 1):
            name = r.get("name_zh") or r["market_hash_name"]
            score = (r.get("accumulation_score") or r.get("distribution_score")
                     or r.get("anomaly_score") or 0)
            lines.append(f"{i}. {name}  Score {score:.0f}")
    lines.append(f"\n市场状态：{regime_zh or '-'} "
                 f"{f'{market_score:.0f}/100' if market_score else ''}")
    lines.append(f"时间：{to_cn_str(iso_now())}")
    return "\n".join(lines)


class MarketBotService:
    def __init__(self, dao: Dao, cfg: dict,
                 notifier: MarketNotifier | None = None):
        self.dao = dao
        ma_cfg = cfg.get("market_anomaly", {})
        self.gate = ma_cfg.get("broadcast_gate")
        self.cooldown_min = ma_cfg.get("cooldown_min", 120)
        self.top_interval_min = ma_cfg.get("top_interval_min", 60)
        self.notifier = notifier
        self.log = get_logger("csquant.market_bot")

    # ---------------- 异动播报 ----------------
    def dispatch(self, emit_events: list[dict]) -> dict:
        report = {"candidates": len(emit_events), "sent": 0,
                  "gated": 0, "cooled": 0, "deduped": 0}
        for ev in emit_events:
            ok, _why = passes_broadcast_gate(ev["scores"], self.gate)
            # RESOLVED/UPGRADED 为生命周期事件，绕过常规门控但仍需冷却
            if not ok and ev["emit_reason"] in ("NEW",):
                report["gated"] += 1
                continue
            item_uuid = ev["entry"].item_uuid
            event_id = ev["event_fields"]["event_id"]
            # 幂等键：同一事件+同一阶段只成功推送一次（失败不计，下轮重试）
            ref_id = f"{item_uuid}|{event_id}|{ev['emit_reason']}"
            if self.dao.broadcast_pushed_exists("market", ref_id):
                report["deduped"] += 1
                continue
            if self._in_cooldown(item_uuid):
                report["cooled"] += 1
                continue
            message = build_market_message(ev)
            sent, err = (self.notifier.send(
                message, message_type=ev["event_type"], ref_id=ref_id)
                if self.notifier else (False, "not_configured"))
            if sent:
                report["sent"] += 1
            else:
                self.log.warning("播报失败(%s): %s（不计冷却，下轮重试）",
                                 err, ev["entry"].market_hash_name)
        self.dao.commit()
        self.log.info("异动播报: %s", report)
        return report

    def _in_cooldown(self, item_uuid: str) -> bool:
        # 只有【成功推送】才进入冷却（ref_id 前缀 = item_uuid|）
        last = self.dao.get_last_broadcast_ts("market", f"{item_uuid}|")
        if not last:
            return False
        return seconds_since(last) < self.cooldown_min * 60

    # ---------------- TOP 榜定时摘要 ----------------
    def send_top_summary(self, top_lists: dict, market_score: float | None,
                       regime_zh: str | None) -> tuple[bool, str | None]:
        last = self.dao.get_meta("market_top_summary_ts")
        if last and seconds_since(last) < self.top_interval_min * 60:
            return False, "top_summary_cooldown"
        if not any(top_lists.values()):
            return False, "empty_top"
        message = build_top_message(top_lists, market_score, regime_zh)
        ok, err = (self.notifier.send(message, message_type="top_summary")
                   if self.notifier else (False, "not_configured"))
        if ok:
            self.dao.set_meta("market_top_summary_ts", iso_now())
            self.dao.commit()
        return ok, err

    def status(self) -> dict:
        if self.notifier is None:
            return {"online": False, "reason": "MarketNotifier 未配置"}
        return self.notifier.status()
