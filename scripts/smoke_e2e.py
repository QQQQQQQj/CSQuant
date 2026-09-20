"""端到端冒烟测试：用模拟数据走通全链路（不依赖外部API/密钥）。

运行：python scripts/smoke_e2e.py
链路：建仓 -> 模拟K线/大盘 -> MarketScore -> 信号生成 -> 估值 -> 净值 -> 回测
隔离：临时目录数据库 + CSQUANT_OFFLINE（禁用外部 Provider 与 QQ）。
"""
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台兼容
_tmpdir = tempfile.mkdtemp(prefix="csquant_smoke_")
os.environ["DB_PATH"] = str(Path(_tmpdir) / "smoke.db")
os.environ["CSQUANT_OFFLINE"] = "1"

from run import get_context  # noqa: E402
from utils.timeutils import iso_days_ago  # noqa: E402

ctx = get_context()
dao, svc = ctx["dao"], ctx["services"]

print("== 1. 手工建仓 ==")
pid = svc["inventory"].add_manual_position(
    "AK-47 | Redline (Field-Tested)", 1, 85.0, buy_platform="BUFF")
item = dao.get_item_by_hash_name("AK-47 | Redline (Field-Tested)")
print(f"  position_id={pid[:8]}... item_uuid={item['item_uuid'][:8]}...")

print("== 2. 注入120天模拟大盘指数（上行）与K线 ==")
for i in range(120):
    dao.insert_market_index({
        "ts": iso_days_ago(120 - i), "index_name": "csqaq_main",
        "index_value": 2000 + i * 2, "breadth_up": 1200, "breadth_down": 800,
        "sentiment_score": 60, "source": "mock"})
    c = 85 + i * 0.1 + math.sin(i / 5) * 2
    dao.upsert_kline({
        "item_uuid": item["item_uuid"], "platform": "BUFF", "period": "1d",
        "ts": iso_days_ago(120 - i), "open": c, "close": c,
        "high": c * 1.01, "low": c * 0.99, "volume": 50, "source": "mock"})
dao.upsert_snapshot({"item_uuid": item["item_uuid"], "platform": "BUFF",
                     "source": "mock", "sell_price": 97.5, "sell_count": 120,
                     "buy_price": 95.0, "buy_count": 80, "volume_24h": 45})
dao.upsert_snapshot({"item_uuid": item["item_uuid"], "platform": "STEAM",
                     "source": "mock", "sell_price": 98.2, "sell_count": 30})
dao.commit()

print("== 3. MarketScore ==")
mr = svc["market"].compute_and_store_market_score()
print(f"  MarketScore={mr.score} regime={mr.regime_zh} 缺失={mr.missing_factors}")
assert mr.score is not None and mr.score > 50, "上行市场应偏多"

print("== 4. 信号生成 ==")
report = svc["signal"].generate_signals(
    market_result=mr, market_closes=svc["market"].get_index_closes())
print(f"  total={report.total} BUY={report.buy} HOLD={report.hold} SELL={report.sell}")
rows = svc["signal"].get_signals()
assert rows, "应至少生成一条信号"
r = rows[0]
print(f"  [{r['signal']}] final={r['final_score']} conf={r['confidence']} "
      f"risk={r['risk_level']} pos={r['suggested_position']} model={r['model_version']}")
assert r["model_version"].startswith("signal-v1.0.0")  # 自动配置版本: base+参数哈希
assert "+" in r["model_version"], "配置版本必须含参数哈希"
assert r["reason_json"]["item_factors"], "reason_json 应含因子贡献"
assert r["input_snapshot_ref"], "信号必须可追溯"

print("== 5. 库存估值 ==")
pf = svc["inventory"].get_portfolio()
print(f"  现值={pf['total_value']} 浮盈={pf['unrealized_pnl']} "
      f"收益率={pf['return_rate']}")
assert pf["total_value"] == 97.5 and pf["unrealized_pnl"] == 12.5

print("== 6. 净值快照 ==")
nav = svc["inventory"].snapshot_nav()
print(f"  NAV ts={nav['ts'][:10]} value={nav['total_value']}")

print("== 7. 回测（E1基准策略） ==")
bt = svc["backtest"].run_backtest()
print(f"  total_return={bt.get('total_return')} trades={bt.get('trade_count')} "
      f"warnings={bt.get('warnings')}")
assert bt.get("trade_count", 0) >= 1, "上行K线应触发交易"

print("\n[OK] 端到端冒烟测试全部通过")
