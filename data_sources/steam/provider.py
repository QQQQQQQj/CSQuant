"""Steam 官方市场 Provider（低频校验 + 库存同步）。

接口均为未文档化页面接口（不稳定，标注于 DATA_SOURCE_SPEC）：
- priceoverview：免登录，IP 级限频严格（429 风险高）
- /inventory/{steamid64}/730/2：公开库存匿名可取（限频收紧，待验证）
纪律：rate ≤ 0.4/s + 抖动；429 后熔断 30min；Cookie 调用建议专用小号。
"""
from __future__ import annotations

from data_sources.base import (
    ItemRef, MarketDataProvider, ProviderCapabilities, ProviderHealth, UnifiedQuote,
)
from data_sources.http_client import RateLimitedClient

COMMUNITY = "https://steamcommunity.com"


class SteamMarketProvider(MarketDataProvider):
    name = "steam"
    capabilities = ProviderCapabilities(quotes=True, inventory=True)

    def __init__(self, steam_id_64: str | None = None,
                 login_secure: str | None = None,
                 client: RateLimitedClient | None = None):
        self.steam_id_64 = steam_id_64
        self.login_secure = login_secure
        self.client = client or RateLimitedClient(
            source=self.name, rate_per_sec=0.4, jitter=1.5,
            circuit_threshold=3, circuit_cooldown_sec=1800)

    @property
    def _cookies(self) -> dict:
        return {"steamLoginSecure": self.login_secure} if self.login_secure else {}

    # ---------------- priceoverview（低频校验） ----------------
    def get_price_overview(self, market_hash_name: str,
                           currency: int = 23) -> dict | None:
        raw = self.client.get(
            f"{COMMUNITY}/market/priceoverview/",
            params={"appid": 730, "currency": currency,
                    "market_hash_name": market_hash_name},
            cookies=self._cookies or None)
        if not isinstance(raw, dict) or not raw.get("success"):
            return None
        return {
            "lowest_price": _parse_price(raw.get("lowest_price")),
            "median_price": _parse_price(raw.get("median_price")),
            "volume": _parse_volume(raw.get("volume")),
        }

    def get_item_quotes(self, item: ItemRef) -> list[UnifiedQuote]:
        ov = self.get_price_overview(item.market_hash_name)
        if not ov:
            return []
        return [UnifiedQuote(
            market_hash_name=item.market_hash_name,
            item_uuid=item.item_uuid,
            platform="STEAM", source=self.name,
            sell_price=ov["lowest_price"],
            volume_24h=ov["volume"],
            reference_price=ov["median_price"],
        )]

    # ---------------- 库存 ----------------
    def fetch_inventory(self, count: int = 2000) -> list[dict]:
        """返回 [{asset_id, market_hash_name, amount, tradable, marketable}]。

        需要库存公开；匿名访问（带 Cookie 时附带）。分页 start_assetid。
        """
        if not self.steam_id_64:
            raise ValueError("STEAM_ID_64 未配置，无法同步库存")
        items: list[dict] = []
        start: str | None = None
        while True:
            params = {"l": "schinese", "count": count}
            if start:
                params["start_assetid"] = start
            raw = self.client.get(
                f"{COMMUNITY}/inventory/{self.steam_id_64}/730/2",
                params=params, cookies=self._cookies or None)
            if not isinstance(raw, dict):
                break
            assets = raw.get("assets") or []
            descs = {(d.get("classid"), d.get("instanceid")): d
                     for d in (raw.get("descriptions") or [])}
            for a in assets:
                d = descs.get((a.get("classid"), a.get("instanceid"))) or {}
                name = d.get("market_hash_name")
                if not name:
                    continue
                items.append({
                    "asset_id": str(a.get("assetid")),
                    "market_hash_name": name,
                    "amount": int(a.get("amount", 1)),
                    "tradable": bool(d.get("tradable")),
                    "marketable": bool(d.get("marketable")),
                })
            if raw.get("more_items") and raw.get("last_assetid"):
                start = str(raw["last_assetid"])
            else:
                break
        return items

    def health_check(self) -> ProviderHealth:
        try:
            self.get_price_overview("AK-47 | Redline (Field-Tested)")
            return ProviderHealth(source=self.name, status="ok")
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(source=self.name, status="down", error=str(e))


def _parse_price(s: object) -> float | None:
    """'¥ 92.50' / '$1.23' / '1,234.56' -> float。"""
    if not s:
        return None
    text = str(s)
    for ch in ("¥", "$", "￥", ",", " "):
        text = text.replace(ch, "")
    try:
        return float(text)
    except ValueError:
        return None


def _parse_volume(s: object) -> int | None:
    if not s:
        return None
    try:
        return int(str(s).replace(",", ""))
    except ValueError:
        return None
