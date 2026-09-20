"""数据源连通性自检：三源 health_check + Steam 库存接口试拉。

运行：python scripts/check_health.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from run import get_context  # noqa: E402

ctx = get_context()
providers = ctx["providers"]

print("== 数据源健康检查 ==")
for name, provider in providers.items():
    if provider is None:
        print(f"  {name}: 未配置")
        continue
    h = provider.health_check()
    print(f"  {name}: {h.status} {h.error or ''}")

print("\n== Steam 库存试拉（前 5 件）==")
steam = providers.get("steam")
if steam is None:
    print("  Steam 未配置")
else:
    try:
        items = steam.fetch_inventory()
        print(f"  库存共 {len(items)} 件")
        for it in items[:5]:
            print(f"    - {it['market_hash_name']} x{it['amount']}")
    except Exception as e:  # noqa: BLE001
        print(f"  拉取失败: {e}")
        print("  可能原因：库存未公开 / SteamID64 不正确 / 本机访问 steamcommunity.com 受限")
