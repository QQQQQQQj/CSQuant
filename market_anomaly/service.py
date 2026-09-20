"""MarketAnomalyService：全市场异动检测编排（universe→检测→评分→落库→事件）。"""
from __future__ import annotations

from market_anomaly.accumulation import accumulation_score
from market_anomaly.detectors import build_item_metrics, is_stale
from market_anomaly.distribution import distribution_score
from market_anomaly.event_lifecycle import event_step
from market_anomaly.signal_engine import confidence_of, decide
from market_anomaly.universe import UniverseEntry, build_universe
from database.dao import Dao
from utils.logger import get_logger
from utils.timeutils import iso_now


class MarketAnomalyService:
    def __init__(self, dao: Dao, cfg: dict):
        self.dao = dao
        self.cfg = cfg
        self.ma_cfg = cfg.get("market_anomaly", {})
        self._universe: list[UniverseEntry] = []
        self.log = get_logger("csquant.market_anomaly")

    # ---------------- Universe ----------------
    def refresh_universe(self) -> int:
        candidates = self.dao.get_universe_candidates()
        self._universe = build_universe(candidates, self.ma_cfg.get("universe"))
        self.dao.set_meta("market_universe_size", str(len(self._universe)))
        self.dao.commit()
        self.log.info("Universe 刷新: %s 件可量化饰品", len(self._universe))
        return len(self._universe)

    @property
    def universe(self) -> list[UniverseEntry]:
        return self._universe

    # ---------------- 检测主循环 ----------------
    def detect_round(self, market_momentum: float | None = None,
                     now: str | None = None) -> dict:
        now = now or iso_now()
        stale_sec = self.ma_cfg.get("stale_sec", 7200)
        if not self._universe:
            self.refresh_universe()
        report = {"universe": len(self._universe), "scored": 0, "signals": 0,
                  "skipped_insufficient": 0, "skipped_stale": 0,
                  "emit_events": []}
        if not self._universe:
            self.log.warning("Universe 为空（行情未采集），本轮跳过")
            return report

        model_version = self.cfg.get("_config_version") \
            or self.cfg.get("model_version", "signal-v1.0.0")
        latest_mi = self.dao.get_latest_market_index("csquant_market_score") or {}
        market_score = latest_mi.get("market_score")
        for entry in self._universe:
            series = self.dao.get_ticks_series(entry.item_uuid, "BUFF", limit=28)
            if len(series) < 2:
                report["skipped_insufficient"] += 1
                continue
            # 数据过期硬门控：过期直接跳过，不允许只轻微扣分后继续出信号
            if is_stale(series[-1].get("ts"), stale_sec, now=now):
                report["skipped_stale"] += 1
                continue
            snaps = self.dao.get_snapshots_for_item(entry.item_uuid)
            platform_prices = [s.get("sell_price") for s in snaps]
            m = build_item_metrics(
                {"item_uuid": entry.item_uuid,
                 "market_hash_name": entry.market_hash_name,
                 "name_zh": entry.name_zh},
                series, platform_prices, market_momentum, now=now)

            acc, acc_parts = accumulation_score(m)
            dist, dist_parts = distribution_score(m, series)
            conf = confidence_of(m.cross_platform_sync, m.data_quality_score,
                                 m.n_samples)
            scores = {"anomaly": m.anomaly_score, "accumulation": acc,
                      "distribution": dist, "order_flow": m.order_flow_score,
                      "liquidity": m.liquidity_score,
                      "data_quality": m.data_quality_score,
                      "confidence": conf}
            signal, reasons = decide(scores, self.ma_cfg.get("thresholds"))

            d = m.deltas
            self.dao.insert_market_anomaly({
                "item_uuid": entry.item_uuid,
                "buy_count_change": _pct(d, "buy_count"),
                "sell_count_change": _pct(d, "sell_count"),
                "buy_price_change": _pct(d, "buy_price"),
                "sell_price_change": _pct(d, "sell_price"),
                "volume_change": _pct(d, "volume"),
                "buy_count_zscore": _z(d, "buy_count"),
                "sell_count_zscore": _z(d, "sell_count"),
                "volume_zscore": _z(d, "volume"),
                "price_zscore": _z(d, "sell_price"),
                "spread_zscore": None,
                "cross_platform_sync": m.cross_platform_sync,
                "relative_strength": m.relative_strength,
                "anomaly_score": m.anomaly_score,
                "accumulation_score": acc,
                "distribution_score": dist,
                "order_flow_score": m.order_flow_score,
                "liquidity_score": m.liquidity_score,
                "data_quality_score": m.data_quality_score,
                "confidence": conf,
            })
            if signal != "HOLD":
                self.dao.insert_market_signal({
                    "item_uuid": entry.item_uuid, "signal": signal,
                    "anomaly_score": m.anomaly_score,
                    "accumulation_score": acc, "distribution_score": dist,
                    "order_flow_score": m.order_flow_score,
                    "relative_strength": m.relative_strength,
                    "market_score": market_score,
                    "confidence": conf,
                    "reason_json": {
                        "reasons": reasons,
                        "accumulation_parts": acc_parts,
                        "distribution_parts": dist_parts,
                        "relative_strength": m.relative_strength,
                        "note": "概率性量化推断，非确认事实"},
                    "model_version": model_version,
                })
                report["signals"] += 1

            # 事件生命周期（建仓/出货两类）
            for event_type, escore in (("accumulation", acc), ("distribution", dist)):
                open_ev = self.dao.get_open_event(entry.item_uuid, event_type)
                step = event_step(open_ev, event_type, entry.item_uuid,
                                  escore, now,
                                  payload={"anomaly": m.anomaly_score,
                                           "signal": signal})
                if step is None:
                    continue
                self.dao.upsert_signal_event(step["event_fields"])
                if step["action"] == "emit":
                    report["emit_events"].append({
                        "entry": entry, "metrics": m, "scores": scores,
                        "signal": signal, "reasons": reasons,
                        "event_type": event_type,
                        "emit_reason": step["emit_reason"],
                        "event_fields": step["event_fields"],
                        "deltas": d})
            report["scored"] += 1
        self.dao.commit()
        self.log.info("异动检测: %s/%s 评分, %s 信号, %s 播报候选",
                      report["scored"], report["universe"], report["signals"],
                      len(report["emit_events"]))
        return report

    # ---------------- TOP 榜 ----------------
    def top_lists(self, limit: int = 5) -> dict[str, list[dict]]:
        latest = self.dao.get_latest_anomalies(limit=500)
        def top(key, reverse=True, minimum=1.0):
            rows = [r for r in latest if (r.get(key) or 0) >= minimum]
            return sorted(rows, key=lambda r: r.get(key) or 0, reverse=reverse)[:limit]
        return {
            "疑似建仓榜": top("accumulation_score", minimum=50),
            "疑似出货榜": top("distribution_score", minimum=50),
            "求购激增榜": top("buy_count_change", minimum=20),
            "在售激增榜": top("sell_count_change", minimum=20),
            "逆势强势榜": sorted(
                [r for r in latest if r.get("relative_strength") is not None],
                key=lambda r: r.get("relative_strength") or -9,
                reverse=True)[:limit],
        }


def _pct(deltas: dict, key: str) -> float | None:
    d = deltas.get(key)
    return d.pct_change if d else None


def _z(deltas: dict, key: str) -> float | None:
    d = deltas.get(key)
    return d.zscore if d else None
