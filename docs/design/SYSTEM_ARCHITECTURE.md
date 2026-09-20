# CSQuant 系统架构设计（SYSTEM_ARCHITECTURE）

> 与 `architecture_comparison.md` 的技术栈裁决一致。V1 目标：单进程、可运行、可测试、可演进。

---

## 1. 分层架构

```
┌─────────────────────────── 输出层 ───────────────────────────┐
│  frontend/app.py (Streamlit 9页签)   alerts   backtest report │
├─────────────────────────── 服务层 ───────────────────────────┤
│  services/  InventoryService MarketService SignalService      │
│             BacktestService DataSourceService                 │
├──────────────────────── 领域引擎层 ──────────────────────────┤
│  inventory/(同步,估值,净值)  market/(宽度,MarketScore)         │
│  quant/factors(指标→ItemScore) quant/signals(信号引擎)         │
│  risk/(仓位,风险等级)         quant/backtest(回测)             │
├───────────────────────── 数据层 ─────────────────────────────┤
│  database/(schema,DAO,降采样)  items/(item_master,metadata)   │
├───────────────────────── 采集层 ─────────────────────────────┤
│  data_sources/ base(统一模型+协议) ratelimit http_client       │
│    csqaq/  steamdt/  steam/   unified/(降级编排+质量管道)      │
└──────────────────────────────────────────────────────────────┘
调度: scheduler/jobs.py (APScheduler, 缺库时 stdlib 循环兜底)
配置: .env(密钥) + config.json(策略参数) + utils/config.py
V5: agents/ 复用CSGOTrading llm推理层, 仅事件理解与信号解释
```

**跨层禁令**：页面禁止直查数据库（必须经 services）；采集层禁止知道因子；LLM 输出禁止作为数值输入；平台特有字段禁止穿出 Provider。

## 2. 目录结构（文件级）

```
CSQuant/
├── run.py                    # CLI 入口：collect/score/signals/dashboard/backtest/scheduler
├── requirements.txt          # 完整部署依赖（核心逻辑零依赖可跑）
├── .env.example / config.json
├── data_sources/
│   ├── base.py               # UnifiedQuote/Kline/MarketIndex(dataclass)+Provider协议+能力声明
│   ├── ratelimit.py          # TokenBucket，按 source:endpoint 分桶
│   ├── http_client.py        # requests封装: 超时/重试/退避/429熔断
│   ├── csqaq/provider.py     # 主源
│   ├── steamdt/provider.py   # 辅源+磨损
│   ├── steam/provider.py     # 库存+低频校验
│   └── unified/service.py    # 主备降级 + data_quality_pipeline(异常检测)
├── database/
│   ├── schema.sql            # 全部DDL（=DATABASE_SCHEMA.md §3）
│   ├── db.py                 # 连接(WAL)+schema初始化+轻量迁移
│   └── dao.py                # 唯一写入路径（tick/snapshot/kline/position/trade/signal/health/event）
├── items/
│   ├── item_master.py        # ID-Mapper五平台JSON导入(-1→NULL,键集校验)
│   └── metadata.py           # CSGO-API JSON每日同步(manifestId增量)
├── inventory/
│   ├── steam_inventory.py    # /inventory/{id}/730/2 解析+defindex/market_hash_name双路径匹配
│   ├── portfolio.py          # 持仓CRUD/成本/状态机(HOLDING/RENTED/SOLD)
│   └── valuation.py          # 估值/盈亏KPI/净值快照(注入资金流调整)
├── market/
│   ├── breadth.py            # 涨跌分布/宽度/品类强弱
│   └── market_score.py       # 六因子→MarketScore 0-100→五档regime
├── quant/
│   ├── factors/indicators.py # SMA/EMA/RSI/MACD/BB/动量/zscore/percentile(纯stdlib)
│   ├── factors/item_score.py # 八因子→ItemScore
│   ├── signals/signal_engine.py # FinalScore+阈值+confidence+reason_json+model_version
│   └── backtest/engine.py    # 纯python long-only回测(bid/ask执行+费+滑点+流动性过滤)
│       └── vectorbt_engine.py# vectorbt可选适配层(guarded import, 独立venv)
├── risk/risk.py              # 风险等级(L/M/H)+建议仓位矩阵+敞口/回撤监控
├── services/                 # 服务层（页面/调度/CLI唯一入口）
├── scheduler/jobs.py         # 任务表（见§4）
├── frontend/app.py           # Streamlit 9页签（guarded import）
├── utils/ config.py logger.py timeutils.py fees.py(calculate_after_fee移植)
├── agents/ (V5 占位: llm_client.py + event_agent.py + explainer.py)
└── tests/ (unittest, 核心纯函数)
```

## 3. 核心数据流

