"""MarketScore 六因子与合成测试。"""
import unittest

from market.market_score import (
    breadth_score_fn, compute_market_score, cross_platform_score_fn,
    event_sentiment_score_fn, liquidity_score_fn, regime_from_score,
    supply_demand_score_fn, trend_score, weighted_score,
)

BANDS = [
    [0, 20, "extreme_fear", "极度恐慌"], [20, 40, "bearish", "偏空"],
    [40, 60, "neutral", "震荡"], [60, 80, "bullish", "偏多"],
    [80, 101, "extreme_greed", "极度贪婪"],
]
WEIGHTS = {"trend": 0.25, "breadth": 0.20, "liquidity": 0.20,
           "supply_demand": 0.15, "cross_platform": 0.10, "event_sentiment": 0.10}


class TestMarketScore(unittest.TestCase):
    def test_trend_up(self):
        closes = [100 + i for i in range(80)]  # 稳定上行
        score = trend_score(closes)
        self.assertIsNotNone(score)
        self.assertGreater(score, 50)

    def test_trend_down(self):
        closes = [200 - i for i in range(80)]
        self.assertLess(trend_score(closes), 50)

    def test_trend_insufficient(self):
        self.assertIsNone(trend_score([1, 2, 3]))

    def test_breadth(self):
        self.assertEqual(breadth_score_fn(3, 1), 75.0)
        self.assertIsNone(breadth_score_fn(None, 5))
        self.assertIsNone(breadth_score_fn(0, 0))

    def test_liquidity(self):
        self.assertGreater(liquidity_score_fn(200, 100), 50)  # 放量
        self.assertLess(liquidity_score_fn(50, 100), 50)      # 缩量
        self.assertIsNone(liquidity_score_fn(None, 100))

    def test_supply_demand(self):
        self.assertGreater(supply_demand_score_fn(200, 100), 50)
        self.assertLess(supply_demand_score_fn(50, 100), 50)
        self.assertIsNone(supply_demand_score_fn(None, 100))

    def test_cross_platform(self):
        self.assertEqual(cross_platform_score_fn(0.0), 100.0)
        self.assertEqual(cross_platform_score_fn(0.10), 0.0)
        self.assertIsNone(cross_platform_score_fn(None))

    def test_event_sentiment(self):
        self.assertEqual(event_sentiment_score_fn(61), 61.0)
        self.assertIsNone(event_sentiment_score_fn(None))

    def test_weighted_score_missing_downweight(self):
        # 缺失因子降权：b 缺失时 a 权重归一化为 1
        score, contribs = weighted_score({"a": 80.0, "b": None},
                                         {"a": 0.3, "b": 0.7})
        self.assertEqual(score, 80.0)
        a = next(c for c in contribs if c.key == "a")
        b = next(c for c in contribs if c.key == "b")
        self.assertEqual(a.weight, 1.0)
        self.assertFalse(b.available)

    def test_weighted_score_all_missing(self):
        score, _ = weighted_score({"a": None}, {"a": 1.0})
        self.assertIsNone(score)

    def test_regime_bands(self):
        self.assertEqual(regime_from_score(10, BANDS)[0], "extreme_fear")
        self.assertEqual(regime_from_score(30, BANDS)[0], "bearish")
        self.assertEqual(regime_from_score(50, BANDS)[0], "neutral")
        self.assertEqual(regime_from_score(70, BANDS)[0], "bullish")
        self.assertEqual(regime_from_score(90, BANDS)[0], "extreme_greed")

    def test_compute_full(self):
        scores = {"trend": 70, "breadth": 60, "liquidity": None,
                  "supply_demand": 55, "cross_platform": 80,
                  "event_sentiment": 50}
        result = compute_market_score(scores, WEIGHTS, BANDS, "v-test")
        self.assertIsNotNone(result.score)
        self.assertIn("liquidity", result.missing_factors)
        self.assertEqual(result.model_version, "v-test")


if __name__ == "__main__":
    unittest.main()
