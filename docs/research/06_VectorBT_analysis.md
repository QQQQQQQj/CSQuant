# vectorbt 深度分析

> 分析对象：`CS Coding/vectorbt-master/vectorbt-master/`
> 分析目的：评估向量化回测框架 vectorbt 在 CSQuant 回测模块中的复用价值与集成方式
> 分析方式：精读 README、pyproject.toml、LICENSE、`vectorbt/portfolio/base.py`（5774 行）核心方法签名与文档字符串、`portfolio/nb.py` 订单执行内核、`portfolio/enums.py`、`signals/`、`indicators/`、`records/`、`generic/splitters.py`、examples/WalkForwardOptimization.ipynb、benchmarks/BENCHMARKS.md

---

## 1. 项目定位

vectorbt 是 Oleg Polakow（polakowo）开发的**向量化回测与研究框架**，是商业产品 VectorBT PRO 的开源社区版（README.md 第 55 行明确声明）。

核心理念（README.md 第 47-49 行）："Thinks in matrices, backtests at scale"——不是逐 bar 循环单个策略，而是把成千上万种参数配置打包进 NumPy 数组，用 Numba/Rust 加速热路径一次性跑完，把数小时的网格搜索压缩到秒级。

功能全貌（README.md Features 节）：
- 基于 pandas/NumPy/Numba 的快速向量化回测，可选 Rust 引擎
- pandas 原生 API（自定义 accessors）+ 灵活广播（多资产、大规模参数扫描）
- 指标生态：内置 8 个基础指标 + TA-Lib / pandas-ta / ta 集成
- 组合回测：trades、drawdowns、绩效分析（含 QuantStats 集成）
- 信号工具：生成、排序、映射、分布分析
- 内置数据接入（Yahoo、Binance、CCXT、Alpaca、合成数据）
- 稳健性测试：walk-forward 优化、ML 标签生成
- Plotly 交互可视化、Jupyter widgets、定时更新与 Telegram 通知

对 CSQuant 的意义：它是一个**成熟的回测引擎 + 参数扫描基础设施**，恰好覆盖 CS 饰品量化中"多饰品 × 多参数 × 费用敏感"的批量回测需求。

## 2. 技术栈与版本

| 项目 | 值 | 来源 |
|---|---|---|
| 版本号 | **1.1.0** | `vectorbt/_version.py` |
| 版权年份 | 2017-2026 | `_version.py` 头部注释 |
| 许可证 | **Apache 2.0 with Commons Clause**（fair-code） | `LICENSE.md` 第 1-13 行 |
| Python | >=3.11, <3.15 | `pyproject.toml` |
| 核心依赖 | numpy>=2.4.6、pandas>=3.0.3,<4.0、scipy、matplotlib、plotly>=4.12、ipywidgets、numba>=0.66、dill、tqdm、dateparser、imageio、scikit-learn、schedule、requests、pytz | `pyproject.toml` dependencies |
| 可选依赖 | `vectorbt[rust]`=vectorbt-rust 1.1.0；`vectorbt[full]`=TA-Lib、yfinance、python-binance、ccxt、alpaca-py、ray、ta、pandas-ta-classic、python-telegram-bot、quantstats | `pyproject.toml` optional-dependencies |

**重要判断：这不是旧版 0.2x 经典 vectorbt，而是 2025-2026 年重写的新一代 1.x 版本**（带 Rust 引擎调度层 `vectorbt/_engine.py`、dispatch 分发机制），处于**活跃维护**状态（README 引用 2026 年数据、CI badge、依赖全部锁定到很新的版本）。

许可证关键点（LICENSE.md 第 5-7 行）：个人与组织可**免费使用**，但不得"销售价值主要来源于本软件的产品或服务"（Commons Clause）。内部量化研究、自建交易系统**不受限**；若 CSQuant 未来做成对外收费 SaaS 且核心价值来自 vectorbt 回测，则需联系作者授权。

## 3. 目录结构（重点子模块）

```
vectorbt/
├── __init__.py            # 顶层导出
├── _version.py            # 1.1.0
├── _engine.py             # Numba/Rust 双引擎调度（RustConversion、RustSupport、is_rust_available）
├── _settings.py           # 全局默认配置（settings.portfolio 等）
├── _typing.py             # 类型别名
├── base/                  # 广播、reshape、索引等基础设施
├── data/                  # 数据接入：base.py(Data 基类+from_data)、custom.py(SyntheticData/GBMData/YFData/BinanceData/CCXTData/AlpacaData)、updater.py
├── generic/               # 通用时序分析：splitters.py(RollingSplitter/ExpandingSplitter walk-forward)、drawdowns.py(Drawdowns)、nb.py(fshift/bshift/pct_change 等 Numba 内核)、dispatch.py
├── indicators/            # basic.py(MA/MSTD/BBANDS/RSI/STOCH/MACD/ATR/OBV)、factory.py(IndicatorFactory，155KB)、dispatch.py
├── labels/                # ML 标签生成
├── messaging/             # Telegram 通知
├── portfolio/             # ★核心：base.py(Portfolio)、nb.py(283KB 模拟内核)、enums.py(Order/SizeType/Direction...)、orders.py、trades.py、logs.py、dispatch.py
├── records/               # 记录体系：base.py(Records)、mapped_array.py、col_mapper.py
├── returns/               # 收益率分析（ReturnsAccessor，Sharpe/Sortino 等）
├── signals/               # accessors.py(SignalsAccessor)、generators.py(RAND/RANDX/RPROB/STX/OHLCSTX...)、factory.py(SignalFactory)、nb.py
├── utils/                 # array_/checks/datetime_/params/config 等 21 个工具模块
├── ohlcv_accessors.py / px_accessors.py / root_accessors.py  # pandas 扩展访问器
└── templates/             # 绘图模板
```

