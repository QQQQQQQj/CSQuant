"""全市场异动监控测试：异动检测/双score/信号/事件生命周期/双QQ路由/门控。

覆盖指示文档 §31 核心场景：求购激增/减少、在售激增/减少、多平台一致/冲突、
小基数、NULL恢复、低流动性排除、消息去重/升级、双QQ不串群、门控降级。
"""
import random
import unittest

from alerter.notifiers import MarketNotifier, build_notifiers
from database.dao import Dao
from database.db import connect, init_schema
from market_anomaly.accumulation import accumulation_score
from market_anomaly.baseline import (
    null_recovered, significant_change, zscore_of, series_stats,
)
from market_anomaly.bot_service import MarketBotService, build_market_message
from market_anomaly.detectors import (
    build_item_metrics, compute_deltas, cross_platform_sync, order_flow_score,
)
from market_anomaly.distribution import distribution_score
from market_anomaly.event_lifecycle import event_step, severity_of
from market_anomaly.service import MarketAnomalyService
from market_anomaly.signal_engine import decide, passes_broadcast_gate
from market_anomaly.universe import build_universe, passes_filter

CFG = {"model_version": "v-test", "market_anomaly": {"cooldown_min": 120,
                                                     "top_interval_min": 60}}
FROZEN_NOW = "2026-07-28T00:30:00+00:00"   # 注入时钟：距最后一根tick 30分钟


def make_dao() -> Dao:
    conn = connect(":memory:")
    init_schema(conn)
    return Dao(conn)


def make_series(n=28, seed=7, base=None, last_overrides=None,
                null_second_last=False):
    """构造 n 个带轻微波动的 tick 序列；last_overrides 突变最后一点。"""
    rng = random.Random(seed)
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
    if null_second_last:
        rows[-2]["sell_count"] = None
    if last_overrides:
        rows[-1].update(last_overrides)
    return rows


ITEM = {"item_uuid": "u1", "market_hash_name": "AK-47 | Redline (Field-Tested)",
        "name_zh": "AK-47 | 红线 （久经沙场）"}


class TestBaseline(unittest.TestCase):
    def test_small_base_change_not_significant(self):
        # 求购 2→4 的 +100% 不显著（指示文档 §九）
        sig, abs_c, pct_c = significant_change(2, 4)
        self.assertFalse(sig)

    def test_large_base_change_significant(self):
        sig, _, pct_c = significant_change(1000, 1700)
        self.assertTrue(sig)
        self.assertAlmostEqual(pct_c, 70.0)

    def test_zscore_needs_samples(self):
        stats = series_stats([1.0, 2.0, 3.0])
        self.assertIsNone(zscore_of(10.0, stats, min_samples=8))

    def test_null_recovered(self):
        self.assertTrue(null_recovered([1.0, None, 5.0]))
        self.assertFalse(null_recovered([1.0, 2.0, 3.0]))


class TestDetectors(unittest.TestCase):
    def test_buy_count_surge_detected(self):
        series = make_series(last_overrides={"buy_count": 400, "volume_24h": 120})
        deltas = compute_deltas(series)
        self.assertTrue(deltas["buy_count"].significant)
        self.assertGreater(deltas["buy_count"].pct_change, 100)
        self.assertIsNotNone(deltas["buy_count"].zscore)
        self.assertGreater(deltas["buy_count"].zscore, 3)

    def test_sell_count_drop_detected(self):
        series = make_series(last_overrides={"sell_count": 60})
        deltas = compute_deltas(series)
        self.assertLess(deltas["sell_count"].zscore, -3)

    def test_cross_platform_sync(self):
        self.assertGreater(cross_platform_sync([100, 101, 99.5]), 0.9)
        self.assertLess(cross_platform_sync([100, 130]), 0.5)
        self.assertIsNone(cross_platform_sync([100]))     # 单平台
        self.assertIsNone(cross_platform_sync([100, None]))

    def test_order_flow_buyer_dominant(self):
        series = make_series(last_overrides={
            "buy_count": 500, "sell_count": 80, "buy_price": 99.0})
        deltas = compute_deltas(series)
        score = order_flow_score(series[-1], deltas)
        self.assertGreater(score, 40)   # 买方优势

    def test_order_flow_seller_dominant(self):
        series = make_series(last_overrides={
            "buy_count": 20, "sell_count": 600, "buy_price": 80.0})
        deltas = compute_deltas(series)
        score = order_flow_score(series[-1], deltas)
        self.assertLess(score, -20)


