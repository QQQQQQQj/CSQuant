"""正确性修复回归测试（先于修复编写，验证 P0/P2/P3 全部要求）。

约束：临时/内存数据库、Mock Provider、Mock Notifier，不触真实 API/QQ/数据库。
"""
import unittest

from database.dao import Dao
from database.db import connect, init_schema

NOW = "2026-07-28T00:30:00+00:00"   # 冻结时钟（距最后一根tick 30分钟，新鲜）


def make_dao() -> Dao:
    conn = connect(":memory:")
    init_schema(conn)
    return Dao(conn)


def seed_item(dao, name="AK-47 | Redline (Field-Tested)", buff_id=1):
    dao.upsert_item_master({"market_hash_name": name, "buff_id": buff_id})
    dao.commit()
    return dao.get_item_by_hash_name(name)


def make_series(n=28, base=None, last_overrides=None, null_second_last_key=None):
    import random
    rng = random.Random(7)
    base = base or {"buy_count": 100, "sell_count": 200,
                    "buy_price": 90.0, "sell_price": 100.0, "volume_24h": 30}
    rows = []
    for i in range(n):
        rows.append({
            "ts": f"2026-07-{1 + i:02d}T00:00:00+00:00",
            "buy_count": int(base["buy_count"] * (1 + rng.uniform(-0.06, 0.06))),
            "sell_count": int(base["sell_count"] * (1 + rng.uniform(-0.06, 0.06))),
            "buy_price": round(base["buy_price"] * (1 + rng.uniform(-0.02, 0.02)), 2),
            "sell_price": round(base["sell_price"] * (1 + rng.uniform(-0.02, 0.02)), 2),
            "volume_24h": int(base["volume_24h"] * (1 + rng.uniform(-0.1, 0.1))),
            "data_quality": "A"})
    if null_second_last_key:
        rows[-2][null_second_last_key] = None
    if last_overrides:
        rows[-1].update(last_overrides)
    return rows


# =============== R1/R2: 信号引擎数据质量 ===============

class TestR1R2SignalQuality(unittest.TestCase):
    CFG = {"model_version": "v-test",
           "blend": {"item": 0.6, "market": 0.4},
           "signal_thresholds": {"buy": 75.0, "sell": 45.0, "min_confidence": 0.4},
           "confidence_weights": {"agreement": 0.45, "data_quality": 0.30,
                                  "sample": 0.25}}

    def _item(self, score, factor_scores):
        from market.market_score import FactorContribution
        from quant.factors.item_score import ItemScoreResult
        factors = [FactorContribution(k, v, 0.125, 0, v is not None)
                   for k, v in factor_scores.items()]
        return ItemScoreResult(score, factors, [])

    def test_r1_mixed_quality_uses_worst(self):
        """["A","D"] 必须按 D 级计算置信度（等于纯 ["D"] 的结果）。"""
        from quant.signals.signal_engine import compute_confidence
        item = self._item(80, {"trend": 80, "volume": 80})
        conf_ad = compute_confidence(item.factors, 80, ["A", "D"], 180,
                                     self.CFG["confidence_weights"], 0)
        conf_d = compute_confidence(item.factors, 80, ["D"], 180,
                                    self.CFG["confidence_weights"], 0)
        conf_a = compute_confidence(item.factors, 80, ["A"], 180,
                                    self.CFG["confidence_weights"], 0)
        self.assertEqual(conf_ad, conf_d)
        self.assertLess(conf_ad, conf_a)

    def test_r2_all_d_quality_cannot_buy(self):
        """全 D 级数据无论分数多高都不得产生 BUY。"""
        from quant.signals.signal_engine import generate_signal
        item = self._item(95, {"trend": 95, "supply_demand": 95, "volume": 95})
        sig = generate_signal(item, None, self.CFG, ["D", "D"], 200)
        self.assertNotEqual(sig.signal, "BUY")

    def test_r2b_critical_missing_cannot_buy(self):
        """关键因子缺失时禁止 BUY（即使 confidence 恰好等于门槛）。"""
        from market.market_score import FactorContribution
        from quant.factors.item_score import ItemScoreResult
        from quant.signals.signal_engine import generate_signal
        factors = [FactorContribution("volume", 95, 1.0, 95, True)]
        item = ItemScoreResult(95, factors, ["trend", "supply_demand"])
        sig = generate_signal(item, None, self.CFG, ["A"], 200)
        self.assertNotEqual(sig.signal, "BUY")


