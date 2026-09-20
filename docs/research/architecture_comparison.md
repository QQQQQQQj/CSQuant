# CSQuant 参考项目横向架构比较

> 本文基于 `docs/research/` 下 6 份逐项目深度分析（01~06）与 1 份数据源网络调研笔记，回答指示文档 Phase 2 的 10 个必答问题，并给出最终的「参考项目 → CSQuant 模块」映射表。
>
> 原则：**Architecture Mining，而不是 Code Copying。**

---

## 1. 六项目总览对比

| 维度 | CSGOTrading | csgo_investment | SteamTradingSiteTracker | ID-Mapper | CSGO-API | VectorBT |
|---|---|---|---|---|---|---|
| 定位 | 多Agent量化决策原型 | 个人库存台账 | 多平台行情采集流水线 | 跨平台ID映射数据集 | 饰品元数据静态API | 向量化回测引擎 |
| 语言/栈 | Python+LangGraph+SQLite | Python(Streamlit)+Go爬虫+pickle | Python(aiohttp)+Redis+MongoDB | 纯JSON数据 | Node.js生成器→静态JSON | Python+Numba(+Rust可选) |
| 数据真实性 | **疑似合成假行情**（seed=42随机游走与自带CSV吻合） | BUFF裸爬（已失效风险高） | 五平台实采（接口部分过期） | 社区维护映射（覆盖至~2025） | 游戏文件解包（权威） | N/A |
| 历史时序 | 有（伪K线，不可信） | 无 | 无（整文档覆盖） | 无 | 无 | 框架无关 |
| 决策/信号 | Bullish/Bearish/Neutral 三档，无置信度 | 无 | 无（挂刀比例≠交易信号） | 无 | 无 | entries/exists布尔矩阵 |
| 对CSQuant角色 | 决策流/风控/LLM层蓝本 | 库存KPI口径蓝本+反面清单 | 采集调度/快照字段/手续费模型蓝本 | item_master初始化数据源 | 元数据层数据源 | 回测内核 |

---

## 2. 十个必答问题

### 2.1 哪个项目最适合作为主架构参考？

**CSGOTrading 是决策链路的主架构参考，但不是全系统的主架构。**

- 它的「Planner → 并行 Analyst → Portfolio Manager（两段式：LLM风控定比例 → Python算量 → LLM定订单）→ Python执行」扇入扇出结构，是本组中唯一覆盖「数据→信号→风控→决策→留痕」全链路的设计；
- 但它的数据源层（cs2market 伪K线 + 疑似合成CSV）完全不可信，采集/元数据/ID映射/回测必须分别从 Tracker、CSGO-API、ID-Mapper、VectorBT 吸收；
- 因此 CSQuant 的架构 = **Tracker 的数据接入思想 + CSGO-API/ID-Mapper 的数据底座 + CSGOTrading 的决策与风控骨架 + VectorBT 的回测内核 + csgo_investment 的库存KPI口径**。任何单一项目都不能作为整体骨架照搬。

### 2.2 哪些功能已有成熟实现？

| 功能 | 成熟实现来源 | 复用方式 |
|---|---|---|
| 饰品全量元数据（名称/武器/磨损/稀有度/收藏品/图片/多语言） | CSGO-API 的 `public/api/{en,zh-CN}` 成品 JSON | **数据直接复用**，每日同步入库 |
| 跨平台ID映射（Steam/BUFF/C5/IGXE/UUYP，34,417条） | ID-Mapper 的 730.json 系列 | **数据直接复用**，初始化 item_master |
| 技术指标计算（SMA/EMA/RSI/MACD/BB/动量） | CSGOTrading `agents/analysts/technical.py` 六个指标函数 | **代码可直接复用** |
| Steam手续费精确逆运算 | Tracker `calculate_after_fee` | **代码可直接复用** |
| LLM调用层（结构化输出、多Provider聚合、失败降级） | CSGOTrading `llm/inference.py`、`llm/provider.py` | **代码可直接复用** |
| 回测内核（信号→组合→统计、费用/滑点、多资产） | VectorBT 1.1.0 `Portfolio.from_signals` | **集成库+薄封装** |
| 库存盈亏KPI口径（总收益vs浮盈、双平台计价） | csgo_investment 聚合公式组 | 口径借鉴、代码重写 |

### 2.3 哪些必须自己开发？

1. **SteamDT / CSQAQ / Steam 三个 Provider**（参考项目里没有任何一个接入这三个源，Tracker 的直连爬虫方案已大面积过期）；
2. **时序行情存储**（quote_ticks / 快照表）——Tracker 无历史、csgo_investment 无历史、CSGOTrading 是伪K线，全部不可用；
3. **MarketScore 大盘情绪模型**（六因子加权，0~100）——无参考实现，全新设计；
4. **ItemScore 单品评分 + BUY/HOLD/SELL + 置信度**——CSGOTrading 只有三档方向无置信度，必须重写信号模型；
5. **库存净值曲线/历史快照**（csgo_investment 的反面清单直接定义了这件事）；
6. **CS饰品语义适配层**（无做空、整数件、长期零成交、流动性过滤）——VectorBT 之上必须自研；
7. **统一配置/密钥/时区/数据质量标记体系**。

