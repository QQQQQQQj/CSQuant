"""回测引擎测试：t+1成交 / 流动性过滤 / 费用 / 统计口径。"""
import unittest

from quant.backtest.engine import Bar, compute_stats, run_backtest

FEE_TABLE = {"BUFF": {"rate": 0.025}, "STEAM": {"rate": 0.15}}
CFG = {"init_cash": 10000.0, "max_position_per_item": 0.5,
       "liquidity_filter_min_volume": 3,
       "slippage_bands": {"high_liquidity": 0.005, "mid_liquidity": 0.01,
                          "low_liquidity": 0.03}}


def make_bars(prices, volume=100, spread=0.01):
    return [Bar(ts=f"2026-01-{i + 1:02d}", close=p,
                bid=round(p * (1 - spread), 4), ask=round(p * (1 + spread), 4),
                volume=volume)
            for i, p in enumerate(prices)]


class TestBacktestEngine(unittest.TestCase):
    def test_buy_sell_roundtrip(self):
        # 10天价格 100->110，第1天 entry 第8天 exit
        bars = make_bars([100 + i for i in range(10)])
        entries = [True] + [False] * 9
        exits = [False] * 8 + [True] + [False]
        result = run_backtest({"u1": bars}, {"u1": entries}, {"u1": exits},
                              CFG, FEE_TABLE)
        self.assertEqual(len(result.trades), 2)
        buy, sell = result.trades
        self.assertEqual(buy.side, "BUY")
        self.assertEqual(buy.ts, "2026-01-02")   # t+1 成交
        self.assertEqual(sell.side, "SELL")
        self.assertEqual(sell.ts, "2026-01-10")
        self.assertGreater(sell.price, buy.price)
        self.assertGreater(result.stats["total_return"], 0)
        self.assertGreater(result.stats["fee_cost"], 0)

    def test_no_trade_on_illiquid(self):
        bars = make_bars([100 + i for i in range(10)], volume=1)  # 低于阈值
        entries = [True] + [False] * 9
        result = run_backtest({"u1": bars}, {"u1": entries}, {"u1": {}},
                              CFG, FEE_TABLE)
        self.assertEqual(len(result.trades), 0)

    def test_position_limit_and_integer_qty(self):
        # 价格 400，max_pos 0.5 -> 预算 5000 -> 12 件（整数）
        bars = make_bars([400] * 10)
        entries = [True] + [False] * 9
        result = run_backtest({"u1": bars}, {"u1": entries}, {"u1": {}},
                              CFG, FEE_TABLE)
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.quantity, int(5000 / trade.price))
        self.assertLessEqual(trade.price * trade.quantity, 10000 * 0.5 + 1)

    def test_stats_flat_equity_no_crash(self):
        stats = compute_stats([10000.0] * 30, [], 10000.0, 0, 0, 0)
        self.assertEqual(stats["total_return"], 0.0)
        self.assertEqual(stats["max_drawdown"], 0.0)
        self.assertIsNone(stats["sharpe"])  # 零波动 -> None 不崩溃
        self.assertIsNone(stats["win_rate"])

    def test_stats_benchmark(self):
        stats = compute_stats([10000.0, 11000.0], [], 10000.0, 0, 0, 0,
                              benchmark_closes=[1000.0, 1050.0])
        self.assertEqual(stats["benchmark_return"], 0.05)


if __name__ == "__main__":
    unittest.main()
