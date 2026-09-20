"""信号引擎测试：blend / decide / confidence / 降级规则。"""
import unittest

from market.market_score import FactorContribution
from quant.factors.item_score import ItemScoreResult
from quant.signals.signal_engine import (
    blend_scores, compute_confidence, decide, generate_signal,
)

CFG = {
    "model_version": "signal-v1.0.0",
    "blend": {"item": 0.6, "market": 0.4},
    "signal_thresholds": {"buy": 75.0, "sell": 45.0, "min_confidence": 0.4},
    "confidence_weights": {"agreement": 0.45, "data_quality": 0.30, "sample": 0.25},
}


def make_item(score, factor_scores: dict, missing: list):
    factors = [
        FactorContribution(k, v, 0.125, round((v or 0) * 0.125, 2), v is not None)
        for k, v in factor_scores.items()
    ]
    return ItemScoreResult(score, factors, missing)


class TestSignalEngine(unittest.TestCase):
    def test_blend(self):
        self.assertEqual(blend_scores(80, 60, CFG["blend"]), 72.0)
        self.assertEqual(blend_scores(80, None, CFG["blend"]), 80)
        self.assertIsNone(blend_scores(None, None, CFG["blend"]))

    def test_decide(self):
        self.assertEqual(decide(80, CFG["signal_thresholds"]), "BUY")
        self.assertEqual(decide(75, CFG["signal_thresholds"]), "BUY")
        self.assertEqual(decide(50, CFG["signal_thresholds"]), "HOLD")
        self.assertEqual(decide(44.9, CFG["signal_thresholds"]), "SELL")

    def test_confidence_perfect(self):
        item = make_item(80, {"trend": 80, "volume": 80}, [])
        conf = compute_confidence(item.factors, 80, ["A"], 180,
                                  CFG["confidence_weights"], 0)
        self.assertAlmostEqual(conf, 1.0)

    def test_confidence_critical_missing_capped(self):
        item = make_item(80, {"trend": None, "supply_demand": None, "volume": 80},
                         ["trend", "supply_demand"])
        conf = compute_confidence(item.factors, 80, ["A"], 180,
                                  CFG["confidence_weights"], 2)
        self.assertLessEqual(conf, 0.4)

    def test_buy_downgraded_on_low_confidence(self):
        # final=80 触发 BUY，但因子全反向 -> agreement=0 -> 低置信 -> 降 HOLD
        item = make_item(80, {"trend": 20, "volume": 20}, [])
        sig = generate_signal(item, None, CFG, ["D"], 0)
        self.assertEqual(sig.final_score, 80)
        self.assertLess(sig.confidence, 0.4)
        self.assertEqual(sig.signal, "HOLD")

    def test_buy_kept_on_high_confidence(self):
        item = make_item(80, {"trend": 80, "supply_demand": 85, "volume": 75}, [])
        sig = generate_signal(item, None, CFG, ["A"], 200)
        self.assertEqual(sig.signal, "BUY")
        self.assertGreaterEqual(sig.confidence, 0.4)
        self.assertEqual(sig.model_version, "signal-v1.0.0")
        self.assertIn("item_factors", sig.reason)

    def test_insufficient_data(self):
        item = make_item(None, {"trend": None}, ["trend"])
        sig = generate_signal(item, None, CFG, ["D"], 0)
        self.assertEqual(sig.signal, "HOLD")
        self.assertIsNone(sig.final_score)


if __name__ == "__main__":
    unittest.main()
