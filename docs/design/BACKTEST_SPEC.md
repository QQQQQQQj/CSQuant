# CSQuant 回测设计规格（BACKTEST_SPEC）

> 依据：`docs/research/06_VectorBT_analysis.md`（vectorbt 1.1.0 真实 API 签名）、`docs/research/datasource_web_research_notes.md`（历史数据来源边界）、`docs/research/03_SteamTradingSiteTracker_analysis.md`（`calculate_after_fee` 手续费逆运算）、`docs/design/DATA_SOURCE_SPEC.md`（quote_ticks / kline_daily / data_quality A-D 约定）。
> 被验证对象：`FACTOR_AND_SIGNAL_SPEC.md` 定义的 MarketScore / ItemScore / BUY-HOLD-SELL 信号规则。**本文不重复定义任何因子公式，只定义验证方法。**
> 工程基线：回测与实盘共用同一套因子/信号代码（策略即配置）；无法确认的数值一律标注「待验证」并集中登记于附录 A 假设表。

---

## 0. 全局约定

1. **同一代码路径**：回测调用 `quant.signals.signal_engine` 的公开接口生成信号，与实盘完全一致；回测侧只额外做时间错位（fshift）与执行模拟。禁止在回测模块内重写因子逻辑。
2. **历史数据来源优先级**：自建 `quote_ticks` / `kline_daily` 积累（主）→ CSQAQ 企业 K 线（t/o/c/h/l/v，若获得授权）→ SteamDT K 线（o/c/h/l，**无成交量**，使用时必须在报告标注局限）→ Steam `pricehistory`（日级中位价，需 Cookie，低频补漏）→ SteamTradingSiteTracker-Data 大型仓库（后续按需，当前不下载）。
3. **基准**：CSQAQ 大盘指数（`market_index` 表，`index_name="csqaq_main"`）；备选 SteamDT 大盘指数（`"steamdt_broad"`）。CSQAQ 指数成分与回溯修正规则不完全公开，基准可比性标注「待验证」。
4. **频率与口径**：统一日级回测（`freq="1D"`），年化按 365 自然日；所有时间 UTC；所有价格 CNY（USD 源按采集日汇率换算落库，汇率存 `extras`）。
5. **交易约束**：`direction="longonly"`（饰品无做空）、`size_granularity=1`（整数件）、`min_size=1`、`cash_sharing=True` 共享资金池、信号 `fshift(1)` 次周期成交。

---

## 1. 回测目标与验证假设清单

回测不是"跑一个收益曲线"，而是依次证伪/确认以下假设。每条假设对应 §6 的实验与通过标准。

| 编号 | 待验证假设 | 验证方法 | 通过标准（初始值，可修订） |
|---|---|---|---|
| H1 | MarketScore 分档与未来大盘收益正相关 | 事件研究：按 MarketScore 五档（0-20/20-40/40-60/60-80/80-100）分组，统计每组后 5/10/20 日 CSQAQ 指数收益均值与胜率 | 分组均值单调递增；顶档-底档差显著（t 检验 p<0.05） |
| H2 | ItemScore 有截面预测力 | 每日截面 Spearman Rank IC（ItemScore vs 未来 N=5/10/20 日收益）序列 | IC 均值 > 0.03，ICIR > 0.3，IC>0 占比 > 55% |
| H3 | ItemScore 分层单调 | 按 ItemScore 十分位分组，各组未来 N 日等权收益 | 组收益单调性 Kendall tau > 0.7；顶组 > 底组 |
| H4 | BUY-HOLD-SELL 规则费后跑赢基准 | §6 实验 1：组合回测 vs CSQAQ 指数 vs 等权买入持有 vs 随机信号 | 费后总收益与 Sharpe 均超基准；超过随机策略分布 95 分位；最大回撤 < 30% |
| H5 | 默认阈值（75/45）处于稳健区而非过拟合尖峰 | §6 实验 2：阈值网格扫描 | 默认参数邻域存在"高原"（±5 邻域内 Sharpe 衰减 < 50%） |
| H6 | 因子权重扰动下结论稳定 | §6 实验 3：权重 ±20% 扰动 | 费后年化收益 CV < 0.5；信号翻转率 < 30% |
| H7 | 策略样本外有效 | §6 实验 4：walk-forward | 样本外 Sharpe 中位数 > 0；样本外/内衰减比 > 0.5；>60% 窗口跑赢基准 |

