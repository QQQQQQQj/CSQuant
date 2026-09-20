# CSQuant 数据源可行性设计（DATA_SOURCE_SPEC）

> 依据：`docs/research/datasource_web_research_notes.md`（2026-07-21 网络调研，含官方文档原文与来源URL）。
> 本文所有接口字段均来自官方文档；无法确认的标注「待验证」，禁止当作事实编码。

---

## 1. 选型结论与接入优先级

| 优先级 | 数据源 | 角色 | 理由 |
|---|---|---|---|
| ① 主源 | **CSQAQ**（api.csqaq.com） | 行情/大盘/供需/成交量/存世量/租赁/ID映射 | 官方开放API、免费档 1次/秒不限总量、字段最全（7平台+挂刀比例+租赁+存世量）、数据自2021-07起、5-15分钟全量更新 |
| ② 辅源 | **SteamDT**（open.steamdt.com） | 价格兜底/跨源校验/磨损查询/检视图/大盘K线 | 官方开放平台、Bearer认证、分钟级限频；K线无成交量，不能单独支撑量价策略 |
| ③ 校验源 | **Steam 官方市场** | 低频抽样校验/库存同步/成交历史补漏 | 无正式行情API、IP级429风控严格，仅低频使用 |
| ④ 补充校验 | **C5 开放平台**（opendoc.c5game.com） | 第三方价格交叉校验 | 官方文档存在（伙伴价格接口批量200个/10qps，需申请权限），「待验证」 |
| 禁止 | BUFF / 悠悠有品直连 | — | 无公开行情API、反爬严格、有封号风险；一律经聚合站间接获取 |

**合规红线**：CSQAQ 官方声明「严禁用于商业用途」。CSQuant V1 定位为**个人投研学习用途**，符合要求；任何商业化演进必须先解决数据授权（CSQAQ企业合作 / SteamDT付费 / 自建采集）。

---

## 2. 三源可行性矩阵

| 维度 | CSQAQ | SteamDT | Steam 官方 |
|---|---|---|---|
| 认证方式 | Header `ApiToken` + IP白名单绑定 | Header `Authorization: Bearer {KEY}` | 匿名（priceoverview/listings）/ Cookie（pricehistory、库存） |
| 限频 | 单IP 1次/秒，不限总量 | base 1次/日；price/single 60次/分；price/batch 1次/分(≤100个)；kline 120次/分；wear 36000次/时 | 未公开；社区经验全站 20-30次/分/IP，429 IP级封禁（可信度中） |
| 单品价格 | ✅ 7平台 sell/buy 价+量 | ✅ 全平台 sellPrice/sellCount/biddingPrice/biddingCount | ✅ lowest/median/volume（无求购价）；listings 有买卖深度 |
| 成交量 | ✅ `turnover_number`（Steam日成交）+ 图表接口日成交量 | ❌（K线明确不含成交） | ✅ priceoverview 24h volume / pricehistory 日销量 |
| 历史K线 | 免费档为分段图表；**含量K线(t/o/c/h/l/v)仅企业档** | ✅ o/c/h/l（无量），type 1-3 含义「待验证」 | pricehistory 日级中位价（需登录Cookie） |
| 大盘数据 | ✅ 首页指数/子指数/涨跌分布(2分钟)/情绪(greedy)/排行榜 | ✅ 大盘指数+历史+大盘K线 | ❌ |
| BUFF/悠悠覆盖 | ✅ 含租赁、过户价、年化 | ✅ BUFF/YOUPIN/C5/STEAM/HALOSKINS | ❌ |
| 存世量 | ✅ statistic + 180天走势 | ❌ | ❌ |
| 库存相关 | ✅ 库存监控系列接口 | ❌ | ✅ /inventory/{steamid64}/730/2（需公开库存，限频收紧） |
| ID映射 | ✅ get_good_id（good_id↔market_hash_name，分页≤500） | ✅ base（全量 marketHashName→platformList，每日1次须落库） | name_id 需从挂单页HTML提取 |
| 实时性 | 5-15分钟全量；涨跌分布2分钟 | 未公开，带 updateTime 字段「待验证」 | 准实时 |
| 成本 | 免费档+企业档（未公开价） | 目前限时免费，政策不确定 | 免费但风控成本高 |
| 稳定性 | 个人/小团队运营，无SLA | 新上线开放平台，接口可能迭代 | 无版本承诺，可随时变更 |
| 主要风险 | 严禁商用条款、断供、IP白名单对动态IP不友好 | 转付费、无成交量 | 429封IP、Cookie高频调用牵连账号 |

