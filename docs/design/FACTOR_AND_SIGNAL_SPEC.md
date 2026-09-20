# FACTOR_AND_SIGNAL_SPEC — CSQuant 因子与信号规范

> 版本：v0.1（设计稿，未回测）  日期：2026-07-21
> 地位：`quant/factors/` 与 `quant/signals/` 的唯一实现依据；回测与实盘共用同一份因子/信号代码（策略即配置），禁止两套实现。
> 依赖：`docs/research/datasource_web_research_notes.md`（字段边界）、`docs/research/01_CSGOTrading_analysis.md`（可复用指标函数）、`DATABASE_SCHEMA.md`（表结构）。

---

## 0. 全局约定

1. **确定性计算**：所有数值计算由 pandas/numpy 完成；LLM 只做事件理解与信号解释，其输出**永远不作为数值输入**（总指示文档 §9、§16）。
2. **缺失数据不为 0**：任何因子输入缺失时按该因子"数据缺失降级"规则处理（降权 / 置中性 50 分 + quality 降权），并在 `reason_json` 中注明（§16.14）。
3. **数据质量标记**：每个因子输出除 `score` 外同时输出 `quality ∈ [0,1]`，反映输入数据的完整性/新鲜度；最终分数合成时低质量因子权重按比例转移给剩余因子（见 0.5）。
4. **可配置**：所有权重、阈值、窗口参数集中于 `config/factors.yaml`，带 `model_version`；本文中所有具体数字均为**初始值，待回测校准**。
5. **时间纪律**：因子只使用 t 时刻及之前已落库的数据（详见 Part 4.2 防泄漏约束）；统一 UTC 存储；价格带币种（CNY 基准，Steam 美元价按当日汇率折算）。
6. **数据源字段速查**（来自 web 调研笔记，未二次验证处标注「待验证」）：
   - CSQAQ 单件详情（api-187131780）：`buff_sell_price/buff_buy_price/buff_sell_num/buff_buy_num`、`yyyp_*`、`steam_sell_price/steam_sell_num/steam_buy_price/steam_buy_num`、`turnover_number`（Steam 成交量）、`statistic`（存世量）、`sell_price_rate_1/7/30/180`（%）、挂刀转换比例 4 个、租金属性。
   - CSQAQ 首页 `current_data`（api-187131779）：大盘指数、子指数、涨跌分布（2 分钟级）、市场情绪 `greedy`、在线人数、饰品异动。
   - CSQAQ 排行榜（api-187131776）：成交量/存世量/在售/求购排行（10 分钟级）。SteamDT：`price/single|batch` 全平台 `sellPrice/sellCount/biddingPrice/biddingCount/updateTime`；`kline`（**明确无成交量**）；`broad/v1/index` 大盘指数（限频待验证）。更新频率：CSQAQ 全站饰品 5–15 分钟、涨跌分布 2 分钟、排行榜 10 分钟、存世量 25 分钟。

### 0.1 因子统一输出契约

```python
@dataclass
class FactorOutput:
    factor_name: str      # 如 "market.trend"
    score: float          # 0~100，50 为中性
    quality: float        # 0~1，数据质量系数
    raw: dict             # 关键原始值（进 reason_json）
    degraded: bool        # 是否触发降级
    notes: str            # 降级/异常说明（进 reason_json）
```

### 0.2 加权合成与缺失降权规则

```python
def weighted_combine(factors: list[FactorOutput], weights: dict[str, float]) -> tuple[float, dict]:
    """有效权重 w_i' = w_i * quality_i；归一化加权。quality=0 的因子自然退出（降权而非置零）。"""
    eff = {f.factor_name: weights[f.factor_name] * f.quality for f in factors}
    total = sum(eff.values())
    if total < cfg.min_effective_weight:      # 初始 0.35，待回测校准；兜底防"少数因子独裁"
        return 50.0, {"fatal": "effective_weight_below_min", "effective_total": total}
    return clip(sum(f.score * eff[f.factor_name] for f in factors) / total, 0, 100), eff
```

### 0.3 数据质量 quality 的通用构成

```python
quality = completeness * freshness * source_penalty
# completeness: 所需字段实际可用比例（3/4 字段 → 0.75）
# freshness: 快照年龄衰减，<=15min→1.0；15-60min 线性降至 0.6；>60min→0.3；>6h→0
# source_penalty: 备份源/样本估算 → 0.8；直接字段 → 1.0
```

quality 阈值：factor.quality < 0.4 时 `degraded=True` 且 score 钳到中性 50（"宁可中性，不可误导"）。

---

# Part 1 — MarketScore 六因子

```
MarketScore = Σ 因子分 × 有效权重   初始权重（待回测校准）：
大盘趋势 25% | 市场宽度 20% | 成交/流动性 20% | 求购/在售供需 15% | 跨平台一致性 10% | 事件/情绪 10%
五档（阈值可配置，待校准）：0-20 极度恐慌 | 20-40 偏空 | 40-60 震荡 | 60-80 偏多 | 80-100 极度贪婪
```

计算频率：V1 每 30 分钟一次（对齐 CSQAQ 5–15 分钟更新），结果落 `market_index` 表。

## M1. 大盘趋势因子（初始权重 25%）

**定义**：CSQAQ 大盘指数的均线排列、斜率与历史位置的综合趋势度量。

**输入字段**：
| 字段 | 表 | 来源 API |
|---|---|---|
| index_value, ts | `market_index_history` | CSQAQ `current_data?type=kline`（指数 K 线，api-278085071）/ `current_data?type=init` |
| broadMarketIndex, updateTime | 同上（备份源） | SteamDT `GET /open/cs2/broad/v1/index`（含 historyMarketIndexList，限频待验证） |

**计算**：