**H1-H3 为因子层验证（截面统计，不依赖 vectorbt 组合模拟）；H4-H7 为组合层验证（vectorbt 回测）。因子层未通过的假设不得进入组合层调参。**

---

## 2. 数据准备管道

### 2.1 样本集（universe）定义

CS 饰品全库约 34k 件，回测样本集按以下规则**在每个验证窗口起点动态确定**（point-in-time universe，禁止用期末幸存者）：

1. `item_master` 中映射有效（各平台 ID 至少其一非空，非人工黑名单）；
2. 该窗口起点前已上市 ≥ `min_lookback=60` 天（因子滚动窗口需要历史）；
3. 窗口前 60 天数据覆盖率 ≥ `min_coverage=0.6`（有 A/B 级报价的自然日占比）；
4. 通过流动性过滤：窗口前 20 日日均成交量 ≥ `min_volume`（默认 2 件/日，附录 A-7）；成交量缺失时按 §5.3 降级规则判定；
5. 每期 universe 快照落库（`universe_snapshot` 表：snapshot_id, as_of_date, item_uuid 列表, 过滤统计 JSON），回测结果必须引用 snapshot_id。

**幸存者偏差与选择偏差讨论**：CS 饰品一般不因退市消失，但存在三类偏差——(a) 新饰品持续上市，静态 universe 会系统性偏老；(b) 被 V 社封禁/交易锁定的饰品事实死亡，若剔除则高估收益，本规格保留至最后一条有效报价并记 `exit_reason="stale"`；(c) 自选池/库存池天然偏向热门高流动性品，**实验 1 必须在全库过滤后的 universe 上运行**，自选池结果只能作为补充对照，报告中注明偏差方向（高估）。

### 2.2 quote_ticks → 日线 panel 重采样

对 universe 内每个 `item_uuid`、每个平台（默认 BUFF，实验可切 Steam/悠悠）：

1. **日终快照**：取每个自然日（UTC）最后一条 A/B 级 `quote_ticks` 记录；
2. **字段映射**：`ask = sell_price`（卖一价，可买入价）、`bid = buy_price`（买一价，可卖出价）、`close_ref = reference_price` 缺失时退化为 `(ask+bid)/2`，单边缺失时取可用边；
3. **成交量**：当日 `volume_24h` 末值（CSQAQ `turnover_number` 口径为日累计）；SteamDT K 线回填时 `volume = NULL` 并全局标注；
4. **K 线回填**：自建 tick 覆盖不足的时段，按 §0.2 优先级用 K 线数据回填 `close_ref`（K 线无 bid/ask → 执行价按 §4.4 降级方案，half-spread 代理）；
5. **陈旧剔除**：距当日超过 `staleness_limit_days=3` 天无 A/B 级更新的 bar，标记 `STALE`，该 bar 信号屏蔽（见 §3 `quality_mask`）；
6. **异常过滤**：`data_quality ∈ {C, D}` 或 `quality_flags` 含 `PRICE_SPIKE_SUSPECT` / `INVERTED_BOOK` / `CROSS_PLATFORM_DIVERGENCE` 的 bar，价格置 NaN（**禁止置 0**，工程原则第 14 条）；
7. **缺失语义**：NaN 只允许前向填充于估值层（vectorbt `ffill_val_price=True`），**信号层与执行层使用前必须显式处理**——vectorbt 对 NaN 价格的订单会以 `PriceNaN` 状态静默忽略，若不清洗会造成"信号发了但没成交"的隐性失真，入库前必须完成上述清洗并输出质量报告（每列 NaN 占比、STALE 占比、过滤条数）。

输出物为 `PricePanel`（§3.1）。管道实现位置 `quant/backtest/panel.py`，全程可重放：输入 = 数据库快照 + 管道版本号。

---

## 3. vectorbt 薄封装适配层设计（`quant/backtest/`）

### 3.1 模块结构与核心数据结构

