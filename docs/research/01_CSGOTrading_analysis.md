# CSGOTrading 深度代码分析报告

> 分析对象：`CS Coding/CSGOTrading-master/CSGOTrading-master`（CS2 饰品量化交易多 Agent 系统）
> 分析方法：逐文件通读全部核心源码（agents/、graph/、apis/、database/、llm/、util/、run/view/clear.py 及代表性 config），所有结论均引用实际代码，不依赖 README 宣传口径。

---

## 1. 项目定位

CSGOTrading 是一个**研究型（论文级）多 Agent LLM 回测框架**，而非生产交易系统。其核心目标是验证"LLM 多智能体协作能否在 CS2 饰品市场产生有效的 BUY/HOLD/SELL 决策"，而非真实下单。

关键证据：

- [run.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/run.py) 是**按日循环的批量回测驱动器**（`while current_date <= end_date` 逐日调用 `run_single_experiment`），输入为 `start-date`/`end-date`，没有任何实盘接口。
- 数据全部来自**离线预抓取 CSV**（`apis/cs2market/cs2_data.csv`、`apis/reddit/reddit_data.csv`、`apis/steam/steam_data.csv`），README 明确说"main experiment workflow will use these CSV files to avoid making API calls during backtesting"。
- `config/` 下 57 个 YAML 是**消融实验矩阵**：Direct / T / TS / TSL / TSLE / TSrL / TSrLE / TSrL-nofee 共 8 种分析师组合 × DeepSeek/Gemini/GPT/Claude/Kimi/Qwen 等 7 种 LLM，典型的学术消融设计。
- 项目中甚至带 `apis/cs2market/generate_sample_data.py`（用固定 seed=42 的随机游走生成假行情用于部署验证），进一步说明其"实验框架"属性。

一句话定位：**基于 LangGraph 的 CS2 饰品市场多 Agent LLM 决策回测框架，产出论文式实验结果，不具备实盘能力。**

## 2. 技术栈

| 类别 | 技术 | 依据 |
|---|---|---|
| 语言 | Python 3.8+ | requirements.txt |
| Agent 编排 | **LangGraph 1.0.7**（StateGraph/START/END） | [graph/workflow.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/graph/workflow.py) |
| LLM 抽象 | langchain_core 1.2.7 + langchain_openai 1.1.7 + langchain_deepseek 1.0.1，`with_structured_output(method="function_calling")` | [llm/inference.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/llm/inference.py) |
| LLM 供应商 | DeepSeek、OpenAI、Qwen（DashScope 兼容模式）、Kimi（Moonshot）、AiHubMix、Yizhan（中转站） | [llm/provider.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/llm/provider.py) |
| 数据模型 | pydantic 2.12.5 + typing_extensions TypedDict | graph/schema.py |
| 数据处理 | pandas、numpy | apis/cs2market/api.py、agents/analysts/technical.py |
| 存储 | **SQLite**（标准库 sqlite3，无 ORM） | database/cs2_sqlite_helper.py |
| 外部 API | praw 7.8.1（Reddit）、requests（Steam Web API / Steam 市场 priceoverview） | apis/reddit/api.py、apis/steam/api.py |
| 配置 | PyYAML + python-dotenv | util/config.py、.env.example |
| 观察工具 | 自研 CLI（view.py / clear.py），无 Web UI | view.py |

值得注意：requirements.txt **没有** anthropic / langchain_anthropic / langchain_google_genai 等依赖——Claude/Gemini/GPT 配置实际上是通过 AiHubMix/Yizhan 等 OpenAI 兼容中转站调用的（`TSLE-cd.yaml` 中 `provider: "Yizhan", model: "claude-sonnet-4-20250514"`），README 宣称的"Anthropic、Gemini 原生支持"在代码层面不成立。

## 3. 目录结构

