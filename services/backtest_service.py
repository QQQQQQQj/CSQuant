"""服务层：回测（数据准备 -> 策略 -> 引擎 -> 落库 -> 报告）。

正确性规则：
- strategy 与 params 必须真正参与执行（策略注册表 + 参数透传）；
- 缺失成交量保持 None（引擎按不可交易处理），禁止伪造为 10；
- benchmark 取大盘指数同期收盘，输出 benchmark_return 与 excess_return。
"""
from __future__ import annotations

import json

from database.dao import Dao
from quant.backtest.engine import Bar, run_backtest, BacktestResult
from quant.factors.indicators import ema, rsi

DEFAULT_HALF_SPREAD = 0.015   # bid/ask 缺失时的半价差估计（假设，params 可覆盖）


def build_bars(klines: list[dict], half_spread: float = DEFAULT_HALF_SPREAD) -> list[Bar]:
    bars: list[Bar] = []
    for k in klines:
        close = k["close"]
        # K线无 bid/ask：按估计半价差推导（保守假设，报告声明）
        # 缺失成交量保持 None：引擎视为不可交易，绝不伪造数值
        bars.append(Bar(ts=k["ts"][:10], close=close,
                        bid=round(close * (1 - half_spread), 4),
                        ask=round(close * (1 + half_spread), 4),
                        volume=k.get("volume")))
    return bars


def strategy_ema_rsi(closes: list[float], fast: int = 20, slow: int = 60,
                     rsi_period: int = 14, rsi_entry_max: float = 70.0,
                     rsi_exit_min: float = 80.0) -> tuple[list[bool], list[bool]]:
    """E1 基准策略：EMA(fast)>EMA(slow) 且 RSI<entry_max -> entry；
    EMA(fast)<EMA(slow) 或 RSI>exit_min -> exit。参数全部可调。"""
    e_fast = ema(closes, fast)
    e_slow = ema(closes, min(slow, max(len(closes) - 1, 2)))
    r = rsi(closes, rsi_period)
    entries: list[bool] = []
    exits: list[bool] = []
    for i in range(len(closes)):
        if e_fast[i] is None or e_slow[i] is None:
            entries.append(False)
            exits.append(False)
            continue
        entries.append(e_fast[i] > e_slow[i]
                       and (r[i] is not None and r[i] < rsi_entry_max))
        exits.append(e_fast[i] < e_slow[i]
                     or (r[i] is not None and r[i] > rsi_exit_min))
    return entries, exits


STRATEGIES = {
    "default": strategy_ema_rsi,
    "ema_rsi": strategy_ema_rsi,
}

_STRATEGY_PARAM_KEYS = ("fast", "slow", "rsi_period", "rsi_entry_max",
                        "rsi_exit_min")


class BacktestService:
    def __init__(self, dao: Dao, cfg: dict):
        self.dao = dao
        self.cfg = cfg

    def _benchmark_closes(self, start: str, end: str) -> list[float]:
        """大盘指数同期收盘（按日期对齐回测区间）。"""
        history = (self.dao.get_market_index_history("csqaq_main", limit=2000)
                   or self.dao.get_market_index_history("steamdt_broad",
                                                        limit=2000))
        return [h["index_value"] for h in history
                if h.get("index_value")
                and start <= h["ts"][:10] <= end]

    def run_backtest(self, strategy: str = "default",
                     params: dict | None = None,
                     universe: str = "watchlist",
                     limit_days: int = 365) -> dict:
        strategy_fn = STRATEGIES.get(strategy)
        if strategy_fn is None:
            return {"error": "unknown_strategy",
                    "message": f"未知策略 {strategy}，可用: {list(STRATEGIES)}"}
        params = dict(params or {})
        half_spread = float(params.pop("half_spread", DEFAULT_HALF_SPREAD))
        strategy_params = {k: params[k] for k in _STRATEGY_PARAM_KEYS
                           if k in params}

        items = self.dao.get_watchlist(tiers=("L1", "L2")) \
            if universe == "watchlist" else self.dao.get_watchlist()
        bars_by_item: dict[str, list[Bar]] = {}
        closes_by_item: dict[str, list[float]] = {}
        for row in items:
            klines = self.dao.get_klines(row["item_uuid"], "BUFF", "1d",
                                         limit=limit_days)
            if len(klines) < 30:
                continue
            bars_by_item[row["item_uuid"]] = build_bars(klines, half_spread)
            closes_by_item[row["item_uuid"]] = [k["close"] for k in klines]
        if not bars_by_item:
            return {"error": "no_data",
                    "message": "自选池无足够K线数据（>=30天），请先积累数据"}

        entries: dict[str, list[bool]] = {}
        exits: dict[str, list[bool]] = {}
        for item_uuid, closes in closes_by_item.items():
            e, x = strategy_fn(closes, **strategy_params)
            entries[item_uuid] = e
            exits[item_uuid] = x

        bt_cfg = dict(self.cfg.get("backtest", {}))
        bt_cfg["slippage_bands"] = self.cfg["slippage_bands"]
        all_dates = sorted({b.ts for bars in bars_by_item.values() for b in bars})
        benchmark = self._benchmark_closes(all_dates[0], all_dates[-1])
        result: BacktestResult = run_backtest(
            bars_by_item, entries, exits, bt_cfg,
            self.cfg["fee_table"], platform="BUFF",
            benchmark_closes=benchmark or None)

        warnings = []
        if len(result.dates) < 90:
            warnings.append("回测区间 <90 天，结论不可信（数据积累期）")
        warnings.append("bid/ask 为估计半价差推导，非真实盘口（假设待校准）")
        if not benchmark:
            warnings.append("大盘指数无同期数据，benchmark/excess 为空")

        strategy_version = (self.cfg.get("_config_version")
                            or self.cfg.get("model_version", ""))
        backtest_id = self.dao.insert_backtest_result({
            "strategy_name": strategy,
            "strategy_version": strategy_version,
            "start_date": result.dates[0] if result.dates else "",
            "end_date": result.dates[-1] if result.dates else "",
            "params_json": {"strategy": strategy,
                            "strategy_params": strategy_params,
                            "half_spread": half_spread,
                            "universe": universe},
            "equity_json": json.dumps({"dates": result.dates,
                                       "equity": result.equity}),
            **{k: v for k, v in result.stats.items()
               if k not in ("annualization_basis_days", "excess_return")},
        })
        for t in result.trades:
            self.dao.insert_backtest_trade(backtest_id, {
                "item_uuid": t.item_uuid, "side": t.side,
                "quantity": t.quantity, "price": t.price, "fee": t.fee,
                "traded_at": t.ts})
        self.dao.commit()
        return {"backtest_id": backtest_id, "warnings": warnings,
                **result.stats}

    def get_backtest_result(self, backtest_id: str) -> dict | None:
        row = self.dao.get_backtest(backtest_id)
        if row:
            row["params_json"] = json.loads(row.get("params_json") or "{}")
            row["equity_json"] = json.loads(row.get("equity_json") or "{}")
        return row

    def list_backtests(self, limit: int = 20) -> list[dict]:
        return self.dao.list_backtests(limit)

    def get_backtest_trades(self, backtest_id: str) -> list[dict]:
        return self.dao.get_backtest_trades(backtest_id)