# =============== R3/R4: 异动评分重构 ===============

class TestR3R4AnomalyScoring(unittest.TestCase):
    def test_r3_sync_alone_not_high_anomaly(self):
        """只有跨平台一致、没有任何异常变化时 AnomalyScore 不得为高分。"""
        from market_anomaly.detectors import build_item_metrics
        series = make_series()   # 平稳序列，无显著变化
        m = build_item_metrics(
            {"item_uuid": "u1", "market_hash_name": "X", "name_zh": None},
            series, [100.0, 100.1, 99.9], 0.0, now=NOW)   # 完美一致
        self.assertLess(m.anomaly_score, 20)

    def test_r4_small_base_not_scored(self):
        """求购 2→4（+100%）不得计入异常分。"""
        from market_anomaly.detectors import build_item_metrics
        series = make_series(base={"buy_count": 2, "sell_count": 200,
                                   "buy_price": 90.0, "sell_price": 100.0,
                                   "volume_24h": 30},
                             last_overrides={"buy_count": 4})
        m = build_item_metrics(
            {"item_uuid": "u1", "market_hash_name": "X", "name_zh": None},
            series, [100.0], 0.0, now=NOW)
        d = m.deltas["buy_count"]
        self.assertFalse(d.significant)
        # buy_count 不显著 → 不进异常分（其余指标平稳 → 总分低）
        self.assertLess(m.anomaly_score, 20)

    def test_r4b_null_recovery_not_scored(self):
        """某指标 NULL 恢复时该指标本轮不得计分。"""
        from market_anomaly.detectors import compute_deltas
        series = make_series(last_overrides={"sell_count": 900},
                             null_second_last_key="sell_count")
        deltas = compute_deltas(series)
        self.assertTrue(deltas["sell_count"].null_recovered)
        self.assertIsNone(deltas["sell_count"].zscore)

    def test_stale_data_hard_gate(self):
        """数据过期必须硬门控：整件跳过评分而非轻微扣分。"""
        from market_anomaly.service import MarketAnomalyService
        dao = make_dao()
        item = seed_item(dao)
        dao.upsert_snapshot({"item_uuid": item["item_uuid"], "platform": "BUFF",
                             "source": "mock", "sell_price": 100.0,
                             "sell_count": 200, "buy_count": 100,
                             "volume_24h": 40})
        for row in make_series(last_overrides={"buy_count": 400}):
            dao.insert_tick({"item_uuid": item["item_uuid"], "platform": "BUFF",
                             "source": "mock", **{k: row[k] for k in (
                                 "ts", "sell_price", "buy_price", "sell_count",
                                 "buy_count", "volume_24h")}})
        dao.commit()
        svc = MarketAnomalyService(dao, {"model_version": "v-test",
                                         "market_anomaly": {"stale_sec": 3600}})
        # 最新 tick 是 2026-07-28，用远未来时钟 → 全部过期 → 硬跳过
        report = svc.detect_round(now="2026-08-30T00:00:00+00:00")
        self.assertEqual(report["scored"], 0)
        self.assertGreaterEqual(report.get("skipped_stale", 0), 1)

    def test_clock_injection_deterministic(self):
        """数据质量分必须可用注入时钟复现（不依赖真实当前时间）。"""
        from market_anomaly.detectors import data_quality_score
        series = make_series()
        s1 = data_quality_score(series, series[-1], now=NOW)
        s2 = data_quality_score(series, series[-1], now=NOW)
        self.assertEqual(s1, s2)
        stale = data_quality_score(series, series[-1],
                                   now="2027-01-01T00:00:00+00:00")
        self.assertLess(stale, s1)


# =============== R5/R6/R7: 回测正确性 ===============

