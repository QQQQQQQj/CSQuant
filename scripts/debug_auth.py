"""调试 403：打印 CSQAQ / SteamDT 真实响应体（定位白名单/Key 问题）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import truststore  # noqa: E402
truststore.inject_into_ssl()
import requests  # noqa: E402

from utils.config import get_config  # noqa: E402

cfg = get_config()

print("== 本机出口 IP ==")
try:
    ip = requests.get("https://api.ipify.org", timeout=10).text
    print(f"  {ip}")
except Exception as e:  # noqa: BLE001
    print(f"  获取失败: {e}")

print("\n== CSQAQ /current_data ==")
r = requests.get("https://api.csqaq.com/api/v1/current_data",
                 headers={"ApiToken": cfg.csqaq_token},
                 params={"type": "init"}, timeout=15)
print(f"  HTTP {r.status_code}")
print(f"  body: {r.text[:400]}")

print("\n== SteamDT /broad/v1/index ==")
r2 = requests.get("https://open.steamdt.com/open/cs2/broad/v1/index",
                  headers={"Authorization": f"Bearer {cfg.steamdt_key}"}, timeout=15)
print(f"  HTTP {r2.status_code}")
print(f"  body: {r2.text[:400]}")
