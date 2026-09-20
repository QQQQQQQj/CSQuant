"""双 QQ 通知器：QQNotifier 基类 → InventoryNotifier / MarketNotifier。

路由纪律（指示文档 §三/§二十四）：
- 两套机器人独立账号、独立群、独立模板、独立开关；
- MarketNotifier 缺失自身配置时 fail-closed（宁可不发，绝不错发到库存群）；
- 每次发送记 qq_broadcast_logs（router 字段审计）。
"""
from __future__ import annotations

from alerter.qq_bot import QQBotClient
from database.dao import Dao


class QQNotifier:
    router = "base"

    def __init__(self, qq_client: QQBotClient | None, dao: Dao | None = None):
        self.qq = qq_client
        self.dao = dao

    @property
    def configured(self) -> bool:
        return self.qq is not None and self.qq.configured

    @property
    def group_id(self) -> str | None:
        return str(self.qq.group_id) if self.qq and self.qq.group_id else None

    def send(self, message: str, message_type: str = "text",
             ref_id: str | None = None) -> tuple[bool, str | None]:
        if not self.configured:
            ok, err = False, "not_configured"
        else:
            ok, err = self.qq.send_group_message(message)
        if self.dao is not None:
            self.dao.insert_broadcast_log({
                "router": self.router, "target_qq_group": self.group_id,
                "message_type": message_type, "ref_id": ref_id,
                "message": message, "pushed": 1 if ok else 0, "error": err})
            self.dao.commit()
        return ok, err

    def status(self) -> dict:
        if self.qq is None:
            return {"online": False, "reason": f"{self.router} 未配置"}
        result = self.qq.health_check()
        result["router"] = self.router
        result["group_id"] = self.group_id
        return result


class InventoryNotifier(QQNotifier):
    """库存播报通道（我的库存/信号/资产）。"""
    router = "inventory"


class MarketNotifier(QQNotifier):
    """全市场异动播报通道（建仓/出货/TOP榜/事件）。"""
    router = "market"


def build_notifiers(cfg, dao: Dao) -> tuple[InventoryNotifier, MarketNotifier]:
    """从配置构建双通道。cfg 为 utils.config.Config。"""
    get = cfg.get
    # Inventory：优先 INVENTORY_*，回退旧 QQ_BOT_*（兼容既有配置）
    inv = InventoryNotifier(QQBotClient(
        get("INVENTORY_QQ_BOT_HTTP") or get("QQ_BOT_HTTP", "http://127.0.0.1:3000"),
        get("INVENTORY_QQ_BOT_TOKEN") or get("QQ_BOT_TOKEN"),
        get("INVENTORY_QQ_GROUP_ID") or get("QQ_GROUP_ID")), dao)
    # Market：只用 MARKET_*，不回退（fail-closed，防止串群）
    mkt = MarketNotifier(QQBotClient(
        get("MARKET_QQ_BOT_HTTP", "http://127.0.0.1:3000"),
        get("MARKET_QQ_BOT_TOKEN"),
        get("MARKET_QQ_GROUP_ID")), dao)
    return inv, mkt
