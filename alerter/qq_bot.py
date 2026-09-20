"""QQ Bot 客户端（OneBot 11 HTTP 协议）。

适配 NapCat / Lagrange.Core 本地 HTTP 服务（默认 http://127.0.0.1:3000）。
安全：仅监听本机回环地址；access_token 走 .env；必须用小号登录。
"""
from __future__ import annotations

import requests


class QQBotClient:
    def __init__(self, http_base: str, access_token: str | None = None,
                 group_id: str | int | None = None,
                 timeout: tuple[float, float] = (3.0, 10.0),
                 session: requests.Session | None = None):
        self.http_base = (http_base or "").rstrip("/")
        self.access_token = access_token
        self.group_id = group_id
        self.timeout = timeout
        self.session = session or requests.Session()

    @property
    def _headers(self) -> dict:
        return ({"Authorization": f"Bearer {self.access_token}"}
                if self.access_token else {})

    @property
    def configured(self) -> bool:
        return bool(self.http_base and self.group_id)

    def health_check(self) -> dict:
        """GET /get_status → 在线状态。未配置/离线/异常均返回结构化结果。"""
        if not self.http_base:
            return {"online": False, "reason": "QQ_BOT_HTTP 未配置"}
        try:
            resp = self.session.get(f"{self.http_base}/get_status",
                                    headers=self._headers, timeout=self.timeout)
            data = resp.json()
            online = bool((data.get("data") or {}).get("online"))
            return {"online": online,
                    "reason": None if online else "NapCat 在线状态为 false"}
        except Exception as e:  # noqa: BLE001
            return {"online": False, "reason": f"连接失败: {e}"}

    def send_group_message(self, text: str,
                           group_id: str | int | None = None) -> tuple[bool, str | None]:
        """POST /send_group_msg。返回 (成功?, 错误信息)。"""
        gid = group_id or self.group_id
        if not self.http_base:
            return False, "QQ_BOT_HTTP 未配置"
        if not gid:
            return False, "QQ_GROUP_ID 未配置"
        try:
            resp = self.session.post(
                f"{self.http_base}/send_group_msg",
                json={"group_id": int(gid), "message": text},
                headers=self._headers, timeout=self.timeout)
            data = resp.json()
            if resp.status_code == 200 and data.get("retcode") == 0:
                return True, None
            return False, f"retcode={data.get('retcode')} {data.get('msg', '')}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)