class TestR5R6R7Backtest(unittest.TestCase):
    def test_r5_multi_item_different_dates_strict_t_plus_1(self):
        """稀疏日期饰品的 t 信号必须在其自身下一可交易日成交，不得错位。"""
        from quant.backtest.engine import Bar, run_backtest
        # A: 1-10 号连续；B: 只有 3/5/7/9 号
        bars_a = [Bar(ts=f"2026-01-{d:02d}", close=100 + d, bid=99.0 + d,
                      ask=101.0 + d, volume=100) for d in range(1, 11)]
        b_days = [3, 5, 7, 9]
        bars_b = [Bar(ts=f"2026-01-{d:02d}", close=200.0, bid=199.0,
                      ask=201.0, volume=100) for d in b_days]
        entries = {"A": [False] * 10, "B": [True, False, False, False]}
        exits = {"A": [False] * 10, "B": [False] * 4}
        cfg = {"init_cash": 100000.0, "max_position_per_item": 0.5,
               "liquidity_filter_min_volume": 3,
               "slippage_bands": {"high_liquidity": 0.005,
                                  "mid_liquidity": 0.01, "low_liquidity": 0.03}}
        result = run_backtest({"A": bars_a, "B": bars_b}, entries, exits,
                              cfg, {"BUFF": {"rate": 0.025}})
        b_trades = [t for t in result.trades if t.item_uuid == "B"]
        self.assertEqual(len(b_trades), 1)
        # B 的 bar0（1-03）信号 → B 自身下一交易日 1-05 成交（不是全局的 1-04）
        self.assertEqual(b_trades[0].ts, "2026-01-05")

    def _seed_backtest_db(self):
        dao = make_dao()
        item = seed_item(dao)
        dao.add_watchlist(item["item_uuid"], tier="L1")
        import math
        for i in range(120):
            c = 85 + i * 0.1 + math.sin(i / 5) * 2
            dao.upsert_kline({"item_uuid": item["item_uuid"], "platform": "BUFF",
                              "period": "1d",
                              "ts": f"2026-{3 + i // 30:02d}-{1 + i % 30:02d}T00:00:00+00:00",
                              "open": c, "close": c, "high": c * 1.01,
                              "low": c * 0.99, "volume": 50, "source": "mock"})
        # 大盘 benchmark（同期）
        for i in range(120):
            dao.insert_market_index({
                "ts": f"2026-{3 + i // 30:02d}-{1 + i % 30:02d}T00:00:00+00:00",
                "index_name": "csqaq_main", "index_value": 2000 + i,
                "source": "mock"})
        dao.commit()
        return dao

    def test_r6_params_change_result(self):
        """params 必须真正参与执行：改变参数应改变回测结果。"""
        from services.backtest_service import BacktestService
        dao = self._seed_backtest_db()
        cfg = {"model_version": "v-test",
               "backtest": {"init_cash": 100000.0,
                            "liquidity_filter_min_volume": 3,
                            "max_position_per_item": 0.5},
               "slippage_bands": {"high_liquidity": 0.005,
                                  "mid_liquidity": 0.01, "low_liquidity": 0.03},
               "fee_table": {"BUFF": {"rate": 0.025}}}
        svc = BacktestService(dao, cfg)
        r_default = svc.run_backtest()
        r_fast = svc.run_backtest(params={"fast": 5, "slow": 15})
        self.assertNotIn("error", r_default)
        key_metrics = ("trade_count", "total_return", "turnover")
        self.assertTrue(any(r_default.get(k) != r_fast.get(k)
                            for k in key_metrics),
                        f"params 未生效: {r_default} vs {r_fast}")

    def test_r7_benchmark_and_excess_nonnull(self):
        """benchmark_return 与 excess_return 必须非空。"""
        from services.backtest_service import BacktestService
        dao = self._seed_backtest_db()
        cfg = {"model_version": "v-test",
               "backtest": {"init_cash": 100000.0,
                            "liquidity_filter_min_volume": 3,
                            "max_position_per_item": 0.5},
               "slippage_bands": {"high_liquidity": 0.005,
                                  "mid_liquidity": 0.01, "low_liquidity": 0.03},
               "fee_table": {"BUFF": {"rate": 0.025}}}
        result = BacktestService(dao, cfg).run_backtest()
        self.assertIsNotNone(result.get("benchmark_return"))
        self.assertIsNotNone(result.get("excess_return"))

    def test_no_fake_volume(self):
        """缺失成交量不得伪造成 10：volume=None 的 bar 不可交易。"""
        from services.backtest_service import build_bars
        bars = build_bars([{"ts": "2026-01-01T00:00:00+00:00", "close": 100.0,
                            "volume": None}])
        self.assertIsNone(bars[0].volume)

    def test_annualized_uses_calendar_span(self):
        """年化必须按真实日历跨度：每周一根 K 线（10根跨 63 天）而非 10 天。"""
        from datetime import date, timedelta
        from quant.backtest.engine import compute_stats
        equity = [10000.0 * (1.01 ** i) for i in range(10)]
        start = date(2026, 1, 1)
        dates = [(start + timedelta(days=i * 7)).isoformat() for i in range(10)]
        stats = compute_stats(equity, [], 10000.0, 0, 0, 0, dates=dates)
        total = equity[-1] / 10000.0 - 1
        span_days = 63   # 9 个周间隔
        expected = (1 + total) ** (365 / span_days) - 1
        self.assertEqual(stats["annualization_basis_days"], span_days)
        self.assertAlmostEqual(stats["annualized_return"], round(expected, 4),
                               places=3)


