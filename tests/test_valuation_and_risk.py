"""估值口径与风控测试。"""
import unittest

from inventory.valuation import PositionValue, aggregate, compute_position_value
from risk.risk import (
    assess_risk_level, check_exposure, drawdown_breach, suggested_position,
)

RISK_BANDS = {
    "volatility": {"low": 0.03, "high": 0.10},
    "spread": {"low": 0.02, "high": 0.08},
    "price": {"low": 50.0, "high": 2000.0},
}
MATRIX = {
    "L": {"high_conf": 0.15, "mid_conf": 0.08, "low_conf": 0.0},
    "M": {"high_conf": 0.08, "mid_conf": 0.04, "low_conf": 0.0},
    "H": {"high_conf": 0.03, "mid_conf": 0.0, "low_conf": 0.0},
}


class TestValuation(unittest.TestCase):
    def test_position_value_profit(self):
        pos = {"position_id": "p1", "item_uuid": "u1", "quantity": 2,
               "cost_basis": 100.0}
        pv = compute_position_value(pos, 60.0, "BUFF", "A", "2026-07-21T00:00:00+00:00")
        self.assertEqual(pv.current_value, 120.0)
        self.assertEqual(pv.unrealized_pnl, 20.0)
        self.assertEqual(pv.return_rate, 0.2)

    def test_position_value_missing_price_not_zero(self):
        pos = {"position_id": "p1", "item_uuid": "u1", "quantity": 1,
               "cost_basis": 100.0}
        pv = compute_position_value(pos, None, None, "D", None)
        self.assertIsNone(pv.current_value)   # 缺失为 None 不为 0
        self.assertIsNone(pv.unrealized_pnl)

    def test_aggregate(self):
        p1 = PositionValue("p1", "u1", quantity=1, cost_basis=100.0,
                           current_price=120.0, price_platform="BUFF",
                           current_value=120.0, unrealized_pnl=20.0,
                           return_rate=0.2)
        p2 = PositionValue("p2", "u2", quantity=2, cost_basis=200.0,
                           current_price=90.0, price_platform="UUYP",
                           current_value=180.0, unrealized_pnl=-20.0,
                           return_rate=-0.1)
        v = aggregate([p1, p2], realized_pnl=50.0)
        self.assertEqual(v.total_value, 300.0)
        self.assertEqual(v.total_cost_known, 300.0)
        self.assertEqual(v.unrealized_pnl, 0.0)
        self.assertEqual(v.net_deposit, 300.0)
        # (300 + 50 - 300) / 300 = 0.1667
        self.assertAlmostEqual(v.return_rate, 0.1667, places=3)
        self.assertEqual(v.value_by_platform["BUFF"], 120.0)

    def test_aggregate_no_cost(self):
        v = aggregate([], realized_pnl=0.0)
        self.assertIsNone(v.return_rate)


class TestRisk(unittest.TestCase):
    def test_risk_level_high(self):
        self.assertEqual(
            assess_risk_level(0.20, 0.15, 5000.0, RISK_BANDS), "H")

    def test_risk_level_low(self):
        self.assertEqual(
            assess_risk_level(0.01, 0.01, 10.0, RISK_BANDS), "L")

    def test_risk_level_missing_neutral(self):
        self.assertEqual(
            assess_risk_level(None, None, None, RISK_BANDS), "M")

    def test_position_matrix(self):
        self.assertEqual(suggested_position("L", 0.8, MATRIX), 0.15)
        self.assertEqual(suggested_position("L", 0.5, MATRIX), 0.08)
        self.assertEqual(suggested_position("H", 0.5, MATRIX), 0.0)
        self.assertEqual(suggested_position("L", 0.3, MATRIX), 0.0)

    def test_exposure_warning(self):
        positions = [{"item_uuid": "a", "value": 600},
                     {"item_uuid": "b", "value": 400}]
        warnings = check_exposure(positions, max_per_item=0.5)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].item_uuid, "a")

    def test_drawdown_breach(self):
        breach, mdd = drawdown_breach([100, 80, 90], threshold=0.15)
        self.assertTrue(breach)
        self.assertEqual(mdd, 0.2)
        breach, _ = drawdown_breach([100, 95, 98], threshold=0.15)
        self.assertFalse(breach)


if __name__ == "__main__":
    unittest.main()