```python
def market_trend(index_series: pd.Series) -> FactorOutput:
    ema20, ema60 = ema(index_series, 20), ema(index_series, 60)
    # 1) 均线排列: ema20>ema60 且上行 → 多头
    align = 0.5 * sign_score(ema20[-1] - ema60[-1]) + 0.5 * sign_score(slope(ema20, 5))
    # 2) 斜率: ema20 的 10 日回归斜率/指数水平（无量纲化），±0.5%/日 满刻度（待校准）
    slope_s = tanh_scale((linregress_slope(ema20.tail(10)) / index_series[-1]) / 0.005)
    pos = rolling_percentile(index_series, 252)      # 3) 近 252 点位置分位
    raw = 0.4 * align + 0.3 * slope_s + 0.3 * (2 * pos - 1)   # 权重待校准，raw ∈ [-1,1]
    score = 50 + 50 * raw
```

**归一化**：固定阈值 tanh 映射。论证：大盘趋势是慢变量、历史样本短（CSQAQ 自 2021-07 起），分位数法样本早期不稳定；固定阈值（±0.5%/日）语义清晰、跨期可比，回测再扫描。
**数据缺失降级**：CSQAQ 指数缺失 → 切 SteamDT 大盘指数（source_penalty=0.8）；两源均缺 → score=50、quality=0、degraded=True；历史不足 60 点 → 仅用 ema20 斜率+位置，completeness=0.5。**初始权重**：25%（待回测校准）。

## M2. 市场宽度因子（初始权重 20%）

**定义**：全市场上涨/下跌家数力量对比与极端涨跌分布。

**输入字段**：
| 字段 | 表 | 来源 API |
|---|---|---|
| 涨跌分布（涨/平/跌家数、按类型与价格区间分组） | `market_breadth_snapshot` | CSQAQ `current_data?type=init/hours`（涨跌分布，2 分钟级，api-187131779） |
| sell_price_rate_1（单品日涨跌，自算备选） | `item_snapshot` | CSQAQ 单件详情 api-187131780（自选池逐件） |

**计算**：

```python
def market_breadth(breadth_snap: dict) -> FactorOutput:
    up, down = breadth_snap["up"], breadth_snap["down"]
    adr_s = tanh_scale(log((up + 1) / (down + 1)) / log(2.0))   # 涨跌比 2:1 满刻度（待校准）
    ext = breadth_snap["up_gt_5pct_ratio"] - breadth_snap["down_gt_5pct_ratio"]  # 极端家数差
    raw = 0.6 * adr_s + 0.4 * clip(ext / 0.10, -1, 1)           # 10% 差为满刻度（待校准）
    score = 50 + 50 * raw
```

备选自算口径（接口不可用时）：对自选池（≥300 件，覆盖刀/手套/步枪/箱子等）用 `sell_price_rate_1` 自算 up/down 与新高新低；completeness=0.7 并注明"样本估算"。**归一化**：固定阈值（ADR=2 满刻度）+ tanh。论证：涨跌比是比例量，固定锚点（1:1 中性、2:1 强）有直接市场语义，不随 regime 漂移。
**数据缺失降级**：涨跌分布接口缺失 → 自算备选（quality×0.7）；自选池快照不足 50 件 → score=50, quality=0。**初始权重**：20%（待回测校准）。

## M3. 成交/流动性因子（初始权重 20%）

**定义**：全市场成交量水平与趋势、活跃饰品占比。

**输入字段**：
| 字段 | 表 | 来源 API |
|---|---|---|
| turnover_number（Steam 日成交量）、turnover_avg_price | `item_snapshot` | CSQAQ 单件详情 api-187131780 |
| 成交量排行榜（top N 及其成交量） | `ranking_snapshot` | CSQAQ 排行榜 api-187131776（10 分钟级） |
| 全市场成交量/成交额（若接口提供） | `market_index_history` | CSQAQ current_data 首页字段（待验证） |

**计算**（V1 用排行榜样本集 + 自选池聚合估算全市场，标注局限）：

```python
def market_volume(turnover_series: pd.Series, pool_snap: pd.DataFrame) -> FactorOutput:
    # turnover_series: 样本集日成交额时序（自选池 turnover_number×turnover_avg_price 聚合）
    vol_z = zscore(turnover_series, window=90)                 # 相对自身 90 日
    trend = sign_score(linregress_slope(turnover_series.tail(14)))
    active_s = rolling_percentile_score((pool_snap["turnover_number"] > 0).mean(), window=180)
    raw = 0.5 * tanh_scale(vol_z / 2) + 0.3 * trend + 0.2 * (2 * active_s - 1)
    score = 50 + 50 * raw
```

**归一化**：Z-score（90 日窗口）+ tanh。论证：成交量有长期扩容趋势，固定阈值会失效；对自身历史标准化自适应扩容，90 日窗口平衡灵敏与稳健（待校准）。
**数据缺失降级**：`turnover_number` 为 Steam 侧口径，缺失 → 仅用排行榜 top 成交趋势（quality×0.7）；排行榜也缺 → CSQAQ 在线人数+指数成交额代理（quality×0.5，待验证字段）；全缺 → 中性 50。
**局限标注**：V1 为样本集估算（自选池+排行榜），非全市场精确值；CSQAQ 全量接口为企业档，升级后切全量口径并 bump `model_version`。**初始权重**：20%（待回测校准）。

## M4. 求购/在售供需因子（初始权重 15%）

**定义**：全市场买盘力量与卖盘压力的比值。

**输入字段**：
| 字段 | 表 | 来源 API |
|---|---|---|
| buff_sell_num / buff_buy_num / yyyp_sell_num / yyyp_buy_num / steam_sell_num / steam_buy_num | `item_snapshot` | CSQAQ 单件详情 api-187131780（自选池逐件，5–15 分钟级） |
| 在售/求购数量排行榜 | `ranking_snapshot` | CSQAQ 排行榜 api-187131776 |
| 库存监控（大户持有量变动） | `inventory_watch` | CSQAQ 库存监控系列 api-187131809/813（V1 可选增强） |