class TestAccumulationDistribution(unittest.TestCase):
    def test_accumulation_pattern(self):
        # 建仓形态：求购激增+买价抬升+在售减少+量放大+价格温和
        series = make_series(last_overrides={
            "buy_count": 400, "buy_price": 98.0, "sell_count": 80,
            "volume_24h": 150, "sell_price": 101.0})
        m = build_item_metrics(ITEM, series, [101.0, 100.5, 102.0], 0.01,
                               now=FROZEN_NOW)
        acc, parts = accumulation_score(m)
        self.assertGreater(acc, 60)
        self.assertEqual(parts["price_mild"], 100.0)

    def test_distribution_pattern(self):
        # 出货形态：在售暴增+求购减少+买价下移+冲高回落
        series = make_series(last_overrides={
            "buy_count": 30, "buy_price": 80.0, "sell_count": 700,
            "volume_24h": 200, "sell_price": 93.0})
        series[-5]["sell_price"] = 112.0  # 前期冲高
        m = build_item_metrics(ITEM, series, [93.0, 92.0], 0.01,
                               now=FROZEN_NOW)
        dist, parts = distribution_score(m, series)
        self.assertGreater(dist, 60)
        self.assertEqual(parts["price_reversal"], 100.0)


class TestSignalEngine(unittest.TestCase):
    def test_strong_buy(self):
        sig, _ = decide({"anomaly": 80, "accumulation": 80, "distribution": 10,
                         "order_flow": 50, "liquidity": 80,
                         "data_quality": 80, "confidence": 0.8})
        self.assertEqual(sig, "STRONG BUY")

    def test_buy_downgraded_on_low_quality(self):
        # 门控：低数据质量禁止 BUY → 降 WATCH（指示文档 §十八）
        sig, _ = decide({"anomaly": 80, "accumulation": 80, "distribution": 10,
                         "order_flow": 50, "liquidity": 30,
                         "data_quality": 80, "confidence": 0.8})
        self.assertEqual(sig, "WATCH")

    def test_risk_alert(self):
        sig, _ = decide({"anomaly": 90, "accumulation": 10, "distribution": 80,
                         "order_flow": -50, "liquidity": 80,
                         "data_quality": 80, "confidence": 0.8})
        self.assertEqual(sig, "RISK ALERT")

    def test_sell(self):
        sig, _ = decide({"anomaly": 70, "accumulation": 10, "distribution": 80,
                         "order_flow": -50, "liquidity": 80,
                         "data_quality": 80, "confidence": 0.8})
        self.assertEqual(sig, "SELL")

    def test_broadcast_gate(self):
        ok, _ = passes_broadcast_gate({"anomaly": 80, "confidence": 0.8,
                                       "liquidity": 70})
        self.assertTrue(ok)
        ok, _ = passes_broadcast_gate({"anomaly": 80, "confidence": 0.5,
                                       "liquidity": 70})
        self.assertFalse(ok)
        ok, _ = passes_broadcast_gate({"anomaly": 95, "confidence": 0.1,
                                       "liquidity": 1})
        self.assertTrue(ok)   # 重大异动直发


