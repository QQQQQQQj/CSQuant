"""技术指标库单元测试（已知输入 -> 已知输出）。"""
import unittest

from quant.factors.indicators import (
    bollinger, daily_returns, ema, macd, max_drawdown, momentum,
    percentile_rank, rsi, sharpe_ratio, sma, sortino_ratio, volatility, zscore,
)


class TestIndicators(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])
        self.assertEqual(sma([1, 2, 3], 5), [None, None, None])

    def test_ema(self):
        result = ema([1, 2, 3], 2)
        self.assertIsNone(result[0])
        self.assertAlmostEqual(result[1], 1.5)
        self.assertAlmostEqual(result[2], 2.5)

    def test_rsi_all_up(self):
        closes = [float(i) for i in range(1, 30)]
        values = rsi(closes, 14)
        self.assertEqual(values[-1], 100.0)  # 只涨不跌 -> RSI 100

    def test_rsi_all_down(self):
        closes = [float(100 - i) for i in range(30)]
        values = rsi(closes, 14)
        self.assertEqual(values[-1], 0.0)

    def test_macd_lengths(self):
        closes = [float(i % 7 + 10) for i in range(60)]
        line, signal, hist = macd(closes)
        self.assertEqual(len(line), len(closes))
        self.assertEqual(len(signal), len(closes))
        self.assertEqual(len(hist), len(closes))
        self.assertIsNotNone(line[-1])

    def test_bollinger_constant(self):
        mid, upper, lower = bollinger([5.0] * 25, 20)
        self.assertEqual(mid[-1], 5.0)
        self.assertAlmostEqual(upper[-1], 5.0)   # 常数序列 std=0
        self.assertAlmostEqual(lower[-1], 5.0)

    def test_momentum(self):
        result = momentum([100.0, 110.0, 121.0], 1)
        self.assertIsNone(result[0])
        self.assertAlmostEqual(result[1], 0.1)
        self.assertAlmostEqual(result[2], 0.1)

    def test_zscore_constant(self):
        self.assertIsNone(zscore([3.0] * 30, 20)[-1])  # sd=0 -> None

    def test_percentile_rank(self):
        self.assertEqual(percentile_rank([1, 2, 3, 4], 3), 0.75)
        self.assertIsNone(percentile_rank([], 3))

    def test_max_drawdown(self):
        self.assertEqual(max_drawdown([100, 80, 90, 60]), 0.4)
        self.assertEqual(max_drawdown([100, 100, 100]), 0.0)  # 零波动 -> 0 非 NaN

    def test_volatility(self):
        self.assertIsNone(volatility([None, None]))
        self.assertIsNone(volatility([0.01]))
        self.assertAlmostEqual(volatility([0.01, -0.01]), (2 * (0.01 ** 2)) ** 0.5)

    def test_sharpe_flat(self):
        # 长期零收益（CS饰品常见）-> None 而非崩溃
        self.assertIsNone(sharpe_ratio([0.0] * 30))

    def test_sortino(self):
        rets = [0.01, -0.02, 0.03, -0.01] * 10
        self.assertIsNotNone(sortino_ratio(rets))
        self.assertIsNone(sortino_ratio([0.01] * 10))  # 无下行 -> None

    def test_daily_returns(self):
        self.assertAlmostEqual(daily_returns([100, 110, 99])[1], 0.1)


if __name__ == "__main__":
    unittest.main()