**计算**：

```python
def market_supply_demand(pool_snap: pd.DataFrame) -> FactorOutput:
    # 样本集聚合（自选池 ~300-500 件 + 排行榜高流动件）
    buy_amt  = (pool_snap["buy_price"]  * pool_snap["buy_count"]).sum()    # 买盘资金
    sell_amt = (pool_snap["sell_price"] * pool_snap["sell_count"]).sum()   # 卖盘挂单金额
    bsr_s = tanh_scale(log((buy_amt + eps) / (sell_amt + eps)) / log(1.5))  # 1.5:1 满刻度（待校准）
    spread = ((pool_snap["sell_price"] - pool_snap["buy_price"]) / pool_snap["sell_price"]).median()
    spread_s = tanh_scale((0.10 - spread) / 0.10)            # 中位价差率 10% 中性锚（待校准）
    raw = 0.5 * bsr_s + 0.3 * spread_s + 0.2 * tanh_scale(zscore(bsr_series, 30))
    score = 50 + 50 * raw
```

**归一化**：固定阈值 log 映射（买卖比 1.5 倍锚点）。论证：供需比经济含义稳定（买>卖即强），固定锚点跨期可比；spread 锚点 10% 取自常见费率+流动性溢价量级，回测校准。
**数据缺失降级**：CSQAQ 全量买卖盘接口为**企业档**——V1 用自选池+排行榜+库存监控样本集估算，`quality×0.75` 并在 reason 注明"样本估算"；样本<100 件 → score=50, quality=0.2；SteamDT `biddingCount/sellCount` 作交叉校验。**初始权重**：15%（待回测校准）。

## M5. 跨平台一致性因子（初始权重 10%）

**定义**：BUFF / 悠悠 / Steam 三平台价差的离散程度；挂刀比例偏离度。价差异常放大通常预示某平台流动性枯竭或恐慌/抢购单边行情。

**输入字段**：
| 字段 | 表 | 来源 API |
|---|---|---|
| buff_sell_price / yyyp_sell_price / steam_sell_price | `item_snapshot` | CSQAQ 单件详情 api-187131780 |
| steam_buff_buy_conversion / steam_buff_sell_conversion / buff_steam_buy_conversion / buff_steam_sell_conversion（4 个挂刀转换比例） | `item_snapshot` | 同上 |
| sellPrice per platform（交叉校验） | `item_snapshot_steamdt` | SteamDT price/batch |

**计算**：

```python
def cross_platform_consistency(pool_snap: pd.DataFrame) -> FactorOutput:
    prices = pool_snap[["buff_sell_price", "yyyp_sell_price", "steam_sell_price_cny"]]
    cv_med = (prices.std(axis=1) / prices.mean(axis=1)).median()   # 逐件三平台 CV 的中位数
    cons_s = clip((0.15 - cv_med) / (0.15 - 0.03), 0, 1)           # CV 3% 满分 / 15% 零分（待校准）
    knife = pool_snap["steam_buff_sell_conversion"].median()       # 中位挂刀比例
    knife_dev = abs(knife - knife_series.rolling(90).median()[-1]) / 0.05   # 偏离 5pct 满刻度（待校准）
    raw = 0.65 * (2 * cons_s - 1) + 0.35 * (-tanh_scale(knife_dev))
    score = 50 + 50 * raw
```

**归一化**：固定阈值线性映射。论证：CV 是绝对度量（3%/15% 有经验语义：正常摩擦 vs 显著失锚），无需相对历史再归一；挂刀偏离用滚动中位数自适应基准。
**数据缺失降级**：仅 2 平台可用 → 两者价差率代替 CV（completeness=0.6）；挂刀字段缺失 → 仅 CV 分量（0.65）；单平台 → score=50, quality=0。**初始权重**：10%（待回测校准）。

## M6. 事件/情绪因子（初始权重 10%）

**定义**：市场恐慌/贪婪情绪代理。V1 用 CSQAQ 情绪监测指标量化；V5 接入 Event Agent（LLM 事件理解）作为**同一因子的增强输入**，LLM 职责边界见下。

**输入字段**：
| 字段 | 表 | 来源 API |
|---|---|---|
| greedy（市场情绪监测值） | `market_sentiment_snapshot` | CSQAQ `current_data?type=init`（api-187131779） |
| 饰品异动列表（暴涨暴跌件数） | 同上 | 同上 |
| event_score ∈ [-1,1]（V5） | `event_log` | Event Agent（LLM 结构化输出，仅存库后由确定性代码读取） |

**计算**：

```python
def market_sentiment(greedy_series, anomaly: dict, event_score: float | None) -> FactorOutput:
    g = rolling_percentile(greedy_series, window=180)       # greedy 量纲待验证，用分位免假设
    anom = clip((anomaly["surge_count"] - anomaly["crash_count"]) / 50, -1, 1)  # 50 件满刻度（待校准）
    raw = 0.7 * (2 * g - 1) + 0.3 * anom
    if event_score is not None:                             # V5 起，融合比例待校准
        raw = 0.6 * raw + 0.4 * event_score
    score = 50 + 50 * raw
```

