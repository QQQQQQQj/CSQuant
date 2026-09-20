"""SteamID 校验与转换：32 位账户 ID ↔ 64 位 SteamID。"""
from __future__ import annotations

STEAM64_BASE = 76561197960265728


def normalize_steam_id(value: str | int) -> str:
    """接受 32 位账户 ID 或 64 位 SteamID，统一返回合法的 64 位字符串。

    校验规则：纯数字；64 位必须以 7656 开头且长度 17；
    32 位（≤10 位数字）自动加基数转换。非法输入抛 ValueError。
    """
    s = str(value).strip()
    if not s or not s.isdigit():
        raise ValueError(f"SteamID 必须为纯数字: {value!r}")
    if len(s) <= 10:
        return str(STEAM64_BASE + int(s))
    if len(s) == 17 and s.startswith("7656"):
        return s
    raise ValueError(f"无法识别的 SteamID（既非32位账户ID也非64位ID）: {value!r}")
