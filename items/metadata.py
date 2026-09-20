"""饰品元数据同步：CSGO-API（ByMykel）成品 JSON -> item_metadata。

数据源（静态托管，每日构建）：
  https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/{lang}/skins_not_grouped.json
 skins_not_grouped 含 market_hash_name（强制英文），是关联 item_master 的键。
 manifestId 变化才全量同步（存 sync_meta）。
"""
from __future__ import annotations

import json
from typing import Any

import requests

from database.dao import Dao
from utils.logger import get_logger

log = get_logger("csquant.metadata")

BASE_RAW = ("https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api")
TIMEOUT = (5, 30)


def _fetch(url: str) -> Any:
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _first(d: dict, *keys) -> Any:
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def _map_skin(rec: dict) -> dict | None:
    name = _first(rec, "market_hash_name", "name")
    if not name:
        return None
    rarity = rec.get("rarity") or {}
    wears = rec.get("wears") or []
    wear = None
    if isinstance(wears, list) and wears:
        wear = wears[0] if isinstance(wears[0], str) else (
            wears[0].get("name") if isinstance(wears[0], dict) else None)
    collections = rec.get("collections") or []
    crates = rec.get("crates") or []
    return {
        "market_hash_name": name,
        "weapon": _first(rec, "weapon_name", "weapon")
        if isinstance(rec.get("weapon"), str)
        else (rec.get("weapon") or {}).get("name") if isinstance(rec.get("weapon"), dict)
        else _first(rec, "weapon_name"),
        "skin": _first(rec, "pattern", "skin")
        if isinstance(rec.get("pattern"), str)
        else (rec.get("pattern") or {}).get("name") if isinstance(rec.get("pattern"), dict)
        else None,
        "wear": wear if isinstance(wear, str) else None,
        "rarity": rarity.get("name") if isinstance(rarity, dict) else None,
        "rarity_color": rarity.get("color") if isinstance(rarity, dict) else None,
        "collection": (collections[0].get("name") if collections
                       and isinstance(collections[0], dict) else None),
        "crate": (crates[0].get("name") if crates
                  and isinstance(crates[0], dict) else None),
        "phase": rec.get("phase") if isinstance(rec.get("phase"), str) else None,
        "min_float": rec.get("min_float"),
        "max_float": rec.get("max_float"),
        "paint_index": rec.get("paint_index"),
        "image_url": rec.get("image"),
        "extra_json": json.dumps({
            "stattrak": rec.get("stattrak"), "souvenir": rec.get("souvenir"),
        }, ensure_ascii=False),
    }


def sync_metadata(dao: Dao, lang: str = "zh-CN", force: bool = False,
                  base_url: str = BASE_RAW) -> dict:
    """同步 skins_not_grouped 元数据。返回统计。"""
    marker_key = f"metadata_sync_{lang}"
    if not force and dao.get_meta(marker_key):
        log.info("今日已同步元数据（%s），跳过；force=True 可强制", lang)
        return {"skipped": True}

    url = f"{base_url}/{lang}/skins_not_grouped.json"
    log.info("拉取元数据: %s", url)
    data = _fetch(url)
    records = data if isinstance(data, list) else list(data.values())

    matched = created = 0
    for rec in records:
        if not isinstance(rec, dict):
            continue
        mapped = _map_skin(rec)
        if not mapped:
            continue
        item = dao.get_item_by_hash_name(mapped.pop("market_hash_name"))
        if not item:
            continue  # 只给已有 item_master 补元数据（映射由 ID-Mapper 负责）
        mapped["item_uuid"] = item["item_uuid"]
        dao.upsert_metadata(mapped)
        matched += 1
        if matched % 5000 == 0:
            dao.commit()
    dao.commit()

    from utils.timeutils import iso_now
    dao.set_meta(marker_key, iso_now())
    dao.commit()
    report = {"matched": matched, "total_remote": len(records), "lang": lang}
    log.info("元数据同步完成: %s", report)
    return report