**LLM 角色边界（V5 起）**：LLM 只做事件文本（Valve 更新/赛事/箱子变动）→ 结构化 `event_score ∈ [-1,1] + event_type + affected_scope` 的理解转换，输出落库为数据；数值融合（0.6/0.4 加权）由确定性代码完成。LLM 不可接触 MarketScore 计算、不可输出因子分值。供给机制>曝光度>情绪的先验沿用 CSGOTrading EVENT_PROMPT。
**归一化**：滚动分位数（greedy 量纲待验证，分位数免假设）+ 固定阈值（异动）。
**数据缺失降级**：greedy 缺失 → M2 宽度分×0.5+异动分量代理（quality×0.5，注明代理）；V1 恒无 event_score（不进入，不算缺失）。**初始权重**：10%（待回测校准）。

# Part 2 — ItemScore 八因子

```
ItemScore = Σ 因子分 × 有效权重   初始权重（待回测校准）：
趋势 20% | 相对大盘强弱 15% | 供需 20% | 成交量 15% | 流动性 10% | 跨平台一致性 10% | 估值 5% | 事件 5%
```

计算频率：V1 每 30 分钟对自选池+持仓件计算；价格主口径为 CSQAQ BUFF 在售价（`buff_sell_price`），SteamDT 全平台价交叉校验。

## I1. 趋势因子（初始权重 20%）

**定义**：单品价格的均线排列、RSI、MACD、动量综合。复用 CSGOTrading `agents/analysts/technical.py` 的纯 pandas 指标函数（修正其均值回复 z-score 疑似符号 bug 后引入）。

**输入字段**：
| 字段 | 表 | 来源 API |
|---|---|---|
| ts, close（BUFF 在售价快照重采样为 1h/1d OHLC） | `item_price_history` | CSQAQ 单件图表 api-187131781（BUFF/悠悠/Steam 出售价多周期） |
| o/c/h/l（备份，无成交量） | `item_kline_steamdt` | SteamDT `POST /open/cs2/item/v1/kline` |

**计算**：

```python
def item_trend(close: pd.Series) -> FactorOutput:
    ema8, ema21, ema52 = ema(close, 8), ema(close, 21), ema(close, 52)
    align = 1.0 if ema8[-1] > ema21[-1] > ema52[-1] else (-1.0 if ema8[-1] < ema21[-1] < ema52[-1] else 0.0)
    rsi_s = clip((rsi_wilder(close, 14) - 50) / 25, -1, 1)   # Wilder 版 RSI，25/75 满刻度（待校准）
    macd_s = tanh_scale(macd(close, 12, 26, 9).hist[-1] / (atr(close, 14) + eps))  # ATR 无量纲化
    mom_s = tanh_scale((close[-1] / close[-21] - 1) / 0.15)  # 21 点动量，±15% 满刻度（待校准）
    raw = 0.30 * align + 0.20 * rsi_s + 0.25 * macd_s + 0.25 * mom_s
    score = 50 + 50 * raw
```

**归一化**：固定阈值+tanh（RSI/动量满刻度有经验语义）；MACD 用 ATR 归一解决价位不可比。论证：单品价位差 4 个数量级，绝对量必须无量纲化。
**数据缺失降级**：历史不足 52 点 → 退化为 ema8/21+RSI（completeness=0.6）；不足 21 点 → score=50, quality=0.1；SteamDT K 线作备份源（source_penalty=0.8）。**初始权重**：20%（待回测校准）。

## I2. 相对大盘强弱因子（初始权重 15%）

**定义**：单品 N 日收益率 − 大盘 N 日收益率的滚动差值（超额收益）。

**输入字段**：
| 字段 | 表 | 来源 API |
|---|---|---|
| close（单品日收盘序列） | `item_price_history` | CSQAQ api-187131781 |
| index_value（大盘日值序列） | `market_index_history` | CSQAQ current_data / 指数 K 线 api-278085071 |
| sell_price_rate_7/30/180（交叉校验） | `item_snapshot` | CSQAQ api-187131780 |

**计算**：

```python
def relative_strength(close: pd.Series, index: pd.Series, n: int = 14) -> FactorOutput:
    rs_n = close.pct_change(n) - index.pct_change(n)         # N 日超额收益（n=14 待校准）
    rs_s = tanh_scale(rs_n.rolling(7).mean()[-1] / 0.10)     # 平滑后 ±10% 满刻度（待校准）
    rs_long = close.pct_change(60) - index.pct_change(60)    # 长窗方向确认
    agree = 0.2 * sign(rs_n[-1]) * (1 if sign(rs_n[-1]) == sign(rs_long[-1]) else 0.3)
    raw = clip(0.8 * rs_s + agree, -1, 1)
    score = 50 + 50 * raw
```

**归一化**：固定阈值 tanh（±10%/14 日超额满刻度）。论证：超额收益是相对量，固定锚点跨饰品可比；备选为全池横截面分位数，回测对比两者 IC 后定稿。
**数据缺失降级**：大盘指数序列缺失 → 用 `sell_price_rate_7` 与全池中位之差估算（quality×0.6）；单品历史不足 → quality=0。**初始权重**：15%（待回测校准）。

## I3. 供需因子（初始权重 20%）

**定义**：单品求购侧与在售侧力量对比及订单簿压力。

**输入字段**：
| 字段 | 表 | 来源 API |
|---|---|---|
| buff_sell_price / buff_buy_price / buff_sell_num / buff_buy_num | `item_snapshot` | CSQAQ api-187131780 |
| yyyp_sell_num / yyyp_buy_num、steam_sell_num / steam_buy_num | 同上 | 同上 |
| statistic（存世量，25 分钟级） | 同上 | 同上 |

**计算**：