---

## 3. Provider / Adapter 层设计

### 3.1 抽象基类（`data_sources/base.py`）

```python
class MarketDataProvider(ABC):
    name: str  # "csqaq" | "steamdt" | "steam"

    # 行情
    def get_item_quote(self, item: ItemRef) -> UnifiedQuote | None: ...
    def get_batch_quotes(self, items: list[ItemRef]) -> list[UnifiedQuote]: ...
    def get_price_history(self, item: ItemRef, period: KlinePeriod,
                          platform: Platform | None = None) -> list[UnifiedKline]: ...
    def get_orderbook_or_depth(self, item: ItemRef) -> OrderBookDepth | None: ...

    # 大盘
    def get_market_index(self) -> UnifiedMarketIndex | None: ...
    def get_market_breadth(self) -> MarketBreadth | None: ...

    # 元数据与映射
    def get_item_metadata(self) -> Iterable[ItemMetadataRecord]: ...

    # 运维
    def health_check(self) -> ProviderHealth: ...
    @property
    def capabilities(self) -> ProviderCapabilities: ...  # 声明本源可提供的字段集
```

`ItemRef` 携带 `item_uuid + market_hash_name + 各平台ID`，Provider 自行选择本平台可用的键（CSQAQ 用 good_id，SteamDT 用 marketHashName，Steam 用 market_hash_name）。

**铁律**：任何平台特有字段（如 `yyyp_lease_annual`、`biddingPrice`）不得穿透出 Provider；统一结构外的扩展字段进 `extra: dict` 并登记字典文档。

### 3.2 Provider 职责划分

| Provider | 职责 | 不负责 |
|---|---|---|
| CSQAQProvider | 日常行情主采集（单品详情7平台字段）、大盘指数/涨跌分布/情绪、排行榜、存世量、ID映射增量、成交量、库存监控（可选） | 磨损/检视图 |
| SteamDTProvider | 价格兜底与跨源校验、大盘K线备份、磨损查询（wear）、检视图（inspect，每日100次，仅按需） | 成交量 |
| SteamMarketProvider | 库存同步（/inventory）、priceoverview 低频抽样校验、listings 深度快照（低频）、pricehistory 补历史（Cookie，低频，账号风险隔离） | 日常批量行情 |

---

## 4. UnifiedMarketData 统一数据结构（pydantic v2）

```python
class UnifiedQuote(BaseModel):
    item_uuid: UUID
    market_hash_name: str
    platform: Platform            # BUFF/UUYP/STEAM/C5/IGXE/ECO/...
    source: str                   # 实际数据来源 "csqaq" | "steamdt" | "steam"
    sell_price: Decimal | None    # 最低在售价（缺失=NULL，禁止置0）
    sell_count: int | None
    buy_price: Decimal | None     # 最高求购价
    buy_count: int | None
    volume_24h: int | None        # 成交量（仅部分源/平台有）
    reference_price: Decimal | None  # 源给出的参考/成交均价
    currency: Currency            # CNY/USD，入库统一 CNY，汇率记录于 extras
    source_update_time: datetime  # 源侧 updateTime（UTC）
    fetched_at: datetime          # 本系统采集时间（UTC）
    data_quality: DataQuality     # 见 §8
    extras: dict = {}

class UnifiedKline(BaseModel):
    item_uuid: UUID
    platform: Platform
    period: KlinePeriod           # 1h/4h/1d/7d
    ts: datetime                  # K线起点（UTC）
    open: Decimal; close: Decimal; high: Decimal; low: Decimal
    volume: int | None            # SteamDT 必为 None → data_quality 降级

class UnifiedMarketIndex(BaseModel):
    index_name: str               # "csqaq_main" | "steamdt_broad" | 子指数名
    index_value: Decimal
    change_1d: float | None       # 比率
    breadth_up: int | None        # 上涨家数
    breadth_down: int | None
    sub_indices: list[SubIndex]   # 品类子指数（刀/手套/步枪…，映射自CSQAQ sub_index_data）
    sentiment_greedy: float | None  # CSQAQ 市场情绪值
    source: str
    ts: datetime
```