examples/ 全部 11 个文件：BitcoinDMAC.ipynb（24MB，双均线+参数热图）、MACDVolume.ipynb、PairsTrading.ipynb（配对交易）、PortfolioOptimization.ipynb、PortingBTStrategy.ipynb（迁移 backtrader 策略）、StopSignals.ipynb（24MB，止损止盈）、TelegramSignals.ipynb、TradingSessions.ipynb、**WalkForwardOptimization.ipynb（85KB，walk-forward 完整流程，已精读）**、dmac_heatmap.gif、requirements-backtrader.txt。

## 4. 核心模块

### 4.1 `portfolio/base.py` — Portfolio 类（5774 行）

回测的中枢。类方法三种模拟模式 + 两种快捷方式：

| 方法 | 模式 | 特点 |
|---|---|---|
| `from_orders` | 订单流 | 最直接、最快；size/price/fees 各自成数组，广播后逐元素生成订单 |
| `from_signals` | 信号 | 在 from_orders 之上加信号抽象：持仓中忽略重复入场、内置止损止盈 |
| `from_order_func` | 回调 | 事件驱动，Numba 回调逐 bar 执行自定义逻辑，可访问运行时状态（现金/持仓/PnL） |
| `from_holding` | 买入持有 | 基准对照 |
| `from_random_signals` | 随机信号 | 蒙特卡洛/基准检验 |

四阶段工作流（base.py 模块 docstring 第 33-62 行）：**Preparation**（参数广播、校验、pandas→NumPy）→ **Simulation**（Numba 逐行逐列遍历，生成/填充/拒绝订单，更新现金与持仓状态）→ **Construction**（由订单记录构建 Portfolio 对象）→ **Analysis**（风险与绩效指标）。

### 4.2 `portfolio/nb.py` — Numba 模拟内核（283KB）

- `order_nb(...)`：构建 Order 元组
- `execute_order_nb` / `buy_nb` / `sell_nb`：成交逻辑，含滑点调价
- `simulate_nb` / `flex_simulate_nb` / `simulate_row_wise_nb` / `flex_simulate_row_wise_nb`：四种模拟器（from_order_func 通过 `flexible`/`row_wise` 参数选择）
- `simulate_from_signal_func_nb`：from_signals 的底层实现
- `replace_inf_price_nb(prev_close, close, order)`：`price=+np.inf` 替换为当前 close，`-np.inf` 替换为 prev_close（nb.py 第 1094-1098 行）

### 4.3 `portfolio/enums.py` — 类型体系

- `Order`（NamedTuple，enums.py 第 1502-1517 行）：`size=inf, price=inf, size_type=Amount, direction=Both, fees=0.0, fixed_fees=0.0, slippage=0.0, min_size=0.0, max_size=inf, size_granularity=nan, reject_prob=0.0, lock_cash=False, allow_partial=True, raise_reject=False, log=False`
- `SizeType`：`Amount / Value / Percent / TargetAmount / TargetValue / TargetPercent`
- `Direction`：`LongOnly / ShortOnly / Both`
- `InitCashMode`：`Auto / AutoAlign`
- `CallSeqType`：`Default / Reversed / Random / Auto`（Auto 按订单价值排序，先卖后买释放资金）
- `OrderStatus`：`Filled / Ignored / Rejected`；`OrderStatusInfo`：含 `PriceNaN、NoCashLong、NoOpenPosition、MaxSizeExceeded、MinSizeNotReached、PartialFill、CantCoverFees` 等 17 种拒绝原因——**对诊断 CS 饰品低流动性下的废单极有价值**

### 4.4 统计输出体系

- `portfolio/trades.py`：`Trades(Ranges)`（第 568 行）+ `EntryTrades`/`ExitTrades`/`Positions`，方法 `win_rate()`（625 行）、`profit_factor()`（635 行）、`expectancy()`（651 行）
- `portfolio/orders.py`：`Orders(Records)`（164 行）
- `generic/drawdowns.py`：`Drawdowns(Ranges)`（236 行）
- `returns/`：`ReturnsAccessor`，提供 sharpe_ratio、sortino_ratio、calmar_ratio、max_drawdown、annualized_return 等 23 个指标，通过 `@attach_returns_acc_methods(returns_acc_config)` 装饰器挂到 Portfolio 上（base.py 第 1451-1497 行）

### 4.5 signals 与 indicators 的组织

- **indicators**：`IndicatorFactory` 以声明式配置生成指标类，如 `MA = IndicatorFactory(class_name="MA", input_names=["close"], param_names=["window", "ewm"], output_names=["ma"], ...)`（indicators/basic.py 第 76-82 行）。指标对象有 `run()` / `run_combs()`（参数组合笛卡尔积）方法，输出对象支持信号生成方法如 `ma_crossed_above()` / `ma_crossed_below()`
- **signals**：`SignalFactory` + 生成器（RAND/RANDX/RANDNX 随机信号、RPROB* 概率信号、STX/OHLCSTX 止损信号），`SignalsAccessor`（signals/accessors.py 第 219 行）挂在 pandas Series/DataFrame 上（`vbt` 命名空间），提供 `fshift` 等防未来函数工具

