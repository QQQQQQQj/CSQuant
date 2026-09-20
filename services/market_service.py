"""服务层：市场大盘（指数采集落库 + MarketScore 计算）。"""
from __future__ import annotations

import json
from dataclasses import asdict

from database.dao import Dao
from market import market_score as ms
from market.breadth import breadth_ratio, category_strength


class MarketService:
    def __init__(self, dao: Dao, cfg: dict, primary=None, fallback=None):
        self.dao = dao
        self.cfg = cfg
        self.primary = primary      # CSQAQ
        self.fallback = fallback    # SteamDT

    # ---- 采集 ----
    def collect_market_index(self) -> dict | None:
        idx = None
        for provider in (self.primary, self.fallback):
            if provider is None:
                continue
            try:
                idx = provider.get_market_index()
            except Exception as e:  # noqa: BLE001
                self.dao.log_event("WARN", "market",
                                   f"{provider.name} 大盘拉取失败", {"error": str(e)})
            if idx is not None:
                break
        if idx is None:
            self.dao.log_event("ERROR", "market", "全部大盘源失败")
            self.dao.commit()
            return None
        self.dao.insert_market_index({
            "ts": idx.ts, "index_name": idx.index_name,
            "index_value": idx.index_value,
            "change_1d": idx.change_1d, "change_7d": idx.change_7d,
            "change_30d": idx.change_30d,
            "breadth_up": idx.breadth_up, "breadth_down": idx.breadth_down,
            "sentiment_score": idx.sentiment_greedy,
            "sub_indices_json": json.dumps(idx.sub_indices, ensure_ascii=False),
            "source": idx.source,
        })
        self.dao.commit()
        return {"index_name": idx.index_name, "index_value": idx.index_value}

    # ---- MarketScore ----
    def compute_and_store_market_score(self) -> ms.MarketScoreResult:
        history = self.dao.get_market_index_history("csqaq_main", limit=120)
        if not history:
            history = self.dao.get_market_index_history("steamdt_broad", limit=120)
        closes = [h["index_value"] for h in history if h.get("index_value")]
        latest = history[-1] if history else {}

        factor_scores = {
            "trend": ms.trend_score(closes),
            "breadth": ms.breadth_score_fn(latest.get("breadth_up"),
                                           latest.get("breadth_down")),
            "liquidity": None,          # 全市场成交量：企业档接口，V1 缺失降权
            "supply_demand": None,      # 全市场买卖盘：V1 缺失降权（样本集方案见规格）
            "cross_platform": None,     # 由跨源校验任务另行计算写入
            "event_sentiment": ms.event_sentiment_score_fn(
                latest.get("sentiment_score")),
        }
        result = ms.compute_market_score(
            factor_scores, self.cfg["market_weights"],
            self.cfg["market_regime_bands"], self.cfg["model_version"])
        if result.score is not None:
            self.dao.insert_market_index({
                "index_name": "csquant_market_score",
                "index_value": latest.get("index_value") or 0,
                "change_1d": latest.get("change_1d"),
                "breadth_up": latest.get("breadth_up"),
                "breadth_down": latest.get("breadth_down"),
                "market_score": result.score,
                "market_regime": result.regime,
                "source": "csquant",
            })
            self.dao.commit()
        return result

    # ---- 查询 ----
    def get_market_overview(self) -> dict:
        latest_idx = self.dao.get_latest_market_index("csqaq_main") \
            or self.dao.get_latest_market_index("steamdt_broad") or {}
        latest_score = self.dao.get_latest_market_index("csquant_market_score") or {}
        sub = json.loads(latest_idx.get("sub_indices_json") or "[]")
        return {
            "index_value": latest_idx.get("index_value"),
            "change_1d": latest_idx.get("change_1d"),
            "change_7d": latest_idx.get("change_7d"),
            "change_30d": latest_idx.get("change_30d"),
            "market_score": latest_score.get("market_score"),
            "regime": latest_score.get("market_regime"),
            "breadth": {
                "up": latest_idx.get("breadth_up"),
                "down": latest_idx.get("breadth_down"),
                "ratio": breadth_ratio(latest_idx.get("breadth_up"),
                                       latest_idx.get("breadth_down")),
            },
            "sentiment_greedy": latest_idx.get("sentiment_score"),
            "category_strength": category_strength(sub),
            "as_of": latest_idx.get("ts"),
            "source": latest_idx.get("source"),
        }

    def get_market_score_history(self, days: int = 90) -> list[dict]:
        return self.dao.get_market_index_history("csquant_market_score", limit=days)

    def get_index_closes(self, limit: int = 120) -> list[float]:
        history = self.dao.get_market_index_history("csqaq_main", limit=limit) \
            or self.dao.get_market_index_history("steamdt_broad", limit=limit)
        return [h["index_value"] for h in history if h.get("index_value")]
