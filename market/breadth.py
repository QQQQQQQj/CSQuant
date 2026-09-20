"""市场宽度与品类强弱。"""
from __future__ import annotations


def breadth_ratio(up: int | None, down: int | None) -> float | None:
    if not up and not down:
        return None
    up = up or 0
    down = down or 0
    if down == 0:
        return float(up) if up else None
    return round(up / down, 3)


def breadth_score(up: int | None, down: int | None) -> float | None:
    """上涨家数占比 -> 0-100。缺失返回 None（不为 0）。"""
    if up is None or down is None or (up + down) == 0:
        return None
    return round(100.0 * up / (up + down), 2)


def category_strength(sub_indices: list[dict]) -> list[dict]:
    """子指数/品类按涨跌幅排序（强->弱）。"""
    valid = [s for s in sub_indices if s.get("change") is not None]
    return sorted(valid, key=lambda s: s["change"], reverse=True)