```
CSGOTrading-master/
├── run.py                  # 批量回测入口（按日循环，唯一执行入口）
├── view.py                 # 结果查看/导出 CLI（725 行，直接裸写 SQL）
├── clear.py                # 按 exp_name 清理实验数据
├── verify_deployment.py    # 部署自检脚本
├── agents/
│   ├── planner.py          # Meta-Planner：LLM 动态选择分析师
│   ├── portfolio_manager.py# 组合经理：风控 + 最终决策
│   ├── registry.py         # Agent 注册表（类方法 + 类字典）
│   └── analysts/           # technical / sentiment / sentiment_reverse / liquidity / event
├── graph/
│   ├── workflow.py         # AgentWorkflow：构建并执行 LangGraph
│   ├── schema.py           # FundState / AnalystSignal / Decision / Portfolio / PositionRisk
│   └── constants.py        # AgentKey / Signal / Action 枚举
├── apis/
│   ├── router.py           # 数据源路由（CS2_MARKET / REDDIT / STEAM）
│   ├── common_model.py     # OHLCVCandle / MediaNews
│   ├── cs2market/          # 行情 CSV 读取 + Steam priceoverview 抓取 + 假数据生成
│   ├── steam/              # GetNewsForApp 新闻 + 历史 CSV
│   └── reddit/             # praw 封装 + 历史 CSV + 饰品同义词表
├── database/
│   ├── interface.py        # BaseDB 抽象基类
│   ├── cs2_sqlite_setup.py # 4 张表 DDL
│   └── cs2_sqlite_helper.py# SQLite 实现（551 行，所有 CRUD）
├── llm/
│   ├── inference.py        # agent_call：结构化输出 + 重试
│   ├── prompt.py           # 全部 9 个 prompt 模板
│   └── provider.py         # 供应商注册
├── config/                 # 57 个实验 YAML（8 组合 × 7 LLM）
├── util/                   # config 解析 / DB 单例 / 日志
├── logs/                   # 运行日志
└── assets/                 # cs2.db（SQLite 文件，CS2_DB_PATH 指向）
```

## 4. 核心模块