```python
def item_supply_demand(snap: pd.Series) -> FactorOutput:
    # 1) 求购价/在售价比（→1 说明求购顶到在售，强买盘），90% 中性、98% 满分（待校准）
    pr_s = tanh_scale((snap["buff_buy_price"] / (snap["buff_sell_price"] + eps) - 0.90) / 0.08)
    # 2) 数量比，平台可信度加权 buff 0.5 / yyyp 0.3 / steam 0.2（待校准），2:1 满刻度
    w = {"buff": 0.5, "yyyp": 0.3, "steam": 0.2}
    bc = sum(wt * snap[f"{p}_buy_num"] for p, wt in w.items())
    sc = sum(wt * snap[f"{p}_sell_num"] for p, wt in w.items())
    cnt_s = tanh_scale(log((bc + 1) / (sc + 1)) / log(2))
    # 3) 订单簿压力: 在售量变化（下降=惜售/被吃）
    raw = 0.4 * pr_s + 0.35 * cnt_s + 0.25 * tanh_scale(-zscore(snap["sell_num_series"], 30))
    score = 50 + 50 * raw
```

**归一化**：固定阈值 tanh。论证：求购价/在售价比有天然上界 1.0 与经验中性区（BUFF 常见 0.85–0.95），固定锚点最贴合盘口语义。
**数据缺失降级**：某平台字段缺失 → 平台权重重归一（completeness 按缺失平台权重扣减）；存世量 V1 不参与（留作 V2 供给冲击研究）；仅剩单平台 → quality×0.5。**初始权重**：20%（待回测校准）。

## I4. 成交量因子（初始权重 15%）

**定义**：单品成交量的相对水平与趋势。

**输入字段**：
| 字段 | 表 | 来源 API |
|---|---|---|
| turnover_number（Steam 日成交量） | `item_snapshot` / `item_volume_history` | CSQAQ api-187131780 / 图表 api-187131781（日成交量序列） |
| 成交量排行榜名次 | `ranking_snapshot` | CSQAQ api-187131776 |
| K 线成交量 v（企业档） | — | CSQAQ api-278065737（V1 不可用，标注） |

**计算**：

```python
def item_volume(turnover: pd.Series) -> FactorOutput:
    level_s = tanh_scale(zscore(turnover, window=60) / 2)    # 相对自身 60 日（待校准）
    trend_s = sign_score(linregress_slope(turnover.tail(14)))
    pv_corr = turnover.tail(20).corr(price_series.tail(20).pct_change())  # 量价配合
    raw = 0.5 * level_s + 0.25 * trend_s + 0.25 * clip(pv_corr, -1, 1)
    score = 50 + 50 * raw
```

**归一化**：Z-score（对自身 60 日）+ tanh。论证：饰品间成交量差 3 个数量级，横截面绝对量无意义，必须对自身标准化。
**SteamDT 无量降级（关键）**：SteamDT K 线明确无成交量；CSQAQ turnover 缺失时**不得**以"K 线条数/价格变动次数"伪造成交量。降级路径：① CSQAQ 图表接口日成交量序列（同族字段，quality×0.9）；② 均无 → score=50, quality=0，reason 注明"成交量缺失，因子退出合成"。
**数据缺失降级**：见上；`turnover_number` 为 Steam 口径，与 BUFF 实际成交有口径差，视为代理，quality 上限 0.9。**初始权重**：15%（待回测校准）。

## I5. 流动性因子（初始权重 10%）

**定义**：单品可快速以合理价格买入/卖出的能力。是对 CSGOTrading "成交量+Reddit 双代理"的升级（用真实盘口字段）。

**输入字段**：
| 字段 | 表 | 来源 API |
|---|---|---|
| buff_sell_num / buff_buy_num（在售/求购深度） | `item_snapshot` | CSQAQ api-187131780 |
| buff_sell_price / buff_buy_price（spread） | 同上 | 同上 |
| turnover_number（成交频率代理） | 同上 | 同上 |
| source_update_time（快照时间，算陈旧度） | 同上 | 同上 |

**计算**：

```python
def item_liquidity(snap: pd.Series) -> FactorOutput:
    depth_s = rolling_percentile_score(depth_series, window=180)   # 深度=在售+求购，对自身历史
    spread = (snap["buff_sell_price"] - snap["buff_buy_price"]) / snap["buff_sell_price"]
    spread_s = clip((0.15 - spread) / 0.15, -1, 1)                 # 15% 价差零分（待校准）
    freq_s = rolling_percentile_score(snap["turnover_7d_avg"], window=180)  # 成交频率
    stale_penalty = 1.0 if hours_since(snap["source_update_time"]) <= 1 else (0.5 if <= 6 else 0.1)
    raw = 0.35 * (2*depth_s - 1) + 0.30 * spread_s + 0.35 * (2*freq_s - 1)
    score = (50 + 50 * raw) * stale_penalty + 50 * (1 - stale_penalty)   # 陈旧向中性收缩
```

**归一化**：滚动分位数（深度/频率，自适应自身量级）+ 固定阈值（spread 相对量用锚点）。**价格陈旧度惩罚**乘性作用于 score 向 50 收缩，防止用隔夜盘口出信号。
**数据缺失降级**：无求购字段（冷门件常见）→ spread 与 buy depth 分量置中性、completeness=0.5；turnover 缺失 → 仅 depth+spread（0.65）。**初始权重**：10%（待回测校准）。

## I6. 跨平台一致性因子（初始权重 10%）

**定义**：单品在 BUFF/悠悠/Steam 间的价格离散度与异常偏离惩罚。某平台价格显著偏离他台，常为该平台扫货/砸盘或数据异常。

**输入字段**：
| 字段 | 表 | 来源 API |
|---|---|---|
| buff_sell_price / yyyp_sell_price / steam_sell_price（折算 CNY） | `item_snapshot` | CSQAQ api-187131780 |
| steam_buff_sell_conversion（挂刀比例） | 同上 | 同上 |
| SteamDT 全平台 sellPrice（交叉校验） | `item_snapshot_steamdt` | SteamDT price/single |

**计算**：