### 4.6 walk-forward 支持

`generic/splitters.py`：`RollingSplitter`（第 185 行，滚动窗口）与 `ExpandingSplitter`（第 234 行，扩张窗口），pandas 层 API 为 `price.vbt.rolling_split(...)`。

## 5. 主数据流（数据→指标→信号→组合→统计）

以 WalkForwardOptimization.ipynb 与 README 示例为蓝本的真实流水线：

```
原始行情 (YFData/CCXT/CSV → pandas Series/DataFrame, 行=时间, 列=资产)
   │
   ├─[walk-forward]── price.vbt.rolling_split(n=30, window_len=730, set_lens=(180,))
   │                     → (in_price, in_indexes), (out_price, out_indexes)
   ▼
指标计算:  vbt.MA.run(price, 10)  或参数扫描 vbt.MA.run_combs(price, window=np.arange(2,101), r=2, short_names=["fast","slow"])
   ▼
信号生成:  entries = fast_ma.ma_crossed_above(slow_ma); exits = fast_ma.ma_crossed_below(slow_ma)
   │  （防泄漏：signals.vbt.fshift(1)）
   ▼
组合模拟:  pf = vbt.Portfolio.from_signals(close, entries, exits, size=..., price=...,
              fees=..., fixed_fees=..., slippage=..., init_cash=..., freq="1D")
   │        ──→ Preparation(广播/校验) → Simulation(Numba 逐bar) → Construction(订单记录)
   ▼
统计提取:  pf.total_return() / pf.sharpe_ratio() / pf.max_drawdown()
           pf.trades.win_rate() / pf.trades.expectancy() / pf.stats()
           参数扫描结果 → pf.total_return().vbt.heatmap(x_level="fast_window", y_level="slow_window")
   ▼
样本外验证: 同一套参数在 out_price 上重跑，对比 in/out sharpe
```

## 6. 数据模型（输入输出格式）

### 6.1 输入

**价格**：`close` 是必填的 array_like——pandas Series（单资产）或 DataFrame（行=时间索引，列=资产）。**不强制 OHLC**；只有用止损止盈（sl_stop/tp_stop）时才建议传 `open/high/low`（from_signals 参数，缺省时 high 自动取 max(open,close)、low 取 min(open,close)）。

- CS 饰品日线 → 直接构造 `DataFrame(index=交易日, columns=饰品market_hash_name, values=收盘价)` 即可
- `close` 用于计算浮动盈亏与组合估值；真实成交价通过 `price` 参数单独指定（可以是完全不同的数组，例如下一根 K 线开盘价、或卖一/买一价）
- NaN 价格：会产生 `PriceNaN` 状态的订单（被忽略/拒绝）；估值侧有 `ffill_val_price=True`（默认）前向填充旧价，`fillna_close` 属性控制 total_profit 计算时的填充

**信号**：`entries`/`exits`（/`short_entries`/`short_exits`）为 bool 型 array_like，与 close 同形（或可广播）。默认语义：`direction='longonly'` 下 entries=开多、exits=平多。

**尺寸**：`size` + `size_type`（Amount=件数 / Value=金额 / Percent=可用资金比例 / TargetXxx=目标调仓）。`size=np.inf` = 全仓买入，`-np.inf` = 全平。

**广播规则**（base.py Broadcasting 节）：任意参数可传标量（全帧）、1-D（每列或每行）、2-D（每元素），内部用 flexible indexing（`flex_select_auto_nb`）按需取值，**不实际展开大数组**，几乎零内存开销。这是"一列=一个参数组合"的大规模扫描得以实现的根基。

### 6.2 输出

`Portfolio` 对象，核心输出物：

| 输出 | 类型 | 说明 |
|---|---|---|
| `pf.orders` | Orders(Records) | 订单记录，`records_readable` 转可读 DataFrame（含 Size/Price/Fees/Side） |
| `pf.trades` | Trades | 交易（开平仓对），`.closed`/`.open`、`.pnl`、`.returns`、`.win_rate()`、`.expectancy()`、`.profit_factor()` |
| `pf.positions` | Positions | 持仓记录 |
| `pf.drawdowns` | Drawdowns | 回撤区间记录 |
| `pf.value()` | Series/DataFrame | 组合净值曲线 |
| `pf.returns()` | Series/DataFrame | 收益率序列 |
| `pf.total_profit()` / `pf.total_return()` / `pf.final_value()` | 标量或Series | 按列/组聚合 |
| `pf.sharpe_ratio()` / `pf.max_drawdown()` / `pf.annualized_return()` 等 | 标量或Series | 经 returns accessor 挂接的 23 个指标 |
| `pf.stats()` | Series | 汇总报告（默认 25 项指标，见下） |
| `pf.returns_stats()` | Series | 纯收益率侧统计 |

