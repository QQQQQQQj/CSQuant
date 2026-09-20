"""探测本机可用代理：扫描常见代理端口，验证能否借道访问数据域名。"""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import truststore  # noqa: E402
truststore.inject_into_ssl()
import requests  # noqa: E402

from utils.config import get_config  # noqa: E402

CANDIDATES = [7890, 7897, 10809, 10808, 1080, 8888, 8889, 8080, 8118, 4780, 2080]


def port_open(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def try_proxy(port: int, key: str) -> tuple[bool, str]:
    proxies = {"http": f"http://127.0.0.1:{port}",
               "https": f"http://127.0.0.1:{port}"}
    try:
        r = requests.get("https://open.steamdt.com/open/cs2/broad/v1/index",
                         headers={"Authorization": f"Bearer {key}"},
                         proxies=proxies, timeout=10)
        ok = r.status_code == 200 and "broadMarketIndex" in r.text
        return ok, f"HTTP {r.status_code} {r.text[:120]}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:100]


def main() -> None:
    key = get_config().steamdt_key or ""
    open_ports = [p for p in CANDIDATES if port_open(p)]
    print(f"开放的本地端口: {open_ports or '无'}")
    for port in open_ports:
        ok, detail = try_proxy(port, key)
        print(f"  127.0.0.1:{port} -> {'✅ 可用' if ok else '❌'} ({detail})")
        if ok:
            print(f"\nFOUND_PROXY=http://127.0.0.1:{port}")
            return
    print("\n未找到可用代理")


if __name__ == "__main__":
    main()