存库映射：`UnifiedQuote` → `quote_ticks`（时序）+ `market_snapshot`（最新值覆盖）；`UnifiedKline` → `kline_daily`；`UnifiedMarketIndex` → `market_index`（DDL 详见 DATABASE_SCHEMA.md）。

---

## 5. 限频与配额管理

| 源 | 规则 | 客户端策略 |
|---|---|---|
| CSQAQ | 1 req/s/IP | 全局令牌桶 rate=1/s；429→退避60s并告警；503→指数退避 |
| SteamDT | price/single 60/min；batch 1/min(≤100)；kline 120/min；base 1/日 | 批量优先（100个/req）；base 每日定时任务+落库+失败当日禁止重试超过3次 |
| Steam | 经验 ≤1 req/2-3s + 随机抖动 | 单令牌桶 rate=0.4/s + jitter；429→指数退避（5min起）并熔断该接口30min |

实现：`data_sources/ratelimit.py` 提供 `TokenBucket(key, rate, burst)`，按 `source:endpoint` 维度独立桶；所有 Provider 调用必须经 `RateLimitedClient`（httpx 封装，含超时 connect=5s/read=15s）。

---

## 6. 缓存设计

1. **L1 内存缓存**：单品报价 TTL=60s（Dashboard 刷新不重复打 API）；
2. **L2 数据库即缓存**：`market_snapshot` 为最新值表，读路径永远先读库；采集器负责写；
3. **base/元数据**：CSQAQ get_good_id 分页、SteamDT base（每日1次）、CSGO-API JSON —— 全部落库，运行时禁止现查；
4. **K线**：落库 `kline_daily`，增量拉取（按已存最大 ts 续传）；
5. 缓存键统一含 `source + item_uuid + platform`，禁止以价格为键。

---

## 7. 重试、超时与失败处理

- 网络错误：指数退避重试 3 次（1s/4s/15s + jitter）；
- 业务错误（errorCode≠0 / success=false）：不重试，记录 `event_log`；
- 401/400（Token/IP白名单失效）：立即熔断该 Provider，告警，禁止自动重试风暴；
- 429：按 §5 退避+熔断；
- 单饰品失败不阻塞批次：批次内逐项 try，失败项 `data_quality=FAILED_FETCH`，下轮按退避优先级重试；
- 所有失败计入 `datasource_health`（见 §9）。

---

## 8. 数据质量评分（data_quality 字段）

每条约带质量等级 `A/B/C/D`（入库字段 `data_quality` + `quality_flags` JSON）：

| 等级 | 条件 |
|---|---|
| A | 主源直采、新鲜度达标（quote≤15min，index≤30min）、字段完整 |
| B | 主源直采但部分字段缺失（如 SteamDT K线无 volume）、或新鲜度略超（≤2×阈值） |
| C | 备源降级产出（如 CSQAQ 断供时 SteamDT 兜底）、或跨源校验不一致（偏离>5%） |
| D | 陈旧数据（>2×阈值）、校验失败、人工标记异常 |

质量分用于：因子计算输入过滤（C/D 不进因子）、Dashboard 角标提示、回测数据清洗规则。

---

## 9. 数据源健康监控与主备切换

`datasource_health` 表记录：`source, endpoint, ts, latency_ms, status(ok/degraded/down), error_code, success_rate_1h, freshness_p50`。

- 每 5 分钟汇总成功率/延迟/新鲜度；成功率<90% 或连续3次失败 → `degraded`，<60% → `down`；
- **字段级降级矩阵**（自动切换）：

