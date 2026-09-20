"""告警规则引擎与 QQ 客户端测试。"""
import unittest

from alerter.qq_bot import QQBotClient
from alerter.rules import (
    build_alert_message, current_drawdown, eval_datasource_down,
    eval_nav_drawdown, eval_price_change, eval_regime_change, eval_signal_trigger,
)
from alerter.service import AlertService
from database.dao import Dao
from database.db import connect, init_schema


def make_dao() -> Dao:
    conn = connect(":memory:")
    init_schema(conn)
    return Dao(conn)


class TestRuleFunctions(unittest.TestCase):
    def test_price_change_trigger(self):
        hit, pct = eval_price_change(100.0, 106.0, 5.0)
        self.assertTrue(hit)
        self.assertAlmostEqual(pct, 6.0)

    def test_price_change_below_threshold(self):
        hit, _ = eval_price_change(100.0, 103.0, 5.0)
        self.assertFalse(hit)

    def test_price_change_missing_not_trigger(self):
        self.assertEqual(eval_price_change(None, 100.0, 5.0), (False, None))
        self.assertEqual(eval_price_change(100.0, 0, 5.0), (False, None))

    def test_signal_trigger(self):
        self.assertTrue(eval_signal_trigger({"signal": "BUY", "confidence": 0.7}, 0.6))
        self.assertFalse(eval_signal_trigger({"signal": "HOLD", "confidence": 0.9}, 0.6))
        self.assertFalse(eval_signal_trigger({"signal": "SELL", "confidence": 0.5}, 0.6))

    def test_regime_change(self):
        self.assertTrue(eval_regime_change("neutral", "bullish"))
        self.assertFalse(eval_regime_change("neutral", "neutral"))
        self.assertFalse(eval_regime_change(None, "bullish"))

    def test_nav_drawdown(self):
        # 峰值110，最新99 → 当前回撤10%
        self.assertAlmostEqual(current_drawdown([100, 110, 99]), 0.1)
        hit, dd = eval_nav_drawdown([100, 110, 99], 0.05)
        self.assertTrue(hit)
        hit, _ = eval_nav_drawdown([100, 110, 108], 0.05)
        self.assertFalse(hit)
        self.assertIsNone(current_drawdown([]))

    def test_datasource_down(self):
        health = [{"source": "csqaq", "status": "down"},
                  {"source": "steamdt", "status": "ok"},
                  {"source": "steam", "status": "down"}]
        self.assertEqual(eval_datasource_down(health), ["csqaq", "steam"])

    def test_message_template(self):
        msg = build_alert_message("price_change", {
            "item_name": "AK-47 | 红线", "platform": "BUFF",
            "old_price": 92.5, "new_price": 101.8, "change_pct": 10.05,
            "time_cn": "2026-07-22 10:30"})
        self.assertIn("价格异动", msg)
        self.assertIn("92.50 → 101.80", msg)
        self.assertIn("+10.05%", msg)


class FakeResponse:
    status_code = 200

    def json(self):
        return {"retcode": 0, "data": {"message_id": 1}}


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return FakeResponse()


class TestQQBotClient(unittest.TestCase):
    def test_send_payload(self):
        session = FakeSession()
        client = QQBotClient("http://127.0.0.1:3000/", "tok123", "88888",
                             session=session)
        ok, err = client.send_group_message("hello")
        self.assertTrue(ok)
        call = session.calls[0]
        self.assertEqual(call["url"], "http://127.0.0.1:3000/send_group_msg")
        self.assertEqual(call["json"], {"group_id": 88888, "message": "hello"})
        self.assertEqual(call["headers"]["Authorization"], "Bearer tok123")

    def test_unconfigured(self):
        client = QQBotClient("", None, None, session=FakeSession())
        ok, err = client.send_group_message("hi")
        self.assertFalse(ok)
        self.assertIsNotNone(err)


class OkNotifier:
    """成功推送的 Mock 通知器（冷却验证用）。"""
    router = "inventory"
    configured = True

    def __init__(self):
        self.sent = 0

    def send(self, message, message_type="alert", ref_id=None):
        self.sent += 1
        return True, None

    def status(self):
        return {"online": True}


class TestAlertServiceIntegration(unittest.TestCase):
    def test_price_change_alert_and_cooldown(self):
        dao = make_dao()
        dao.upsert_item_master({"market_hash_name": "AK-47 | Redline (Field-Tested)"})
        dao.commit()
        item = dao.get_item_by_hash_name("AK-47 | Redline (Field-Tested)")
        dao.add_watchlist(item["item_uuid"], tier="L1")
        # 两次采集：100 -> 106（+6% > 5% 阈值）
        dao.insert_tick({"item_uuid": item["item_uuid"], "platform": "BUFF",
                         "source": "mock", "sell_price": 100.0,
                         "ts": "2026-07-22T01:00:00+00:00"})
        dao.insert_tick({"item_uuid": item["item_uuid"], "platform": "BUFF",
                         "source": "mock", "sell_price": 106.0,
                         "ts": "2026-07-22T02:00:00+00:00"})
        dao.commit()

        svc = AlertService(dao, notifier=OkNotifier())   # 推送成功→进入冷却
        report1 = svc.evaluate_all()
        self.assertGreaterEqual(report1["triggered"], 1)
        self.assertEqual(report1["pushed"], 1)

        records = dao.get_alert_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["rule_type"], "price_change")
        self.assertIn("+6.00%", records[0]["message"])

        # 冷却期内第二次评估：不再产生新记录（仅成功推送才冷却）
        report2 = svc.evaluate_all()
        self.assertGreaterEqual(report2["skipped_cooldown"], 1)
        self.assertEqual(len(dao.get_alert_records()), 1)

    def test_rules_seeded(self):
        dao = make_dao()
        AlertService(dao, notifier=None)
        rules = dao.get_alert_rules()
        self.assertEqual(len(rules), 5)
        self.assertEqual({r["rule_type"] for r in rules},
                         {"price_change", "signal_trigger", "market_regime_change",
                          "nav_drawdown", "datasource_down"})


if __name__ == "__main__":
    unittest.main()
