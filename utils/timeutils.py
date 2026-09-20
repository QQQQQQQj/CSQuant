"""时间工具：全系统统一 UTC ISO8601 存储，展示层自行转 UTC+8。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC = timezone.utc
CN_TZ = timezone(timedelta(hours=8))


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def days_ago(days: float) -> datetime:
    return utc_now() - timedelta(days=days)


def iso_days_ago(days: float) -> str:
    return to_iso(days_ago(days))


def to_cn_str(s: str) -> str:
    """UTC ISO 字符串 -> UTC+8 展示字符串。"""
    return parse_iso(s).astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M")


def seconds_since(s: str) -> float:
    return (utc_now() - parse_iso(s)).total_seconds()