`pf.stats()` 默认指标（base.py 第 4846-4995 行 `_metrics` 定义）：Start、End、Period、Start Value、End Value、Total Return [%]、Benchmark Return [%]、Max Gross Exposure [%]、Total Fees Paid、Max Drawdown [%]、Max Drawdown Duration、Total Trades、Total Closed Trades、Total Open Trades、Open Trade PnL、Win Rate [%]、Best/Worst Trade [%]、Avg Winning/Losing Trade [%] 及 Duration、Profit Factor、Expectancy、Sharpe Ratio、Calmar Ratio、Omega Ratio、Sortino Ratio。指标可自定义扩展（`pf.stats(metrics=my_metrics)`）。

## 7. API/外部依赖

- **必需**：numpy>=2.4.6、pandas>=3.0.3（注意：pandas 3.x 大版本，与存量 pandas 2.x 代码有兼容风险）、numba>=0.66（JIT 核心）、plotly、ipywidgets、scikit-learn、scipy 等
- **可选 Rust 引擎**：`vectorbt-rust==1.1.0`（PyPI 预编译 wheel），由 `vectorbt/_engine.py` 统一调度——`is_rust_available()` 检查版本兼容后，各计算函数通过 `engine=` 参数或 dispatch 层在 Numba/Rust 间切换。benchmarks/BENCHMARKS.md 显示 Rust 对 generic 内核（fillna、pct_change、rolling/expanding、find_ranges 等）有 1.5-10x 加速，大数据下优势扩大（如 bshift 在 10Kx100 矩阵上 10.15x）
- **数据源集成**（`vectorbt[full]`）：yfinance、python-binance、ccxt、alpaca-py；`Data.from_data(...)`（data/base.py 第 472 行）可把任意 DataFrame 包装成 Data 对象
- **QuantStats**：`pf.qs_sharpe...` 类指标通过 QSAdapter 桥接（base.py 4725-4732 行）
- **TA-Lib / pandas-ta / ta**：`vbt.talib(func_name)`、`vbt.pandas_ta(...)`、`vbt.ta(...)` 工厂快捷方式（indicators/__init__.py）

## 8. 核心算法（向量化回测原理简述）

vectorbt 的"向量化"不是简单的 pandas 向量化运算，而是**"广播 + Numba 逐元素模拟"的双层架构**：

1. **广播层（Preparation）**：所有输入（价格、信号、size、fees、滑点……）广播到统一的 `(n_timesteps, n_columns)` 形状。"列"是虚拟实验单元——可以是不同资产、不同参数组合、不同时间窗口的任意笛卡尔积。用 flexible indexing 避免物理展开。
2. **模拟层（Simulation）**：Numba JIT 编译的循环按行主序遍历每个元素。对每列维护状态机（现金、持仓、债务、可用现金、最近估值价、组合价值），信号/订单进入后走 `execute_order_nb`：滑点调价 → 计算可成交 size → 检查现金/持仓约束 → 生成订单记录或拒绝原因 → 更新状态。这与事件驱动回测的成交逻辑完全同构，只是成千上万条"事件流"被压进一次编译好的 tight loop。
3. **记录层（Construction）**：只输出稀疏的订单记录数组（record array），不存全量状态轨迹——内存友好。
4. **分析层（Analysis）**：所有指标惰性计算（`@cached_method`），从订单记录重放出现金流、净值、收益、交易对、回撤区间。

**关键推论**：模拟层逐列独立（除非开启 cash_sharing），因此"1000 个参数组合 × 30 个饰品"与"1 个组合"的模拟成本几乎相同——这就是 README 所谓 "backtests at scale"。

## 9. 关键API用法（真实签名）

以下签名均逐字摘自 `vectorbt/portfolio/base.py` 与 `vectorbt/portfolio/enums.py`，未做臆测。

### 9.1 from_signals（base.py 第 2048-2111 行）

```python
def from_signals(
    cls,
    close,                                   # array_like, 必填，估值价
    entries=None, exits=None,                # bool array_like，多/空信号（方向由 direction 决定）
    short_entries=None, short_exits=None,    # bool array_like，显式做空信号
    signal_func_nb=nb.no_signal_func_nb, signal_args=(),   # 自定义 Numba 信号函数
    size=None, size_type=None,               # 支持 Amount/Value/Percent（TargetXxx 不兼容信号模式）
    price=None,                              # array_like，实际成交价；默认 np.inf→当前 close
    fees=None, fixed_fees=None, slippage=None,
    min_size=None, max_size=None, size_granularity=None,
    reject_prob=None, lock_cash=None, allow_partial=None, raise_reject=None, log=None,
    accumulate=None, upon_long_conflict=None, upon_short_conflict=None,
    upon_dir_conflict=None, upon_opposite_entry=None, direction=None,
    val_price=None,                          # 估值价；-np.inf→上一根 close，np.inf→当前订单价
    open=None, high=None, low=None,          # 仅供止损止盈使用
    sl_stop=None, sl_trail=None, tp_stop=None,               # 止损/移动止损/止盈（0.01=1%）
    stop_entry_price=None, stop_exit_price=None,
    upon_stop_exit=None, upon_stop_update=None,
    adjust_sl_func_nb=nb.no_adjust_sl_func_nb, adjust_sl_args=(),
    adjust_tp_func_nb=nb.no_adjust_tp_func_nb, adjust_tp_args=(),
    use_stops=None,
    init_cash=None, cash_sharing=None, call_seq=None,
    ffill_val_price=None, update_value=None,
    max_orders=None, max_logs=None, init_temp_records=None, seed=None,
    group_by=None, broadcast_named_args=None, broadcast_kwargs=None,
    template_mapping=None, wrapper_kwargs=None, freq=None,
    attach_call_seq=None, engine=None, **kwargs,
) -> PortfolioT
```