```text
quant/backtest/
├── panel.py       # prepare_price_matrix()：§2 管道产出 PricePanel
├── signals.py     # build_signal_matrices()：信号错位与可成交性修正
├── costs.py       # CostModel、费率表、calculate_after_fee 复用
├── execution.py   # ExecutionModel、滑点分档、exec_price 构造
├── engine.py      # run()：调用 vectorbt，返回 BacktestResult
├── metrics.py     # report()：统计提取、基准对比、落库、markdown 报告
└── experiments.py # E1-E4 实验编排与参数网格
```

```python
@dataclass
class PricePanel:
    close: pd.DataFrame            # index=date(UTC), columns=item_uuid；估值参考价
    ask: pd.DataFrame              # 卖一价；NaN=当日无在售（不可买入）
    bid: pd.DataFrame              # 买一价；NaN=当日无求购（不可卖出）
    volume: pd.DataFrame | None    # 日成交量；无量数据源整体为 None
    quality_mask: pd.DataFrame     # bool；False=该 bar 不可用（C/D级/STALE/异常）
    liquidity_tier: pd.Series      # index=item_uuid → "high"|"mid"|"low"（§5.1）
    meta: dict                     # 数据源、平台、覆盖率、管道版本、universe_snapshot_id

@dataclass
class SignalMatrices:
    entries: pd.DataFrame          # bool，已 fshift + 可成交性修正，可直接喂 vectorbt
    exits: pd.DataFrame
    raw_entries: pd.DataFrame      # 修正前（审计用）
    raw_exits: pd.DataFrame
    blocked: pd.DataFrame          # 被屏蔽/顺延的信号计数矩阵（审计用）

@dataclass
class CostModel:
    platform: str                  # "buff" | "uuyp" | "steam"
    fee_buy: float                 # 买家侧比例费率
    fee_sell: float                # 卖家侧比例费率
    fixed_fee_per_trade: float = 0.0
    withdrawal_fee_rate: float = 0.0   # 提现费，报告层单列，不进撮合
    steam_exact_fee: bool = False      # True → 报告层用 calculate_after_fee 逐笔复核

@dataclass
class ExecutionModel:
    slippage_by_tier: dict         # {"high": 0.005, "mid": 0.01, "low": 0.03}
    reject_prob_by_tier: dict      # {"high": 0.0, "mid": 0.1, "low": 0.3}
    min_volume: float = 2.0
    liquidity_window: int = 20
    half_spread_proxy: dict | None = None  # 无 bid/ask 历史时的半价差代理（§4.4）
    seed: int = 42                 # reject_prob 随机种子，保证可复现
```

### 3.2 核心函数签名与职责

```python
# panel.py
def prepare_price_matrix(
    item_uuids: list[str],
    start: date,
    end: date,
    platform: str = "buff",
    staleness_limit_days: int = 3,
    min_coverage: float = 0.6,
    min_lookback: int = 60,
    exec_model: ExecutionModel | None = None,
) -> PricePanel:
    """执行 §2 全部清洗与重采样规则；内部完成 universe 过滤与流动性分档，
    并把 universe 快照写入 universe_snapshot 表。返回的矩阵可直接用于回测。"""

# signals.py
def build_signal_matrices(
    panel: PricePanel,
    strategy: StrategyConfig,      # 来自 FACTOR_AND_SIGNAL_SPEC 的策略配置（版本化）
    exec_lag: int = 1,
) -> SignalMatrices:
    """1) 调用实盘同款 signal_engine 逐 bar 生成 raw BUY/SELL（仅 t 及之前数据）；
    2) fshift(exec_lag) 错位到执行 bar（防未来函数，信号默认当根 close 成交必须错位）；
    3) quality_mask=False 的 bar 信号清零；
    4) 可成交性修正（§4.3）：买入信号遇 ask=NaN 直接作废（机会型，过期重评）；
       卖出信号遇 bid=NaN 向后顺延至首个可成交 bar（风险型，必须保持）。"""

# engine.py
def run(
    panel: PricePanel,
    signals: SignalMatrices,
    cost_model: CostModel,
    exec_model: ExecutionModel,
    init_cash: float = 100_000.0,
    size: float | pd.DataFrame = np.inf,   # 组合模式传 suggested_position 比例
    size_type: str = "amount",             # 单饰品="amount"；共享资金池="percent"
    cash_sharing: bool = True,
    freq: str = "1D",
) -> BacktestResult:
    """构造 exec_price / fees / slippage / reject_prob 矩阵（§4），
    调用 vbt.Portfolio.from_signals（CS 语义默认值全部在此封装），
    返回 BacktestResult（pf 对象 + 输入指纹 + 质量审计计数）。"""

# metrics.py
def report(
    result: BacktestResult,
    benchmark: pd.Series,          # CSQAQ 指数日级序列
    output_dir: Path,
    persist: bool = True,
) -> BacktestReport:
    """提取 §7 全部指标；Sharpe NaN 按 §5.4 规则降级展示；
    写 backtest_result / backtest_trade；生成 §9 markdown 报告。"""

# experiments.py
def run_experiment(exp_id: str, grid: dict, panel: PricePanel,
                   strategy: StrategyConfig, **run_kwargs) -> ExperimentResult:
    """E1-E4 统一入口；参数经 vectorbt 广播（列=参数组合）一次模拟跑完。"""

# costs.py（复用 SteamTradingSiteTracker scripts/utils.py，零改动）
def calculate_after_fee(amount: float) -> float:
    """Steam 手续费逆运算：从买家支付价反推卖家到手（steam_fee 5% + publisher_fee 10%，
    各向下取整、最低 1 分，迭代逼近 ≤10 轮）。"""
```

