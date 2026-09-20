"""item_master 初始化：导入 SteamTradingSite-ID-Mapper 五平台 730.json。

结构（经 04_ID_Mapper_analysis.md 实测）：
- steam/730.json：34,417 条，含 en_name / cn_name / name_id
- buff|c5|igxe|uuyp/730.json：同键集，值为平台整数 ID，未收录为 -1
- 键为跨平台唯一关联键；c5_id 可能超 JS 安全整数 → 按 TEXT 存
解析器做结构自适应（dict 或 list），键集对齐做抽样校验。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from database.dao import Dao
from utils.logger import get_logger

log = get_logger("csquant.items")

_PLATFORMS = ("buff", "c5", "igxe", "uuyp")


def _load_json(path: Path) -> Any:
    if not path.exists():
        log.warning("映射文件缺失: %s", path)
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_steam(data: Any) -> dict[str, dict]:
    """统一为 {key: {"name_en":..., "name_zh":..., "name_id":...}}。"""
    result: dict[str, dict] = {}
    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, dict):
                result[key] = val
            else:  # key -> name_id
                result[key] = {"name_id": val}
    elif isinstance(data, list):
        for rec in data:
            if not isinstance(rec, dict):
                continue
            key = (rec.get("market_hash_name") or rec.get("en_name")
                   or rec.get("name") or rec.get("key"))
            if key:
                result[str(key)] = rec
    return result


def _normalize_platform(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        out: dict[str, Any] = {}
        for rec in data:
            if isinstance(rec, dict):
                key = (rec.get("market_hash_name") or rec.get("en_name")
                       or rec.get("name") or rec.get("key"))
                if key is not None:
                    out[str(key)] = rec.get("id") or rec.get("goods_id")
        return out
    return {}


def _to_platform_id(v: Any) -> int | str | None:
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("id") or v.get("goods_id")
    if v in (-1, "-1", ""):
        return None
    return v


def _flags_from_name(name: str) -> tuple[bool, bool]:
    return ("StatTrak™" in name or "StatTrak" in name), ("Souvenir" in name or "纪念品" in name)


def _guess_category(name: str) -> str:
    if name.startswith("★") or "Knife" in name or "刀" in name:
        return "knife"
    if "Gloves" in name or "手套" in name:
        return "gloves"
    if "Case" in name or "武器箱" in name:
        return "case"
    if "Sticker" in name or "印花" in name:
        return "sticker"
    if "Capsule" in name or "胶囊" in name:
        return "capsule"
    if "Music Kit" in name or "音乐盒" in name:
        return "music_kit"
    if "Patch" in name or "布章" in name:
        return "patch"
    return "weapon"


def import_from_id_mapper(dao: Dao, mapper_dir: str | Path,
                          app_id: int = 730) -> dict:
    """导入五平台映射到 item_master。返回统计报告。"""
    base = Path(mapper_dir)
    steam = _normalize_steam(_load_json(base / "steam" / f"{app_id}.json"))
    if not steam:
        raise FileNotFoundError(f"steam/{app_id}.json 缺失或为空: {base}")
    platform_maps = {p: _normalize_platform(_load_json(base / p / f"{app_id}.json"))
                     for p in _PLATFORMS}

    # 键集对齐抽样校验（04 分析结论：四平台键集与 steam 完全一致）
    steam_keys = set(steam)
    align = {p: len(steam_keys & set(m)) / max(len(steam_keys), 1)
             for p, m in platform_maps.items() if m}

    count = 0
    for key, rec in steam.items():
        is_st, is_svn = _flags_from_name(key)
        dao.upsert_item_master({
            "app_id": app_id,
            "market_hash_name": rec.get("market_hash_name") or key,
            "name_en": rec.get("en_name") or rec.get("name_en"),
            "name_zh": rec.get("cn_name") or rec.get("name_zh"),
            "category": _guess_category(key),
            "is_stattrak": is_st, "is_souvenir": is_svn,
            "steam_name_id": _to_platform_id(rec.get("name_id") or rec.get("id")),
            "buff_id": _to_platform_id(platform_maps.get("buff", {}).get(key)),
            "c5_id": (str(v) if (v := _to_platform_id(
                platform_maps.get("c5", {}).get(key))) is not None else None),
            "igxe_id": _to_platform_id(platform_maps.get("igxe", {}).get(key)),
            "uuyp_id": _to_platform_id(platform_maps.get("uuyp", {}).get(key)),
        })
        count += 1
        if count % 5000 == 0:
            dao.commit()
            log.info("item_master 导入进度: %s", count)
    dao.commit()
    report = {"imported": count, "key_align_ratio": align}
    log.info("item_master 导入完成: %s", report)
    return report


def update_csqaq_good_ids(dao: Dao, csqaq_provider, max_pages: int = 200) -> int:
    """运行期增量补录 csqaq_good_id（POST /info/get_good_id 分页）。"""
    updated = 0
    for page_items in csqaq_provider.iter_good_ids(max_pages=max_pages):
        for rec in page_items:
            if not isinstance(rec, dict):
                continue
            name = rec.get("market_hash_name") or rec.get("en_name")
            good_id = rec.get("good_id") or rec.get("id")
            if not name or not good_id:
                continue
            item = dao.get_item_by_hash_name(str(name))
            if item and item.get("csqaq_good_id") != good_id:
                dao.upsert_item_master({"market_hash_name": str(name),
                                        "csqaq_good_id": int(good_id)})
                updated += 1
        dao.commit()
    log.info("csqaq_good_id 补录: %s 条", updated)
    return updated