最小用法（README 第 122 行）：
```python
pf = vbt.Portfolio.from_signals(price, entries, exits, init_cash=100)
```

### 9.2 from_orders（base.py 第 1617-1652 行）

```python
def from_orders(
    cls,
    close, size=None, size_type=None, direction=None, price=None,
    fees=None, fixed_fees=None, slippage=None,
    min_size=None, max_size=None, size_granularity=None,
    reject_prob=None, lock_cash=None, allow_partial=None, raise_reject=None, log=None,
    val_price=None, init_cash=None, cash_sharing=None, call_seq=None,
    ffill_val_price=None, update_value=None,
    max_orders=None, max_logs=None, init_temp_records=None, seed=None,
    group_by=None, broadcast_kwargs=None, wrapper_kwargs=None,
    freq=None, attach_call_seq=None, engine=None, **kwargs,
) -> PortfolioT
```

订单语义（base.py 示例）：`size>0` 买、`size<0` 卖、`size=np.inf` 全仓买、`-np.inf` 全平、`size=0/NaN` 跳过。

### 9.3 from_signals vs from_orders：CS 饰品场景选型

| 维度 | from_signals | from_orders |
|---|---|---|
| 语义 | 信号→状态机自动开平仓（默认持仓中忽略重复 entry） | 每个元素就是一个订单指令，原样执行 |
| 重复信号 | 自动过滤（除非 `accumulate=True`） | 全部照单执行（`accumulate=True` 时 from_signals 行为与之等价，base.py 第 115 行） |
| 止损止盈 | 内置 sl_stop/tp_stop/sl_trail | 不支持 |
| 成交价 | 单一 `price` 数组（买卖共用） | 单一 `price` 数组，但订单本身已含方向，可事先把"买单用卖一价、卖单用买一价"烘进 price 数组 |
| 稀疏成交 | 持仓期无信号=继续持有，天然适配 | 无订单元素=无操作，天然适配 |

**结论：CS 饰品低流动性、长持仓期场景首选 `from_signals`**（信号稀疏、持仓期间无需逐日下单，止损止盈可直接表达）。当需要"每笔成交精确控制买一/卖一价"或表达复杂挂单逻辑时，用 `from_orders`（自行把目标成交价写入 price 数组）或 `from_order_func`。

### 9.4 费用与滑点

三个参数均支持广播（标量/每列/每元素）：

- `fees`：订单价值的**百分比**费率（如 BUFF 卖出 2.5% → `fees=0.025`；注意 vectorbt 对买卖双向都收，若只需单边收，可把 fees 做成 2-D 数组、买单位置填 0）
- `fixed_fees`：**每单固定金额**（如 Steam 市场最低 0.01 美元费用、或按笔提现费）
- `slippage`：价格百分比滑点。成交逻辑（nb.py 第 90 行 / 第 233 行）：买单 `adj_price = price * (1 + slippage)`，卖单 `adj_price = price * (1 - slippage)`——**天然朝不利方向调价**
- 校验（nb.py 第 408-409 行）：`slippage` 必须有限且 >=0

官方示例（base.py 第 247-249 行）：
```python
pf = vbt.Portfolio.from_orders(
    ohlcv['Close'], size, price=ohlcv['Open'],
    init_cash='autoalign', fees=0.001, slippage=0.001)
```

### 9.5 bid-ask 执行（"买按卖一价、卖按买一价"）

vectorbt 没有专门的 ask/bid 参数，但 `price` 是完整 array_like，**支持每元素独立定价**，三种实现路径：

1. **近似法（推荐起步）**：`price=中间价（或收盘价）`，`slippage=半价差`。买价×(1+s)、卖价×(1-s)，正好等效于以卖一/买一成交。CS 饰品买卖价差通常 1-5%，可设 `slippage=0.01~0.025`。
2. **精确法**：构造两张价格矩阵 `ask_price`、`bid_price`，买单用 ask、卖单用 bid。`from_signals` 只有一个 price 入口，需要把 entries 日 price 设为 ask、exits 日 price 设为 bid 合并成单张矩阵后传入（信号日与非信号日的 price 无关紧要）；或直接用 `from_orders`（每笔订单价格已确定）。
3. **事件驱动法**：`from_order_func` 中在 `order_func_nb` 里读当刻盘口（作为 order_args 传入 ask/bid 数组）动态定价，并可按挂单量决定 `max_size`/`allow_partial`。

### 9.6 多资产组合与资金分配

- **列=资产**：close/DataFrame 的每列是一个资产（或一个参数组合）
- **默认独立核算**：每列独立 `init_cash`（如 `init_cash=100` 广播到每列），互不干扰——适合"每个饰品独立回测"
- **共享资金池**：`cash_sharing=True` + `group_by` 把多列编入一组共用一份现金（base.py 第 323-340 行 Grouping 节）。此时 `init_cash` 按组广播；`call_seq` 控制同 bar 多列的下单顺序，`CallSeqType.Auto` 先卖后买释放资金（注意 docstring 警告：Auto 假设同组订单同时成交且价格不随顺序变化，现实中有失真）
- **资金分配比例**：`size_type='percent'`（按当前可用现金比例下单）或 `size_type='targetpercent'`（目标权重调仓，from_orders 专属）
- **特殊初始资金**：`init_cash='auto'`（模拟中假设无限现金，事后取总花费）/ `'autoalign'`（所有列对齐同一初始资金）

