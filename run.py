"""CSQuant CLI 入口。

用法：
  python run.py init-db                      初始化数据库
  python run.py import-items --mapper-dir <ID-Mapper目录>
  python run.py sync-metadata [--force]      同步 CSGO-API 元数据
  python run.py update-good-ids              补录 csqaq_good_id
  python run.py add-position --name "AK-47 | Redline (Field-Tested)" --qty 1 --price 85.5 --platform BUFF
  python run.py portfolio                    查看库存估值
  python run.py nav                          生成今日净值快照
  python run.py collect                      采集自选池报价
  python run.py klines                       采集自选池K线
  python run.py market-collect               采集大盘指数
  python run.py market-score                 计算 MarketScore
  python run.py signals                      生成买卖信号
  python run.py signals-show                 查看最新信号
  python run.py backtest                     运行回测（E1基准策略）
  python run.py health                       数据源健康状态
  python run.py scheduler                    启动常驻调度
  python run.py dashboard                    打印 Dashboard 启动方式
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils.config import get_config
from utils.logger import get_logger, mask_secret

log = get_logger("csquant.run")


def get_context() -> dict:
    """构建运行上下文：配置 + 数据库 + 三源 Provider（按密钥可用性）+ 服务层。

    CSQUANT_OFFLINE=1 时禁用全部外部 Provider 与 QQ 通道（质量检查/测试用）。
    """
    cfg = get_config()
    # 配置自动版本注入：信号/回测均以 model_version+参数哈希 落库
    cfg.strategy["_config_version"] = cfg.config_version
    from database.db import connect, init_schema
    from database.dao import Dao
    conn = connect(cfg.db_path)
    init_schema(conn)
    dao = Dao(conn)

    from services.datasource_service import make_health_recorder
    recorder = make_health_recorder(dao)

    csqaq = steamdt = steam = None
    if cfg.offline:
        log.info("OFFLINE 模式：外部 Provider 与 QQ 通道全部禁用")
    else:
        from data_sources.http_client import RateLimitedClient
        if cfg.csqaq_token:
            from data_sources.csqaq.provider import CSQAQProvider
            csqaq = CSQAQProvider(cfg.csqaq_token, client=RateLimitedClient(
                "csqaq", rate_per_sec=1.0, on_result=recorder))
            log.info("CSQAQ Provider 已启用 (token=%s)", mask_secret(cfg.csqaq_token))
        else:
            log.warning("未配置 CSQAQ_API_TOKEN，主源不可用")
        if cfg.steamdt_key:
            from data_sources.steamdt.provider import SteamDTProvider
            steamdt = SteamDTProvider(cfg.steamdt_key, client=RateLimitedClient(
                "steamdt", rate_per_sec=1.0, on_result=recorder,
                endpoint_rates={"price/batch": 1 / 60,   # 1次/分
                                "v1/base": 1 / 3600,     # 每日1次（保守）
                                "item/v1/kline": 2.0}))
            log.info("SteamDT Provider 已启用（端点级限频）")
        else:
            log.warning("未配置 STEAMDT_API_KEY，辅源不可用")
        if cfg.steam_id_64:
            from data_sources.steam.provider import SteamMarketProvider
            from utils.steamid import normalize_steam_id
            try:
                steam_id = normalize_steam_id(cfg.steam_id_64)
            except ValueError as e:
                log.error("STEAM_ID_64 非法：%s，库存同步禁用", e)
                steam_id = None
            if steam_id:
                steam = SteamMarketProvider(
                    steam_id, cfg.steam_login_secure,
                    client=RateLimitedClient("steam", rate_per_sec=0.4,
                                             jitter=1.5, circuit_threshold=3,
                                             on_result=recorder))
                log.info("Steam Provider 已启用（库存同步可用, id=%s）", steam_id)

    from data_sources.unified.service import UnifiedMarketService
    unified = UnifiedMarketService(dao, csqaq, steamdt, steam)

    from services.inventory_service import InventoryService
    from services.market_service import MarketService
    from services.signal_service import SignalService
    from services.datasource_service import DataSourceService
    from services.backtest_service import BacktestService
    services = {
        "inventory": InventoryService(dao, steam),
        "market": MarketService(dao, cfg.strategy, csqaq, steamdt),
        "signal": SignalService(dao, cfg.strategy),
        "datasource": DataSourceService(dao, unified, steamdt),
        "backtest": BacktestService(dao, cfg.strategy),
    }
    # 双 QQ 通道（库存/全市场，路由严格隔离；OFFLINE 时不构造任何客户端）
    from alerter.notifiers import (InventoryNotifier, MarketNotifier,
                                   build_notifiers)
    from alerter.service import AlertService
    if cfg.offline:
        inventory_notifier = InventoryNotifier(None, dao)
        market_notifier = MarketNotifier(None, dao)
    else:
        inventory_notifier, market_notifier = build_notifiers(cfg, dao)
    services["alert"] = AlertService(
        dao, inventory_notifier,
        enabled=cfg.strategy.get("alert", {}).get("enabled", True))
    # 全市场异动监控 + 扫描 + 播报
    from market_anomaly.service import MarketAnomalyService
    from market_anomaly.bot_service import MarketBotService
    from data_sources.unified.market_scan import MarketScanService
    services["market_anomaly"] = MarketAnomalyService(dao, cfg.strategy)
    services["market_bot"] = MarketBotService(dao, cfg.strategy, market_notifier)
    services["market_scan"] = MarketScanService(dao, unified, steamdt,
                                                cfg.strategy)
    return {"config": cfg, "dao": dao, "services": services,
            "providers": {"csqaq": csqaq, "steamdt": steamdt, "steam": steam}}


def main() -> None:
    parser = argparse.ArgumentParser(prog="csquant", description="CS饰品量化投研系统")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    p = sub.add_parser("import-items")
    p.add_argument("--mapper-dir", required=True)
    p = sub.add_parser("sync-metadata")
    p.add_argument("--force", action="store_true")
    sub.add_parser("update-good-ids")
    p = sub.add_parser("add-position")
    p.add_argument("--name", required=True)
    p.add_argument("--qty", type=int, default=1)
    p.add_argument("--price", type=float, default=None)
    p.add_argument("--time", default=None)
    p.add_argument("--platform", default=None)
    sub.add_parser("portfolio")
    sub.add_parser("nav")
    sub.add_parser("collect")
    sub.add_parser("klines")
    sub.add_parser("market-collect")
    sub.add_parser("market-score")
    sub.add_parser("signals")
    sub.add_parser("signals-show")
    sub.add_parser("backtest")
    sub.add_parser("health")
    sub.add_parser("alert-test")
    sub.add_parser("alerts")
    sub.add_parser("universe")
    sub.add_parser("anomaly-detect")
    sub.add_parser("market-dispatch")
    sub.add_parser("market-top")
    sub.add_parser("market-signals")
    sub.add_parser("market-scan")
    sub.add_parser("sync-inventory")
    sub.add_parser("scheduler")
    sub.add_parser("dashboard")
    args = parser.parse_args()

    ctx = get_context()
    dao: object = ctx["dao"]
    svc = ctx["services"]

    if args.cmd == "init-db":
        print(f"数据库已初始化: {ctx['config'].db_path}")
    elif args.cmd == "import-items":
        from items.item_master import import_from_id_mapper
        print(json.dumps(import_from_id_mapper(dao, args.mapper_dir),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "sync-metadata":
        print(json.dumps(svc["datasource"].trigger_metadata_sync(args.force),
                         ensure_ascii=False))
    elif args.cmd == "update-good-ids":
        from items.item_master import update_csqaq_good_ids
        csqaq = ctx["providers"]["csqaq"]
        if not csqaq:
            sys.exit("未配置 CSQAQ_API_TOKEN")
        print(f"补录 {update_csqaq_good_ids(dao, csqaq)} 条")
    elif args.cmd == "add-position":
        pid = svc["inventory"].add_manual_position(
            args.name, args.qty, args.price, args.time, args.platform)
        print(f"已建仓: {pid}")
    elif args.cmd == "portfolio":
        print(json.dumps(svc["inventory"].get_portfolio(),
                         ensure_ascii=False, indent=2, default=str))
    elif args.cmd == "nav":
        print(json.dumps(svc["inventory"].snapshot_nav(), ensure_ascii=False))
    elif args.cmd == "collect":
        report = svc["datasource"].trigger_collect()
        print(report if report else "数据源未配置")
    elif args.cmd == "klines":
        print(f"K线入库 {svc['datasource'].collect_klines()} 条")
    elif args.cmd == "market-collect":
        print(json.dumps(svc["market"].collect_market_index(), ensure_ascii=False))
    elif args.cmd == "market-score":
        r = svc["market"].compute_and_store_market_score()
        print(f"MarketScore={r.score} regime={r.regime_zh} 缺失因子={r.missing_factors}")
    elif args.cmd == "signals":
        closes = svc["market"].get_index_closes()
        market_result = svc["market"].compute_and_store_market_score()
        report = svc["signal"].generate_signals(market_result=market_result,
                                                market_closes=closes)
        print(f"信号生成: 共{report.total} BUY={report.buy} "
              f"HOLD={report.hold} SELL={report.sell}")
    elif args.cmd == "signals-show":
        rows = svc["signal"].get_signals(limit=20)
        for r in rows:
            print(f"[{r['signal']:4}] {r['final_score']} "
                  f"{r.get('name_zh') or r['market_hash_name']} "
                  f"conf={r['confidence']} risk={r['risk_level']}")
        if not rows:
            print("暂无信号，先运行: python run.py signals")
    elif args.cmd == "backtest":
        print(json.dumps(svc["backtest"].run_backtest(),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "health":
        print(json.dumps(svc["datasource"].get_health(),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "alert-test":
        ok, err = svc["alert"].send_test_message()
        print("已发送" if ok else f"发送失败: {err}")
    elif args.cmd == "alerts":
        print(json.dumps(svc["alert"].evaluate_all(), ensure_ascii=False))
    elif args.cmd == "universe":
        print(f"Universe 大小: {svc['market_anomaly'].refresh_universe()}")
    elif args.cmd == "anomaly-detect":
        report = svc["market_anomaly"].detect_round()
        report["emit_events"] = f"{len(report['emit_events'])} 件"
        print(json.dumps(report, ensure_ascii=False, default=str))
    elif args.cmd == "market-dispatch":
        report = svc["market_anomaly"].detect_round()
        print(json.dumps(svc["market_bot"].dispatch(report["emit_events"]),
                         ensure_ascii=False))
    elif args.cmd == "market-top":
        ok, err = svc["market_bot"].send_top_summary(
            svc["market_anomaly"].top_lists(),
            (dao.get_latest_market_index("csquant_market_score") or {})
            .get("market_score"), None)
        print("已发送" if ok else f"未发送: {err}")
    elif args.cmd == "market-signals":
        rows = dao.get_market_signals(limit=20)
        for r in rows:
            print(f"[{r['signal']:11}] {r['anomaly_score']:.0f} "
                  f"{r.get('name_zh') or r['market_hash_name']} "
                  f"conf={r['confidence']}")
        if not rows:
            print("暂无全市场信号")
    elif args.cmd == "market-scan":
        print(json.dumps(svc["market_scan"].scan_tick(), ensure_ascii=False))
    elif args.cmd == "sync-inventory":
        try:
            report = svc["inventory"].sync_steam_inventory()
            print(f"同步完成: 新增{report.added} 已有{report.existing} "
                  f"未匹配{len(report.unmatched)}")
        except Exception as e:  # noqa: BLE001
            print(f"同步失败: {e}")
    elif args.cmd == "scheduler":
        from scheduler.jobs import run_scheduler
        run_scheduler(ctx)
    elif args.cmd == "dashboard":
        print("启动 Dashboard: streamlit run frontend/app.py")


if __name__ == "__main__":
    main()