### 3.3 为什么用 `from_signals` 而非 `from_orders`

| 维度 | from_signals | from_orders | 结论 |
|---|---|---|---|
| 语义匹配 | CSQuant 策略本质是 BUY/HOLD/SELL 状态机，与信号语义一致；持仓中自动忽略重复入场 | 每 bar 需显式订单，持仓状态要自行维护 | 信号模式与"回测实盘共用代码"原则兼容 |
| 稀疏成交 | 持仓期无信号=继续持有，天然适配饰品长持仓 | 需自行生成持仓保持语义 | 适配 |
| 成交价控制 | 单一 `price` 入口，但支持每元素独立定价——把 bid/ask 预先烘入 price 矩阵即可（§4.2） | 订单自带方向，价格控制更直接 | 烘入法可等价表达，无需换 API |
| 重复信号 | 默认过滤（`accumulate=False`） | 全部照单执行，需自行去重 | 信号模式更安全 |
| from_order_func | — | Numba 回调无法复用实盘策略代码 | 排除 |

**结论：主路径固定 `Portfolio.from_signals`。** 仅在未来需要表达复杂挂单逻辑时评估 `from_order_func`，且必须保持策略代码不侵入回调。

### 3.4 CS 语义默认值封装（engine.run 内部）

```python
vbt.Portfolio.from_signals(
    close=panel.close,                 # 估值价
    entries=signals.entries, exits=signals.exits,
    price=exec_price,                  # §4.2 烘入 bid/ask 的执行价矩阵
    size=size, size_type=size_type,
    direction="longonly",              # 饰品无做空
    fees=fees_matrix,                  # §4.1 二维：买入 0 / 卖出 fee_sell
    fixed_fees=cost_model.fixed_fee_per_trade,
    slippage=slippage_matrix,          # §4.2 按列流动性档广播
    min_size=1, size_granularity=1,    # 整数件
    reject_prob=reject_matrix,         # §5.2 稀疏成交惩罚
    init_cash=init_cash, cash_sharing=cash_sharing,
    call_seq="default",                # 不用 Auto：官方警告其"同 bar 同时成交"假设在低频市场失真
    ffill_val_price=True,              # 无报价期按旧价估值（报告须注明）
    freq=freq, seed=exec_model.seed,
)
```

### 3.5 独立 venv 与依赖钉死

vectorbt 1.1.0 强制 `pandas>=3.0.3`（3.x 大版本）+ `numpy>=2.4.6` + `numba>=0.66` + `Python>=3.11,<3.15`，与主系统 pandas 2.x 生态存在冲突风险。**回测模块使用独立虚拟环境 `.venv-backtest`**，与主程序仅通过数据库 / parquet 文件交换 `PricePanel`，不共享进程：

```text
# quant/backtest/requirements-backtest.txt（钉死版本，pip-compile 生成 lock）
vectorbt==1.1.0
numpy==2.4.6      # 以 lock 文件为准
pandas==3.0.3     # 以 lock 文件为准
numba==0.66       # 以 lock 文件为准
pyarrow           # parquet 交换
```

