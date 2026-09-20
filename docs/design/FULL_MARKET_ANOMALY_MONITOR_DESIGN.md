# 全市场饰品异动监控与双 QQ 播报系统设计文档

> 依据：《项目增设新功能提示词.txt》（全市场异动监控指示文档）
> 核心设计目标：**发现价格变化之前的供需与资金行为异常**。
> 表述纪律：一切"建仓/出货"判断均为概率性量化推断，必须展示数据依据、置信度与风险提示，不得表述为已确认事实。

---

## 1. 当前系统架构（现状）

采集层（CSQAQ/SteamDT/Steam 三源 Provider + 降级 + 质量管道）→ 数据层（SQLite 15+2 表）→ 领域引擎（库存估值/MarketScore/ItemScore/信号/风控/回测/告警）→ 服务层 → 输出层（Streamlit 10 页签 + CLI + 调度 + QQ Bot）。

**复用映射（不重复建设）**：
- 指示文档的 `MarketSnapshot` = 现有 `quote_ticks`（时序）+ `market_snapshot`（最新），字段完全对齐（ts/item/platform/sell_price/buy_price/sell_count/buy_count/volume/source/source_update_time/data_quality）；
- 现有 `MarketScore`（大盘 regime）直接作为全市场信号引擎的 MarketScore 输入；
- 现有 `indicators.zscore` 思想扩展为滚动基准模块；
- 现有 QQ Bot（OneBot/NapCat）技术方案保持不变，仅做双实例拆分。

## 2. 新功能插入位置

```
data_sources(现有) ──► quote_ticks/market_snapshot(现有)
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
        inventory/信号链(现有,不动)   market_anomaly/(新增)
                                      universe → baseline → detectors
                                      → acc/dist → order_flow
                                      → anomaly_score → signal_engine
                                      → event_lifecycle
                                             │
                                             ▼
                                    MarketSignalBot ──► 全市场QQ群
现有 AlertService ──► InventorySignalBot ──► 库存QQ群（消息路由严格隔离）
```

## 3. 数据源

| 数据 | 来源 | 频率 |
|---|---|---|
| 全市场分页列表（价格/在售/求购粗筛） | CSQAQ `POST /info/get_page_list`（免费档） | L3 慢采 30 min 轮换 |
| 批量报价（100个/次） | SteamDT `POST /open/cs2/v1/price/batch`（1次/分） | Tier A 5 min / Tier B 15 min |
| 单品详情（7 平台全字段） | CSQAQ `/info/good`（1次/秒） | Tier A+异动件升频 |
| 大盘 regime | 现有 MarketScore 管线 | 30 min |

网关拦截期：全链路优雅空转（universe 为空 → 扫描跳过并记事件），加白/代理后自动工作。

## 4. 数据模型（新增 4 表；快照复用现有两表）

```sql
market_anomalies   -- 异动特征与评分（回测数据基础）
  id, ts, item_uuid,
  buy_count_change, sell_count_change, buy_price_change, sell_price_change, volume_change,
  buy_count_zscore, sell_count_zscore, volume_zscore, price_zscore, spread_zscore,
  cross_platform_sync,              -- 0-1 多平台一致度
  anomaly_score, accumulation_score, distribution_score, order_flow_score,
  liquidity_score, data_quality_score, confidence

market_signals     -- 全市场信号（STRONG BUY/BUY/WATCH/HOLD/REDUCE/SELL/RISK ALERT）
  id, ts, item_uuid, signal, anomaly_score, accumulation_score, distribution_score,
  order_flow_score, relative_strength, market_score, confidence, reason_json, model_version

signal_events      -- 事件生命周期（NEW/ONGOING/UPGRADED/DOWNGRADED/RESOLVED）
  event_id, item_uuid, event_type, state, severity, first_ts, last_ts,
  peak_score, times_reported, payload_json

qq_broadcast_logs  -- 双通道播报日志（router=inventory|market）
  id, ts, router, target_qq_group, message_type, ref_id, message, pushed, error
```

