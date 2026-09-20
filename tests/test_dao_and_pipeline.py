"""DAO（内存SQLite）与数据质量管道测试。"""
import unittest

from data_sources.base import UnifiedQuote
from data_sources.unified.service import UnifiedMarketService
from database.dao import Dao
from database.db import connect, init_schema


def make_dao() -> Dao:
    conn = connect(":memory:")
    init_schema(conn)
    return Dao(conn)


def seed_item(dao: Dao, name="AK-47 | Redline (Field-Tested)") -> dict:
    dao.upsert_item_master({"market_hash_name": name, "buff_id": 33960})
    dao.commit()
    return dao.get_item_by_hash_name(name)


class TestDao(unittest.TestCase):
    def test_item_upsert_and_get(self):
        dao = make_dao()
        item = seed_item(dao)
        self.assertEqual(item["buff_id"], 33960)
        # upsert 不覆盖已有 ID（COALESCE）
        dao.upsert_item_master({"market_hash_name": item["market_hash_name"],
                                "csqaq_good_id": 12345})
        dao.commit()
        item2 = dao.get_item_by_hash_name(item["market_hash_name"])
        self.assertEqual(item2["buff_id"], 33960)
        self.assertEqual(item2["csqaq_good_id"], 12345)
        self.assertEqual(item2["item_uuid"], item["item_uuid"])

    def test_position_sell_realized_pnl(self):
        dao = make_dao()
        item = seed_item(dao)
        from inventory.portfolio import add_manual_position, record_sell
        pid = add_manual_position(dao, item["market_hash_name"], 2, 50.0,
                                  buy_platform="BUFF")
        result = record_sell(dao, pid, 60.0, fee=1.5, platform="BUFF")
        # proceeds = (60-1.5)*2 = 117; cost = 100; pnl = 17
        self.assertEqual(result.proceeds, 117.0)
        self.assertEqual(result.realized_pnl, 17.0)
        self.assertAlmostEqual(result.return_rate, 0.17)
        self.assertEqual(dao.get_position(pid)["status"], "SOLD")
        with self.assertRaises(ValueError):
            record_sell(dao, pid, 60.0)  # 重复卖出拦截

    def test_snapshot_upsert(self):
        dao = make_dao()
        item = seed_item(dao)
        dao.upsert_snapshot({"item_uuid": item["item_uuid"], "platform": "BUFF",
                             "source": "csqaq", "sell_price": 90.0})
        dao.upsert_snapshot({"item_uuid": item["item_uuid"], "platform": "BUFF",
                             "source": "csqaq", "sell_price": 92.0})
        dao.commit()
        snaps = dao.get_snapshots_for_item(item["item_uuid"])
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["sell_price"], 92.0)

    def test_signal_traceable(self):
        dao = make_dao()
        item = seed_item(dao)
        sig_id = dao.insert_signal({
            "item_uuid": item["item_uuid"], "signal": "BUY", "final_score": 80.0,
            "confidence": 0.7, "risk_level": "M",
            "reason_json": {"item_factors": []},
            "suggested_position": 0.08, "model_version": "v-test",
            "input_snapshot_ref": "{\"kline_days\": 60}"})
        dao.commit()
        detail = dao.get_signal_detail(sig_id)
        self.assertEqual(detail["model_version"], "v-test")
        self.assertIn("kline_days", detail["input_snapshot_ref"])
        self.assertEqual(detail["market_hash_name"],
                         "AK-47 | Redline (Field-Tested)")

    def test_ticks_quality_default(self):
        dao = make_dao()
        item = seed_item(dao)
        dao.insert_tick({"item_uuid": item["item_uuid"], "platform": "BUFF",
                         "source": "csqaq", "sell_price": None})  # 缺失=NULL
        dao.commit()
        row = dao.conn.execute("SELECT * FROM quote_ticks").fetchone()
        self.assertIsNone(row["sell_price"])
        self.assertEqual(row["data_quality"], "A")
        self.assertEqual(row["currency"], "CNY")


class TestQualityPipeline(unittest.TestCase):
    def setUp(self):
        self.dao = make_dao()
        self.item = seed_item(self.dao)
        self.svc = UnifiedMarketService(self.dao, primary=None)

    def _q(self, platform, sell, buy=None, quality="A"):
        return UnifiedQuote(market_hash_name=self.item["market_hash_name"],
                            item_uuid=self.item["item_uuid"], platform=platform,
                            source="csqaq", sell_price=sell, buy_price=buy,
                            data_quality=quality)

    def test_missing_sell_downgraded(self):
        quotes = self.svc.quality_pipeline([self._q("BUFF", None)])
        self.assertEqual(quotes[0].data_quality, "B")
        self.assertIn("MISSING_SELL_PRICE", quotes[0].quality_flags)

    def test_inverted_book_flagged(self):
        quotes = self.svc.quality_pipeline([self._q("BUFF", 100.0, buy=105.0)])
        self.assertIn("INVERTED_BOOK", quotes[0].quality_flags)

    def test_cross_platform_divergence(self):
        quotes = self.svc.quality_pipeline([
            self._q("BUFF", 100.0), self._q("STEAM", 120.0)])
        for q in quotes:
            self.assertIn("CROSS_PLATFORM_DIVERGENCE", q.quality_flags)
            self.assertEqual(q.data_quality, "B")

    def test_consistent_platforms_clean(self):
        quotes = self.svc.quality_pipeline([
            self._q("BUFF", 100.0), self._q("STEAM", 103.0)])
        for q in quotes:
            self.assertEqual(q.quality_flags, [])
            self.assertEqual(q.data_quality, "A")

    def test_spike_flagged(self):
        self.dao.upsert_snapshot({"item_uuid": self.item["item_uuid"],
                                  "platform": "BUFF", "source": "csqaq",
                                  "sell_price": 100.0, "volume_24h": 10})
        self.dao.commit()
        quotes = self.svc.quality_pipeline([self._q("BUFF", 140.0)])
        self.assertIn("PRICE_SPIKE_SUSPECT", quotes[0].quality_flags)


if __name__ == "__main__":
    unittest.main()
