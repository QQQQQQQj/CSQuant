"""事件生命周期状态机：NEW/ONGOING/UPGRADED/DOWNGRADED/RESOLVED。

防刷屏（指示文档 §二十二）：同一饰品同一事件类型，
只在"等级变化"时再次播报（同级 ONGOING 静默）。
"""
from __future__ import annotations

import uuid

SEVERITY_ORDER = {"NONE": 0, "WATCH": 1, "MID": 2, "MAJOR": 3, "CRITICAL": 4}


def severity_of(score: float | None) -> str:
    s = score or 0.0
    if s >= 92:
        return "CRITICAL"
    if s >= 85:
        return "MAJOR"
    if s >= 75:
        return "MID"
    if s >= 60:
        return "WATCH"
    return "NONE"


def _fields(open_event: dict, state: str, severity: str, score: float,
            now_iso: str, times_reported: int) -> dict:
    return {
        "event_id": open_event["event_id"],
        "item_uuid": open_event["item_uuid"],
        "event_type": open_event["event_type"],
        "state": state,
        "severity": severity,
        "first_ts": open_event["first_ts"],
        "last_ts": now_iso,
        "peak_score": max(open_event.get("peak_score") or 0.0, score),
        "times_reported": times_reported,
        "payload_json": open_event.get("payload_json") or "{}",
    }


def event_step(open_event: dict | None, event_type: str, item_uuid: str,
               score: float, now_iso: str,
               payload: dict | None = None) -> dict | None:
    """推进一步。返回 None（无需动作）或
    {"action": "emit"/"silent", "emit_reason": str, "event_fields": dict}。"""
    import json
    sev = severity_of(score)
    payload_json = json.dumps(payload or {}, ensure_ascii=False)

    if open_event is None:
        if sev == "NONE":
            return None
        return {
            "action": "emit", "emit_reason": "NEW",
            "event_fields": {
                "event_id": str(uuid.uuid4()), "item_uuid": item_uuid,
                "event_type": event_type, "state": "NEW", "severity": sev,
                "first_ts": now_iso, "last_ts": now_iso, "peak_score": score,
                "times_reported": 1, "payload_json": payload_json}}

    base_times = open_event.get("times_reported", 0)
    old_sev = open_event.get("severity", "WATCH")

    if sev == "NONE":
        return {"action": "emit", "emit_reason": "RESOLVED",
                "event_fields": _fields(open_event, "RESOLVED", "NONE", score,
                                        now_iso, base_times + 1)}
    if SEVERITY_ORDER[sev] > SEVERITY_ORDER.get(old_sev, 1):
        return {"action": "emit", "emit_reason": "UPGRADED",
                "event_fields": _fields(open_event, "UPGRADED", sev, score,
                                        now_iso, base_times + 1)}
    if SEVERITY_ORDER[sev] < SEVERITY_ORDER.get(old_sev, 1):
        return {"action": "emit", "emit_reason": "DOWNGRADED",
                "event_fields": _fields(open_event, "DOWNGRADED", sev, score,
                                        now_iso, base_times + 1)}
    return {"action": "silent", "emit_reason": "ONGOING",
            "event_fields": _fields(open_event, "ONGOING", sev, score,
                                    now_iso, base_times)}