## 5. 异动算法（anomaly_detector + baseline）

- **变化量**：对 1H/4H/24H 三个窗口同时计算 Absolute Change 与 Percentage Change（求购数/在售数/最高求购价/最低在售价/成交量）；小基数过滤（abs<10 且 base<20 的变化不记分）。
- **滚动基准**（baseline.py）：对每件饰品每个指标维护近 N=28 个采集周期的序列 → Rolling Mean/Median/Std → Z-Score 与 Percentile。Z-Score>|3| 视为显著异常；历史不足 8 个点 → data_quality_score 降级，不出高级别信号。
- **AnomalyScore**（0-100）：|buy_count_z|、|sell_count_z|、|volume_z|、|price_z|、|spread_z| 各自映射 0-100 后加权（默认 25/20/20/15/10/10 含跨平台同步），阈值：`<60 不播报 / 60-75 观察 / 75-85 中级 / >85 重大`（config 可调）。

## 6. 疑似建仓检测（AccumulationScore 0-100）

求购数突增 25% + 最高求购价持续抬升 20% + 在售持续下降 20% + 成交量放大 15% + 价格温和（未大涨）10% + 多平台同步 10%。典型形态：`BuyCount↑↑ + BuyPrice↑ + SellCount↓ + Volume↑ + Price未大涨` → 输出"疑似资金建仓/吸筹特征（概率性推断）"。

## 7. 疑似出货检测（DistributionScore 0-100）

在售快速增加 25% + 求购减少 20% + 最高求购价下移 15% + 成交量异常放大 15% + 价格冲高回落 15% + 多平台同步走弱 10%。输出"疑似集中出货/抛压增强特征"。

## 8. OrderFlowScore（-100~+100）

买卖力量综合：DemandSupplyRatio（BuyCount/SellCount 取 log）40% + 求购数变化方向 20% + 在售数变化方向（反向）20% + bid/ask 价格变化方向 10% + Spread 收窄/走阔 10%。分档：≥+60 明显买方优势 / +20~60 买方偏强 / ±20 平衡 / ≤-60 明显卖方优势。

## 9. FullMarketSignalEngine

输入：AnomalyScore + AccumulationScore + DistributionScore + OrderFlowScore + MarketScore（大盘）+ LiquidityScore + RelativeStrength（vs 大盘 20 日动量差）+ DataQualityScore。
规则（阈值 config 化）：
- `RISK ALERT`：Anomaly≥85 且 Distribution≥70
- `STRONG BUY`：Anomaly≥75 且 Accumulation≥75 且 OrderFlow≥+40 且 Confidence≥0.7
- `BUY`：Accumulation≥65 且 OrderFlow≥+20
- `WATCH`：Anomaly≥60 且 Accumulation≥55（进入观察）
- `REDUCE`：Distribution≥60 且 OrderFlow≤-20
- `SELL`：Distribution≥75 且 OrderFlow≤-40
- 其余 `HOLD`（不播报）
门控（误报过滤）：LiquidityScore<60 或 DataQualityScore<60 禁止 BUY/STRONG BUY（可发 RISK ALERT 降级为观察）。
Confidence = 跨平台一致度 0.35 + 数据质量 0.35 + 样本充分性 0.30。

## 10. 双 QQ Bot 架构与消息路由

```
alerter/notifiers.py
  QQNotifier(基类: name, qq_client, group_id, cooldown, 模板)
    ├── InventoryNotifier  → 库存播报（现有 AlertService 迁移至此，行为不变）
    └── MarketNotifier     → 全市场异动播报（新模板：建仓/出货/TOP榜/事件升级）
```
- 配置：`.env` 分设 `INVENTORY_QQ_BOT_HTTP/TOKEN/GROUP_ID` 与 `MARKET_QQ_BOT_HTTP/TOKEN/GROUP_ID`（兼容旧 `QQ_BOT_*` 作为 inventory 回退）；
- **路由纪律**：AlertService 只拿 InventoryNotifier；MarketBotService 只拿 MarketNotifier；播报日志 `qq_broadcast_logs.router` 字段审计；测试断言两通道群号永不交叉（含配置缺失时 fail-closed：宁可不发不可错发）。