### 9.7 统计指标提取

```python
pf.total_return()      # 总收益率（小数）
pf.total_profit()      # 总盈亏（货币）
pf.final_value()       # 期末净值
pf.value()             # 净值曲线 Series/DataFrame
pf.returns()           # 收益率序列
pf.sharpe_ratio()      # 夏普（需 freq；挂自 ReturnsAccessor）
pf.sortino_ratio(); pf.calmar_ratio(); pf.omega_ratio()
pf.max_drawdown(); pf.annualized_return(); pf.annualized_volatility()
pf.alpha(); pf.beta(); pf.information_ratio(); pf.tail_ratio(); pf.value_at_risk()
pf.drawdowns.max_drawdown(); pf.drawdowns.max_duration()
pf.trades.count(); pf.trades.win_rate(); pf.trades.profit_factor(); pf.trades.expectancy()
pf.trades.pnl; pf.trades.returns; pf.trades.records_readable
pf.orders.fees.sum()   # 总费用
pf.stats()             # 25 项汇总（可传 metrics= 自定义）
pf.stats(column=(10, 20, 'ETH-USD'))  # 按列索引取某参数组合
```

多列输出均为带 MultiIndex 的 Series，可直接 `.vbt.heatmap(x_level=..., y_level=...)` 画参数热图（README 第 163-166 行）。

### 9.8 参数扫描与 walk-forward

**参数扫描**（README 第 149-167 行真实代码）：
```python
windows = np.arange(2, 101)
fast_ma, slow_ma = vbt.MA.run_combs(price, window=windows, r=2, short_names=["fast", "slow"])
entries = fast_ma.ma_crossed_above(slow_ma); exits = fast_ma.ma_crossed_below(slow_ma)
pf = vbt.Portfolio.from_signals(price, entries, exits, size=np.inf, fees=0.001, freq="1D")
pf.total_return().vbt.heatmap(x_level="fast_window", y_level="slow_window", slider_level="symbol")
```
`run_combs(..., r=2)` 生成参数窗口的两两组合，组合维度编码进列的 MultiIndex，一次 from_signals 跑完 10000 组。

**walk-forward**（WalkForwardOptimization.ipynb 真实代码）：
```python
split_kwargs = dict(n=30, window_len=365*2, set_lens=(180,), left_to_right=False)
(in_price, in_indexes), (out_price, out_indexes) = price.vbt.rolling_split(**split_kwargs)
# 样本内全参数扫描
in_sharpe = simulate_all_params(in_price, windows, **pf_kwargs)   # from_signals + MA.run_combs
in_best_index = get_best_index(in_sharpe)                          # 每窗口选最优参数
# 样本外仅用最优参数重跑，对比 pf.sharpe_ratio()
```
底层是 `generic/splitters.py` 的 `RollingSplitter` / `ExpandingSplitter`。

### 9.9 防未来数据泄漏

- **vectorbt 层面**：`signals/ndarray.vbt.fshift(1)` 前向移位信号（base.py 第 241 行官方注释 "Don't look into the future"）；`from_order_func` 事件驱动模式"less risk of exposure to the look-ahead bias"（base.py 第 186 行）；`val_price=-np.inf` 强制用上一根 close 估值；`price=次日开盘价` 实现 T+1 成交
- **用户层面责任**：指标计算不能用未来数据（vectorbt 内置滚动指标安全）；信号默认当根 close 成交——必须自行 fshift 或改 price 到下一根；walk-forward 中参数选择只基于 in-sample

## 10. 最值得复用的设计

1. **"列=实验单元"的广播架构**：资产、参数、窗口全部编码为列维度，一次模拟跑完整个实验矩阵——直接解决 CSQuant "数千饰品 × 数百参数"的计算量问题
2. **灵活索引（flexible indexing）**：不物理展开广播数组，模拟内核内按需 `flex_select_auto_nb` 取值，内存开销近零
3. **订单记录稀疏存储 + 惰性指标**：只存成交记录，所有统计按需重放，支持任意后验分析
4. **完整的订单状态机**：17 种 OrderStatusInfo 拒绝原因（PriceNaN/NoCashLong/MinSizeNotReached/PartialFill/CantCoverFees...），低流动性场景的诊断利器
5. **三参数费用模型（fees 百分比 + fixed_fees 固定 + slippage 方向性调价）**：恰好覆盖饰品市场的手续费结构
6. **size_granularity**：数量粒度约束——CS 饰品不可拆分（整数件）的天然表达
7. **min_size / max_size / allow_partial / reject_prob**：最小成交量、最大成交量（流动性上限）、部分成交、随机拒单概率——可粗略模拟挂单不成交风险
8. **walk-forward splitter 内置于 pandas accessor**：`price.vbt.rolling_split()` 一行切分
9. **stats 指标注册表模式**（`_metrics` Config）：新指标 = 一条 dict 注册，CSQuant 可照抄此模式做自定义指标
10. **InitCashMode.Auto/AutoAlign**：免调参的资金初始化

## 11. 可直接复用的部分