| 数据 | 主 | 备1 | 备2 |
|---|---|---|---|
| 单品多平台价格 | CSQAQ 单件详情 | SteamDT price/single(batch) | 上次快照（标C/D） |
| Steam 成交量 | CSQAQ turnover_number | Steam priceoverview（低频抽样） | NULL+降级 |
| 大盘指数 | CSQAQ current_data | SteamDT broad/v1/index | 上次快照 |
| 涨跌分布/宽度 | CSQAQ current_data | 自算（quote_ticks 全库涨跌统计） | — |
| K线 | CSQAQ 图表接口 | SteamDT kline | Steam pricehistory（Cookie,低频） |
| 磨损/检视图 | SteamDT wear/inspect | 第三方float API「待验证」 | — |
| 库存 | Steam /inventory | CSQAQ 库存监控 | 手工导入 |
| ID映射 | 本地 item_master | CSQAQ get_good_id | SteamDT base |

- Dashboard「数据源状态」页必须展示三源健康灯与最近错误。

---

## 10. 异常价格检测规则（采集后管道 `data_quality_pipeline`）

触发即标记（quality_flags）并按规则降级，**禁止静默写入**：

1. **跨平台偏离**：同一 item 同一时刻，两平台 sell_price 相对偏差 >15% → flag `CROSS_PLATFORM_DIVERGENCE`（可能是数据陈旧或扫货）；
2. **跨源不一致**：主备源同平台价格偏差 >5% → 降 C 级并记录；
3. **买卖倒挂**：buy_price > sell_price（求购高于在售） → flag `INVERTED_BOOK`（可能瞬时抢筹，保留但标注）；
4. **时序突变**：|Δprice|/price > 30% 且 volume_24h 无显著放大 → flag `PRICE_SPIKE_SUSPECT`；
5. **零成交陈旧**：sell_count 长时间不变 + source_update_time 超过阈值 → 降 D；
6. **缺失字段**：关键字段（sell_price）缺失 → 禁止置 0，置 NULL 降 B/C。

---

## 11. 采集调度策略（V1 规模：自选股+库存 ≈ 100-500 件，全库后台慢采）

| 任务 | 频率 | 说明 |
|---|---|---|
| 自选池+库存批量报价 | 5 min（CSQAQ 1/s 足够） | 对齐 CSQAQ 5-15min 更新节奏 |
| 大盘指数/涨跌分布 | 5 min | current_data type=init/hours |
| 排行榜/存世量 | 30 min | |
| K线增量（自选池） | 1 h | |
| SteamDT 校验抽样 | 30 min 轮换 50 件 | 跨源一致性 |
| Steam priceoverview 校验 | 每日低频轮换 | 防429 |
| 库存同步 | 15 min（可手动触发） | |
| CSGO-API 元数据 | 每日 | manifestId 增量 |
| SteamDT base / CSQAQ get_good_id | 每日 | 落库 |

热度分级（借鉴 Tracker 思想，权重改为：持仓>自选>高流动性>全库尾部），全库 34k 件不在 V1 高频采集范围。

---

## 12. 待验证事项清单（编码前必须实测确认）

1. SteamDT K线 `type`（1-3）的具体含义与历史长度；
2. SteamDT 价格更新频率（用 updateTime 实测）；
3. SteamDT 大盘K线接口路径与限频（文档目录有、权限列表无）；
4. CSQAQ 免费档「分段图表」接口的实际时间粒度与长度；
5. Steam 库存接口匿名访问在当下的实际可用性与限频阈值；
6. CSQAQ 库存监控系列接口的授权范围（能否监控自己的库存）；
7. C5 伙伴价格接口的申请门槛；
8. 动态 IP 下 CSQAQ 白名单「绑定本机」接口的自动化可行性。

## 13. 风险与合规总结

数据断供（CSQAQ 个人运营无 SLA）、政策转付费（SteamDT）、IP 风控（Steam）、合规（CSQAQ 严禁商用）。应对：多源降级 + 全量落库（断供后历史仍可用）+ 采集层与业务层彻底解耦（可插拔新源）+ V1 仅限个人投研用途。