许可证：Apache 2.0 + Commons Clause，内部投研自用免费合法；若未来对外收费且核心价值来自 vectorbt，需重新评估。TA-Lib 为可选依赖，Windows 不装。

---

## 4. 费用 / 滑点 / 执行模型完整规格

### 4.1 费率表（分平台，假设集中登记于附录 A）

| 平台 | 买入费 | 卖出费 | 固定费 | 提现费（报告层单列） | 状态 |
|---|---|---|---|---|---|
| Steam | 0 | ≈15%（5% Steam + 10% 游戏费，各向下取整、最低 1 分；精算用 `calculate_after_fee` 逆运算） | 单笔最低 0.01 USD 量级 | — | 高置信（Tracker 真实代码实现） |
| BUFF | 0 | 2.5% 交易费 | 0 | 1% | **待验证** |
| 悠悠有品 | 0 | 2.0%（占位值，敏感性分析覆盖 0-3%） | 0 | 未知，暂按 0 | **待验证** |

vectorbt `fees` 对买卖双向收费，故构造二维费率矩阵实现单边收费：`fees_matrix[entries]=fee_buy`、`fees_matrix[exits]=fee_sell`，其余位置 0。**Steam 精算两阶段**：引擎内按 `fee_sell=0.15` 近似撮合；报告层对每笔 Steam 卖出用 `calculate_after_fee` 重算实际到手，差异 > 0.5% 时在报告标注修正值。

### 4.2 执行价格模型（bid-ask 烘入 price 矩阵）

目标语义：**买入成交价 = 卖一价 × (1 + slippage_buy)；卖出成交价 = 买一价 × (1 − slippage_sell)**。实现（方案 A，利用 vectorbt 原生方向性滑点）：

```python
exec_price = panel.close.copy()
exec_price[signals.entries] = panel.ask[signals.entries]   # 买入 bar 用卖一
exec_price[signals.exits]   = panel.bid[signals.exits]     # 卖出 bar 用买一
slippage_matrix = panel.liquidity_tier.map(exec_model.slippage_by_tier)  # 按列广播
```

vectorbt 撮合内核买单 `price×(1+s)`、卖单 `price×(1−s)`，与目标公式精确一致。非信号 bar 的 price 值无关紧要（不触发订单）。**铁律：最低挂售价（卖一）不得直接当成交价**——必须叠加滑点；K 线 close 更不得直接当成交价（只在无盘口历史时按 §4.4 降级）。

### 4.3 分档滑点与不可成交规则

| 流动性档 | 划分（近 20 日日均成交量，附录 A-6） | slippage | reject_prob |
|---|---|---|---|
| high | ≥ 20 件/日 | 0.5% | 0 |
| mid | 5-20 件/日 | 1% | 0.1 |
| low | < 5 件/日（但 ≥ min_volume，否则已在 universe 过滤剔除） | 3% | 0.3 |

（以上数值为初始假设，附录 A-5/A-10，待校准。）

不可成交规则：

1. **无买一报价 → 该时点不可卖出**：卖出信号顺延至首个 `bid` 非 NaN 的 bar（`_defer_exits_until_tradable`），顺延期间的持仓风险在报告的 `blocked` 审计表中披露；顺延超过 10 天仍未成交记 `exit_reason="illiquid_timeout"`。
2. **无卖一报价 → 该时点不可买入**：买入信号当日作废不延后（价格已变，信号应重新评估）。
3. `quality_mask=False` 的 bar 双向信号清零。
4. `reject_prob` 触发拒单时，from_signals 的 exit 信号不会自动重发——适配层对 low 档饰品的 exit 信号做 `N=3` 个连续 bar 的重复置位，模拟"挂单未成交后继续挂"。

### 4.4 无盘口历史数据的降级方案

自建 `quote_ticks` 积累之前的 K 线历史（SteamDT / CSQAQ 企业档）只有 o/c/h/l，无 bid/ask。降级：`ask = bid = close`，用 `half_spread_proxy` 按档设半价差（high 1% / mid 2.5% / low 5%，初始假设待校准），即买单 `close×(1+half_spread+s)`、卖单 `close×(1−half_spread−s)`。**使用该降级方案的回测必须在报告头部标注"执行价为代理模型，费后收益存在系统性高估风险"**；Steam pricehistory 中位价同理。

