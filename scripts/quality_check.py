"""系统级质量检验（完全离线）：解析 + 导入 + CLI + SQLite 完整性。

隔离保证：
- 临时目录数据库（不触真实 data/csquant.db 写路径）；
- CSQUANT_OFFLINE=1：run.py 不构造任何外部 Provider / QQ 客户端；
- 真实数据库仅做只读 PRAGMA integrity_check（immutable 模式打开）。
"""
import ast
import importlib
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MODULES = [
    "utils.config", "utils.logger", "utils.timeutils", "utils.fees",
    "utils.steamid",
    "database.db", "database.dao",
    "data_sources.base", "data_sources.ratelimit", "data_sources.http_client",
    "data_sources.csqaq.provider", "data_sources.steamdt.provider",
    "data_sources.steam.provider", "data_sources.unified.service",
    "data_sources.unified.market_scan",
    "items.item_master", "items.metadata",
    "inventory.steam_inventory", "inventory.portfolio", "inventory.valuation",
    "market.breadth", "market.market_score",
    "quant.factors.indicators", "quant.factors.item_score",
    "quant.signals.signal_engine", "quant.backtest.engine",
    "risk.risk",
    "services.inventory_service", "services.market_service",
    "services.signal_service", "services.datasource_service",
    "services.backtest_service",
    "alerter.rules", "alerter.qq_bot", "alerter.notifiers", "alerter.service",
    "market_anomaly.universe", "market_anomaly.baseline",
    "market_anomaly.detectors", "market_anomaly.accumulation",
    "market_anomaly.distribution", "market_anomaly.signal_engine",
    "market_anomaly.event_lifecycle", "market_anomaly.service",
    "market_anomaly.bot_service",
    "scheduler.jobs", "run",
]

CLI_COMMANDS = [
    "init-db", "portfolio", "nav", "collect", "klines", "market-collect",
    "market-score", "signals", "signals-show", "backtest", "health",
    "alert-test", "alerts", "universe", "anomaly-detect", "market-dispatch",
    "market-top", "market-signals", "market-scan", "sync-inventory",
]

SKIP_DIRS = {"venv", ".venv", ".git", "__pycache__", "node_modules"}


def check_parse() -> tuple[int, list[str]]:
    """全部 .py 文件 AST 解析。"""
    failures = []
    count = 0
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        count += 1
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:
            failures.append(f"{path.relative_to(ROOT)}: {e}")
    return count, failures


def check_imports() -> list[str]:
    os.environ["CSQUANT_OFFLINE"] = "1"
    failures = []
    for mod in MODULES:
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            failures.append(f"{mod}: {type(e).__name__}: {e}")
    return failures


def check_cli(tmp_db: str) -> list[str]:
    env = {**os.environ, "DB_PATH": tmp_db, "CSQUANT_OFFLINE": "1"}
    failures = []
    for cmd in CLI_COMMANDS:
        proc = subprocess.run(
            [sys.executable, "run.py", cmd], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=120, errors="replace")
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        last = tail[-1][:110] if tail else "(无输出)"
        status = "OK " if proc.returncode == 0 else "FAIL"
        print(f"  [{status}] {cmd:18s} -> {last}")
        if proc.returncode != 0:
            failures.append(f"{cmd}: exit={proc.returncode} {last}")
    return failures


def check_sqlite_integrity(tmp_db: str) -> list[str]:
    failures = []
    # 临时库
    conn = sqlite3.connect(tmp_db)
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    print(f"  临时库 integrity_check = {result}")
    if result != "ok":
        failures.append(f"临时库 integrity: {result}")
    # 真实库只读检查（immutable 打开，绝不写入）
    real_db = ROOT / "data" / "csquant.db"
    if real_db.exists():
        uri = f"file:{real_db.as_posix()}?immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        print(f"  真实库(只读) integrity_check = {result}")
        if result != "ok":
            failures.append(f"真实库 integrity: {result}")
    return failures


def main() -> None:
    failures: list[str] = []

    print("== 1. Python 文件 AST 解析 ==")
    count, parse_failures = check_parse()
    print(f"  {count} 个文件解析，失败 {len(parse_failures)}")
    failures += parse_failures

    print("\n== 2. 模块导入检查（OFFLINE） ==")
    import_failures = check_imports()
    if import_failures:
        for f in import_failures:
            print(f"  [FAIL] {f}")
    else:
        print(f"  全部 {len(MODULES)} 个模块导入成功")
    failures += import_failures

    with tempfile.TemporaryDirectory(prefix="csquant_qc_") as tmp:
        tmp_db = str(Path(tmp) / "qc.db")
        print("\n== 3. CLI 子命令遍历（临时库 + OFFLINE，零外部访问） ==")
        failures += check_cli(tmp_db)
        print("\n== 4. SQLite 完整性 ==")
        failures += check_sqlite_integrity(tmp_db)

    print("\n== 结果 ==")
    if failures:
        print(f"发现 {len(failures)} 个问题:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("全部通过 ✅")


if __name__ == "__main__":
    main()