**① 采集→入库**（每 5 min）：调度器→unified.service 按 watchlist 拉 CSQAQ 批量详情→质量管道（跨平台偏离/倒挂/突变/陈旧）→quote_ticks + upsert market_snapshot→health 记录；失败按降级矩阵切 SteamDT。

**② 库存→估值→净值**（每 15 min）：steam_inventory 同步→匹配 item_master→valuation 取 market_snapshot 多平台价（主 BUFF 备 UUYP/Steam）→KPI（成本/现值/浮盈/已实现/收益率，资金流调整）→portfolio_snapshot 每日落库。

**③ 评分→信号**（每 30 min）：market_score 六因子→market_index 落库；watchlist 逐件取 kline/snapshot→item_score 八因子→signal_engine 合成→risk 定级+建议仓位→signal 落库（含 reason_json + input_snapshot_ref）→Dashboard 展示→**人工确认执行**（V1 不下单）。

**④ 回测**（手动）：kline_daily+quote_ticks→日线 panel→清洗（NULL/陈旧/D级剔除）→engine 按信号规则模拟→统计+落库 backtest_result/trade→报告。

## 4. 调度器任务表

| 任务 | 频率 | 说明 |
|---|---|---|
| collect_quotes(L1/L2) | 5 min | 对齐 CSQAQ 更新节奏 |
| collect_market_index | 5 min | 指数+涨跌分布 |
| sync_inventory | 15 min | Steam 库存（可手动触发） |
| snapshot_nav | 每日 23:55 | 净值快照 |
| score_market | 30 min | MarketScore |
| score_items+signals | 30 min | ItemScore+信号 |
| kline_incremental | 1 h | 自选池K线增量 |
| verify_cross_source | 30 min | SteamDT 抽样 50 件跨源校验 |
| steam_lowfreq_check | 每日轮换 | priceoverview 抽样（防429） |
| metadata_sync | 每日 | CSGO-API manifestId 增量 |
| id_map_daily | 每日 | SteamDT base / CSQAQ get_good_id |
| downsample_ticks | 每日 02:00 | 7天→小时→日线 |

APScheduler 缺失时 `run.py scheduler` 退化为 stdlib 优先级循环（同任务表）。

## 5. 配置体系

- `.env`：`CSQAQ_API_TOKEN / STEAMDT_API_KEY / STEAM_LOGIN_SECURE / STEAM_ID_64 / 数据库路径 / 日志级别`（.env.example 模板，不进 Git）；
- `config.json`：策略参数（MarketScore/ItemScore 权重、信号阈值、confidence 权重、risk 分档、费率表、滑点分档、采集 tier 频率）+ `model_version`；**信号落库带 model_version**，阈值变更必须递增版本并附回测报告。

## 6. 可观测性

loguru 结构化日志（`logs/{date}.log` 滚动）；`datasource_health` 5min 聚合（成功率<90% degraded/<60% down）；熔断事件进 `event_log`；Dashboard「数据源状态」页三源健康灯。

## 7. 部署（V1）

Windows 单机：`python run.py scheduler`（常驻采集+评分）+ `streamlit run frontend/app.py`（看板）；SQLite 文件每日备份（复制 WAL 检查点后 db 文件）；docker-compose 后置。

## 8. 演进路径

| 版本 | 架构增量 | 预留点 |
|---|---|---|
| V2 | MarketScore 上线（market/） | breadth 自算接口已在 unified 预留 |
| V3 | 信号引擎+风控（quant/, risk/） | signal 表 model_version/input_snapshot_ref |
| V4 | 回测（engine 纯python→vectorbt 独立venv 热切换） | BacktestEngine 协议 |
| V5 | agents/（LLM 事件+解释，复用CSGOTrading llm层） | Factor 接口保留 event_score 输入槽；信号 reason_json 可挂 LLM 解释 |

## 9. 架构决策记录（ADR）

1. **SQLite 而非 Mongo/PG**：单用户单机、零运维；DAO 抽象保证可迁。
2. **Streamlit 而非前后端分离**：V1 验证投研价值优先；逻辑全在 services 层，可平移 FastAPI+React。
3. **单进程而非微服务/消息队列**：Tracker 式 4 进程+Redis 对个人系统是过度工程。
4. **回测：纯 python 引擎为主 + vectorbt 可选**：环境无 numba/pandas 时仍可跑；vectorbt 独立 venv 隔离激进依赖。
5. **纯函数管线而非 LangGraph（V1）**：无 Agent 需求不引框架；接口按 CSGOTrading 两段式设计预留。
6. **统一行情结构 dataclass（stdlib）而非 pydantic**：核心零依赖可测试；pydantic 校验在 HTTP API 层（V2+）引入。