```python
def item_cross_platform(snap: pd.Series) -> FactorOutput:
    p = [snap["buff_sell_price"], snap["yyyp_sell_price"], snap["steam_sell_price_cny"]]
    cons_s = clip((0.12 - np.std(p)/np.mean(p)) / (0.12 - 0.02), 0, 1)  # CV 2% 满分/12% 零分（待校准）
    max_dev = max(abs(x - np.median(p)) / np.median(p) for x in p)      # 单平台偏离中位
    raw = 0.7 * (2 * cons_s - 1) + 0.3 * (-1.0 if max_dev > 0.08 else 0.0)  # 偏离>8% 惩罚（待校准）
    score = 50 + 50 * raw
```

**归一化**：固定阈值（单品 CV 锚点比大盘 M5 略宽，单品噪声更大）。
**数据缺失降级**：Steam 价缺失（部分件 Steam 无挂单）→ 两平台价差率替代 CV（completeness=0.6）；单平台 → score=50, quality=0，且该件 BUY 信号 confidence 额外降档（见 Part 3.2）。**初始权重**：10%（待回测校准）。

## I7. 估值因子（初始权重 5%）

**定义**：当前价格相对自身历史的位置（分位与回撤）。注意：低分位不必然看多（可能基本面恶化），故初始权重仅 5%，方向处理为"中位区中性、极端区分多空"，待回测验证方向。

**输入字段**：
| 字段 | 表 | 来源 API |
|---|---|---|
| close 序列（≥180 天） | `item_price_history` | CSQAQ api-187131781 / 存世量走势 api-366480669（辅助） |
| sell_price_180 / sell_price_rate_180 | `item_snapshot` | CSQAQ api-187131780（180 天前价格交叉校验） |

**计算**：

```python
def item_valuation(close: pd.Series) -> FactorOutput:
    pct180 = (close.tail(180*24) < close[-1]).mean()         # 当前价 180 天分位（1h 快照）
    pct365 = (close.tail(365*24) < close[-1]).mean() if len(close) >= 365*24 else pct180
    dd = close[-1] / close.tail(365*24).max() - 1            # 距高点回撤（≤0）
    # 方向假设（待回测验证）: 35% 分位附近得分最高; 分位>90% 过热偏空
    pos_s = 1 - 2 * clip(abs(0.5*(pct180 + pct365) - 0.35) / 0.55, 0, 1)
    raw = 0.6 * pos_s + 0.4 * tanh_scale((dd + 0.30) / 0.15)  # 回撤 30% 中性锚（待校准）
    score = 50 + 50 * raw
```

**归一化**：自身历史分位数（180/365 天）——估值天然是"相对自身"概念，分位数是唯一合理口径。方向待验证，回测做分位分组收益分析后确认方向与权重。
**数据缺失降级**：历史<180 天 → 用 `sell_price_rate_180` 反推 180 天前价格两点定位（quality×0.5）；<60 天 → score=50, quality=0。**初始权重**：5%（待回测校准）。

## I8. 事件因子（初始权重 5%）

**定义**：单品级事件冲击（所属箱子进稀有掉落、战队贴纸、武器平衡性调整等）。

**V1 实现**：恒中性 `score=50, quality=1.0, notes="V1 占位，未接入事件源"`。权重 5% 保留在配置中，V5 接入时不改总分结构。
**V5 接入**：Event Agent（LLM）输出单品级 `event_score ∈ [-1,1]` 落 `event_log` 表，确定性代码读取后 `score = 50 + 50 * event_score`。LLM 只做"事件文本→方向与强度"转换，数值合成由代码完成（同 M6 边界）。
**数据缺失降级**：V1 不视为缺失（占位即设计）；V5 起无事件覆盖的件 score=50、quality=0.5。**初始权重**：5%（待回测校准）。

# Part 3 — 信号合成

## 3.1 FinalScore 合成

```python
FinalScore = w_item * ItemScore + w_market * MarketScore     # 初始 0.6 / 0.4（待回测校准）
```

**论证**：CS2 饰品市场贝塔极强（Valve 政策、大行动等系统性事件驱动同涨同跌），但单品流动性/供需差异巨大。0.6/0.4 即"择物略重于择时"——低流动性单品逆大盘行情的持续性强于股票。该比例需回测扫描（0.5/0.5、0.7/0.3 对照）确认，并允许按 market_regime 分档动态调整（如"极度恐慌"档 MarketScore 权重升至 0.5，待回测）。

**信号阈值**（初始值，待回测校准）：`FinalScore >= 75 → BUY 候选；45–75 → HOLD；< 45 → SELL 候选`。

**持仓件与非持仓件差异**：
- 非持仓件：BUY 候选即输出 BUY；SELL 候选输出 AVOID（并入 SELL 档位，语义"回避"）。
- 持仓件：引入**滞回带**抑制反复横跳——SELL 阈值下移至 `FinalScore < 40` 且要求 ItemScore < 45（双条件，待校准）；BUY 阈值不变（加仓视为新 BUY）。
- 持仓件叠加成本考量：浮动盈亏 < 卖出摩擦成本（平台费率约 2.5% + 价差）时不触发 SELL（沿用 CSGOTrading 2% 摩擦思想，费率按平台配置）。

## 3.2 confidence 置信度

```python
confidence = clip(0.45 * factor_agreement + 0.30 * data_quality + 0.25 * sample_sufficiency, 0, 1)
# factor_agreement: 因子方向一致性 = |Σ sign(score_i - 50) * w_i'| / Σ w_i'  ∈ [0,1]
#   （所有因子同向 → 1；多空参半 → 0）
# data_quality:     全部因子 quality 的加权均值（权重同合成权重）
# sample_sufficiency: min(1, 价格历史点数 / 180) * min(1, 快照覆盖平台数 / 3)
```

