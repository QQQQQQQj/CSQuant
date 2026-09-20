"""纯 stdlib 回测引擎（CS 饰品语义，long-only）。

正确性规则：
- 每个饰品按【自身时间轴】定位信号：t 信号只能在该饰品自身的下一可交易日成交
  （稀疏日期饰品不得与全局日历错位）；
- 缺失成交量不伪造：volume=None 视为不可交易（流动性过滤）；
- 买入价 = ask*(1+slippage)；卖出价 = bid*(1-slippage)；无 bid 当日不可卖；
- 年化收益按【真实日历跨度】计算（dates 首末差），非 bar 根数；
- benchmark 对齐输出 benchmark_return 与 excess_return。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date

from quant.factors.indicators import max_drawdown, sharpe_ratio, sortino_ratio
from utils.fees import apply_fee


@dataclass
class Bar:
    ts: str
    close: float
    bid: float | None = None      # 最高求购价（卖出能拿到的价）
    ask: float | None = None      # 最低在售价（买入要付的价）
    volume: float | None = None


@dataclass
class Trade:
    item_uuid: str
    side: str
    quantity: int
    price: float
    fee: float
    ts: str


@dataclass
class BacktestResult:
    dates: list[str]
    equity: list[float]
    trades: list[Trade]
    stats: dict = field(default_factory=dict)


def _slippage_for(volume: float | None, bands: dict) -> float:
    if volume is None:
        return bands.get("low_liquidity", 0.03)
    if volume >= 50:
        return bands.get("high_liquidity", 0.005)
    if volume >= 10:
        return bands.get("mid_liquidity", 0.01)
    return bands.get("low_liquidity", 0.03)


def run_backtest(bars_by_item: dict[str, list[Bar]],
                 entries: dict[str, list[bool]],
                 exits: dict[str, list[bool]],
                 cfg: dict, fee_table: dict,
                 platform: str = "BUFF",
                 benchmark_closes: list[float] | None = None) -> BacktestResult:
    init_cash = cfg.get("init_cash", 100000.0)
    max_pos = cfg.get("max_position_per_item", 0.2)
    min_volume = cfg.get("liquidity_filter_min_volume", 3)
    slippage_bands = cfg.get("slippage_bands",
                             {"high_liquidity": 0.005, "mid_liquidity": 0.01,
                              "low_liquidity": 0.03})

    all_dates = sorted({b.ts for bars in bars_by_item.values() for b in bars})
    bar_map: dict[str, dict[str, Bar]] = {
        item: {b.ts: b for b in bars} for item, bars in bars_by_item.items()}
    # 每个饰品自身时间轴：日期 -> 该饰品自己的 bar 序号（信号索引空间）
    own_idx: dict[str, dict[str, int]] = {
        item: {b.ts: i for i, b in enumerate(bars)}
        for item, bars in bars_by_item.items()}

    cash = init_cash
    holdings: dict[str, tuple[int, float]] = {}   # item -> (qty, cost_price)
    last_close: dict[str, float] = {}             # 估值用最近已知收盘
    trades: list[Trade] = []
    equity_curve: list[float] = []
    fee_total = 0.0
    slippage_total = 0.0
    turnover_value = 0.0

    for date in all_dates:
        # ---- 执行信号：t（该饰品上一自身交易日）→ 当日成交（严格逐饰品 T+1）----
        for item in bars_by_item:
            k = own_idx[item].get(date)
            if k is None or k == 0:
                continue   # 当日非该饰品交易日 / 无自身前一日
            sig_i = k - 1
            bar = bar_map[item][date]
            ent = entries.get(item, [])
            ext = exits.get(item, [])
            # 缺失成交量按不可交易处理（不伪造）
            liquid = bar.volume is not None and bar.volume >= min_volume
            slip = _slippage_for(bar.volume, slippage_bands)
            # 卖出（先卖释放现金）
            if item in holdings and sig_i < len(ext) and ext[sig_i]:
                if bar.bid and liquid:
                    qty, _ = holdings.pop(item)
                    gross = bar.bid * (1 - slip) * qty
                    net = apply_fee(bar.bid * (1 - slip), platform, fee_table) * qty
                    fee = gross - net
                    fee_total += fee
                    slippage_total += bar.bid * slip * qty
                    cash += net
                    turnover_value += gross
                    trades.append(Trade(item, "SELL", qty,
                                        round(bar.bid * (1 - slip), 4),
                                        round(fee, 2), date))
            # 买入
            elif item not in holdings and sig_i < len(ent) and ent[sig_i]:
                if bar.ask and liquid:
                    price = bar.ask * (1 + slip)
                    equity_now = cash + sum(
                        q * last_close.get(i, c)
                        for i, (q, c) in holdings.items())
                    budget = min(cash, equity_now * max_pos)
                    qty = int(budget // price)
                    if qty >= 1:
                        cost = price * qty
                        cash -= cost
                        slippage_total += bar.ask * slip * qty
                        turnover_value += cost
                        holdings[item] = (qty, price)
                        trades.append(Trade(item, "BUY", qty,
                                            round(price, 4), 0.0, date))
        # ---- 更新最近收盘并记录权益 ----
        for item in bars_by_item:
            bar = bar_map[item].get(date)
            if bar is not None:
                last_close[item] = bar.close
        equity = cash
        for item, (qty, cost) in holdings.items():
            equity += qty * last_close.get(item, cost)
        equity_curve.append(round(equity, 2))

    stats = compute_stats(equity_curve, trades, init_cash, fee_total,
                          slippage_total, turnover_value, benchmark_closes,
                          dates=all_dates)
    return BacktestResult(all_dates, equity_curve, trades, stats)


def _calendar_span_days(dates: list[str] | None) -> int | None:
    """首末日期的真实日历跨度（天）。解析失败返回 None。"""
    if not dates or len(dates) < 2:
        return None
    try:
        first = _date.fromisoformat(dates[0][:10])
        last = _date.fromisoformat(dates[-1][:10])
    except ValueError:
        return None
    span = (last - first).days
    return span if span > 0 else None


def compute_stats(equity: list[float], trades: list[Trade], init_cash: float,
                  fee_total: float, slippage_total: float,
                  turnover_value: float,
                  benchmark_closes: list[float] | None = None,
                  dates: list[str] | None = None) -> dict:
    if len(equity) < 2:
        return {"error": "insufficient_data"}
    total_return = equity[-1] / init_cash - 1
    # 年化按真实日历跨度；无法解析日期时退回 bar 根数（并明确记录口径）
    span = _calendar_span_days(dates)
    basis_days = span if span is not None else len(equity)
    annualized = ((1 + total_return) ** (365 / basis_days) - 1
                  if basis_days and basis_days > 0 else None)
    returns = [equity[i] / equity[i - 1] - 1 if equity[i - 1] else None
               for i in range(1, len(equity))]
    mdd = max_drawdown(equity)
    # 逐笔配对算胜率/盈亏比
    wins = losses = 0
    gross_win = gross_loss = 0.0
    buy_map: dict[str, list[Trade]] = {}
    for t in trades:
        if t.side == "BUY":
            buy_map.setdefault(t.item_uuid, []).append(t)
        else:
            buys = buy_map.get(t.item_uuid) or []
            if buys:
                b = buys.pop(0)
                pnl = (t.price - t.fee / max(t.quantity, 1) - b.price) * t.quantity
                if pnl > 0:
                    wins += 1
                    gross_win += pnl
                else:
                    losses += 1
                    gross_loss += abs(pnl)
    closed = wins + losses
    bench_ret = (benchmark_closes[-1] / benchmark_closes[0] - 1
                 if benchmark_closes and len(benchmark_closes) >= 2
                 and benchmark_closes[0] else None)
    return {
        "total_return": round(total_return, 4),
        "annualized_return": round(annualized, 4) if annualized is not None else None,
        "annualization_basis_days": basis_days,
        "max_drawdown": mdd,
        "sharpe": sharpe_ratio(returns),
        "sortino": sortino_ratio(returns),
        "calmar": (round(annualized / mdd, 3)
                   if (annualized is not None and mdd) else None),
        "win_rate": round(wins / closed, 4) if closed else None,
        "profit_factor": (round(gross_win / gross_loss, 3)
                          if gross_loss > 0 else (None if gross_win == 0 else 999.0)),
        "trade_count": len(trades),
        "turnover": round(turnover_value / init_cash, 3),
        "fee_cost": round(fee_total, 2),
        "slippage_cost": round(slippage_total, 2),
        "benchmark_return": round(bench_ret, 4) if bench_ret is not None else None,
        "excess_return": (round(total_return - bench_ret, 4)
                          if bench_ret is not None else None),
    }