# =============== R8: 播报幂等 ===============

class TestR8BroadcastIdempotency(unittest.TestCase):
    class FlakyNotifier:
        router = "market"

        def __init__(self, dao, fail_first=False):
            self.dao = dao
            self.fail = fail_first
            self.sent = 0

        @property
        def configured(self):
            return True

        @property
        def group_id(self):
            return "g_mkt"

        def send(self, message, message_type="text", ref_id=None):
            ok = not self.fail
            self.fail = False
            if ok:
                self.sent += 1
            self.dao.insert_broadcast_log({
                "router": self.router, "target_qq_group": self.group_id,
                "message_type": message_type, "ref_id": ref_id,
                "message": message, "pushed": 1 if ok else 0,
                "error": None if ok else "mock_fail"})
            self.dao.commit()
            return ok, None if ok else "mock_fail"

    def _emit_event(self, item_uuid="u1", event_id="e1", reason="NEW"):
        from market_anomaly.universe import UniverseEntry
        from market_anomaly.detectors import ItemMetrics
        entry = UniverseEntry(item_uuid, "X", None, None, "A", 80.0,
                              100.0, 200, 100, 50)
        m = ItemMetrics(item_uuid, "X", None, 28)
        return {"entry": entry, "metrics": m,
                "scores": {"anomaly": 95, "accumulation": 80, "distribution": 10,
                           "order_flow": 50, "liquidity": 80,
                           "data_quality": 80, "confidence": 0.8},
                "signal": "STRONG BUY", "reasons": [],
                "event_type": "accumulation", "emit_reason": reason,
                "event_fields": {"event_id": event_id, "state": reason},
                "deltas": {}}

    def test_same_event_not_rebroadcast(self):
        from market_anomaly.bot_service import MarketBotService
        dao = make_dao()
        notifier = self.FlakyNotifier(dao)
        bot = MarketBotService(dao, {"market_anomaly": {"cooldown_min": 0}},
                               notifier)
        ev = self._emit_event()
        bot.dispatch([ev])
        bot.dispatch([ev])   # 同 event_id+reason，冷却为0也不得重发
        self.assertEqual(notifier.sent, 1)

    def test_failed_send_not_cooled(self):
        """发送失败不得被当作成功冷却：下一轮必须重试。"""
        from market_anomaly.bot_service import MarketBotService
        dao = make_dao()
        notifier = self.FlakyNotifier(dao, fail_first=True)
        bot = MarketBotService(dao, {"market_anomaly": {"cooldown_min": 120}},
                               notifier)
        ev = self._emit_event()
        r1 = bot.dispatch([ev])
        self.assertEqual(r1["sent"], 0)
        r2 = bot.dispatch([ev])   # 失败未冷却/未去重 → 重试成功
        self.assertEqual(r2["sent"], 1)

    def test_alert_failed_send_not_cooled(self):
        """库存告警：pushed=0 的记录不得进入冷却。"""
        from alerter.service import AlertService
        dao = make_dao()
        item = seed_item(dao)
        dao.add_watchlist(item["item_uuid"], tier="L1")
        dao.insert_tick({"item_uuid": item["item_uuid"], "platform": "BUFF",
                         "source": "mock", "sell_price": 100.0,
                         "ts": "2026-07-22T01:00:00+00:00"})
        dao.insert_tick({"item_uuid": item["item_uuid"], "platform": "BUFF",
                         "source": "mock", "sell_price": 110.0,
                         "ts": "2026-07-22T02:00:00+00:00"})
        dao.commit()
        svc = AlertService(dao, notifier=None)   # 推送必然失败
        svc.evaluate_all()
        report2 = svc.evaluate_all()             # 失败不冷却 → 再次触发
        self.assertGreaterEqual(report2["triggered"], 1)
        self.assertEqual(report2.get("skipped_cooldown", 0), 0)