### 2.4 哪些模块存在重复？

- **行情采集**：Tracker（直连五平台）与 CSGOTrading（cs2market）功能重叠且均已过时 → CSQuant 只保留一套 Provider/Adapter 层，两项目均不作为实现基础，仅 Tracker 提供调度与字段设计思想；
- **饰品分类/稀有度**：CSGO-API 与 Tracker 的 meta 爬虫重叠 → 以 CSGO-API 为唯一权威元数据源；
- **盈亏计算**：csgo_investment 与 CSGOTrading 的组合更新函数重叠 → 以 csgo_investment 的 KPI 口径为准，CSGOTrading 的 `calculate_ticker_shares` 仅作执行层参考；
- **数据库**：SQLite（CSGOTrading）vs MongoDB（Tracker）vs pickle（csgo_investment）三套 → 统一为一套（见 2.5）。

### 2.5 哪些技术栈冲突？如何裁决？

| 冲突 | 裁决 | 理由 |
|---|---|---|
| Node.js（CSGO-API）vs Python | **Python 唯一主线**；不引入 Node 运行时 | CSGO-API 只消费其生成的 JSON 产物（HTTP 拉取），不嵌套其代码 |
| Go 爬虫（csgo_investment）vs Python | **弃用 Go** | 指示文档明确 Python 优先；双语言维护成本高，且其爬虫目标接口已失效 |
| MongoDB+Redis（Tracker）vs SQLite（CSGOTrading） | **V1 用 SQLite（WAL模式）**，接口层抽象，预留 PostgreSQL/TimescaleDB 升级路径 | 单用户投研系统，4进程+Redis+Mongo 属过度工程；但时序数据增长后必须可迁移 |
| Streamlit（csgo_investment）vs 前后端分离 | **V1 用 Streamlit 快速成型 Dashboard**，API 层（FastAPI）与前端解耦后置 | 先验证投研价值，再做重前端；但所有业务逻辑必须在服务层，禁止写进 Streamlit 页面 |
| LangGraph（CSGOTrading）vs 纯函数管线 | **V1 纯函数管线，V5 再引入 Agent 编排** | V1 无 Event/LLM Agent，引入 LangGraph 是过度工程；决策流接口预留 |
| vectorbt 依赖激进（pandas>=3.0.3） | **独立 venv 安装，版本钉死** | 避免污染主环境；numba 安装风险隔离 |

### 2.6 哪些 API 已经过时？

明确不可继续依赖（证据见各分析文档）：

- csgo_investment：BUFF sell_order 裸连接口、UUYP 租赁链路（5 个字段硬编码=1，输出垃圾值）；
- Tracker：C5 `sga/v3` 路径、IGXE/C5 HTML 搜索页解析、UUYP 无签名 es 接口、Steam `currency=23&country=HK` 参数组合、Chrome 103 UA、`retrying` 库；ECO 平台代码缺失；
- Steam 官方：`ISteamEconomy/GetAssetPrices` 对 730 基本不可用（陈旧接口）；
- CSGOTrading：cs2market 数据源整体（疑似合成 + 单日快照伪K线）；
- **裁决**：BUFF/悠悠价格一律经由 SteamDT/CSQAQ 间接获取，不直连。

### 2.7 哪些模块可以直接移植？

| 模块 | 来源 | 移植说明 |
|---|---|---|
| 技术指标六函数 | CSGOTrading `technical.py` | 去除 LangChain 依赖后为纯 pandas 函数 |
| LLM 推理层 | CSGOTrading `llm/inference.py` + `provider.py` | OpenAI 兼容模式，直接搬 |
| 图状态/信号 Schema 思想 | CSGOTrading `graph/schema.py`、`constants.py` | 移植思想，字段重写（加 score/confidence/risk_level） |
| Steam 手续费逆运算 | Tracker `calculate_after_fee` | 纯函数直接搬 |
| 库存 KPI 公式组 | csgo_investment 聚合公式 | 修正 `&` 优先级 bug 后移植口径 |
| item_master 初始化数据 | ID-Mapper 730.json × 5 | 导入脚本自写，数据直接用 |
| 元数据 JSON | CSGO-API `public/api` | 定时同步脚本自写，数据直接用 |
| 回测统计体系 | VectorBT | pip 集成，薄封装 |

### 2.8 哪些只能重新实现？

MarketScore、ItemScore 与信号引擎、三源 Provider、时序存储与净值快照、库存 Steam 自动同步、异常价格检测与数据质量评分、回测适配层（bid-ask 执行模型、流动性过滤、整数件约束）、Dashboard。理由见 2.3。

### 2.9 如何形成一套统一架构？