---

## 5. 流动性过滤与稀疏成交处理

### 5.1 流动性过滤（universe 层）

- 指标：近 `liquidity_window=20` 日日均成交量；阈值 `min_volume=2` 件/日（附录 A-7）剔除出 universe；
- 成交量字段缺失时（SteamDT 无量 K 线）按 §5.3 降级；
- 分档（§4.3 表）写入 `PricePanel.liquidity_tier`，驱动滑点与拒单率。

### 5.2 稀疏成交的撮合层惩罚

低流动性饰品"信号日无真实成交"会导致回测按收盘价假设成交、系统性高估可实现性。两道惩罚（可叠加，实验 1 默认全开）：(a) low 档 `slippage=3%`；(b) low 档 `reject_prob=0.3`、mid 档 `0.1`，固定 `seed=42` 保证可复现，报告中注明拒单率假设与实际拒单计数（从 vectorbt 订单日志 `OrderStatusInfo` 统计）。

### 5.3 成交量缺失的降级判定

无成交量数据时，用挂单侧代理：日均 `sell_count` ≥ 50 且 `bid` 非空率 ≥ 80% 视为可交易（参考 Tracker 准入过滤 `quick_price<10 且 sell_num<50` 剔除的经验阈值）。该代理偏松（挂单≠成交），报告中必须标注"流动性判定基于挂单代理，结论偏乐观"。

### 5.4 零收益日与 Sharpe NaN 应对

饰品价格长期不变 → 收益序列大量 0 → rolling std=0 → Sharpe/Sortino 出现 NaN/inf。规则：

1. `report()` 对每个指标检测 NaN/inf，输出原因码（`ZERO_VARIANCE` / `INSUFFICIENT_TRADES` / `NO_DATA`），**禁止静默显示 NaN**；
2. 核心指标并列展示 **Sharpe / Sortino / Calmar** 三项，Sharpe 失效时以 Sortino（下行波动）与 Calmar（收益/最大回撤）为主评估；
3. 辅助披露 `nonzero_return_ratio`（非零收益日占比）与 `flat_price_ratio`（价格不变日占比），低于 0.2 时在报告标注"该标的价格发现极不活跃，风险指标可信度低"；
4. 实验级通过标准只看组合层面（cash_sharing 合并净值），不以单饰品 Sharpe 为准。

---

## 6. 回测实验矩阵

| 实验 | 目的（对应假设） | 输入 | 参数网格 / 设计 | 通过标准 |
|---|---|---|---|---|
| E1 信号规则基准验证 | H4 | 全库过滤后 universe（非自选池）；默认策略参数；全可用历史区间；BUFF 平台费率；惩罚全开 | 单跑。对照组：① CSQAQ 指数买入持有 ② universe 等权买入持有（`from_holding` + 等权合成） ③ `from_random_signals` 随机策略蒙特卡洛 100 次（seed 固定序列） | 费后总收益 > 基准①②；Sharpe > 基准；总收益 > 随机分布 95 分位；最大回撤 < 30% |
| E2 阈值参数扫描 | H5 | 同 E1 universe 与区间 | `buy_threshold ∈ {60,65,70,75,80}` × `sell_threshold ∈ {35,40,45,50,55}` 共 25 组（围绕默认 75/45）；vectorbt 广播一次跑完，输出 Sharpe/费后收益热图 | 默认点邻域 ±5 内 Sharpe 衰减 < 50%（"高原"非"尖峰"）；最优区与默认参数方向一致 |
| E3 权重扰动稳健性 | H6 | 同 E1 | MarketScore 六维权重与 ItemScore 八维权重：每个权重独立 ±20%（其余等比归一）+ 全权重拉丁超立方抽样 N=50，共约 64 组 | 费后年化收益与 Sharpe 的 CV < 0.5；BUY 信号翻转率 < 30%；无一组出现回撤 > 1.5× 基准组 |
| E4 walk-forward 滚动验证 | H7 | 同 E1 universe（每窗口起点重建 point-in-time universe） | `RollingSplitter`：样本内 540 天 / 样本外 90 天 / 步进 90 天（`window_len=540, set_lens=(90,)`，初始设计，随数据积累调整）；每窗口样本内跑 E2 粗网格（3×3）选最优参数，样本外仅用该参数重跑 | ≥3 个窗口才可下结论；样本外 Sharpe 中位数 > 0；样本外/内 Sharpe 衰减比 > 0.5；>60% 窗口费后跑赢基准 |