## 11. 播报门控与事件生命周期

- 门控（刷屏防护）：普通异动 `Anomaly≥75 AND Confidence≥0.70 AND Liquidity≥60`；重大 `Anomaly≥90` 直发；同 item+event_type 冷却（默认 120 min）。
- 事件生命周期（event_lifecycle.py）：NEW → ONGOING（同级不重复发）→ UPGRADED/DOWNGRADED（等级变化才发）→ RESOLVED（恢复常态，可摘要）。severity 分档：WATCH(60-75)/MID(75-85)/MAJOR(85+)/CRITICAL(92+)。
- TOP 榜（每 30-60 min 可配）：Top Accumulation / Top Distribution / Top Demand Surge / Top Supply Surge / Top Relative Strength → 汇总一条消息（最多每类 5 条）。

## 12. 调度方案

| 任务 | 频率 | 说明 |
|---|---|---|
| market_scan | 5 min | Tier A 批量报价（SteamDT batch 100个/分） |
| market_scan_slow | 30 min | Tier B/C 轮换 + CSQAQ 分页粗筛 |
| anomaly_detect | 10 min | baseline→detectors→scores→signal_engine→落库 |
| market_bot_dispatch | 10 min | 门控+生命周期+推送+TOP榜（到点） |
| universe_refresh | 6 h | Tradable Universe 重建+分级 |

## 13. 性能方案

- Universe 分级：Tier A（高流动/当前异常，≤500 件）5min；Tier B（活跃，≤2000）15min；Tier C（其余）30-60min 轮换；**异动件自动升 Tier A**。
- 批量优先：SteamDT batch（100/次）打头，CSQAQ 详情只用于 Tier A 与异动件；请求全程走现有限流/熔断/健康管道。
- 基线序列存内存 + 落 `market_anomalies`（每轮仅 UPSERT 最新行）。

## 14. 防误报方案

低流动性排除（universe 过滤）、小基数变化忽略、NULL 恢复跳变检测（前值为 NULL 的跳变不计 Z-Score，只记 data_quality 降级）、平台批量刷新识别（同类全站同时跳变→当轮抑制）、单平台异常降 Confidence（跨平台一致度<0.34 禁高级别信号）、数据陈旧（source_update_time 超 2×周期）禁信号。

## 15. 开发文件清单

```
market_anomaly/
  universe.py          # Tradable Universe 过滤+分级
  baseline.py          # 滚动基准/Z-Score/Percentile/小基数与NULL恢复过滤
  detectors.py         # 变化量计算+AnomalyScore+OrderFlowScore+跨平台一致度
  accumulation.py      # AccumulationScore
  distribution.py      # DistributionScore
  signal_engine.py     # FullMarketSignalEngine（7档信号+门控+confidence）
  event_lifecycle.py   # 事件状态机
  service.py           # MarketAnomalyService 编排（扫描→评分→落库→TOP榜）
  bot_service.py       # MarketSignalBot 播报服务（门控+生命周期+推送）
alerter/notifiers.py   # QQNotifier/InventoryNotifier/MarketNotifier（双通道）
数据库 schema+DAO：4 新表；config.json：market_anomaly 节；.env：双QQ配置
scheduler：+4 任务；frontend：+🌐全市场异动页签；tests：test_market_anomaly.py
```

## 16. 测试计划

求购激增/减少、在售激增/减少、价格上涨/下跌、多平台一致/冲突、API 断线（Provider 异常→跳过且健康记录）、重复数据（同 ts 去重）、NULL 恢复（不计 Z-Score）、低流动性（universe 排除+禁信号）、消息去重（冷却）、消息升级（MID→MAJOR 才重发）、双 QQ 路由（两群号隔离+fail-closed）、TOP 榜组装、门控（低质量数据禁 BUY）、事件 RESOLVED。
