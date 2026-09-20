"""服务层：信号生成与查询（ItemContext 组装 -> 因子 -> 信号 -> 风控 -> 落库）。"""
from __future__ import annotations

import json
from dataclasses import dataclass

from database.dao import Dao
from market.market_score import MarketScoreResult
from quant.factors.item_score import ItemContext, compute_item_score, item_volatility
from quant.signals.signal_engine import generate_signal, SignalResult
from risk.risk import assess_risk_level, suggested_position

MAIN_PLATFORM = "BUFF"


@dataclass
class GenerateReport:
    total: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0


class SignalService:
    def __init__(self, dao: Dao, cfg: dict):
        self.dao = dao
        self.cfg = cfg

    # ---- 上下文组装（只使用 t 时刻及之前数据）----
    def build_item_context(self, item_uuid: str, market_hash_name: str,
                           market_closes: list[float]) -> tuple[ItemContext, list[str], int]:
        klines = self.dao.get_klines(item_uuid, MAIN_PLATFORM, "1d", limit=180)
        closes = [k["close"] for k in klines]
        volumes = [k["volume"] for k in klines if k.get("volume") is not None]

        snapshots = self.dao.get_snapshots_for_item(item_uuid)
        main = next((s for s in snapshots if s["platform"] == MAIN_PLATFORM),
                    snapshots[0] if snapshots else {})
        qualities = [s.get("data_quality", "C") for s in snapshots] or ["D"]

        ctx = ItemContext(
            market_hash_name=market_hash_name,
            closes=closes,
            volumes=volumes,
            sell_price=main.get("sell_price"),
            buy_price=main.get("buy_price"),
            sell_count=main.get("sell_count"),
            buy_count=main.get("buy_count"),
            volume_24h=main.get("volume_24h"),
            platform_prices=[s["sell_price"] for s in snapshots
                             if s.get("sell_price")],
            market_closes=market_closes,
            history_prices_180d=closes,
        )
        return ctx, qualities, len(closes)

    # ---- 生成 ----
    def generate_signals(self, scope: str = "watchlist",
                         market_result: MarketScoreResult | None = None,
                         market_closes: list[float] | None = None) -> GenerateReport:
        report = GenerateReport()
        items = self.dao.get_watchlist(tiers=("L1", "L2")) if scope == "watchlist" \
            else self.dao.get_watchlist()
        market_closes = market_closes or []
        model_version = (self.cfg.get("_config_version")
                         or self.cfg.get("model_version", ""))
        for row in items:
            ctx, qualities, sample_days = self.build_item_context(
                row["item_uuid"], row["market_hash_name"], market_closes)
            item_result = compute_item_score(ctx, self.cfg["item_weights"])
            sig: SignalResult = generate_signal(item_result, market_result,
                                                self.cfg, qualities, sample_days)
            # 风控定级 + 建议仓位
            spread = None
            if ctx.sell_price and ctx.buy_price and ctx.sell_price > 0:
                spread = (ctx.sell_price - ctx.buy_price) / ctx.sell_price
            risk_level = assess_risk_level(
                item_volatility(ctx), spread, ctx.sell_price,
                self.cfg["risk_bands"])
            position = suggested_position(risk_level, sig.confidence,
                                          self.cfg["position_matrix"])
            # 不可变输入快照（P3）：行情+双侧因子+权重阈值+版本+数据版本
            snapshots = self.dao.get_snapshots_for_item(row["item_uuid"])
            snapshot_payload = {
                "quotes": [{k: s.get(k) for k in (
                    "platform", "source", "sell_price", "sell_count",
                    "buy_price", "buy_count", "volume_24h", "ts",
                    "data_quality")} for s in snapshots],
                "market_factors": (sig.reason or {}).get("market_factors", []),
                "item_factors": (sig.reason or {}).get("item_factors", []),
                "item_factor_raw": item_result.raw,
                "weights": {"item": self.cfg["item_weights"],
                            "market": self.cfg.get("market_weights")},
                "thresholds": self.cfg["signal_thresholds"],
                "blend": self.cfg["blend"],
                "model_version": model_version,
                "data_version": {"kline_days": sample_days,
                                 "latest_snapshot_ts": max(
                                     (s.get("ts") or "" for s in snapshots),
                                     default=None)},
            }
            snapshot_id = self.dao.insert_signal_snapshot(snapshot_payload)
            self.dao.insert_signal({
                "item_uuid": row["item_uuid"],
                "market_score": sig.market_score,
                "item_score": sig.item_score,
                "final_score": sig.final_score,
                "signal": sig.signal,
                "confidence": sig.confidence,
                "risk_level": risk_level,
                "reason_json": sig.reason,
                "suggested_position": position,
                "model_version": model_version,
                "input_snapshot_ref": snapshot_id,
            })
            report.total += 1
            if sig.signal == "BUY":
                report.buy += 1
            elif sig.signal == "SELL":
                report.sell += 1
            else:
                report.hold += 1
        self.dao.commit()
        return report

    # ---- 查询 ----
    def get_signals(self, signal_filter: str | None = None,
                    risk_level: str | None = None, limit: int = 100) -> list[dict]:
        rows = self.dao.get_signals(signal_filter, risk_level, limit)
        for r in rows:
            r["reason_json"] = json.loads(r.get("reason_json") or "{}")
        return rows

    def get_signal_detail(self, signal_id: int) -> dict | None:
        row = self.dao.get_signal_detail(signal_id)
        if row:
            row["reason_json"] = json.loads(row.get("reason_json") or "{}")
        return row

    def get_signal_history(self, item_uuid: str, days: int = 30) -> list[dict]:
        return self.dao.get_signal_history(item_uuid, limit=days)