附加规则：关键因子（趋势/供需）quality=0 时 confidence 封顶 0.4；confidence<0.35 标记 `low_confidence=True`，Dashboard 降权展示、不推送告警（阈值待校准）。

## 3.3 risk_level 风险等级

三维度分档（各维度阈值初始值，待回测校准）：

| 维度 | 分档规则 |
|---|---|
| 流动性 L_score | I5 流动性因子分：≥65 → 1 档；35–65 → 2 档；<35 → 3 档 |
| 波动率 V_score | 20 日收益率年化波动率：<40% → 1；40–80% → 2；>80% → 3 |
| 单价 P_score | BUFF 在售价：<100 CNY → 1；100–2000 → 2；>2000 → 3（高价件 Float/Pattern 特异风险） |

```python
risk_score = L_score + V_score + P_score          # ∈ [3, 9]
risk_level = "L" if risk_score <= 4 else ("M" if risk_score <= 6 else "H")
```

流动性分缺失时按最差 3 档处理（未知即高风险）。

## 3.4 reason_json 结构

```json
{
  "final_score": 78.2, "item_score": 82.1, "market_score": 72.3, "weights": {"item": 0.6, "market": 0.4},
  "item_factors": {
    "trend":  {"score": 84.0, "weight": 0.20, "eff_weight": 0.20, "contribution": 16.8, "quality": 1.0,
               "raw": {"ema_align": 1, "rsi14": 68.2, "mom_21": 0.09}, "degraded": false, "notes": ""},
    "volume": {"score": 50.0, "weight": 0.15, "eff_weight": 0.0, "contribution": 0.0, "quality": 0.0,
               "raw": {}, "degraded": true, "notes": "turnover_number 缺失，因子退出合成"}
  },
  "market_factors": {"...": "同构"},
  "confidence": {"value": 0.71, "agreement": 0.82, "data_quality": 0.78, "sample": 0.55},
  "risk": {"level": "M", "liquidity_band": 2, "vol_band": 1, "price_band": 2},
  "data_asof": "2026-07-21T12:00:00Z",
  "input_snapshot_ref": {"item_snapshot_id": 918273, "market_index_id": 5512}
}
```

`contribution = score × eff_weight / Σeff_weight`，支撑 Dashboard 逐因子归因展示。`input_snapshot_ref` 指向当时输入快照主键，保证信号可追溯（总指示 §16.10）。LLM 生成的自然语言解释（V5）作为独立字段 `llm_commentary` 追加，不改动上述数值字段。

## 3.5 suggested_position 建议仓位

V1–V4 为规则矩阵（V5 才接 Portfolio Manager 组合级优化）：

| risk_level \ confidence | ≥0.7 | 0.5–0.7 | 0.35–0.5 | <0.35 |
|---|---|---|---|---|
| L | 10% | 7% | 4% | 0 |
| M | 6% | 4% | 2% | 0 |
| H | 3% | 2% | 0 | 0 |

（占总资产比例，初始值待回测校准；单件硬上限 `2/N_pool` 沿用 CSGOTrading 思路，N_pool 为信号池件数。）仅 BUY 输出非零仓位；SELL 输出 `{"action_position": "exit_full"}` 或 `"exit_half"`（40<FinalScore<45 时减半，待校准）。

## 3.6 signal 落库

写入 `signal` 表：`timestamp/item_uuid/market_score/item_score/final_score/signal/confidence/risk_level/reason_json/suggested_position/model_version/input_snapshot_ref`。**因子值随信号落库**（reason_json 内含全量因子分），供 IC/分层分析取数。

# Part 4 — 工程与验证

## 4.1 YAML 配置 schema（config/factors.yaml 示例）

```yaml
model_version: "0.1.0"          # 每次权重/阈值/公式变更必须 bump 并记录 CHANGELOG
asof_tz: "UTC"
market_score:
  regime_bands: [20, 40, 60, 80]                 # 五档阈值（待回测校准）
  weights: {trend: 0.25, breadth: 0.20, volume: 0.20, supply_demand: 0.15,
            cross_platform: 0.10, sentiment: 0.10}   # 初始值，待回测校准
  trend:     {ema_fast: 20, ema_slow: 60, slope_window: 10, slope_fullscale: 0.005}
  breadth:   {adr_fullscale: 2.0, extreme_pct: 0.05, extreme_fullscale: 0.10}
  volume:    {zscore_window: 90, trend_window: 14}
  supply_demand: {bsr_fullscale: 1.5, spread_neutral: 0.10, pool_min_size: 100}
  cross_platform: {cv_full: 0.03, cv_zero: 0.15, knife_dev_fullscale: 0.05}
  sentiment: {greedy_window: 180, anomaly_fullscale: 50, llm_blend: 0.4}
item_score:
  weights: {trend: 0.20, relative_strength: 0.15, supply_demand: 0.20, volume: 0.15,
            liquidity: 0.10, cross_platform: 0.10, valuation: 0.05, event: 0.05}
  trend:     {ema: [8, 21, 52], rsi: 14, rsi_fullscale: 25, macd: [12, 26, 9], mom_window: 21, mom_fullscale: 0.15}
  relative_strength: {window: 14, smooth: 7, fullscale: 0.10}
  supply_demand: {pr_neutral: 0.90, pr_fullscale: 0.08, platform_weights: {buff: 0.5, yyyp: 0.3, steam: 0.2}}
  volume:    {zscore_window: 60}
  liquidity: {spread_zero: 0.15, stale_hours: [1, 6]}
  cross_platform: {cv_full: 0.02, cv_zero: 0.12, dev_penalty: 0.08}
  valuation: {lookback_short: 180, lookback_long: 365, dd_neutral: 0.30}
signal:
  combine: {w_item: 0.6, w_market: 0.4}
  thresholds: {buy: 75, sell: 45, sell_holding: 40, sell_holding_item_max: 45}   # 待回测校准
  confidence: {w_agreement: 0.45, w_quality: 0.30, w_sample: 0.25, low: 0.35, key_factor_cap: 0.4}
  risk: {liquidity_bands: [65, 35], vol_bands_annual: [0.40, 0.80], price_bands_cny: [100, 2000], band_sum_L: 4, band_sum_H: 6}
  position_matrix: {L: [0.10, 0.07, 0.04, 0], M: [0.06, 0.04, 0.02, 0], H: [0.03, 0.02, 0, 0]}
quality:
  min_effective_weight: 0.35
  freshness: {full_minutes: 15, half_minutes: 60, dead_hours: 6}
  backup_source_penalty: 0.8
  sample_estimate_penalty: 0.75
```