class TestUniverse(unittest.TestCase):
    def test_filter_and_tier(self):
        candidates = [
            {"item_uuid": "a", "market_hash_name": "A", "sell_price": 100,
             "sell_count": 300, "buy_count": 200, "volume_24h": 80},   # Tier A
            {"item_uuid": "b", "market_hash_name": "B", "sell_price": 50,
             "sell_count": 150, "buy_count": 100, "volume_24h": 30},   # Tier B
            {"item_uuid": "c", "market_hash_name": "C", "sell_price": 10,
             "sell_count": 1, "buy_count": 0, "volume_24h": 0},        # 排除
            {"item_uuid": "d", "market_hash_name": "D", "sell_price": None,
             "sell_count": 100, "buy_count": 100, "volume_24h": 10},   # 排除(无价)
        ]
        universe = build_universe(candidates)
        tiers = {u.item_uuid: u.tier for u in universe}
        self.assertIn("a", tiers)
        self.assertIn("b", tiers)
        self.assertNotIn("c", tiers)   # 低流动性排除
        self.assertNotIn("d", tiers)
        self.assertEqual(tiers["a"], "A")

    def test_passes_filter_null_not_zero(self):
        # 缺失字段按不满足处理，绝不置 0 通过
        self.assertFalse(passes_filter({"sell_count": None, "buy_count": 100,
                                        "sell_price": 100}))


class TestEventLifecycle(unittest.TestCase):
    def test_severity_bands(self):
        self.assertEqual(severity_of(95), "CRITICAL")
        self.assertEqual(severity_of(88), "MAJOR")
        self.assertEqual(severity_of(80), "MID")
        self.assertEqual(severity_of(65), "WATCH")
        self.assertEqual(severity_of(30), "NONE")

    def test_lifecycle_flow(self):
        now = "2026-07-22T00:00:00+00:00"
        # NEW
        step = event_step(None, "accumulation", "u1", 80, now)
        self.assertEqual(step["action"], "emit")
        self.assertEqual(step["event_fields"]["state"], "NEW")
        open_ev = step["event_fields"]
        # ONGOING 同级 → silent（去重）
        step = event_step(open_ev, "accumulation", "u1", 81, now)
        self.assertEqual(step["action"], "silent")
        # UPGRADED 等级提升 → emit
        step = event_step(open_ev, "accumulation", "u1", 90, now)
        self.assertEqual(step["emit_reason"], "UPGRADED")
        self.assertEqual(step["event_fields"]["severity"], "MAJOR")
        # RESOLVED 消退 → emit
        step = event_step(step["event_fields"], "accumulation", "u1", 20, now)
        self.assertEqual(step["emit_reason"], "RESOLVED")

    def test_no_event_for_normal(self):
        self.assertIsNone(event_step(None, "accumulation", "u1", 30,
                                     "2026-07-22T00:00:00+00:00"))


class MockMarketNotifier(MarketNotifier):
    def __init__(self, dao=None):
        super().__init__(None, dao)
        self.sent = []

    @property
    def configured(self):
        return True

    @property
    def group_id(self):
        return "market_group_A"

    def send(self, message, message_type="text", ref_id=None):
        self.sent.append({"message": message, "router": self.router,
                          "ref_id": ref_id, "group": self.group_id})
        if self.dao:
            self.dao.insert_broadcast_log({
                "router": self.router, "target_qq_group": self.group_id,
                "message_type": message_type, "ref_id": ref_id,
                "message": message, "pushed": 1, "error": None})
            self.dao.commit()
        return True, None


class TestDualQQRouting(unittest.TestCase):
    def test_inventory_fallback_market_fail_closed(self):
        class FakeCfg:
            def __init__(self, env):
                self.env = env

            def get(self, key, default=None):
                return self.env.get(key, default)

        # 只有老配置：inventory 回退，market fail-closed（绝不串群）
        inv, mkt = build_notifiers(FakeCfg({"QQ_GROUP_ID": "inv_group"}), None)
        self.assertEqual(inv.group_id, "inv_group")          # 回退兼容
        self.assertFalse(mkt.configured)                     # fail-closed
        self.assertIsNone(mkt.group_id)                      # 拿不到库存群号
        # 双配置齐全：两通道群号必须不同且各自独立
        inv2, mkt2 = build_notifiers(
            FakeCfg({"INVENTORY_QQ_GROUP_ID": "g_inv",
                     "MARKET_QQ_GROUP_ID": "g_mkt"}), None)
        self.assertEqual(inv2.group_id, "g_inv")
        self.assertEqual(mkt2.group_id, "g_mkt")
        self.assertNotEqual(inv2.group_id, mkt2.group_id)

    def test_broadcast_log_router_separated(self):
        dao = make_dao()
        notifier = MockMarketNotifier(dao)
        notifier.send("全市场消息", ref_id="u1")
        logs = dao.get_broadcast_logs("market")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["target_qq_group"], "market_group_A")
        self.assertEqual(dao.get_broadcast_logs("inventory"), [])