# =============== R9: 健康记录接线 ===============

class TestR9HealthWiring(unittest.TestCase):
    def test_mock_collect_writes_health(self):
        from data_sources.http_client import RateLimitedClient
        from services.datasource_service import make_health_recorder
        dao = make_dao()

        class FakeResp:
            status_code = 200
            def json(self):
                return {"ok": True}
            def raise_for_status(self):
                pass

        class FakeSession:
            def request(self, *a, **kw):
                return FakeResp()

        client = RateLimitedClient("mocksrc", rate_per_sec=100,
                                   on_result=make_health_recorder(dao),
                                   session=FakeSession())
        client.get("https://mock.example/api/quote")
        rows = dao.get_health_latest()
        self.assertTrue(any(r["source"] == "mocksrc" for r in rows))


# =============== R10: 全市场采集不依赖 watchlist ===============

class TestR10MarketScan(unittest.TestCase):
    def test_scan_covers_non_watchlist_items(self):
        from data_sources.base import ItemRef, UnifiedQuote
        from data_sources.unified.market_scan import MarketScanService
        from data_sources.unified.service import UnifiedMarketService
        dao = make_dao()
        # 3 件仅存在于 item_master（无 watchlist、无快照）
        names = [f"Item {i} (Field-Tested)" for i in range(3)]
        for i, n in enumerate(names):
            dao.upsert_item_master({"market_hash_name": n, "buff_id": 100 + i})
        dao.commit()

        class MockBatchProvider:
            name = "mock"
            def get_batch_quotes(self, refs):
                return {r.market_hash_name: [UnifiedQuote(
                    market_hash_name=r.market_hash_name, item_uuid=r.item_uuid,
                    platform="BUFF", source="mock", sell_price=50.0,
                    sell_count=100, buy_price=48.0, buy_count=60,
                    volume_24h=20)] for r in refs}

        unified = UnifiedMarketService(dao, primary=None)
        scan = MarketScanService(dao, unified, MockBatchProvider(),
                                 {"market_anomaly": {}})
        report = scan.scan_tick()
        self.assertGreaterEqual(report["collected"], 3)
        self.assertEqual(len(dao.get_watchlist()), 0)   # 确未依赖 watchlist
        # 快照落库后 universe 亦可建立
        from market_anomaly.service import MarketAnomalyService
        ma = MarketAnomalyService(dao, {"model_version": "v",
                                        "market_anomaly": {"universe": {
                                            "min_sell_count": 1,
                                            "min_buy_count": 1,
                                            "min_price": 1.0,
                                            "min_liquidity_score": 0}}})
        self.assertGreaterEqual(ma.refresh_universe(), 3)

    def test_batch_size_limit(self):
        """单批不得超过接口限制（100）。"""
        from data_sources.base import UnifiedQuote
        from data_sources.unified.market_scan import MarketScanService
        from data_sources.unified.service import UnifiedMarketService
        dao = make_dao()
        for i in range(250):
            dao.upsert_item_master({"market_hash_name": f"I{i} (FT)",
                                    "buff_id": i + 1})
        dao.commit()
        batch_sizes = []

        class MockProvider:
            name = "mock"
            def get_batch_quotes(self, refs):
                batch_sizes.append(len(refs))
                return {}

        unified = UnifiedMarketService(dao, primary=None)
        scan = MarketScanService(dao, unified, MockProvider(),
                                 {"market_anomaly": {"scan_batch_size": 100,
                                                     "scan_max_batches": 5}})
        scan.scan_tick()
        self.assertTrue(batch_sizes)
        self.assertTrue(all(s <= 100 for s in batch_sizes))