| 模块 | 文件 | 职责 | 评价 |
|---|---|---|---|
| 工作流引擎 | graph/workflow.py | 每个 ticker 构建一张 `START→analysts…→portfolio_manager→END` 的 StateGraph，串行遍历 ticker，逐日滚动组合 | 结构清晰但 LangGraph 只用到最浅的扇入扇出 |
| 状态定义 | graph/schema.py | `FundState`（TypedDict）+ pydantic 模型 | 信号聚合用 `Annotated[List[AnalystSignal], operator.add]`，LangGraph 惯例写法 |
| 注册表 | agents/registry.py | key → (func, doc) 映射， planner 靠 doc 描述做选择 | 简单有效，易扩展 |
| Planner | agents/planner.py | 把候选分析师描述喂给 LLM，让其选子集 | 失败时回退到全部分析师（workflow.py 中有 fallback） |
| 组合经理 | agents/portfolio_manager.py | 两次 LLM 调用（风控定仓位比例 → 决策定动作/数量）+ 纯 Python 计算可交易股数 | 风控与执行分离是最大设计亮点 |
| 分析师 | agents/analysts/*.py | 各自取数 → Python 预计算 → LLM 出 Bullish/Bearish/Neutral | 数值计算在 Python、判断在 LLM，分工合理 |
| 数据路由 | apis/router.py | 按 APISource 分发到三个 API 封装 | 薄封装层，新增数据源容易 |
| 持久层 | database/cs2_sqlite_helper.py | config/portfolio/decision/signal 四表 CRUD + 决策记忆查询 | 接口抽象良好，实现冗长（每方法重复 try/conn/close） |

## 5. 主数据流（行情进入 → BUY/HOLD/SELL 决策的完整链路）

以一天、一个 ticker 为例，完整链路如下（对应专项问题 1、2）：

1. **入口**：[run.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/run.py) 逐日循环 → `ConfigParser` 读 YAML → `cs2_db_initialize()` 建库 → `load_portfolio_config()` 按 `exp_name` 取/建 `cs2_config` 记录 → 校验日期单调递增（`latest_trading_date > cfg["trading_date"]` 则报错）。
2. **组合快照**：[AgentWorkflow.__init__](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/graph/workflow.py) 从 DB 取最新 portfolio，`copy_portfolio` 复制一份新交易日快照（若当日已存在则复用，保证幂等重跑）。
3. **分析师选择**：`load_analysts(ticker)` 三分支——无分析师走 Direct 模式；`planner_mode=True` 时 planner_agent 用 LLM 从候选中挑子集（失败回退全量）；否则用配置全量。
4. **分析师并行扇出**：每个 analyst 节点从 START 接入、汇入 portfolio manager。以 technical 为例（[technical.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/agents/analysts/technical.py)）：
   - `Router(APISource.CS2_MARKET).get_cs2_stock_daily_candles_df(ticker, trading_date)` → 从 `cs2_data.csv` 读该 item `date <= trading_date` 的全部历史（stock 风格，不泄漏未来）；
   - **纯 pandas 计算** 6 类指标信号（trend/mean_reversion/rsi/volatility/volume/support_resistance）；
   - 把结果填入 `TECHNICAL_PROMPT` → `agent_call(..., AnalystSignal)` 得到结构化信号；
   - `db.save_signal(...)` 落库（含完整 prompt），返回 `{"analyst_signals": [signal]}`，由 `operator.add` 聚合进 state。
   - sentiment/liquidity/event 同构：分别取 Reddit CSV / Reddit+成交量 / Steam 新闻 CSV，预聚合后交给 LLM 出信号。
5. **风控（第一次 LLM）**：[portfolio_agent](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/agents/portfolio_manager.py) 先取当前价（`get_cs2_stock_last_close_price`），算硬上限 `max_position_ratio = round(2/N*20)/20`（N=ticker 数，即单标的最多占 2/N，按 0.05 取整），然后：
   - 有信号 → `RISK_CONTROL_PROMPT.format(ticker_signals, portfolio, max_position_ratio)`；
   - 无信号（Direct）→ `RISK_CONTROL_PROMPT_DIRECT_LLM`；
   - LLM 输出 `PositionRisk.optimal_position_ratio`，**代码强制 clamp 到 [0, max_position_ratio]**（`if ... > max: = max; elif < 0: = 0`）。
6. **可交易量计算（纯 Python，不经 LLM）**：`calculate_ticker_shares()` 由 `position_limit = total_value × ratio`、`gap = limit − current_value` 推出 `tradable_shares`（买：`min(gap, cash) // price`；卖：`max(gap // price, −current_shares)`）。
7. **决策（第二次 LLM）**：`PORTFOLIO_PROMPT` 注入决策记忆（最近 5 条历史决策，`db.get_decision_memory(exp_name, ticker, 5)`）、当前价、持仓、tradable_shares、2% 卖出费率，LLM 输出 `Decision{action, shares, price, justification}`。
8. **后处理与执行**：代码强制 `decision.price = current_price`（不信 LLM 报的价格）、SELL 负股数取绝对值；`db.save_decision()` 落库。
9. **组合更新（纯 Python）**：`update_portfolio_ticker()` —— BUY 无手续费且按现金截断（`actual = min(shares, cash//price)`），SELL 按持仓截断并扣 2% 费（`cashflow += price*shares*(1−0.02)`），重算 position value；当日全部 ticker 处理完后 `db.update_portfolio()` 写回。
10. **查看**：view.py 直接 SQL 查询四张表，展示/导出组合、信号、决策、思维过程 JSON。

协作关系（专项问题 2）：**agents/ 是纯函数式节点实现；graph/ 负责拓扑与状态流转并调用 agents 注册表；apis/ 只被 agents 内的节点调用（Router 薄封装 CSV/网络）；database/ 贯穿全程——分析师存信号、组合经理读决策记忆、工作流读写 portfolio；portfolio_manager 是唯一被允许产出 Decision 的节点，位于所有 analyst 下游。**

## 6. 数据模型

**Signal 结构（专项问题核心）**——[graph/schema.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/graph/schema.py)：

```python
class AnalystSignal(BaseModel):
    signal: Signal          # 枚举: Bullish / Bearish / Neutral（graph/constants.py）
    justification: str      # 文本解释，默认 "No justification provided due to error"
```

注意：**信号只有三档方向，没有置信度/强度字段**（README 示例中的 `confidence` 字段在代码中不存在）。

```python
class Decision(BaseModel):
    action: Action   # Buy / Sell / Hold
    shares: int      # 买/卖数量，Hold 为 0
    price: float     # 会被代码覆写为真实收盘价
    justification: str

class PositionRisk(BaseModel):
    optimal_position_ratio: float  # 目标仓位占比，后被 clamp
    justification: str

class Position(BaseModel): value: float; shares: int
class Portfolio(BaseModel): id: str; cashflow: float; positions: dict[str, Position]

class FundState(TypedDict):   # LangGraph 状态
    exp_name, trading_date, ticker, llm_config, portfolio, num_tickers, enable_transaction_fee
    analyst_signals: Annotated[List[AnalystSignal], operator.add]  # reducer 聚合
    decision: Decision
```

统一数据契约 [apis/common_model.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/apis/common_model.py)：`OHLCVCandle{open,high,low,close,volume,date}` 与 `MediaNews{title,publish_time,publisher,link,summary,score,num_comments}`（Reddit 帖与 Steam 新闻共用一个模型，靠 score/num_comments 区分）。

**数据库表**：见第 8 章。

## 7. API / 外部依赖

| 数据源 | 实际实现 | 调用方式 | 说明 |
|---|---|---|---|
| "cs2market" | **并非独立数据站**——`fetch_cs2_data.py` 直接请求 `steamcommunity.com/market/priceoverview/`（appid=730, currency=1），取 `lowest_price→open`、`median_price→close`、`volume→日成交量`，存 CSV | 离线预抓 + `time.sleep(random.uniform(4,10))` 防限流，失败重试 3 次后用上期数据回填 | 本质是 **Steam 社区市场快照**，一天只采一次，open/close 是同一时刻的最低/中位价，不是真正 K 线 |
| Steam 新闻 | `api.steampowered.com/ISteamNews/GetNewsForApp/v2/`（appid=730，免 key），再按 item 名关键词过滤 | 离线预抓为 steam_data.csv，运行时按 [trading_date−7d, trading_date] 窗口读 CSV | 是**游戏级**新闻，关键词匹配到具体饰品的命中率很低 |
| Reddit | praw，3 个 subreddit（GlobalOffensiveTrade / csgomarketforum / cs2），含中英同义词表匹配饰品 | 离线预抓一年数据为 reddit_data.csv，运行时按 trading_date−7d 窗口过滤 | 需要 REDDIT_CLIENT_ID/SECRET |
| LLM | DeepSeek 原生；OpenAI/Qwen/Kimi/AiHubMix/Yizhan 全部走 `ChatOpenAI` + 自定义 base_url | 每次 agent_call 同步调用，`max_retries=3`，全失败返回 pydantic 默认实例（即 Neutral/Hold 兜底） | 结构化输出强制 `function_calling` |

LLM 被调用的环节（专项问题 14 的一半）：**planner 选分析师、每个 analyst 出信号、风控定仓位比例、portfolio 定动作数量、sentiment_reverse 反转信号**——共 6 类调用点；每个 ticker 每天最多 1(planner)+N(analysts)+2(risk+decision) 次。

## 8. 数据库

SQLite，路径由 `.env` 的 `CS2_DB_PATH=assets/cs2.db` 指定。DDL 见 [cs2_sqlite_setup.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/database/cs2_sqlite_setup.py)，四张表均带 `market_type='cs2'` 冗余字段：

**cs2_config**（实验配置）：`id(uuid) / exp_name / updated_at / items(JSON tickers) / has_planner / llm_model / llm_provider / market_type`

**cs2_portfolio**（每日组合快照）：`id / config_id(FK) / updated_at / trading_date / cashflow / total_assets / positions(JSON: {item: {shares, value}}) / market_type`
决策记录方式：**一天一行组合快照**（copy-on-write，`copy_portfolio` 先复制上一日再当日 update），同日重跑复用已有行保证幂等。

**cs2_decision**（交易决策，一 ticker 一条）：`id / portfolio_id(FK) / updated_at / trading_date / item_name / llm_prompt(完整 prompt 原文!) / action / quantity / price / justification / market_type`

**cs2_signal**（分析师信号，一 analyst×ticker 一条）：`id / portfolio_id / updated_at / item_name / llm_prompt / analyst / signal / justification / market_type`

亮点：每张信号/决策都**完整保存 llm_prompt**，天然支持实验审计与复现（view.py 的 `thinking` 命令据此导出思维过程 JSON）。索引覆盖 exp_name、trading_date、item_name、analyst 等常用查询列。

`get_decision_memory` 的实现链路：exp_name → config_id → 最近 5 个 portfolio_id → 这些 portfolio 下该 ticker 的最近 5 条 decision（只取 `trading_date/action/quantity/price`）。

## 9. 核心算法

### 9.1 技术指标（[technical.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/agents/analysts/technical.py)，全部 pandas 手写，未用 TA-Lib）

| 指标 | 参数 | 逻辑 |
|---|---|---|
| 趋势（EMA 多头排列） | 短 8 / 中 21 / 长 52 | `EMA8>EMA21 且 EMA21>EMA52 → Bullish`；两者皆否 → Bearish；否则 Neutral |
| 均值回复（Bollinger + z-score） | BB 窗口 20、z 窗口 50、极值 2.0、BB 位置阈值 0.2 | 价格处于 BB 下 20% 且 z-score 条件 → Bullish；上 20% → Bearish |
| RSI | 周期 14，30/70 | >70 Bearish，<30 Bullish（手写 Wilder 之前的简单均值版 RSI） |
| 波动率 | 21 日年化、63 日均值 | 低波动 regime(<0.8) 且 vol_z<−1 → Bullish（押注波动扩张）；高波动(>1.2) 且 vol_z>1 → Bearish |
| 成交量 | MA20、量价相关 20、异常倍数 2× | 输出文本（量趋势/量价相关/是否放量）给 LLM 读 |
| 支撑阻力 | pivot 窗口 5、回看 20 | 左右各 ≥2 个点确认局部高低点，取最近支撑/阻力及距离百分比 |

已知问题：`get_mean_reversion_signal` 中 Bullish 条件是 `z_score.iloc[-1] < params["z_score_extreme"]`（即 `< 2.0`，几乎恒真），疑似应为 `< -z_score_extreme` 的对称写法——实际约束只剩 BB 位置，属于**不对称的疑似 bug**。

### 9.2 Liquidity 建模（[liquidity.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/agents/analysts/liquidity.py)）

**不是盘口深度模型**（根本没有 order book 数据），而是两个代理变量的阈值打分：

1. 成交量：近 7 日均量 ≥100 记 high、<10 记 low、其余 moderate；
2. Reddit 参与度：相关帖 ≥3 条时，平均 score≥50 或平均评论≥20 记 high；score<5 且评论<2 记 low；
3. 两段分析文本 + 阈值填入 `LIQUIDITY_PROMPT`，规则写死在 prompt 里（"High volume OR strong engagement → Bullish…"），最终方向由 LLM 按规则判定。

### 9.3 Sentiment / Event 量化（专项问题 5）

- **Sentiment**（[sentiment.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/agents/analysts/sentiment.py)）：取 ticker 相关帖（同义词匹配 + score≥2/comments≥1 质量过滤 + 15 条上限 + 7 日窗口），帖子 JSON 全量塞给 LLM，要求输出 1–2 周短期情绪三档。帖数 <25 时走"数据不足→Neutral"专用 prompt；抓取异常走"保守兜底→Neutral"prompt。**无数值化 sentiment score，量化完全交给 LLM**。
- **Sentiment_Reverse**：先完整调用一遍 sentiment_agent（含一次 Reddit 取数和一次信号落库——会**重复存一条 sentiment 信号**），再让 LLM 按"过度看多=过热→看空"的反指假说反转。实现上有冗余（双重取数 + 双重写库）。
- **Event**（[event.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/agents/analysts/event.py)）：Steam 新闻 7 日窗口 ≤15 条，`EVENT_PROMPT` 中内置领域先验——**供给机制（掉落池/箱子/稀有度）> 曝光度（新箱子/战队贴纸/武器平衡）> 市场情绪**，LLM 按此优先级判断对价格的方向性影响。

两者进入决策的方式：信号对象经 `operator.add` 汇入 `state["analyst_signals"]` 列表，portfolio manager 把**整个列表 dump 进 RISK_CONTROL_PROMPT**，由 LLM 在阅读全部信号后给出目标仓位比例——信号间没有加权投票等数值融合，**融合也是 LLM 做的**。

### 9.4 Portfolio Manager 综合信号方式（专项问题 6）

两段式而非一次性融合：
1. **信号→仓位**：LLM 读全部 analyst 信号 + 组合 JSON，在 [0, max_position_ratio]（步长 0.05）内给目标仓位比例，代码 clamp；
2. **仓位→订单**：Python 算出 tradable_shares（目标仓位与现持仓的差额，受现金/持仓截断），LLM 在 [0, |tradable_shares|] 内决定 action+shares，并显式考虑 2% 卖出摩擦（prompt 规则："预期收益 < 卖出费影响 → Hold"）。

### 9.5 风控约束（专项问题 7）

- 硬上限：`max_position_ratio = round(2/N*20)/20`（单标的最多少于等于两份等权份额，0.05 粒度）——20 个 ticker 时上限 10%；
- 区间钳制：LLM 输出越界直接 clamp 到 [0, max]；
- 执行截断：BUY 不超现金、SELL 不超持仓（update_portfolio_ticker 内 min 截断 + warning 日志）；
- 交易成本：卖出固定 2%（`TRANSACTION_FEE_RATE=0.02`，workflow 与 portfolio_manager 各定义一份，可配置关闭）；
- 决策记忆：注入最近 5 条同标的决策，抑制反复横跳；
- LLM 全失败兜底：`agent_call` 重试 3 次后返回模型默认值——信号默认 Neutral、决策默认 Hold、风险比例默认 0（**失败时自动空仓，方向安全**）。

### 9.6 Prompt 结构（[llm/prompt.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/llm/prompt.py)）

9 个模板统一为"角色设定 + 注入数据 + 判定规则 + 结构化输出要求"四段式；所有 analyst 模板共享 `ANALYST_OUTPUT_FORMAT` 尾巴。风控/组合 prompt 则直接注入数值（当前价、可交易数、费率），把算术约束写成自然语言规则。

## 10. 最值得复用的设计

1. **"Python 算数、LLM 判断"的分工**：技术指标、可交易量、费用、仓位截断全部确定性计算；LLM 只做模式识别与文本理解类判断。这是整个项目最正确的架构决策。
2. **风控-决策两段式**：先定目标仓位比例（可 clamp 的单一标量），再定具体订单——把 LLM 的自由度压缩到一个可被硬约束校验的数值上。
3. **执行层兜底**：所有 LLM 输出都被代码二次校验（价格覆写、负股数修正、现金/持仓截断、LLM 失败默认 Neutral/Hold/0 仓位）。
4. **全量审计**：每条 signal/decision 都存完整 prompt，实验可复现、可导出思维链。
5. **CSV 预抓取 + trading_date 严格过滤**（`date <= trading_date`）的回测防泄漏范式。
6. **注册表 + 配置矩阵**：加分析师=写函数+注册+YAML 加一行，消融实验成本极低。
7. **copy-on-write 的每日组合快照**：同日重跑幂等，历史完整。

## 11. 可直接复用的代码

| 文件 | 复用理由 |
|---|---|
| [graph/schema.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/graph/schema.py) | AnalystSignal/Decision/Position/Portfolio/PositionRisk 定义干净，可直接搬（建议给 Signal 加 confidence 字段） |
| [graph/constants.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/graph/constants.py) | Signal/Action 枚举 |
| [llm/inference.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/llm/inference.py) | `agent_call`（结构化输出 + 重试 + 失败默认兜底）几乎零依赖，开箱即用 |
| [llm/provider.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/llm/provider.py) | OpenAI 兼容模式聚合多家国产 LLM 的配置范式，改 base_url 即可扩展 |
| [agents/analysts/technical.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/agents/analysts/technical.py) 的 6 个指标函数 | 纯 pandas、无 TA-Lib 依赖，修法均值回复 bug 后即可用 |
| [agents/portfolio_manager.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/agents/portfolio_manager.py) 的 `calculate_ticker_shares` + workflow.py 的 `update_portfolio_ticker` | 目标仓位→订单换算与含费成交的核心算术，逻辑正确可直接搬 |
| [database/cs2_sqlite_setup.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/database/cs2_sqlite_setup.py) 四表 DDL | "config/portfolio/decision/signal + llm_prompt 审计列"的 schema 设计直接适用于 CSQuant |
| [llm/prompt.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/llm/prompt.py) 的 EVENT_PROMPT | 供给机制>曝光度>情绪的 CS 饰品领域先验，是唯一有真正领域知识密度的 prompt |

## 12. 只能借鉴的部分

- **apis/cs2market/**：架构（Router + 统一 OHLCV 契约 + CSV 缓存 + 防泄漏过滤）值得借鉴，但数据源本身必须换掉（见 11/13 问）。`_fill_missing_dates` 用前后邻日均值插值填充缺失日期——**前视填充（用未来 +2 天数据）会污染回测**，借鉴时须改为只用历史侧。
- **graph/workflow.py**：LangGraph 编排思路可借鉴，但它每个 ticker 重建一次图、分析师串行等数据、LangGraph 仅当 if-else 用，CSQuant 可自行决定是否引入 LangGraph。
- **liquidity.py**：用"成交量+Reddit 热度"代理流动性的思路可参考，但阈值（100/10、50/20）是拍脑袋的，且与 CSQAQ/SteamDT 的真实在售量/求购量数据相比信息量低一个量级。
- **sentiment 三档输出**：无置信度，无法做加权融合；CSQuant 应扩展为 score∈[−1,1]+confidence。
- **decision memory（最近 5 条决策注入 prompt）**：思路好，但只注入 action/quantity/price，不含盈亏结果，记忆质量有限。

## 13. 已过时/不适用部分（必须重写）

1. **数据源整体**：`fetch_cs2_data.py` 依赖 Steam priceoverview 裸爬（无 cookie、随机 UA、4–10s  sleep），一天一采、只有 lowest/median 两个价位，无历史深度；而仓库内置的 `cs2_data.csv` 经核对为 **20 个 item × 161 天（2025-05-20→2025-10-27）整好 3221 行、逐日无缺口**——与 `generate_sample_data.py` 的生成参数（seed=42 随机游走、相同日期区间、相同 20 个 item）完全吻合，**几乎可以确定仓库自带行情 CSV 就是合成假数据**，不是真实 Steam 行情。
2. **Steam 新闻做 Event 源**：GetNewsForApp 是游戏级新闻，用 item 名关键词过滤命中率极低，event agent 大部分时间在分析空列表。
3. **[util/cs2_db_helper.py](file:///c:/Users/Jie%20Qiao/Desktop/Coding/CS%20Coding/CSGOTrading-master/CSGOTrading-master/util/cs2_db_helper.py) 的 `CS2SupabaseDB`**：该类**在代码库中不存在**，`use_local_db=False` 分支必抛 NameError——是半成品死代码，README 也只字未提 Supabase。
4. **config 中的 `multi_item_mode`**：57 个 YAML 都写了，但全代码库无任何读取处（grep 0 命中），是配置残留。
5. **README 宣称但代码缺失**：Anthropic/Gemini 原生 provider、Ollama、Supabase、"Advanced Risk Models"等均不存在；AnalystSignal 无 confidence 字段。
6. **view.py 的裸 SQL 与复制粘贴**：功能可用但工程上应重写。

## 14. 对 CSQuant 的具体价值

1. **直接提供一套可运行的"多 Agent 决策层"参考实现**：CSQuant 若已有数据层（SteamDT/CSQAQ），本项目恰好补上了它最缺的"信号→仓位→订单"决策链，且边界清晰（apis 层整体替换即可）。
2. **实验治理范式**：config-as-experiment（exp_name 贯穿 DB 四表）、prompt 全量审计、view/clear CLI——CSQuant 做策略 A/B 与消融时可整套照搬。
3. **CS 饰品领域知识**：EVENT prompt 的供给机制优先级、Reddit 同义词表（WEAPON_SYNONYMS/SKIN_VARIANTS 含中英社区黑话）、2% 交易摩擦假设，都是可直接迁移的领域资产。
4. **LLM 使用边界的反面/正面教材**：正面——数值全在 Python；反面——信号融合与仓位比例仍交给 LLM 且信号无置信度，CSQuant 可针对性升级为"数值融合 + LLM 只出方向与文本"。

## 15. 推荐集成方式（专项问题 13）

建议采用**"保留决策骨架、替换数据层、升级信号模型"**的三步改造：

```
SteamDT API ─┐
CSQAQ API ───┼─→ DataRouter（扩展 apis/router.py，新增数据源枚举）
Steam API ───┘        │
                      ▼
        统一 OHLCV + 在售/求购深度契约（扩展 common_model.py）
                      ▼
   analysts（technical 复用 / liquidity 改用真实深度 / sentiment 保留 Reddit）
                      ▼
   信号层升级：AnalystSignal + score∈[-1,1] + confidence + 数值加权融合
                      ▼
   风控（保留两段式，ratio 由规则+LLM 混合决定）→ Portfolio Manager → Decision
                      ▼
        SQLite 四表审计（复用 DDL，加 data_source 字段）
```

具体做法：
- 在 `APISource` 增加 `STEAMDT / CSQAQ` 枚举，各自实现 `get_daily_candles_df` 与新增的 `get_order_book`/`get_sale_stats`，对外仍暴露统一 `Router` 接口，agents 层零改动；
- cs2market/api.py 整体替换为"多源聚合 + 本地 DuckDB/SQLite 缓存"，保留 `trading_date` 防泄漏过滤函数签名；
- Liquidity 改为用 CSQAQ 在售数量、SteamDT 求购深度、换手率建模，阈值按分位数自适应而非硬编码；
- 保留 LangGraph 与否取决于 CSQuant 是否需要 planner 动态编排——若策略固定，普通函数管线更简单。

## 16. 风险和注意事项

1. **最大风险——数据真实性**：仓库自带 `cs2_data.csv` 高度疑似合成数据（与 `generate_sample_data.py` 参数逐一吻合）；即便用 `fetch_cs2_data.py` 实采，Steam priceoverview 也只是一日一快照、open/close 为同刻 lowest/median，且官方接口限流严格（无 cookie 长期抓取易被封）。**该项目的回测结论不可作为策略有效性证据。**
2. **前视偏差**：`_fill_missing_dates` 用缺失日 **±2 天**的邻居均值插值，其中 +2 天是未来数据，回测存在 look-ahead bias。
3. **LLM 非确定性**：温度 0.5 + 多家供应商 + 重试 3 次后静默兜底，同一实验两次运行结果可能不同；决策质量随 LLM 版本漂移，不可复现。
4. **信号无置信度 + 融合靠 LLM 阅读**：三档信号不含强度，PM 如何"权衡"各信号完全不可控、不可归因。
5. **成本与速度**：20 ticker × (1 planner + 4 analyst + 2 PM) ≈ 每天上百次 LLM 调用，回测一个月约数千次调用，API 费用与耗时可观；run.py 遇单日失败直接 `sys.exit(1)`，长回测脆弱。
6. **代码缺陷**：均值回复 z-score 疑似符号 bug；sentiment_reverse 重复取数与重复写库；`CS2SupabaseDB` 死代码；`multi_item_mode` 无效配置；`TRANSACTION_FEE_RATE` 在两个文件重复定义（0.02 一致但易 drift）。
7. **许可与合规**：MIT 许可可自由复用；但 Steam 市场数据抓取违反 ToS 的风险由使用者承担，商用应改用 SteamDT/CSQAQ 等授权 API。
8. **不适合直接实盘**（专项问题 12）：无订单执行、无库存同步、无风控实时化、无异常恢复，仅是研究框架。

---

## 附：14 个专项问题速答

1. **完整数据流**：CSV 行情/Reddit/Steam 新闻 → Router → 各 analyst（Python 预算指标 → LLM 出三档信号，存 cs2_signal）→ 信号列表汇入 FundState → portfolio manager（LLM 风控定仓位比例→clamp→Python 算 tradable_shares→LLM 定 action/shares）→ Python 执行含费成交 → 更新 portfolio 快照存 cs2_portfolio + cs2_decision。详见第 5 章。
2. **模块协同**：agents 是节点函数，graph 管拓扑与状态，apis 仅供节点取数，database 全程读写，portfolio_manager 是唯一决策出口。详见第 5 章末。
3. **技术指标**：EMA(8/21/52) 趋势、Bollinger(20)+z-score(50) 均值回复、RSI(14, 30/70)、21 日年化波动率 regime、量价分析（MA20/相关/异常量）、pivot 支撑阻力。详见 9.1。
4. **Liquidity 建模**：无盘口深度；7 日均量阈值（≥100/<10）+ Reddit 参与度阈值（score 50/评论 20）双代理，规则写进 prompt 由 LLM 判向。详见 9.2。
5. **Sentiment/Event 如何进决策**：都产出 `AnalystSignal{Bullish/Bearish/Neutral, justification}`，经 operator.add 汇入 state 后整体注入风控 prompt，由 LLM 在阅读后给出仓位比例——无数值融合。详见 9.3/9.4。
6. **PM 综合信号**：两段式——信号+组合→目标仓位比例（clamp 到 [0, 2/N]）；比例→tradable_shares（Python）→LLM 在约束内定 BUY/SELL/HOLD 与数量。详见 9.4。
7. **风控约束仓位**：硬上限 2/N（0.05 粒度）+ clamp + 现金/持仓执行截断 + 2% 卖出费 + 最近 5 条决策记忆 + LLM 失败默认 0 仓位。详见 9.5。
8. **可直接复用**：schema/constants、llm/inference.py、provider.py、technical 指标函数、calculate_ticker_shares/update_portfolio_ticker、四表 DDL、EVENT prompt。详见第 11 章。
9. **只能借鉴**：Router/CSV 缓存架构、LangGraph 编排思路、流动性代理思路、决策记忆思路。详见第 12 章。
10. **必须重写**：数据源（cs2market 整个目录的取数逻辑）、Steam 新闻 event 源、信号模型（加置信度/强度）、view.py、Supabase 死分支。详见第 13 章。
11. **数据源局限**：所谓 cs2market 实为 Steam priceoverview 单日快照（lowest/median 伪 OHLC），仓库自带 CSV 高度疑似 seed=42 的合成随机游走；Reddit 仅限 3 个英文 sub；Steam 新闻为游戏级。真实性不足以支撑真实市场结论。详见第 13、16 章。
12. **能否直接用于真实 CS2 市场**：不能。无实盘执行、数据不真实/有前视、LLM 不可复现，仅适合做决策层架构参考。详见第 16 章。
13. **多数据源改造**：APISource 增加 STEAMDT/CSQAQ 枚举 + 统一契约 + liquidity 改用真实深度数据，agents 层零改动。详见第 15 章。
14. **LLM 该保留/不该做的**：保留——文本情绪判断、事件影响解读、planner 选分析师、（有限度的）信号融合解释；不能交给 LLM——指标计算、仓位算术、费用、成交截断、价格（当前实现已正确地把这些放在 Python，且价格由代码覆写）。详见 9.6 与第 10 章。