- **整个回测引擎**：`Portfolio.from_signals / from_orders / from_holding` 直接喂 CS 饰品日线 DataFrame 即可工作，无需修改
- **统计体系**：25 项 stats + trades/win_rate/expectancy/profit_factor + drawdowns，完全适用于饰品策略评估
- **费用模型**：`fees`（BUFF 2.5%、Steam 15%）、`fixed_fees`、`slippage`（价差）开箱即用
- **参数扫描**：`IndicatorFactory.run_combs` + 广播 + `heatmap` 可视化
- **walk-forward**：`RollingSplitter`/`ExpandingSplitter` + notebook 中的完整范式
- **内置指标**：MA/MSTD/BBANDS/RSI/STOCH/MACD/ATR/OBV + TA-Lib 全量接入（需 `pip install TA-Lib`）
- **防泄漏工具**：`vbt.fshift`、`val_price=-np.inf`、`price=次日价`
- **基准对比**：`from_holding`（买入持有基准）、`from_random_signals`（随机策略基准，检验策略是否真优于掷硬币）
- **`Data.from_data(...)`**：把 CSV/数据库读出的饰品行情包装成统一数据对象（data/base.py 第 472 行）
- **`size_granularity=1`**：整数件约束直接可用

## 12. 只能借鉴的部分

- **事件驱动回调架构（from_order_func 的 pre/post sim/group/row/segment/order 共 10 个钩子）**：若自研简化引擎，可裁剪为 2-3 个钩子（每日开盘前、每日决策、成交后）
- **`_metrics` 注册表 + `StatsBuilderMixin`**：自定义统计指标的声明式注册机制，适合移植到 CSQuant 的报告模块
- **Records/Ranges/MappedArray 抽象**（records/ 包）：把稀疏事件（订单、交易、回撤）统一为 record array + 列映射，是自研引擎管理输出的好范式
- **信号 accessors 设计**（SignalsAccessor 挂 pandas）：`signals.vbt.fshift(1)` 这类链式 API 提升研究体验
- **IndicatorFactory 声明式指标定义**：input/param/output 名字列表生成类，CSQuant 可借此模式管理自定义饰品因子（流动性因子、价差因子、平台价差因子等）
- **引擎调度层 `_engine.py`**：RustConversion/RustSupport 的"能力探测 + 软转换"设计，CSQuant 若引入加速层可参照
- **call_seq / cash_sharing 分组语义**：共享资金池下订单排序问题（先卖后买）的显式建模方式

## 13. 不适用CS饰品场景的部分与应对

| vectorbt 特性 | 不适配原因 | 应对 |
|---|---|---|
| 止损止盈基于 open/high/low | 饰品日线无可靠 OHLC，只有收盘价/均价；`high` 缺省退化为 max(open,close) 无意义 | 用日线收盘构造伪 OHLC，或干脆放弃盘中止损，用 exits 信号表达 |
| `sl_stop`/`tp_stop` 的"触发即成交"假设 | 饰品流动性差，触发价≠可成交价 | 用 `slippage` 加大 + `reject_prob` 惩罚；或自写信号 |
| YFData/BinanceData/CCXTData/AlpacaData | 数据源全是股票/加密，无饰品数据 | 不用；走 `Data.from_data` 或直接构造 DataFrame |
| TA-Lib K 线形态识别（apps/candlestick-patterns） | 无真实 OHLC | 不适用 |
| 做空机制（short_entries、Direction.ShortOnly、lock_cash、debt） | 饰品市场无法做空 | 固定 `direction='longonly'` |
| `SizeType.Percent` 的卖空/反手逻辑 | 同上 | 仅做多 + `upon_opposite_entry` 无需关心 |
| 分钟级/日内 freq 相关指标（年化等） | 饰品日线、成交稀疏，年化口径需按日线 | `freq="1D"`，注意年化系数按 365 自然日 |
| TelegramSignals / TradingSessions | 通知/交易时段，无意义 | 不用 |
| QuantStats  tearsheet | 其假设流动性良好的收益率分布 | 指标可看，图形结论谨慎 |

## 14. 对 CSQuant 回测模块的具体价值

1. **省掉自研引擎的 90% 工作量**：订单状态机、现金/持仓记账、部分成交、费用滑点、统计输出全部现成且经多年社区验证（README: "mature, battle-tested backtesting stack"）
2. **多饰品批量回测天然匹配**：CSQuant 的 item_master 可能涉及数万饰品；vectorbt 一列一饰品，一次模拟全跑完。按 benchmarks 数据，10K 行 × 100 列矩阵的 Numba 模拟在秒级
3. **费用敏感性研究的第一公民支持**：饰品策略的生死在费用（Steam 15% vs BUFF 2.5% vs UUYP）。`fees` 按列广播 = 同策略跨平台费率对比一行代码：`fees=np.array([0.15, 0.025, 0.01])` 配三列
4. **参数稳健性**：`run_combs` + walk-forward 范式直接回应"饰品样本少、易过拟合"的核心风险
5. **随机基准检验**：`from_random_signals` 是饰品策略的照妖镜——饰品普涨行情中，随机入场可能也很赚，必须对照
6. **废单诊断**：OrderStatusInfo 可回答"信号为什么没成交"（没钱/价格NaN/低于min_size），对低流动性数据质量审计极有用

## 15. 推荐集成方式（封装 vectorbt 还是自研简化引擎）