```
数据底座（唯一权威源各一）
  元数据 ← CSGO-API JSON 每日同步
  ID映射 ← ID-Mapper 初始化 + Provider 运行时补录
  行情   ← Provider/Adapter 三源（CSQAQ 主、SteamDT 兜底+磨损、Steam 低频校验）
        ↓ 统一 UnifiedMarketData 结构（借鉴 Tracker 快照字段 + 自增时序）
SQLite（WAL）单库，DAO 抽象，可迁 PostgreSQL/TimescaleDB
        ↓
领域服务层（纯 Python，无框架依赖）
  inventory（KPI口径←csgo_investment）/ market（MarketScore）/ factors（指标←CSGOTrading函数）
        ↓
信号引擎（ItemScore→BUY/HOLD/SELL，含置信度/风险等级/理由，全量落库可追溯）
        ↓
风控与组合管理（两段式思想←CSGOTrading：规则定界→执行兜底）
        ↓
输出层：Streamlit Dashboard（V1）/ 告警 / VectorBT 回测（薄封装）
        ↓ V5
Agent/LLM 层（推理层代码←CSGOTrading，仅负责事件理解与信号解释）
```

统一纪律：任何平台特有字段不得穿透 Provider 层；任何 LLM 输出不得作为数值计算输入；任何信号必须可追溯到当时的数据快照。

### 2.10 如何避免过度工程化？

1. V1 不引入 Redis/Mongo/消息队列/K8s/微服务；单进程 + APScheduler + SQLite；
2. V1 不引入 LangGraph（无 Agent 需求时决策流用纯函数管线，接口预留）；
3. V1 不做自研回测引擎（VectorBT 薄封装即可）；
4. V1 不做重前端（Streamlit），但业务逻辑全部下沉服务层，保证未来可换 FastAPI+React；
5. 每个模块的「可升级路径」写进设计文档而不是预先实现（数据库、前端、Agent 三处）。

---

## 3. 参考项目 → CSQuant 模块映射表

| CSQuant 模块 | 主要参考 | 复用级别 | 说明 |
|---|---|---|---|
| `data_sources/` Provider 层 | Tracker（策略模式/调度思想）、调研笔记（真实API） | 思想借鉴，代码全写 | 三源统一 UnifiedMarketData |
| `items/` item_master + ID映射 | **ID-Mapper** | 数据直接复用 | market_hash_name 为唯一关联键；C5 ID 用 TEXT 存 |
| `items/` 元数据 | **CSGO-API** | 数据直接复用 | 每日同步 en/zh-CN JSON；manifestId 增量 |
| `inventory/` 库存与估值 | csgo_investment | 口径借鉴，代码重写 | KPI公式+状态机；升级为自动同步+净值快照 |
| `inventory/` 库存解析匹配 | CSGO-API inventory 反查表 | 方法借鉴 | defindex+paint_index 双路径匹配 |
| `database/` 行情时序 | Tracker 快照字段规范 | 字段借鉴，自建时序层 | 补齐 quote_ticks 历史维度 |
| `quant/factors/` 技术指标 | CSGOTrading `technical.py` | 代码直接复用 | 去 LangChain 化 |
| `quant/factors/` 流动性/供需 | Tracker（sell_list/求购深度字段） | 思想借鉴 | 数据源改由 Provider 供给 |
| `market/` MarketScore | 无 | 全新 | 六因子加权，权重回测校准 |
| `quant/signals/` 信号引擎 | CSGOTrading（三档+理由） | 思想借鉴，模型重写 | 增加 score/confidence/risk_level/version |
| `risk/` 风控 | CSGOTrading 两段式 PM | 思想借鉴 | 2/N 上限→按流动性分档；执行层兜底保留 |
| `agents/` + LLM 层（V5） | CSGOTrading `llm/` | 代码直接复用 | LLM 只做事件理解与解释，不碰数值 |
| `quant/backtest/` | VectorBT | 库集成+薄封装 | CS语义适配层自研 |
| 手续费/滑点模型 | Tracker `calculate_after_fee` + 调研 | 代码复用+扩展 | 分平台费率表 |
| Dashboard | csgo_investment 页面范式 | 范式借鉴 | Streamlit V1，逻辑下沉服务层 |
| 配置/密钥/审计 | CSGOTrading（config-as-experiment、prompt审计） | 思想借鉴 | .env + 信号版本化 + 决策留痕 |

## 4. 核心技术栈结论

Python 3.11+ / SQLite(WAL)→可迁 PostgreSQL / pandas / APScheduler / Streamlit(V1) / vectorbt(独立venv) / httpx(异步采集) / pydantic(数据校验) / python-dotenv / loguru。LLM 层（V5）复用 OpenAI 兼容模式。

## 5. 最大风险汇总（跨项目）

1. **CSQAQ 严禁商用条款 + 个人运营无 SLA** → 合规风险与断供风险，必须多源降级；
2. **SteamDT 限时免费政策不确定 + K线无成交量** → 不可单源依赖；
3. **Steam 官方接口 IP 级 429 风控** → 仅低频校验用，Cookie 调用需隔离账号风险；
4. **CSGOTrading 式假数据教训** → 任何策略结论必须基于真实时序数据，回测数据源必须可审计；
5. **vectorbt Commons Clause** → 自用免费，若商业化需重新评估许可。