加载时校验：权重和 = 1.0（±1e-6）；所有阈值在合法域内；`model_version` 与代码常量不一致时拒绝启动（防止配置漂移）。

## 4.2 防未来数据泄漏约束

1. **时间过滤铁律**：因子入口统一 `get_series(item_uuid, field, asof_ts)`，实现内强制 `WHERE ts <= asof_ts`，禁止裸查全表（借鉴 CSGOTrading `date <= trading_date` 范式）。
2. **快照可见性**：因子只读"asof_ts 已落库"的快照——入库时间戳 `ingested_at` 与数据自带 `source_update_time` 分离，回测按 `ingested_at <= asof_ts` 过滤，防止"数据当时未抓到却被回测使用"。
3. **信号-成交时间错位**：T 时刻信号，回测成交价取 **T+1 采样周期**（V1 为 30 分钟）后的可实现价——BUY 用 T+1 在售最低价×(1+slippage)，SELL 用 T+1 求购最高价×(1−slippage)；禁止用 T 时刻同快照价成交（信号由该快照算出，同时成交等于自证）。
4. **滚动统计只向后看**：`rolling/zscore/pct_change` 全部右端对齐（历史窗口），禁止 center=True 双向窗口；缺失插值只允许 ffill，禁止 CSGOTrading `_fill_missing_dates` 式 ±2 天前视插值。
5. **配置版本隔离**：回测加载的 factors.yaml 必须是回测区间起点之前提交的版本，禁止用"全样本调好的参数"回测同一段样本。

## 4.3 单元测试要求（tests/quant/）

每个因子一个测试类，至少覆盖：

1. **已知输入 → 已知输出**：构造确定性序列（线性上涨 → I1 score>75；ema 全空头 → <25），断言 score 落在预期区间且 raw 精确匹配手算值。
2. **全缺失边界**：全部输入 None/空 DataFrame → score=50、quality=0、degraded=True，总分合成退化到 min_effective_weight 兜底分支。
3. **单平台边界**：仅 BUFF 字段有值 → I6 score=50、quality=0；M5 completeness=0.6。
4. **零成交边界**：turnover_number 全 0 → I4 不崩溃、active_ratio=0；零成交持续 30 天 → I5 流动性分<35。
5. **极端值**：价格 0/负/Inf → 抛 DataQualityError 而非静默产出 NaN（NaN 视为 bug，assert not isnan）。
6. **合成逻辑**：mock 8 个 FactorOutput（含 quality=0）验证有效权重重归一正确；confidence 三分量单调性。
7. **回归基线**：每个因子保存 golden case（固定输入 JSON + 期望输出），公式改动必须同步更新 golden 并写 CHANGELOG。

## 4.4 回测验证钩子

1. **因子落库**：`factor_value` 表（或 signal.reason_json 解析视图）保存 `ts / item_uuid / factor_name / score / quality / model_version`，回测期全量留存。
2. **IC 分析**：每个因子 score 与未来 1d/3d/7d 超额收益（对大盘）计算 Rank IC 时序，输出 IC 均值/ICIR/衰减曲线；IC 显著为负或 |IC|<0.02 的因子进入降权/淘汰评审（阈值为惯例初始值，待实际数据验证）。
3. **分层有效性**：按因子分五分位分组，统计各组未来收益单调性；MarketScore 五档与未来大盘 N 日收益的对应关系需验证"极度恐慌/极度贪婪"档确有区分度。
4. **信号有效性**：BUY 候选组 vs SELL 候选组的未来收益差（含 2.5% 费率与 bid-ask 滑点后），及信号胜率、盈亏比，输出至 `backtest_result` 表关联 `model_version`。
5. **参数扫描边界**：权重/阈值扫描仅在训练区间进行，验证区间一次性确认；扫描结果写 `docs/research/factor_calibration_log.md`（回测阶段产出）。

## 附：待回测校准参数总清单

- 全部六+八个因子权重（MarketScore 25/20/20/15/10/10、ItemScore 20/15/20/15/10/10/5/5）
- 信号合成比 w_item/w_market = 0.6/0.4；信号阈值 75/45/40；滞回带与摩擦过滤参数
- 各因子内部满刻度锚点（斜率 0.5%/日、ADR 2:1、BSR 1.5:1、spread 10%/15%、CV 3%/15%、RSI ±25、动量 ±15%、超额 ±10%、pr 0.90/0.08、回撤 30% 等）
- 窗口参数（EMA20/60、Z-score 60/90/180 日、动量 21、RS 14 日等）
- confidence 权重 0.45/0.30/0.25 与 low_confidence 0.35；risk 三维度分档阈值；position 矩阵数值
- quality 各惩罚系数（backup 0.8 / sample 0.75 / min_effective_weight 0.35 / freshness 阶梯）
- I7 估值因子方向假设（"35% 分位最优"假说）
- CSQAQ `greedy` 情绪指标量纲与有效窗口（待验证字段）