class TestServiceIntegration(unittest.TestCase):
    def _seed(self, dao, last_overrides):
        dao.upsert_item_master({"market_hash_name": ITEM["market_hash_name"],
                                "name_zh": ITEM["name_zh"]})
        dao.commit()
        item = dao.get_item_by_hash_name(ITEM["market_hash_name"])
        dao.upsert_snapshot({"item_uuid": item["item_uuid"], "platform": "BUFF",
                             "source": "mock", "sell_price": 100.0,
                             "sell_count": 200, "buy_count": 100,
                             "volume_24h": 40})
        for row in make_series(last_overrides=last_overrides):
            dao.insert_tick({"item_uuid": item["item_uuid"], "platform": "BUFF",
                             "source": "mock", **{k: row[k] for k in (
                                 "ts", "sell_price", "buy_price", "sell_count",
                                 "buy_count", "volume_24h")}})
        dao.commit()
        return item

    def test_full_pipeline_accumulation(self):
        dao = make_dao()
        self._seed(dao, {"buy_count": 400, "buy_price": 98.0,
                         "sell_count": 80, "volume_24h": 150,
                         "sell_price": 101.0})
        svc = MarketAnomalyService(dao, CFG)
        self.assertEqual(svc.refresh_universe(), 1)
        report = svc.detect_round(market_momentum=0.01, now=FROZEN_NOW)
        self.assertEqual(report["scored"], 1)
        self.assertGreaterEqual(report["signals"], 1)
        # 异动落库
        anomalies = dao.get_latest_anomalies()
        self.assertEqual(len(anomalies), 1)
        self.assertGreater(anomalies[0]["accumulation_score"], 60)
        # 事件已创建且为 NEW
        events = dao.get_signal_events()
        self.assertTrue(events)
        # 播报（mock notifier）
        bot = MarketBotService(dao, CFG, MockMarketNotifier(dao))
        dispatch = bot.dispatch(report["emit_events"])
        self.assertGreaterEqual(dispatch["sent"], 1)
        # 幂等/冷却：同饰品同事件立刻再发被拦（去重或冷却均可）
        dispatch2 = bot.dispatch(report["emit_events"])
        self.assertGreaterEqual(
            dispatch2.get("cooled", 0) + dispatch2.get("deduped", 0), 1)

    def test_message_content_discipline(self):
        dao = make_dao()
        item = self._seed(dao, {"buy_count": 400, "buy_price": 98.0,
                                "sell_count": 80, "volume_24h": 150,
                                "sell_price": 101.0})
        svc = MarketAnomalyService(dao, CFG)
        report = svc.detect_round(now=FROZEN_NOW)
        ev = report["emit_events"][0]
        msg = build_market_message(ev)
        self.assertIn("疑似建仓", msg)
        self.assertIn("概率性量化推断", msg)   # 表述纪律
        self.assertIn("不构成投资建议", msg)
        self.assertNotIn("盘主", msg)          # 禁止断言式表述

    def test_insufficient_data_skipped(self):
        dao = make_dao()   # 空库：API 断线等价场景
        svc = MarketAnomalyService(dao, CFG)
        report = svc.detect_round()
        self.assertEqual(report["universe"], 0)
        self.assertEqual(report["scored"], 0)


if __name__ == "__main__":
    unittest.main()