**结论：用 vectorbt，但做薄封装适配层（Adapter），不要自研引擎，也不要让策略代码直接裸调 vectorbt。**

权衡分析：

| 方案 | 优势 | 劣势 |  verdict |
|---|---|---|---|
| 直接裸用 vectorbt | 零开发量 | 学习曲线陡（广播语义、Column-directed 思维）；策略代码与第三方 API 强耦合；pandas 3.x 强制升级 | 研究阶段可用 |
| **薄封装（推荐）** | 自研量小；隔离依赖；CS 语义化 | 需要设计适配层 | ★ |
| 自研简化引擎 | 完全可控、无依赖风险、无 license 顾虑 | 订单状态机/部分成交/统计体系是深坑，工作量以月计；性能远不及 Numba 内核 | 仅当 license 或依赖成为硬约束时 |

CSQuant 适配层设计建议：

```python
# csquant/backtest/engine.py（建议形态）
class CSBacktester:
    def __init__(self, price_df, fee_rate=0.025, spread=0.02, init_cash=10000):
        self.close = price_df            # index=日期, columns=market_hash_name
        self.fees = fee_rate             # 平台费率，可按列传不同平台
        self.slippage = spread / 2       # 半价差近似 bid-ask
        self.init_cash = init_cash

    def run_signals(self, entries, exits, size=1.0, size_type="amount"):
        return vbt.Portfolio.from_signals(
            self.close, entries.vbt.fshift(1), exits.vbt.fshift(1),  # T+1 成交防泄漏
            size=size, size_type=size_type, size_granularity=1,      # 整数件
            direction="longonly",                                    # 饰品无做空
            fees=self.fees, slippage=self.slippage,
            min_size=1, init_cash=self.init_cash, freq="1D",
        )
```

适配层职责：
1. **数据整形**：CSQuant 行情库 → `(date × item)` 收盘矩阵；缺失日前向填充（配合 `ffill_val_price=True`）
2. **语义默认值**：longonly、整数件、T+1 fshift、min_size=1、费率表（BUFF/Steam/UUYP 常量）
3. **指标白名单**：只暴露回测需要的统计（total_return、sharpe、max_dd、win_rate、trades count、expectancy）
4. **批量入口**：`run_param_grid()` 内部走 `run_combs` 广播

## 16. 风险和注意事项

1. **许可证（Commons Clause）**：Apache 2.0 + Commons Clause，非纯 OSI 开源。内部研究/自用交易完全免费合法；若 CSQuant 商业化且"产品价值主要来自 vectorbt 功能"则违规。封装层隔离可降低传染面，但 Commons Clause 按"价值来源"判定而非代码耦合，商业计划需评估。替代兜底：自研引擎只借鉴设计。
2. **依赖激进**：pandas>=3.0.3（3.x 大版本）、numpy>=2.4.6、numba>=0.66、Python>=3.11。与存量 pandas 2.x 生态（如旧 quantstats、部分 TA 库）可能冲突——建议 CSQuant 独立 venv，先跑通 `pip install vectorbt` + 示例再定集成。TA-Lib 在 Windows 上需预编译 wheel，属可选依赖可不装。
3. **Numba JIT 冷启动**：首次调用编译耗时数秒到数十秒；`cache=True` 已内置（nb.py 装饰器），但仍建议 warmup。Rust 引擎（vectorbt-rust）免 JIT，但仅覆盖部分内核（BENCHMARKS.md 矩阵仅为 generic 层），模拟主路径仍以 Numba 为主。
4. **CS 数据特性触发的坑**：
   - **长期零收益率**：价格长期不变 → `returns` 大量 0 → rolling std=0 → Sharpe/Sortino 出现 NaN/inf；rolling 指标（RSI/STOCH）在平台期输出 NaN 或恒定值，信号失效。应对：回测前筛除波动率过低时段，统计时对 NaN 鲁棒处理
   - **成交稀疏**：信号日无真实成交 → 回测按收盘价"假设成交"，高估可实现性。应对：`reject_prob` 惩罚、`slippage` 加大、或用成交量过滤信号日（vectorbt 不建模成交量，需在适配层做）
   - **价格前向填充的估值错觉**：`ffill_val_price=True`（默认）会让无报价期持仓按旧价估值，净值曲线平滑失真——这是合理近似但报告中需注明
   - **close 含 NaN**：该时点订单被 Ignored（PriceNaN），持仓估值 NaN 传播（除非 ffill）——数据入库前必须清洗
   - **Cash sharing 的 CallSeqType.Auto**：官方警告其假设同 bar 订单同时成交，饰品低频下失真更大，建议 Default 顺序或显式 call_seq
5. **内存与规模**：列数 = 资产×参数×窗口，万级饰品 × 千级参数将产生千万列——广播架构下需分批（chunk）运行；`max_orders` 预分配订单数组可防爆内存。
6. **版本时效**：本仓库为 1.1.0 新版（非旧 0.2x），API 与旧教程（网上大量 0.x 资料）**不兼容**（如旧版 `Portfolio.from_signals` 无 engine 参数、指标方法不同）。查资料时以本仓库 docstring 为准。
7. **免责**：README 明确 "educational purposes only"，实盘决策责任自负。

---

> 文档完成。所有 API 签名、行号引用均来自对 `vectorbt-master` 仓库源码的直接阅读。
