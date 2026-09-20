"""技术指标库（纯 stdlib，list-based，零依赖）。

移植/对齐 CSGOTrading technical.py 的六个指标（SMA/EMA/RSI/MACD/BB/动量），
去掉 LangChain 依赖。约定：
- 输入 closes 为按时间升序的 float 列表（调用方负责清洗掉 None）；
- 输出与输入等长，前 period-1 个为 None（数据不足段）。
"""
from __future__ import annotations

import math


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            window = values[i + 1 - period: i + 1]
            out.append(sum(window) / period)
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    if not values:
        return []
    k = 2 / (period + 1)
    out: list[float | None] = []
    prev: float | None = None
    for i, v in enumerate(values):
        if i + 1 < period:
            out.append(None)
            continue
        if prev is None:
            prev = sum(values[i + 1 - period: i + 1]) / period
        else:
            prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains, losses = [], []
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def macd(closes: list[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple[list[float | None], list[float | None], list[float | None]]:
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid = [v for v in macd_line if v is not None]
    signal_valid = ema(valid, signal) if len(valid) >= signal else [None] * len(valid)
    signal_line: list[float | None] = []
    it = iter(signal_valid)
    for v in macd_line:
        signal_line.append(next(it) if v is not None else None)
    hist = [(m - s) if (m is not None and s is not None) else None
            for m, s in zip(macd_line, signal_line)]
    return macd_line, signal_line, hist


def bollinger(closes: list[float], period: int = 20,
              num_std: float = 2.0) -> tuple[list[float | None], list[float | None], list[float | None]]:
    mid = sma(closes, period)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for i, m in enumerate(mid):
        if m is None:
            upper.append(None)
            lower.append(None)
            continue
        window = closes[i + 1 - period: i + 1]
        mean = m
        var = sum((x - mean) ** 2 for x in window) / period
        sd = math.sqrt(var)
        upper.append(mean + num_std * sd)
        lower.append(mean - num_std * sd)
    return mid, upper, lower


def momentum(closes: list[float], period: int) -> list[float | None]:
    """N 日涨跌幅（比率）。"""
    out: list[float | None] = []
    for i in range(len(closes)):
        if i < period or closes[i - period] == 0:
            out.append(None)
        else:
            out.append(closes[i] / closes[i - period] - 1.0)
    return out


def zscore(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
            continue
        window = values[i + 1 - period: i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        sd = math.sqrt(var)
        out.append(None if sd == 0 else (values[i] - mean) / sd)
    return out


def percentile_rank(history: list[float], value: float) -> float | None:
    """value 在 history 中的分位（0-1）。"""
    if not history:
        return None
    below = sum(1 for v in history if v <= value)
    return below / len(history)


def daily_returns(closes: list[float]) -> list[float | None]:
    out: list[float | None] = [None]
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        out.append(None if prev == 0 else closes[i] / prev - 1.0)
    return out


def volatility(returns: list[float | None]) -> float | None:
    valid = [r for r in returns if r is not None]
    if len(valid) < 2:
        return None
    mean = sum(valid) / len(valid)
    var = sum((r - mean) ** 2 for r in valid) / (len(valid) - 1)
    return math.sqrt(var)


def max_drawdown(equity: list[float]) -> float | None:
    """最大回撤（正数比率）。长期零波动序列返回 0。"""
    if not equity:
        return None
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return round(mdd, 4)


def sharpe_ratio(returns: list[float | None], periods_per_year: int = 365) -> float | None:
    valid = [r for r in returns if r is not None]
    if len(valid) < 2:
        return None
    mean = sum(valid) / len(valid)
    sd = volatility(valid)
    if not sd:
        return None
    return round(mean / sd * math.sqrt(periods_per_year), 3)


def sortino_ratio(returns: list[float | None], periods_per_year: int = 365) -> float | None:
    """下行波动率版本（CS 饰品长期零收益时比 Sharpe 更稳）。"""
    valid = [r for r in returns if r is not None]
    if len(valid) < 2:
        return None
    mean = sum(valid) / len(valid)
    downside = [r for r in valid if r < 0]
    if not downside:
        return None
    dd = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
    if dd == 0:
        return None
    return round(mean / dd * math.sqrt(periods_per_year), 3)
