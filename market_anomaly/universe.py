"""Tradable Universe：可量化饰品池过滤与分级（Tier A/B/C）。

原则（指示文档 §五）：不监控垃圾饰品——流动性极低、求购接近 0、
价格长期不更新的饰品直接排除，不得产生"建仓"类信号。
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULTS = {
    "min_sell_count": 5,
    "min_buy_count": 3,
    "min_price": 1.0,
    "min_liquidity_score": 40.0,
    "tier_a_liquidity": 75.0,
    "tier_a_volume": 50,
    "tier_b_liquidity": 55.0,
}


@dataclass
class UniverseEntry:
    item_uuid: str
    market_hash_name: str
    name_zh: str | None
    category: str | None
    tier: str                      # A / B / C
    liquidity_score: float
    sell_price: float | None
    sell_count: int | None
    buy_count: int | None
    volume_24h: int | None


def liquidity_score_of(sell_count: int | None, buy_count: int | None,
                       volume_24h: int | None) -> float:
    """流动性评分 0-100：在售深度 40% + 求购深度 30% + 日成交量 30%。"""
    sc = min((sell_count or 0) / 200.0, 1.0) * 40
    bc = min((buy_count or 0) / 150.0, 1.0) * 30
    vol = min((volume_24h or 0) / 100.0, 1.0) * 30
    return round(sc + bc + vol, 2)


def passes_filter(candidate: dict, cfg: dict | None = None) -> bool:
    """硬性过滤：数量/价格下限。任一字段缺失按不满足处理（不为0纪律）。"""
    c = {**DEFAULTS, **(cfg or {})}
    if (candidate.get("sell_count") or 0) < c["min_sell_count"]:
        return False
    if (candidate.get("buy_count") or 0) < c["min_buy_count"]:
        return False
    if (candidate.get("sell_price") or 0) < c["min_price"]:
        return False
    return True


def tier_of(liquidity_score: float, volume_24h: int | None,
            cfg: dict | None = None) -> str:
    c = {**DEFAULTS, **(cfg or {})}
    if liquidity_score >= c["tier_a_liquidity"] or (volume_24h or 0) >= c["tier_a_volume"]:
        return "A"
    if liquidity_score >= c["tier_b_liquidity"]:
        return "B"
    return "C"


def build_universe(candidates: list[dict], cfg: dict | None = None) -> list[UniverseEntry]:
    """候选行 -> 过滤 -> 评分 -> 分级。"""
    c = {**DEFAULTS, **(cfg or {})}
    universe: list[UniverseEntry] = []
    for cand in candidates:
        if not passes_filter(cand, c):
            continue
        liq = liquidity_score_of(cand.get("sell_count"), cand.get("buy_count"),
                                 cand.get("volume_24h"))
        if liq < c["min_liquidity_score"]:
            continue
        universe.append(UniverseEntry(
            item_uuid=cand["item_uuid"],
            market_hash_name=cand["market_hash_name"],
            name_zh=cand.get("name_zh"),
            category=cand.get("category"),
            tier=tier_of(liq, cand.get("volume_24h"), c),
            liquidity_score=liq,
            sell_price=cand.get("sell_price"),
            sell_count=cand.get("sell_count"),
            buy_count=cand.get("buy_count"),
            volume_24h=cand.get("volume_24h"),
        ))
    return universe
