"""DAO：全库唯一读写路径。

纪律（对应 DATABASE_SCHEMA.md §6）：UTC 时间、currency 默认 CNY、
缺失为 None 不为 0、quality 默认 A。返回全部为 dict（sqlite3.Row 转换）。
"""
from __future__ import annotations

import json
import sqlite3
import uuid as uuid_lib
from typing import Any, Iterable

from utils.timeutils import iso_now


def new_id() -> str:
    return str(uuid_lib.uuid4())


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _row(cur: sqlite3.Cursor) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r else None


class Dao:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------------- item_master ----------------
    def upsert_item_master(self, item: dict) -> None:
        now = iso_now()
        self.conn.execute(
            """INSERT INTO item_master
               (item_uuid, app_id, market_hash_name, name_zh, name_en, category,
                is_stattrak, is_souvenir, steam_name_id, buff_id, c5_id, igxe_id,
                uuyp_id, csqaq_good_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(app_id, market_hash_name) DO UPDATE SET
                 name_zh=COALESCE(excluded.name_zh, name_zh),
                 name_en=COALESCE(excluded.name_en, name_en),
                 category=COALESCE(excluded.category, category),
                 is_stattrak=excluded.is_stattrak, is_souvenir=excluded.is_souvenir,
                 steam_name_id=COALESCE(excluded.steam_name_id, steam_name_id),
                 buff_id=COALESCE(excluded.buff_id, buff_id),
                 c5_id=COALESCE(excluded.c5_id, c5_id),
                 igxe_id=COALESCE(excluded.igxe_id, igxe_id),
                 uuyp_id=COALESCE(excluded.uuyp_id, uuyp_id),
                 csqaq_good_id=COALESCE(excluded.csqaq_good_id, csqaq_good_id),
                 updated_at=excluded.updated_at""",
            (
                item.get("item_uuid") or new_id(),
                item.get("app_id", 730),
                item["market_hash_name"],
                item.get("name_zh"), item.get("name_en"), item.get("category"),
                int(bool(item.get("is_stattrak"))), int(bool(item.get("is_souvenir"))),
                item.get("steam_name_id"), item.get("buff_id"), item.get("c5_id"),
                item.get("igxe_id"), item.get("uuyp_id"), item.get("csqaq_good_id"),
                now, now,
            ),
        )

    def get_item_by_hash_name(self, market_hash_name: str, app_id: int = 730) -> dict | None:
        return _row(self.conn.execute(
            "SELECT * FROM item_master WHERE app_id=? AND market_hash_name=?",
            (app_id, market_hash_name)))

    def get_item_by_uuid(self, item_uuid: str) -> dict | None:
        return _row(self.conn.execute(
            "SELECT * FROM item_master WHERE item_uuid=?", (item_uuid,)))

    def search_items(self, keyword: str = "", category: str | None = None,
                     limit: int = 50, offset: int = 0) -> list[dict]:
        """中英文模糊搜索：多关键词空格分隔（AND）；英文/中文/别名三路匹配；
        相关性排序（前缀匹配 > 包含位置靠前 > 短名称）。关联 BUFF 快照价。"""
        sql = ("SELECT m.*, d.image_url, d.weapon, d.wear, d.rarity, "
               "sp.sell_price AS buff_price, sp.buy_price AS buff_buy_price, "
               "sp.ts AS price_ts, sp.data_quality AS price_quality "
               "FROM item_master m "
               "LEFT JOIN item_metadata d ON d.item_uuid=m.item_uuid "
               "LEFT JOIN market_snapshot sp ON sp.item_uuid=m.item_uuid "
               "  AND sp.platform='BUFF' WHERE 1=1")
        params: list[Any] = []
        words = keyword.split()
        for word in words:
            like = f"%{word}%"
            sql += (" AND (m.market_hash_name LIKE ? OR m.name_zh LIKE ?"
                    " OR m.name_en LIKE ?)")
            params += [like, like, like]
        if category:
            sql += " AND m.category=?"
            params.append(category)
        if words:
            prefix = f"{words[0]}%"
            sql += (" ORDER BY "
                    "CASE WHEN LOWER(m.market_hash_name) LIKE LOWER(?) THEN 0 ELSE 1 END, "
                    "CASE WHEN m.name_zh LIKE ? THEN 0 ELSE 1 END, "
                    "LENGTH(m.market_hash_name)")
            params += [prefix, prefix]
        else:
            sql += " ORDER BY m.market_hash_name"
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]
        return _rows(self.conn.execute(sql, params))

    # ---------------- metadata ----------------
    def upsert_metadata(self, meta: dict) -> None:
        self.conn.execute(
            """INSERT INTO item_metadata
               (item_uuid, weapon, skin, wear, rarity, rarity_color, collection, crate,
                phase, min_float, max_float, paint_index, def_index, image_url, extra_json, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(item_uuid) DO UPDATE SET
                 weapon=excluded.weapon, skin=excluded.skin, wear=excluded.wear,
                 rarity=excluded.rarity, rarity_color=excluded.rarity_color,
                 collection=excluded.collection, crate=excluded.crate, phase=excluded.phase,
                 min_float=excluded.min_float, max_float=excluded.max_float,
                 paint_index=excluded.paint_index, def_index=excluded.def_index,
                 image_url=excluded.image_url, extra_json=excluded.extra_json,
                 updated_at=excluded.updated_at""",
            (
                meta["item_uuid"], meta.get("weapon"), meta.get("skin"), meta.get("wear"),
                meta.get("rarity"), meta.get("rarity_color"), meta.get("collection"),
                meta.get("crate"), meta.get("phase"), meta.get("min_float"),
                meta.get("max_float"), meta.get("paint_index"), meta.get("def_index"),
                meta.get("image_url"), meta.get("extra_json"), iso_now(),
            ),
        )

    def get_metadata(self, item_uuid: str) -> dict | None:
        return _row(self.conn.execute(
            "SELECT * FROM item_metadata WHERE item_uuid=?", (item_uuid,)))

    # ---------------- 行情 ----------------
    def insert_tick(self, tick: dict) -> None:
        self.conn.execute(
            """INSERT INTO quote_ticks
               (ts, item_uuid, platform, source, sell_price, sell_count, buy_price,
                buy_count, volume_24h, reference_price, currency, source_update_time,
                data_quality, quality_flags)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                tick.get("ts") or iso_now(), tick["item_uuid"], tick["platform"],
                tick["source"], tick.get("sell_price"), tick.get("sell_count"),
                tick.get("buy_price"), tick.get("buy_count"), tick.get("volume_24h"),
                tick.get("reference_price"), tick.get("currency", "CNY"),
                tick.get("source_update_time"), tick.get("data_quality", "A"),
                json.dumps(tick.get("quality_flags", []), ensure_ascii=False),
            ),
        )

    def upsert_snapshot(self, snap: dict) -> None:
        self.conn.execute(
            """INSERT INTO market_snapshot
               (item_uuid, platform, source, sell_price, sell_count, buy_price, buy_count,
                volume_24h, reference_price, currency, ts, source_update_time, data_quality)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(item_uuid, platform) DO UPDATE SET
                 source=excluded.source, sell_price=excluded.sell_price,
                 sell_count=excluded.sell_count, buy_price=excluded.buy_price,
                 buy_count=excluded.buy_count, volume_24h=excluded.volume_24h,
                 reference_price=excluded.reference_price, currency=excluded.currency,
                 ts=excluded.ts, source_update_time=excluded.source_update_time,
                 data_quality=excluded.data_quality""",
            (
                snap["item_uuid"], snap["platform"], snap["source"],
                snap.get("sell_price"), snap.get("sell_count"), snap.get("buy_price"),
                snap.get("buy_count"), snap.get("volume_24h"), snap.get("reference_price"),
                snap.get("currency", "CNY"), snap.get("ts") or iso_now(),
                snap.get("source_update_time"), snap.get("data_quality", "A"),
            ),
        )

    def get_snapshots_for_item(self, item_uuid: str) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM market_snapshot WHERE item_uuid=?", (item_uuid,)))

    def get_snapshot(self, item_uuid: str, platform: str) -> dict | None:
        return _row(self.conn.execute(
            "SELECT * FROM market_snapshot WHERE item_uuid=? AND platform=?",
            (item_uuid, platform)))

    # ---------------- K线 ----------------
    def upsert_kline(self, k: dict) -> None:
        self.conn.execute(
            """INSERT INTO kline_daily
               (item_uuid, platform, period, ts, open, close, high, low, volume, source)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(item_uuid, platform, period, ts) DO UPDATE SET
                 open=excluded.open, close=excluded.close, high=excluded.high,
                 low=excluded.low, volume=excluded.volume, source=excluded.source""",
            (
                k["item_uuid"], k["platform"], k.get("period", "1d"), k["ts"],
                k["open"], k["close"], k["high"], k["low"], k.get("volume"), k["source"],
            ),
        )

    def get_klines(self, item_uuid: str, platform: str, period: str = "1d",
                   limit: int = 180) -> list[dict]:
        return _rows(self.conn.execute(
            """SELECT * FROM kline_daily
               WHERE item_uuid=? AND platform=? AND period=?
               ORDER BY ts DESC LIMIT ?""",
            (item_uuid, platform, period, limit)))[::-1]

    # ---------------- 持仓 ----------------
    def add_position(self, pos: dict) -> str:
        qty = pos.get("quantity", 1)
        if not isinstance(qty, int) or qty <= 0:
            raise ValueError(f"quantity 必须为正整数: {qty}")
        if pos.get("buy_price") is not None and pos["buy_price"] < 0:
            raise ValueError(f"buy_price 不得为负: {pos['buy_price']}")
        position_id = pos.get("position_id") or new_id()
        now = iso_now()
        self.conn.execute(
            """INSERT INTO portfolio_position
               (position_id, item_uuid, asset_id, quantity, buy_price, buy_time,
                buy_platform, cost_basis, status, notes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                position_id, pos["item_uuid"], pos.get("asset_id"),
                pos.get("quantity", 1), pos.get("buy_price"), pos.get("buy_time"),
                pos.get("buy_platform"), pos.get("cost_basis"),
                pos.get("status", "HOLDING"), pos.get("notes"), now, now,
            ),
        )
        return position_id

    def get_positions(self, status: str | None = None) -> list[dict]:
        if status:
            return _rows(self.conn.execute(
                "SELECT * FROM portfolio_position WHERE status=?", (status,)))
        return _rows(self.conn.execute("SELECT * FROM portfolio_position"))

    def get_position(self, position_id: str) -> dict | None:
        return _row(self.conn.execute(
            "SELECT * FROM portfolio_position WHERE position_id=?", (position_id,)))

    def find_position_by_asset(self, item_uuid: str, asset_id: str | None) -> dict | None:
        if asset_id:
            return _row(self.conn.execute(
                """SELECT * FROM portfolio_position
                   WHERE item_uuid=? AND asset_id=? AND status='HOLDING'""",
                (item_uuid, asset_id)))
        return _row(self.conn.execute(
            """SELECT * FROM portfolio_position
               WHERE item_uuid=? AND status='HOLDING' LIMIT 1""", (item_uuid,)))

    def update_position(self, position_id: str, fields: dict) -> None:
        fields["updated_at"] = iso_now()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(
            f"UPDATE portfolio_position SET {sets} WHERE position_id=?",
            (*fields.values(), position_id))

    def insert_trade(self, trade: dict) -> str:
        qty = trade.get("quantity", 1)
        if not isinstance(qty, int) or qty <= 0:
            raise ValueError(f"quantity 必须为正整数: {qty}")
        if trade["price"] is None or trade["price"] < 0:
            raise ValueError(f"price 不得为负/空: {trade.get('price')}")
        if trade.get("fee", 0) < 0:
            raise ValueError(f"fee 不得为负: {trade.get('fee')}")
        trade_id = trade.get("trade_id") or new_id()
        self.conn.execute(
            """INSERT INTO trade_record
               (trade_id, position_id, item_uuid, side, quantity, price, fee,
                platform, currency, traded_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trade_id, trade.get("position_id"), trade["item_uuid"], trade["side"],
                trade.get("quantity", 1), trade["price"], trade.get("fee", 0),
                trade.get("platform"), trade.get("currency", "CNY"),
                trade.get("traded_at") or iso_now(), iso_now(),
            ),
        )
        return trade_id

    def get_realized_pnl(self) -> float:
        r = self.conn.execute(
            """SELECT COALESCE(SUM(
                   CASE WHEN side='SELL' THEN (price - fee) * quantity
                        ELSE 0 END), 0) AS proceeds FROM trade_record""").fetchone()
        r2 = self.conn.execute(
            """SELECT COALESCE(SUM(t.quantity * p.buy_price), 0) AS cost
               FROM trade_record t JOIN portfolio_position p
                 ON t.position_id = p.position_id
               WHERE t.side='SELL' AND p.buy_price IS NOT NULL""").fetchone()
        return round((r["proceeds"] or 0) - (r2["cost"] or 0), 2)

    # ---------------- 净值 ----------------
    def upsert_nav_snapshot(self, snap: dict) -> None:
        self.conn.execute(
            """INSERT INTO portfolio_snapshot
               (ts, total_value, total_cost, cash, unrealized_pnl, realized_pnl,
                return_rate, net_deposit, value_by_platform, currency)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(ts) DO UPDATE SET
                 total_value=excluded.total_value, total_cost=excluded.total_cost,
                 cash=excluded.cash, unrealized_pnl=excluded.unrealized_pnl,
                 realized_pnl=excluded.realized_pnl, return_rate=excluded.return_rate,
                 net_deposit=excluded.net_deposit,
                 value_by_platform=excluded.value_by_platform""",
            (
                snap["ts"], snap["total_value"], snap["total_cost"], snap.get("cash", 0),
                snap["unrealized_pnl"], snap.get("realized_pnl", 0), snap.get("return_rate"),
                snap.get("net_deposit", 0), snap.get("value_by_platform"),
                snap.get("currency", "CNY"),
            ),
        )

    def get_nav_history(self, limit: int = 90) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM portfolio_snapshot ORDER BY ts DESC LIMIT ?", (limit,)))[::-1]

    # ---------------- 大盘 ----------------
    def insert_market_index(self, idx: dict) -> None:
        self.conn.execute(
            """INSERT INTO market_index
               (ts, index_name, index_value, change_1d, change_7d, change_30d,
                breadth_up, breadth_down, volume_score, liquidity_score,
                sentiment_score, market_score, market_regime, sub_indices_json, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                idx.get("ts") or iso_now(), idx["index_name"], idx["index_value"],
                idx.get("change_1d"), idx.get("change_7d"), idx.get("change_30d"),
                idx.get("breadth_up"), idx.get("breadth_down"), idx.get("volume_score"),
                idx.get("liquidity_score"), idx.get("sentiment_score"),
                idx.get("market_score"), idx.get("market_regime"),
                idx.get("sub_indices_json"), idx["source"],
            ),
        )

    def get_latest_market_index(self, index_name: str | None = None) -> dict | None:
        if index_name:
            return _row(self.conn.execute(
                "SELECT * FROM market_index WHERE index_name=? ORDER BY ts DESC LIMIT 1",
                (index_name,)))
        return _row(self.conn.execute(
            "SELECT * FROM market_index ORDER BY ts DESC LIMIT 1"))

    def get_market_index_history(self, index_name: str, limit: int = 90) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM market_index WHERE index_name=? ORDER BY ts DESC LIMIT ?",
            (index_name, limit)))[::-1]

    # ---------------- 信号 ----------------
    def insert_signal(self, sig: dict) -> int:
        cur = self.conn.execute(
            """INSERT INTO signal
               (ts, item_uuid, market_score, item_score, final_score, signal, confidence,
                risk_level, reason_json, suggested_position, model_version, input_snapshot_ref)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sig.get("ts") or iso_now(), sig["item_uuid"], sig.get("market_score"),
                sig.get("item_score"), sig.get("final_score"), sig["signal"],
                sig.get("confidence"), sig.get("risk_level"),
                sig["reason_json"] if isinstance(sig["reason_json"], str)
                else json.dumps(sig.get("reason_json", {}), ensure_ascii=False),
                sig.get("suggested_position"), sig["model_version"],
                sig.get("input_snapshot_ref"),
            ),
        )
        return cur.lastrowid

    def get_signals(self, signal_filter: str | None = None, risk_level: str | None = None,
                    limit: int = 100) -> list[dict]:
        sql = """SELECT s.*, m.market_hash_name, m.name_zh, m.category, d.image_url
                 FROM signal s
                 JOIN item_master m ON m.item_uuid = s.item_uuid
                 LEFT JOIN item_metadata d ON d.item_uuid = s.item_uuid
                 WHERE s.id IN (SELECT MAX(id) FROM signal GROUP BY item_uuid)"""
        params: list[Any] = []
        if signal_filter:
            sql += " AND s.signal=?"
            params.append(signal_filter)
        if risk_level:
            sql += " AND s.risk_level=?"
            params.append(risk_level)
        sql += " ORDER BY s.final_score DESC LIMIT ?"
        params.append(limit)
        return _rows(self.conn.execute(sql, params))

    def get_signal_detail(self, signal_id: int) -> dict | None:
        return _row(self.conn.execute(
            """SELECT s.*, m.market_hash_name, m.name_zh, m.category, d.image_url
               FROM signal s
               JOIN item_master m ON m.item_uuid = s.item_uuid
               LEFT JOIN item_metadata d ON d.item_uuid = s.item_uuid
               WHERE s.id=?""", (signal_id,)))

    def get_signal_history(self, item_uuid: str, limit: int = 30) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM signal WHERE item_uuid=? ORDER BY ts DESC LIMIT ?",
            (item_uuid, limit)))

    # ---------------- 健康与事件 ----------------
    def insert_health(self, h: dict) -> None:
        self.conn.execute(
            """INSERT INTO datasource_health
               (source, endpoint, ts, latency_ms, status, error_code,
                success_rate_1h, freshness_p50_sec)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                h["source"], h["endpoint"], h.get("ts") or iso_now(),
                h.get("latency_ms"), h.get("status", "ok"), h.get("error_code"),
                h.get("success_rate_1h"), h.get("freshness_p50_sec"),
            ),
        )

    def get_health_latest(self) -> list[dict]:
        return _rows(self.conn.execute(
            """SELECT * FROM datasource_health
               WHERE id IN (SELECT MAX(id) FROM datasource_health
                            GROUP BY source, endpoint)"""))

    def log_event(self, level: str, module: str, message: str,
                  detail: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO event_log (ts, level, module, message, detail_json) VALUES (?,?,?,?,?)",
            (iso_now(), level, module, message,
             json.dumps(detail, ensure_ascii=False) if detail else None))

    def get_events(self, limit: int = 50) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM event_log ORDER BY id DESC LIMIT ?", (limit,)))

    # ---------------- 自选池 ----------------
    def add_watchlist(self, item_uuid: str, tier: str = "L2", notes: str | None = None) -> None:
        self.conn.execute(
            """INSERT INTO watchlist (item_uuid, tier, added_at, notes) VALUES (?,?,?,?)
               ON CONFLICT(item_uuid) DO UPDATE SET tier=excluded.tier""",
            (item_uuid, tier, iso_now(), notes))

    def get_watchlist(self, tiers: Iterable[str] | None = None) -> list[dict]:
        if tiers:
            placeholders = ",".join("?" for _ in tiers)
            return _rows(self.conn.execute(
                f"""SELECT w.*, m.market_hash_name, m.name_zh, m.csqaq_good_id
                    FROM watchlist w JOIN item_master m ON m.item_uuid=w.item_uuid
                    WHERE w.tier IN ({placeholders})""", tuple(tiers)))
        return _rows(self.conn.execute(
            """SELECT w.*, m.market_hash_name, m.name_zh, m.csqaq_good_id
               FROM watchlist w JOIN item_master m ON m.item_uuid=w.item_uuid"""))

    # ---------------- sync_meta ----------------
    def get_meta(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM sync_meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # ---------------- 回测 ----------------
    def insert_backtest_result(self, bt: dict) -> str:
        backtest_id = bt.get("backtest_id") or new_id()
        self.conn.execute(
            """INSERT INTO backtest_result
               (backtest_id, strategy_name, strategy_version, start_date, end_date,
                params_json, total_return, annualized_return, max_drawdown, sharpe,
                sortino, calmar, win_rate, profit_factor, trade_count, turnover,
                fee_cost, slippage_cost, benchmark_return, equity_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                backtest_id, bt["strategy_name"], bt["strategy_version"],
                bt["start_date"], bt["end_date"],
                bt["params_json"] if isinstance(bt["params_json"], str)
                else json.dumps(bt.get("params_json", {}), ensure_ascii=False),
                bt.get("total_return"), bt.get("annualized_return"), bt.get("max_drawdown"),
                bt.get("sharpe"), bt.get("sortino"), bt.get("calmar"), bt.get("win_rate"),
                bt.get("profit_factor"), bt.get("trade_count"), bt.get("turnover"),
                bt.get("fee_cost"), bt.get("slippage_cost"), bt.get("benchmark_return"),
                bt.get("equity_json"), iso_now(),
            ),
        )
        return backtest_id

    def insert_backtest_trade(self, backtest_id: str, trade: dict) -> None:
        self.conn.execute(
            """INSERT INTO backtest_trade (backtest_id, item_uuid, side, quantity, price, fee, traded_at)
               VALUES (?,?,?,?,?,?,?)""",
            (backtest_id, trade["item_uuid"], trade["side"], trade["quantity"],
             trade["price"], trade.get("fee", 0), trade["traded_at"]))

    def list_backtests(self, limit: int = 20) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM backtest_result ORDER BY created_at DESC LIMIT ?", (limit,)))

    def get_backtest(self, backtest_id: str) -> dict | None:
        return _row(self.conn.execute(
            "SELECT * FROM backtest_result WHERE backtest_id=?", (backtest_id,)))

    def get_backtest_trades(self, backtest_id: str) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM backtest_trade WHERE backtest_id=? ORDER BY traded_at",
            (backtest_id,)))

    # ---------------- 告警 ----------------
    def ensure_default_alert_rules(self, defaults: list[dict]) -> None:
        now = iso_now()
        for r in defaults:
            self.conn.execute(
                """INSERT OR IGNORE INTO alert_rule
                   (rule_id, rule_type, name, enabled, threshold, cooldown_min,
                    params_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (r["rule_id"], r["rule_type"], r["name"],
                 int(r.get("enabled", 1)), r.get("threshold"),
                 r.get("cooldown_min", 60),
                 json.dumps(r.get("params", {}), ensure_ascii=False), now, now))

    def get_alert_rules(self, enabled_only: bool = False) -> list[dict]:
        if enabled_only:
            return _rows(self.conn.execute(
                "SELECT * FROM alert_rule WHERE enabled=1"))
        return _rows(self.conn.execute("SELECT * FROM alert_rule ORDER BY rule_type"))

    def update_alert_rule(self, rule_id: str, fields: dict) -> None:
        fields["updated_at"] = iso_now()
        if "enabled" in fields:
            fields["enabled"] = int(fields["enabled"])
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(
            f"UPDATE alert_rule SET {sets} WHERE rule_id=?",
            (*fields.values(), rule_id))

    def insert_alert_record(self, rec: dict) -> None:
        self.conn.execute(
            """INSERT INTO alert_record
               (ts, rule_id, rule_type, level, target, message, pushed, error)
               VALUES (?,?,?,?,?,?,?,?)""",
            (rec.get("ts") or iso_now(), rec["rule_id"], rec["rule_type"],
             rec["level"], rec.get("target"), rec["message"],
             int(rec.get("pushed", 0)), rec.get("error")))

    def get_alert_records(self, limit: int = 50) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM alert_record ORDER BY id DESC LIMIT ?", (limit,)))

    def get_last_alert_ts(self, rule_id: str, target: str | None) -> str | None:
        """最近一次【成功推送】时间：发送失败（pushed=0）不得当作冷却。"""
        r = self.conn.execute(
            """SELECT ts FROM alert_record WHERE rule_id=? AND COALESCE(target,'')=COALESCE(?,'')
               AND pushed=1
               ORDER BY id DESC LIMIT 1""",
            (rule_id, target)).fetchone()
        return r["ts"] if r else None

    def get_recent_prices(self, item_uuid: str, platform: str,
                          limit: int = 2) -> list[dict]:
        """最近 N 条有效在售价（时间倒序）——价格异动对比用。"""
        return _rows(self.conn.execute(
            """SELECT sell_price, ts FROM quote_ticks
               WHERE item_uuid=? AND platform=? AND sell_price IS NOT NULL
               ORDER BY ts DESC LIMIT ?""",
            (item_uuid, platform, limit)))

    def get_recent_signals(self, since_ts: str, signal_types: tuple = ("BUY", "SELL"),
                           min_confidence: float = 0.0) -> list[dict]:
        placeholders = ",".join("?" for _ in signal_types)
        return _rows(self.conn.execute(
            f"""SELECT s.*, m.market_hash_name, m.name_zh FROM signal s
                JOIN item_master m ON m.item_uuid=s.item_uuid
                WHERE s.ts>=? AND s.signal IN ({placeholders}) AND s.confidence>=?
                ORDER BY s.ts DESC""",
            (since_ts, *signal_types, min_confidence)))

    def get_recent_regimes(self, limit: int = 2) -> list[dict]:
        return _rows(self.conn.execute(
            """SELECT market_regime, ts FROM market_index
               WHERE index_name='csquant_market_score' AND market_regime IS NOT NULL
               ORDER BY ts DESC LIMIT ?""", (limit,)))

    # ---------------- 全市场异动 ----------------
    def get_ticks_series(self, item_uuid: str, platform: str,
                         limit: int = 28) -> list[dict]:
        """某饰品某平台近 N 个采集周期快照（升序）——滚动基准用。"""
        rows = _rows(self.conn.execute(
            """SELECT ts, sell_price, buy_price, sell_count, buy_count,
                      volume_24h, data_quality FROM quote_ticks
               WHERE item_uuid=? AND platform=?
               ORDER BY ts DESC LIMIT ?""",
            (item_uuid, platform, limit)))
        return rows[::-1]

    def get_universe_candidates(self, platform: str = "BUFF") -> list[dict]:
        """Tradable Universe 候选：有 BUFF 最新快照的全部饰品。"""
        return _rows(self.conn.execute(
            """SELECT m.item_uuid, m.market_hash_name, m.name_zh, m.category,
                      sp.sell_price, sp.buy_price, sp.sell_count, sp.buy_count,
                      sp.volume_24h, sp.ts AS price_ts, sp.data_quality
               FROM market_snapshot sp
               JOIN item_master m ON m.item_uuid=sp.item_uuid
               WHERE sp.platform=?""", (platform,)))

    def insert_market_anomaly(self, a: dict) -> None:
        self.conn.execute(
            """INSERT INTO market_anomalies
               (ts, item_uuid, buy_count_change, sell_count_change,
                buy_price_change, sell_price_change, volume_change,
                buy_count_zscore, sell_count_zscore, volume_zscore,
                price_zscore, spread_zscore, cross_platform_sync,
                relative_strength,
                anomaly_score, accumulation_score, distribution_score,
                order_flow_score, liquidity_score, data_quality_score, confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                a.get("ts") or iso_now(), a["item_uuid"],
                a.get("buy_count_change"), a.get("sell_count_change"),
                a.get("buy_price_change"), a.get("sell_price_change"),
                a.get("volume_change"), a.get("buy_count_zscore"),
                a.get("sell_count_zscore"), a.get("volume_zscore"),
                a.get("price_zscore"), a.get("spread_zscore"),
                a.get("cross_platform_sync"), a.get("relative_strength"),
                a.get("anomaly_score"),
                a.get("accumulation_score"), a.get("distribution_score"),
                a.get("order_flow_score"), a.get("liquidity_score"),
                a.get("data_quality_score"), a.get("confidence")))

    def get_latest_anomalies(self, limit: int = 200) -> list[dict]:
        return _rows(self.conn.execute(
            """SELECT a.*, m.market_hash_name, m.name_zh FROM market_anomalies a
               JOIN item_master m ON m.item_uuid=a.item_uuid
               WHERE a.id IN (SELECT MAX(id) FROM market_anomalies GROUP BY item_uuid)
               ORDER BY a.anomaly_score DESC LIMIT ?""", (limit,)))

    def insert_market_signal(self, s: dict) -> None:
        self.conn.execute(
            """INSERT INTO market_signals
               (ts, item_uuid, signal, anomaly_score, accumulation_score,
                distribution_score, order_flow_score, relative_strength,
                market_score, confidence, reason_json, model_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                s.get("ts") or iso_now(), s["item_uuid"], s["signal"],
                s.get("anomaly_score"), s.get("accumulation_score"),
                s.get("distribution_score"), s.get("order_flow_score"),
                s.get("relative_strength"), s.get("market_score"),
                s.get("confidence"),
                s["reason_json"] if isinstance(s["reason_json"], str)
                else json.dumps(s.get("reason_json", {}), ensure_ascii=False),
                s["model_version"]))

    def get_market_signals(self, limit: int = 100) -> list[dict]:
        return _rows(self.conn.execute(
            """SELECT s.*, m.market_hash_name, m.name_zh FROM market_signals s
               JOIN item_master m ON m.item_uuid=s.item_uuid
               WHERE s.id IN (SELECT MAX(id) FROM market_signals GROUP BY item_uuid)
               ORDER BY s.anomaly_score DESC LIMIT ?""", (limit,)))

    def get_open_event(self, item_uuid: str, event_type: str) -> dict | None:
        return _row(self.conn.execute(
            """SELECT * FROM signal_events
               WHERE item_uuid=? AND event_type=? AND state!='RESOLVED'
               ORDER BY last_ts DESC LIMIT 1""",
            (item_uuid, event_type)))

    def upsert_signal_event(self, e: dict) -> None:
        self.conn.execute(
            """INSERT INTO signal_events
               (event_id, item_uuid, event_type, state, severity,
                first_ts, last_ts, peak_score, times_reported, payload_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(event_id) DO UPDATE SET
                 state=excluded.state, severity=excluded.severity,
                 last_ts=excluded.last_ts, peak_score=excluded.peak_score,
                 times_reported=excluded.times_reported,
                 payload_json=excluded.payload_json""",
            (
                e["event_id"], e["item_uuid"], e["event_type"], e["state"],
                e["severity"], e["first_ts"], e["last_ts"], e.get("peak_score"),
                e.get("times_reported", 0),
                e["payload_json"] if isinstance(e.get("payload_json"), str)
                else json.dumps(e.get("payload_json"), ensure_ascii=False)))

    def get_signal_events(self, states: tuple = ("NEW", "ONGOING", "UPGRADED"),
                          limit: int = 100) -> list[dict]:
        placeholders = ",".join("?" for _ in states)
        return _rows(self.conn.execute(
            f"""SELECT e.*, m.market_hash_name, m.name_zh FROM signal_events e
                JOIN item_master m ON m.item_uuid=e.item_uuid
                WHERE e.state IN ({placeholders})
                ORDER BY e.peak_score DESC LIMIT ?""",
            (*states, limit)))

    def insert_broadcast_log(self, log: dict) -> None:
        self.conn.execute(
            """INSERT INTO qq_broadcast_logs
               (ts, router, target_qq_group, message_type, ref_id,
                message, pushed, error)
               VALUES (?,?,?,?,?,?,?,?)""",
            (log.get("ts") or iso_now(), log["router"], log.get("target_qq_group"),
             log["message_type"], log.get("ref_id"), log["message"],
             int(log.get("pushed", 0)), log.get("error")))

    def get_last_broadcast_ts(self, router: str, ref_prefix: str) -> str | None:
        """最近一次【成功推送】时间（ref_id 前缀匹配，失败不计冷却）。"""
        r = self.conn.execute(
            """SELECT ts FROM qq_broadcast_logs
               WHERE router=? AND ref_id LIKE ? AND pushed=1
               ORDER BY id DESC LIMIT 1""",
            (router, f"{ref_prefix}%")).fetchone()
        return r["ts"] if r else None

    def broadcast_pushed_exists(self, router: str, ref_id: str) -> bool:
        """幂等查询：该 ref_id 是否已成功推送过。"""
        r = self.conn.execute(
            """SELECT 1 FROM qq_broadcast_logs
               WHERE router=? AND ref_id=? AND pushed=1 LIMIT 1""",
            (router, ref_id)).fetchone()
        return r is not None

    # ---------------- 资金流水（P3）----------------
    def insert_cash_flow(self, flow_type: str, amount: float,
                         note: str | None = None, ts: str | None = None) -> None:
        if flow_type not in ("DEPOSIT", "WITHDRAWAL"):
            raise ValueError(f"flow_type 必须为 DEPOSIT/WITHDRAWAL: {flow_type}")
        if amount is None or amount <= 0:
            raise ValueError(f"amount 必须为正数: {amount}")
        self.conn.execute(
            "INSERT INTO cash_flow (ts, flow_type, amount, note) VALUES (?,?,?,?)",
            (ts or iso_now(), flow_type, amount, note))

    def get_net_deposit(self) -> float | None:
        """显式资金流水的净注资；无流水记录时返回 None（由调用方降级）。"""
        r = self.conn.execute(
            """SELECT COUNT(*) AS n,
                      COALESCE(SUM(CASE WHEN flow_type='DEPOSIT' THEN amount
                                        ELSE -amount END), 0) AS net
               FROM cash_flow""").fetchone()
        return round(r["net"], 2) if r["n"] else None

    def get_total_buy_cost(self) -> float:
        """历史全部买入投入（卖出不冲减）——无显式流水时的净注资代理口径。"""
        r = self.conn.execute(
            """SELECT COALESCE(SUM(price * quantity + fee), 0) AS cost
               FROM trade_record WHERE side='BUY'""").fetchone()
        return round(r["cost"], 2)

    # ---------------- 信号不可变快照（P3）----------------
    def insert_signal_snapshot(self, payload: dict) -> str:
        snapshot_id = new_id()
        self.conn.execute(
            "INSERT INTO signal_snapshots (snapshot_id, ts, payload_json) VALUES (?,?,?)",
            (snapshot_id, iso_now(),
             json.dumps(payload, ensure_ascii=False)))
        return snapshot_id

    def get_signal_snapshot(self, snapshot_id: str) -> dict | None:
        r = self.conn.execute(
            "SELECT payload_json FROM signal_snapshots WHERE snapshot_id=?",
            (snapshot_id,)).fetchone()
        return json.loads(r["payload_json"]) if r else None

    def get_items_page(self, after_rowid: int = 0, limit: int = 500,
                       require_buff_id: bool = True) -> list[dict]:
        """item_master 分页游标（全市场扫描轮换用，不依赖 watchlist）。"""
        cond = "AND buff_id IS NOT NULL" if require_buff_id else ""
        return _rows(self.conn.execute(
            f"""SELECT rowid AS rid, item_uuid, market_hash_name, csqaq_good_id
                FROM item_master WHERE rowid > ? {cond}
                ORDER BY rowid LIMIT ?""",
            (after_rowid, limit)))

    def get_broadcast_logs(self, router: str | None = None,
                           limit: int = 50) -> list[dict]:
        if router:
            return _rows(self.conn.execute(
                "SELECT * FROM qq_broadcast_logs WHERE router=? "
                "ORDER BY id DESC LIMIT ?", (router, limit)))
        return _rows(self.conn.execute(
            "SELECT * FROM qq_broadcast_logs ORDER BY id DESC LIMIT ?", (limit,)))

    def commit(self) -> None:
        self.conn.commit()