编排约定：E2/E3 利用 vectorbt "列=参数组合"广播一次模拟完成；E4 逐窗口串行。所有实验产物（参数、universe_snapshot_id、指标、热图）落库并与 `strategy_version` 绑定。数据 < 2 年时 E4 不可执行，降级为前后半段切分的敏感性检查并在报告注明。

---

## 7. 统计输出规格

### 7.1 指标清单（report() 必出）

总收益率、年化收益率、最大回撤、最长回撤期、Sharpe、Sortino、Calmar、胜率、盈亏比（profit_factor）、期望值（expectancy）、交易次数、年化换手率、费用合计、滑点成本估算、**费后净收益**、基准收益、超额收益、随机基准分位（E1）、`nonzero_return_ratio`、拒单/顺延计数。

换手率（vectorbt 无内置，适配层自算）：`turnover_annual = (买入总额 + 卖出总额) / 2 / 平均组合净值 / 年数`。滑点成本估算：`Σ |exec_price − 执行 bar 估值 close| × size`（按订单记录重放）。

### 7.2 落库字段映射

**backtest_result**（主指示 §6.6 扩展版）：

| 字段 | 来源 |
|---|---|
| run_id / strategy_name / strategy_version / params_json | 适配层 |
| universe_snapshot_id / data_version（数据快照哈希+区间）/ engine_version（vectorbt 版本）/ code_version（git hash） | 适配层 |
| start_date / end_date / init_cash / platform / cost_model_json | 适配层 |
| total_return | `pf.total_return()` |
| annualized_return | `pf.annualized_return()` |
| max_drawdown / max_drawdown_duration | `pf.max_drawdown()` / `pf.drawdowns.max_duration()` |
| sharpe / sortino / calmar | `pf.sharpe_ratio()` / `pf.sortino_ratio()` / `pf.calmar_ratio()`（NaN 时存 NULL + `metric_flags` 记原因码） |
| win_rate / profit_factor / expectancy | `pf.trades.win_rate()` / `.profit_factor()` / `.expectancy()` |
| trade_count | `pf.trades.count()` |
| turnover | §7.1 自算 |
| fee_cost | `pf.orders.fees.sum()` |
| slippage_cost | §7.1 自算 |
| net_return_after_cost | 费后净收益（= total_return 已含费，另存费前对照 `gross_return`：同信号 `fees=0, slippage=0` 复跑） |
| benchmark_name / benchmark_return / excess_return | CSQAQ 指数 `from_holding` |
| random_baseline_pct | E1 随机基准分位，其他实验 NULL |
| created_at | 适配层 |

**backtest_trade**（来源 `pf.trades.records_readable`）：trade_id、run_id、item_uuid、market_hash_name、entry_time、entry_price、exit_time、exit_price、size、gross_pnl（`pnl`）、fee_paid、slippage_cost_est、net_pnl、return_pct（`returns`）、holding_days、exit_reason（signal / illiquid_timeout / stale / end_of_data）、entry_signal_id（关联 `signal` 表，保证每笔回测交易可追溯到当时的数据与模型版本）。

---

## 8. 未来数据泄漏检查清单（代码评审 checklist，逐条核对）

1. ☐ 因子计算仅使用 t 时刻及之前数据；所有归一化/z-score/分位数用**滚动窗口**，禁止全周期统计量；
2. ☐ 信号已 `fshift(1)`：vectorbt 信号默认当根 close 成交，未错位即未来函数；
3. ☐ 执行价取执行 bar（t+1）的 ask/bid，而非信号 bar（t）；
4. ☐ universe 在每个验证窗口起点确定（point-in-time），未使用期末幸存者名单；
5. ☐ 流动性分档只用过去 20 日均量，不用全期均量；
6. ☐ E4 参数选择只用样本内数据；样本外不重选参数；
7. ☐ 缺失值只允许 ffill（估值层），禁止 bfill / 插值（含未来信息）；
8. ☐ STALE / 质量标记基于"当时可得"信息判定，不用事后数据回填修正；
9. ☐ 基准指数序列不做事后平滑/修正；CSQAQ 指数是否回溯修正成分「待验证」，报告注明；
10. ☐ 随机基准 seed 固定且与策略参数无关；蒙特卡洛结果不参与调参；
11. ☐ 回测区间、min_volume、阈值网格在跑数前登记（防"试出来的参数"）；
12. ☐ 报告披露全部拒单/顺延/NaN 指标计数，禁止只展示成交后的漂亮曲线。