# =============== R11: 净注资 ===============

class TestR11NetDeposit(unittest.TestCase):
    def test_net_deposit_not_zero_after_sell(self):
        from inventory.portfolio import add_manual_position, record_sell
        from inventory.valuation import valuate_portfolio
        dao = make_dao()
        item = seed_item(dao)
        pid = add_manual_position(dao, item["market_hash_name"], 2, 50.0,
                                  buy_platform="BUFF")
        v_before = valuate_portfolio(dao)
        self.assertEqual(v_before.net_deposit, 100.0)
        record_sell(dao, pid, 60.0, fee=1.0, platform="BUFF")
        v_after = valuate_portfolio(dao)
        # 卖出后投入资本不归零（历史买入 100 仍是投入）
        self.assertEqual(v_after.net_deposit, 100.0)
        self.assertGreater(v_after.realized_pnl, 0)

    def test_explicit_cash_flow_overrides(self):
        from inventory.valuation import valuate_portfolio
        dao = make_dao()
        dao.insert_cash_flow("DEPOSIT", 1000.0, "初始注资")
        dao.insert_cash_flow("WITHDRAWAL", 200.0, "部分提现")
        dao.commit()
        v = valuate_portfolio(dao)
        self.assertEqual(v.net_deposit, 800.0)

    def test_cash_flow_validation(self):
        dao = make_dao()
        with self.assertRaises(ValueError):
            dao.insert_cash_flow("DEPOSIT", -5.0)
        with self.assertRaises(ValueError):
            dao.insert_cash_flow("INVALID", 10.0)


# =============== P3: 快照与配置版本 ===============

class TestSnapshotAndConfigVersion(unittest.TestCase):
    def test_signal_snapshot_immutable_payload(self):
        dao = make_dao()
        payload = {"quotes": [{"platform": "BUFF", "sell_price": 100.0}],
                   "item_factors": [], "weights": {"trend": 0.2},
                   "model_version": "v1"}
        sid = dao.insert_signal_snapshot(payload)
        loaded = dao.get_signal_snapshot(sid)
        self.assertEqual(loaded["quotes"][0]["sell_price"], 100.0)
        self.assertEqual(loaded["model_version"], "v1")

    def test_config_version_auto_generated(self):
        from utils.config import Config
        cfg = Config()
        self.assertIn("+", cfg.config_version)
        self.assertTrue(cfg.config_version.startswith(cfg.model_version))

    def test_trade_validation(self):
        dao = make_dao()
        item = seed_item(dao)
        with self.assertRaises(ValueError):
            dao.insert_trade({"item_uuid": item["item_uuid"], "side": "BUY",
                              "quantity": 0, "price": 10.0})
        with self.assertRaises(ValueError):
            dao.insert_trade({"item_uuid": item["item_uuid"], "side": "BUY",
                              "quantity": 1, "price": -1.0})
        with self.assertRaises(ValueError):
            dao.add_position({"item_uuid": item["item_uuid"], "quantity": -2})


# =============== SteamID 校验转换 ===============

class TestSteamId(unittest.TestCase):
    def test_convert_32_to_64(self):
        from utils.steamid import normalize_steam_id
        self.assertEqual(normalize_steam_id("7904810036"), "76561205865075764")
        self.assertEqual(normalize_steam_id("76561205865075764"),
                         "76561205865075764")

    def test_invalid_rejected(self):
        from utils.steamid import normalize_steam_id
        for bad in ("", "abc", "1234567890123456789012"):
            with self.assertRaises(ValueError):
                normalize_steam_id(bad)


if __name__ == "__main__":
    unittest.main()
