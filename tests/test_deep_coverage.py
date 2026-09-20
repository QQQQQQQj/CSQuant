"""深度覆盖测试：Provider解析 / 降级路径 / 事件重生 / 升频到期 /
Tier节奏 / 并发写库 / ID映射导入 / 手续费 / 元数据映射。

隔离：内存库、Mock Session/Provider，零外部访问。
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from database.dao import Dao
from database.db import connect, init_schema

FROZEN_NOW = "2026-07-28T00:30:00+00:00"


def make_dao() -> Dao:
    conn = connect(":memory:")
    init_schema(conn)
    return Dao(conn)


# =============== Provider 响应解析（按官方文档结构） ===============

class TestCSQAQParsing(unittest.TestCase):
    """CSQAQ 单品详情字段映射（docs.csqaq.com api-187131780 文档结构）。"""

    def _provider(self):
        from data_sources.csqaq.provider import CSQAQProvider
        return CSQAQProvider("dummy-token")

    def test_map_good_detail_seven_platform_fields(self):
        from data_sources.base import ItemRef
        doc_payload = {
            "buff_sell_price": 92.5, "buff_buy_price": 88.0,
            "buff_sell_num": 320, "buff_buy_num": 150,
            "yyyp_sell_price": 93.1, "yyyp_buy_price": 87.5,
            "yyyp_sell_num": 210, "yyyp_buy_num": 90,
            "steam_sell_price": 101.2, "steam_buy_price": 85.0,
            "steam_sell_num": 60, "steam_buy_num": 30,
            "turnover_number": 45, "turnover_avg_price": 95.3,
            "statistic": 120000, "rank_num": 87,
            "sell_price_rate_1": -0.5, "sell_price_rate_7": 3.2,
            "sell_price_rate_30": 8.8,
        }
        ref = ItemRef(market_hash_name="AK-47 | Redline (Field-Tested)",
                      item_uuid="u1", csqaq_good_id=123)
        quotes = self._provider()._map_good_detail(ref, doc_payload)
        by_platform = {q.platform: q for q in quotes}
        self.assertIn("BUFF", by_platform)
        self.assertIn("UUYP", by_platform)
        self.assertIn("STEAM", by_platform)
        self.assertEqual(by_platform["BUFF"].sell_price, 92.5)
        self.assertEqual(by_platform["BUFF"].buy_count, 150)
        # 成交量只挂 STEAM（turnover_number 为 Steam 日成交口径）
        self.assertEqual(by_platform["STEAM"].volume_24h, 45)
        self.assertIsNone(by_platform["BUFF"].volume_24h)
        # 扩展字段进 extras（BUFF 行），不污染统一结构
        self.assertEqual(by_platform["BUFF"].extras["statistic"], 120000)
        self.assertEqual(by_platform["BUFF"].extras["sell_price_rate_7"], 3.2)

    def test_missing_platform_skipped_not_zero(self):
        """缺失平台整行跳过（不为0纪律），异常串值转 None。"""
        from data_sources.base import ItemRef
        quotes = self._provider()._map_good_detail(
            ItemRef(market_hash_name="X", item_uuid="u1", csqaq_good_id=1),
            {"buff_sell_price": "92.5", "buff_buy_price": "-",
             "buff_sell_num": 10, "buff_buy_num": None})
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].platform, "BUFF")
        self.assertEqual(quotes[0].sell_price, 92.5)   # 字符串数字容错
        self.assertIsNone(quotes[0].buy_price)         # "-" -> None


class TestSteamDTParsing(unittest.TestCase):
    """SteamDT price/single 与时间归一化（doc.steamdt.com 文档结构）。"""

    def _provider(self):
        from data_sources.steamdt.provider import SteamDTProvider
        return SteamDTProvider("dummy-key")

    def test_map_price_list_youpin_renamed(self):
        from data_sources.base import ItemRef
        data = [
            {"platform": "BUFF", "platformItemId": "33960",
             "sellPrice": 92.5, "sellCount": 320,
             "biddingPrice": 88.0, "biddingCount": 150,
             "updateTime": 1753142400000},
            {"platform": "YOUPIN", "sellPrice": 93.1, "sellCount": 210,
             "biddingPrice": 87.5, "biddingCount": 90,
             "updateTime": 1753142400},
        ]
        quotes = self._provider()._map_price_list(
            ItemRef(market_hash_name="X", item_uuid="u1"), data)
        platforms = {q.platform for q in quotes}
        self.assertEqual(platforms, {"BUFF", "UUYP"})   # YOUPIN 归一为 UUYP
        buff = next(q for q in quotes if q.platform == "BUFF")
        self.assertEqual(buff.buy_price, 88.0)
        self.assertEqual(buff.extras["platformItemId"], "33960")

    def test_norm_time_ms_and_sec(self):
        from data_sources.steamdt.provider import _norm_time
        iso_ms = _norm_time(1753142400000)   # 毫秒
        iso_sec = _norm_time(1753142400)     # 秒
        self.assertEqual(iso_ms, iso_sec)
        self.assertTrue(iso_ms.startswith("2025-07-22T"))
        self.assertIsNone(_norm_time(None))


class TestSteamParsing(unittest.TestCase):
    def test_parse_price_currency_formats(self):
        from data_sources.steam.provider import _parse_price, _parse_volume
        self.assertEqual(_parse_price("¥ 1,234.56"), 1234.56)
        self.assertEqual(_parse_price("$0.03"), 0.03)
        self.assertIsNone(_parse_price(None))
        self.assertIsNone(_parse_price("N/A"))
        self.assertEqual(_parse_volume("1,234"), 1234)


# =============== 主备降级路径 ===============

class TestFallbackPath(unittest.TestCase):
    def test_primary_fail_fallback_marked_c(self):
        from data_sources.base import ItemRef, UnifiedQuote
        from data_sources.http_client import HttpError
        from data_sources.unified.service import UnifiedMarketService
        dao = make_dao()
        dao.upsert_item_master({"market_hash_name": "X"})
        dao.commit()
        item = dao.get_item_by_hash_name("X")

        class FailingPrimary:
            name = "csqaq"
            def get_item_quotes(self, ref):
                raise HttpError("csqaq down", 503)

        class OkFallback:
            name = "steamdt"
            def get_item_quotes(self, ref):
                return [UnifiedQuote(market_hash_name=ref.market_hash_name,
                                     item_uuid=ref.item_uuid, platform="BUFF",
                                     source="steamdt", sell_price=90.0,
                                     buy_price=88.0)]

        svc = UnifiedMarketService(dao, FailingPrimary(), OkFallback())
        report = svc.collect_for_items(
            [ItemRef(market_hash_name="X", item_uuid=item["item_uuid"])])
        self.assertEqual(report.success, 1)
        self.assertEqual(report.fallback_used, 1)
        snap = dao.get_snapshot(item["item_uuid"], "BUFF")
        self.assertEqual(snap["data_quality"], "C")     # 备源降 C
        tick = dao.conn.execute("SELECT quality_flags FROM quote_ticks").fetchone()
        self.assertIn("SOURCE_FALLBACK", tick["quality_flags"])

    def test_both_fail_reported(self):
        from data_sources.base import ItemRef
        from data_sources.http_client import HttpError
        from data_sources.unified.service import UnifiedMarketService
        dao = make_dao()

        class Failing:
            name = "x"
            def get_item_quotes(self, ref):
                raise HttpError("down", 503)

        svc = UnifiedMarketService(dao, Failing(), Failing())
        report = svc.collect_for_items([ItemRef(market_hash_name="X",
                                                item_uuid="u1")])
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.success, 0)


# =============== 事件生命周期：RESOLVED 后可重生 ===============

class TestEventRebirth(unittest.TestCase):
    def test_new_event_after_resolved(self):
        from market_anomaly.event_lifecycle import event_step
        dao = make_dao()
        dao.upsert_item_master({"market_hash_name": "X"})
        dao.commit()
        item = dao.get_item_by_hash_name("X")
        uid = item["item_uuid"]
        # 第一轮：NEW
        step1 = event_step(None, "accumulation", uid, 80, FROZEN_NOW)
        dao.upsert_signal_event(step1["event_fields"])
        dao.commit()
        first_id = step1["event_fields"]["event_id"]
        # 第二轮：消退 RESOLVED
        open_ev = dao.get_open_event(uid, "accumulation")
        step2 = event_step(open_ev, "accumulation", uid, 10, FROZEN_NOW)
        self.assertEqual(step2["emit_reason"], "RESOLVED")
        dao.upsert_signal_event(step2["event_fields"])
        dao.commit()
        # 第三轮：RESOLVED 后不阻塞新事件（新 event_id）
        self.assertIsNone(dao.get_open_event(uid, "accumulation"))
        step3 = event_step(None, "accumulation", uid, 85, FROZEN_NOW)
        self.assertEqual(step3["emit_reason"], "NEW")
        self.assertNotEqual(step3["event_fields"]["event_id"], first_id)


# =============== 升频到期 / Tier B 节奏 ===============

class TestScanScheduling(unittest.TestCase):
    def _scan(self, dao, provider=None):
        from data_sources.unified.market_scan import MarketScanService
        from data_sources.unified.service import UnifiedMarketService
        return MarketScanService(dao, UnifiedMarketService(dao, None),
                                 provider, {"market_anomaly": {}})

    def test_promotion_expiry(self):
        dao = make_dao()
        scan = self._scan(dao)
        scan.promote(["u1"], now=FROZEN_NOW)
        alive = scan._promoted("2026-07-28T01:00:00+00:00")   # 30分钟后
        self.assertIn("u1", alive)
        expired = scan._promoted("2026-07-28T03:00:00+00:00")  # 150分钟后(>120)
        self.assertNotIn("u1", expired)

    def test_tier_b_cadence(self):
        """Tier B 每 3 轮采一次（无 buff_id 排除轮换干扰）。"""
        from data_sources.base import UnifiedQuote
        dao = make_dao()
        dao.upsert_item_master({"market_hash_name": "B-Item"})  # 无 buff_id
        dao.commit()
        item = dao.get_item_by_hash_name("B-Item")
        # 中等流动性快照 → Tier B（liq≈59 ∈ [55,75)）
        dao.upsert_snapshot({"item_uuid": item["item_uuid"], "platform": "BUFF",
                             "source": "mock", "sell_price": 50.0,
                             "sell_count": 150, "buy_count": 100,
                             "volume_24h": 30})
        dao.commit()
        calls = []

        class Recorder:
            name = "mock"
            def get_batch_quotes(self, refs):
                calls.append(len(refs))
                return {}

        scan = self._scan(dao, Recorder())
        r1 = scan.scan_tick(now=FROZEN_NOW)   # round=1: B 跳过
        r2 = scan.scan_tick(now=FROZEN_NOW)   # round=2: B 跳过
        r3 = scan.scan_tick(now=FROZEN_NOW)   # round=3: B 采集
        self.assertEqual((r1["tier_b"], r2["tier_b"], r3["tier_b"]), (0, 0, 1))


# =============== 并发写库（Streamlit+调度共存场景） ===============

class TestConcurrentWrites(unittest.TestCase):
    def test_multithread_inserts_no_corruption(self):
        dao = make_dao()
        dao.upsert_item_master({"market_hash_name": "X"})
        dao.commit()
        item = dao.get_item_by_hash_name("X")
        errors = []

        def worker(tid):
            try:
                for i in range(50):
                    dao.insert_tick({"item_uuid": item["item_uuid"],
                                     "platform": "BUFF", "source": f"t{tid}",
                                     "sell_price": 100.0 + i})
                dao.commit()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        count = dao.conn.execute("SELECT COUNT(*) FROM quote_ticks").fetchone()[0]
        self.assertEqual(count, 200)
        self.assertEqual(dao.conn.execute(
            "PRAGMA integrity_check").fetchone()[0], "ok")


# =============== ID 映射导入（合成 Mapper 目录） ===============

class TestItemMasterImport(unittest.TestCase):
    def test_import_synthetic_mapper(self):
        from items.item_master import import_from_id_mapper
        dao = make_dao()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            steam = {
                "AK-47 | Redline (Field-Tested)": {
                    "en_name": "AK-47 | Redline (Field-Tested)",
                    "cn_name": "AK-47 | 红线 (久经沙场)", "name_id": 7178002},
                "StatTrak™ AK-47 | Redline (Field-Tested)": {
                    "en_name": "StatTrak™ AK-47 | Redline (Field-Tested)",
                    "cn_name": "StatTrak™ AK-47 | 红线", "name_id": 7178003},
                "★ Karambit | Doppler (Factory New)": {
                    "en_name": "★ Karambit | Doppler (Factory New)",
                    "cn_name": "★ 爪子刀 | 多普勒", "name_id": 9999},
            }
            buff = {"AK-47 | Redline (Field-Tested)": 33960,
                    "StatTrak™ AK-47 | Redline (Field-Tested)": 33961,
                    "★ Karambit | Doppler (Factory New)": -1}
            c5 = {"AK-47 | Redline (Field-Tested)": 1300000000000000001,
                  "StatTrak™ AK-47 | Redline (Field-Tested)": -1,
                  "★ Karambit | Doppler (Factory New)": 22499}
            igxe = {k: -1 for k in steam}
            uuyp = {"AK-47 | Redline (Field-Tested)": 1414,
                    "StatTrak™ AK-47 | Redline (Field-Tested)": 1415,
                    "★ Karambit | Doppler (Factory New)": 9}
            for name, payload in (("steam", steam), ("buff", buff),
                                  ("c5", c5), ("igxe", igxe), ("uuyp", uuyp)):
                d = base / name
                d.mkdir()
                (d / "730.json").write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            report = import_from_id_mapper(dao, base)
            self.assertEqual(report["imported"], 3)
            self.assertEqual(report["key_align_ratio"]["buff"], 1.0)

            ak = dao.get_item_by_hash_name("AK-47 | Redline (Field-Tested)")
            self.assertEqual(ak["buff_id"], 33960)
            self.assertEqual(ak["c5_id"], "1300000000000000001")  # 大整数 TEXT
            self.assertIsNone(ak["igxe_id"])                       # -1 -> NULL
            self.assertEqual(ak["name_zh"], "AK-47 | 红线 (久经沙场)")
            self.assertEqual(ak["is_stattrak"], 0)

            st_ak = dao.get_item_by_hash_name(
                "StatTrak™ AK-47 | Redline (Field-Tested)")
            self.assertEqual(st_ak["is_stattrak"], 1)

            knife = dao.get_item_by_hash_name(
                "★ Karambit | Doppler (Factory New)")
            self.assertEqual(knife["category"], "knife")
            self.assertIsNone(knife["buff_id"])                    # -1 -> NULL


# =============== 手续费精算 / 元数据映射 ===============

class TestFees(unittest.TestCase):
    def test_steam_after_fee(self):
        from utils.fees import calculate_after_fee
        # 100 = 卖家所得 + 5%Steam费 + 10%游戏费 → 净得 85
        self.assertEqual(calculate_after_fee(100.0), 85.0)
        self.assertEqual(calculate_after_fee(0.10), 0.08)   # 最低费 0.01×2
        self.assertEqual(calculate_after_fee(0), 0.0)

    def test_platform_fee_table(self):
        from utils.fees import apply_fee
        table = {"BUFF": {"rate": 0.025}}
        self.assertEqual(apply_fee(100.0, "BUFF", table), 97.5)
        self.assertEqual(apply_fee(100.0, "STEAM", table), 85.0)  # 走精算
        self.assertEqual(apply_fee(100.0, "UNKNOWN", table), 100.0)


class TestMetadataMapping(unittest.TestCase):
    def test_map_skin_docstyle(self):
        from items.metadata import _map_skin
        rec = {
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "weapon": {"name": "AK-47"},
            "pattern": {"name": "Redline"},
            "wears": [{"name": "Field-Tested"}],
            "rarity": {"name": "Classified", "color": "#d32ce6"},
            "collections": [{"name": "The Phoenix Collection"}],
            "crates": [{"name": "Operation Phoenix Weapon Case"}],
            "min_float": 0.15, "max_float": 0.38, "paint_index": 282,
            "image": "https://cdn/x.png", "stattrak": True,
        }
        mapped = _map_skin(rec)
        self.assertEqual(mapped["weapon"], "AK-47")
        self.assertEqual(mapped["skin"], "Redline")
        self.assertEqual(mapped["wear"], "Field-Tested")
        self.assertEqual(mapped["rarity"], "Classified")
        self.assertEqual(mapped["collection"], "The Phoenix Collection")
        self.assertEqual(mapped["paint_index"], 282)
        self.assertIsNone(_map_skin({"no_name": 1}))


# =============== 回测：跨饰品共享资金池约束 ===============

class TestBacktestCashSharing(unittest.TestCase):
    def test_second_buy_limited_by_remaining_cash(self):
        from quant.backtest.engine import Bar, run_backtest
        cfg = {"init_cash": 1000.0, "max_position_per_item": 0.8,
               "liquidity_filter_min_volume": 1,
               "slippage_bands": {"high_liquidity": 0.0,
                                  "mid_liquidity": 0.0, "low_liquidity": 0.0}}
        bars = {item: [Bar(ts=f"2026-01-{d:02d}", close=100.0, bid=100.0,
                           ask=100.0, volume=100) for d in (1, 2)]
                for item in ("A", "B")}
        entries = {"A": [True, False], "B": [True, False]}
        result = run_backtest(bars, entries, {"A": [], "B": []},
                              cfg, {"BUFF": {"rate": 0}})
        # 权益1000×80%=800→第一件买8件耗800；剩200→第二件只能买2件
        total_cost = sum(t.price * t.quantity for t in result.trades)
        self.assertLessEqual(total_cost, 1000.0)
        self.assertEqual(len(result.trades), 2)
        quantities = sorted(t.quantity for t in result.trades)
        self.assertEqual(quantities, [2, 8])


if __name__ == "__main__":
    unittest.main()