---

## 9. 回测报告结构（markdown 模板，`report()` 自动生成）

```markdown
# 回测报告 {run_id}
## 0. 元信息
策略名/版本、参数 JSON、数据区间、universe 规模与 snapshot_id、数据版本、
引擎版本、代码版本、运行时间、降级标注（无盘口代理/无量源/挂单代理流动性）
## 1. 结论摘要（≤10 行：是否通过、核心数字、最大风险）
## 2. 权益曲线（策略 vs CSQAQ 指数 vs 等权基准，费后）
## 3. 回撤曲线（策略 vs 基准）
## 4. 月度收益表/热力图
## 5. 核心指标表（§7.1 全量；NaN 指标附原因码）
## 6. 交易明细（前 50 笔 + 汇总：exit_reason 分布、分平台盈亏、
   分流动性档盈亏、拒单/顺延计数）
## 7. 成本拆解（费用 vs 滑点 vs 费前对照曲线）
## 8. 稳健性附件（E2 热图 / E3 扰动分布 / E4 窗口明细，按实验附）
## 9. 参数与数据版本（可复现的全部指纹）
## 10. 结论与局限（对照 §10 已知局限逐条声明本次受影响程度）
```

---

## 10. 已知局限

1. **历史数据积累初期回测不可信**：自建 `quote_ticks` 从部署日起积累，早期回测区间短、无盘口历史，只能出"研究用"结论；报告必须标注数据起点与置信等级。
2. **CSQAQ 企业档未获得时成交量缺失**：免费 K 线与 SteamDT K 线均无量。替代方案：CSQAQ 单件详情 `turnover_number`（仅 Steam 平台口径）+ 图表接口日成交量 + §5.3 挂单代理。影响：流动性分档不准、稀疏成交识别变弱、E1 结论系统性偏乐观，报告强制声明。
3. **执行价为代理模型**：无 bid/ask 历史时段按 §4.4 降级，费后收益存在高估。
4. **日级频率**：无法建模盘中波动与挂单排队；Steam 7 天交易冷却期未显式建模（对低频策略影响小，列入后续工作）。
5. **个体差异未建模**：Float、Pattern、贴纸溢价对高价品影响大，回测按 market_hash_name 均价处理。
6. **基准可比性**：CSQAQ 指数成分与修正规则不完全公开「待验证」。
7. **合规**：CSQAQ 数据严禁商用，回测产物仅限内部投研。

---

## 附录 A：假设集中登记表

| 编号 | 假设 | 取值 | 状态 |
|---|---|---|---|
| A-1 | Steam 费率 ≈15%（5%+10%，取整规则）+ `calculate_after_fee` 精算 | 见 §4.1 | 高置信 |
| A-2 | BUFF 卖出交易费 | 2.5% | **待验证** |
| A-3 | BUFF 提现费 | 1% | **待验证** |
| A-4 | 悠悠交易费 | 2.0% 占位（敏感性 0-3%） | **待验证** |
| A-5 | 分档滑点 high/mid/low | 0.5% / 1% / 3% | 初始假设，待校准 |
| A-6 | 流动性分档阈值 | ≥20 / 5-20 / <5 件/日 | 初始假设，待校准 |
| A-7 | min_volume 过滤阈值 | 2 件/日 | 初始假设，待校准 |
| A-8 | 半价差代理 high/mid/low | 1% / 2.5% / 5% | 初始假设，待校准 |
| A-9 | reject_prob mid/low | 0.1 / 0.3 | 初始假设，待校准 |
| A-10 | 年化系数 / 无风险利率 | 365 自然日 / 0 | 约定 |
| A-11 | 通过标准阈值（回撤 30%、IC 0.03、衰减比 0.5 等） | 见 §1/§6 | 初始标准，可修订 |
| A-12 | CSQAQ 指数成分不回溯修正 | — | 待验证 |
