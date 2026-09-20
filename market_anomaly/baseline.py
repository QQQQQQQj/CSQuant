"""滚动历史基准：Rolling Mean/Median/Std + Z-Score + Percentile。

防误报（指示文档 §九/§十/§十八）：
- 小基数变化不显著（2→4 的 +100% 意义很弱）；
- NULL 恢复产生的跳变不计 Z-Score（数据刚恢复，不是市场行为）；
- 样本不足（<8 有效点）不出 Z-Score。
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class BaselineStats:
    n: int
    mean: float | None
    std: float | None
    median: float | None


def _valid(values: list[float | None]) -> list[float]:
    return [v for v in values if v is not None]


def series_stats(values: list[float | None]) -> BaselineStats:
    valid = _valid(values)
    n = len(valid)
    if n == 0:
        return BaselineStats(0, None, None, None)
    mean = sum(valid) / n
    if n < 2:
        return BaselineStats(n, mean, None, valid[0])
    var = sum((v - mean) ** 2 for v in valid) / (n - 1)
    ordered = sorted(valid)
    mid = n // 2
    median = (ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2)
    return BaselineStats(n, mean, math.sqrt(var), median)


def zscore_of(value: float | None, stats: BaselineStats,
              min_samples: int = 8) -> float | None:
    """样本不足或零波动返回 None（不强行打分）。"""
    if value is None or stats.std is None or stats.std == 0:
        return None
    if stats.n < min_samples:
        return None
    return round((value - stats.mean) / stats.std, 3)


def percentile_of(value: float | None, values: list[float | None]) -> float | None:
    valid = _valid(values)
    if value is None or not valid:
        return None
    below = sum(1 for v in valid if v <= value)
    return round(below / len(valid), 4)


def significant_change(old: float | None, new: float | None,
                       min_abs: float = 10.0,
                       min_base: float = 20.0) -> tuple[bool, float | None, float | None]:
    """小基数过滤。返回 (是否显著, 绝对变化, 百分比变化%)。

    规则：|new-old| < min_abs 且 old < min_base → 不显著（如求购 2→4）。
    """
    if old is None or new is None:
        return False, None, None
    abs_change = new - old
    pct_change = (abs_change / old * 100) if old > 0 else None
    if abs(abs_change) < min_abs and old < min_base:
        return False, abs_change, pct_change
    return True, abs_change, (round(pct_change, 2) if pct_change is not None else None)


def null_recovered(series_values: list[float | None]) -> bool:
    """检测"刚从 NULL 恢复"：最新值非 None 且前一个样本为 None。"""
    if len(series_values) < 2:
        return False
    return series_values[-1] is not None and series_values[-2] is None
