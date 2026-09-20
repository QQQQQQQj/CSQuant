"""调度器：APScheduler 优先，缺库时 stdlib 循环兜底（同一任务表）。

任务表与 SYSTEM_ARCHITECTURE §4 对齐。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from utils.logger import get_logger

log = get_logger("csquant.scheduler")


@dataclass
class Job:
    name: str
    func: Callable
    interval_sec: int


def build_jobs(ctx: dict) -> list[Job]:
    svc = ctx["services"]
    collect_cfg = ctx["config"].strategy["collect"]

    def score_and_signals() -> None:
        """先算 MarketScore 再生成信号（保证信号带大盘分，而非 None）。"""
        market_result = svc["market"].compute_and_store_market_score()
        svc["signal"].generate_signals(
            market_result=market_result,
            market_closes=svc["market"].get_index_closes())

    def anomaly_pipeline() -> None:
        """全市场：大盘动量 → 检测一轮 → 异动升频 → 播报候选事件。"""
        from quant.factors.indicators import momentum
        closes = svc["market"].get_index_closes()
        mom_series = momentum(closes, 20) if len(closes) >= 21 else []
        market_momentum = mom_series[-1] if mom_series else None
        report = svc["market_anomaly"].detect_round(
            market_momentum=market_momentum)
        emit = report.get("emit_events", [])
        if emit and "market_scan" in svc:
            svc["market_scan"].promote(
                [ev["entry"].item_uuid for ev in emit])   # 异动件升频
        svc["market_bot"].dispatch(emit)

    def top_summary() -> None:
        """全市场 TOP 榜定时摘要。"""
        latest = ctx["dao"].get_latest_market_index("csquant_market_score") or {}
        svc["market_bot"].send_top_summary(
            svc["market_anomaly"].top_lists(), latest.get("market_score"),
            latest.get("market_regime"))

    return [
        Job("collect_quotes", svc["datasource"].trigger_collect,
            collect_cfg["quotes_interval_sec"]),
        Job("collect_market_index", svc["market"].collect_market_index,
            collect_cfg["market_index_interval_sec"]),
        Job("score_and_signals", score_and_signals,
            collect_cfg["score_interval_sec"]),
        Job("snapshot_nav", svc["inventory"].snapshot_nav, 3600 * 6),
        Job("collect_klines", svc["datasource"].collect_klines,
            collect_cfg["kline_interval_sec"]),
        Job("alert_evaluate", svc["alert"].evaluate_all,
            ctx["config"].strategy.get("alert", {}).get(
                "evaluate_interval_sec", 600)),
        Job("universe_refresh", svc["market_anomaly"].refresh_universe,
            ctx["config"].strategy.get("market_anomaly", {}).get(
                "universe_refresh_sec", 21600)),
        Job("market_scan", svc["market_scan"].scan_tick,
            ctx["config"].strategy.get("market_anomaly", {}).get(
                "scan_interval_sec", 300)),
        Job("sync_inventory", _safe_sync_inventory(svc),
            ctx["config"].strategy["collect"].get(
                "inventory_interval_sec", 900)),
        Job("anomaly_pipeline", anomaly_pipeline,
            ctx["config"].strategy.get("market_anomaly", {}).get(
                "detect_interval_sec", 600)),
        Job("market_top_summary", top_summary, 1800),
    ]


def _safe_sync_inventory(svc: dict) -> Callable:
    """Steam 未配置时优雅跳过（不抛异常刷日志）。"""
    def wrapper() -> None:
        try:
            svc["inventory"].sync_steam_inventory()
        except RuntimeError as e:
            log.debug("库存同步跳过: %s", e)
    return wrapper


def run_scheduler(ctx: dict) -> None:
    jobs = build_jobs(ctx)
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        scheduler = BlockingScheduler()
        for job in jobs:
            scheduler.add_job(_guarded(job), "interval",
                              seconds=job.interval_sec, id=job.name,
                              max_instances=1, coalesce=True)
            log.info("已注册任务 %s (每 %ss)", job.name, job.interval_sec)
        log.info("APScheduler 启动")
        scheduler.start()
    except ImportError:
        log.warning("APScheduler 未安装，退化为 stdlib 循环调度")
        _stdlib_loop(jobs)


def _guarded(job: Job) -> Callable:
    def wrapper():
        try:
            log.info("任务开始: %s", job.name)
            job.func()
            log.info("任务完成: %s", job.name)
        except Exception as e:  # noqa: BLE001
            log.exception("任务失败: %s - %s", job.name, e)
    return wrapper


def _stdlib_loop(jobs: list[Job]) -> None:
    next_run = {job.name: time.monotonic() for job in jobs}
    while True:
        now = time.monotonic()
        for job in jobs:
            if now >= next_run[job.name]:
                _guarded(job)()
                next_run[job.name] = now + job.interval_sec
        time.sleep(5)
