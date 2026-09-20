# CSQuant API 设计（API_DESIGN）

> 两层：① 内部服务层 API（V1 必须交付，Streamlit/调度器/CLI 调用，纯 Python）；② HTTP API（FastAPI，V2+，契约先行）。
> 通用约定：响应含 `as_of`(UTC ISO8601) 与 `data_quality`；价格带 `currency`；缺失为 `None` 不为 0；时间一律 UTC。

---

## Part 1 内部服务层 API（Python）

### 1.1 InventoryService（库存与资产）

```python
class InventoryService:
    def sync_steam_inventory(self) -> SyncReport
        # Steam库存→匹配item_master→差异入/出库；SyncReport{added, removed, matched, unmatched[]}
    def add_manual_position(self, market_hash_name: str, quantity: int,
                            buy_price: float|None, buy_time: str|None,
                            buy_platform: str|None, notes: str|None) -> PositionView
    def record_buy(self, position_id: str, price: float, fee: float, platform: str, traded_at: str) -> None
    def record_sell(self, position_id: str, price: float, fee: float, platform: str, traded_at: str) -> SellResult
        # SellResult{realized_pnl, return_rate}；position.status→SOLD；二次确认在页面层
    def get_portfolio(self) -> PortfolioView
        # PortfolioView{positions:[{item, qty, cost, current_price(buff主), value, pnl, return_rate,
        #   status, quality}], total_cost, total_value, unrealized_pnl, realized_pnl,
        #   return_rate, net_deposit, as_of}
    def get_valuation(self) -> ValuationView   # 轻量KPI（总资产/总成本/浮盈/收益率/平台分布）
    def get_nav_history(self, days: int = 90) -> list[NavPoint]  # portfolio_snapshot 曲线
```

### 1.2 MarketService（大盘）

```python
class MarketService:
    def get_market_overview(self) -> MarketOverview
        # {index_value, change_1d/7d/30d, market_score, regime, breadth{up,down,ratio},
        #  category_strength[{category, change_7d}], sub_indices[], as_of, source, quality}
    def get_market_score_history(self, days: int = 90) -> list[ScorePoint]
    def get_market_score_detail(self) -> MarketScoreDetail
        # {score, regime, factors:[{key,name,score,weight,contribution,raw,quality}], model_version}
    def get_breadth(self) -> BreadthView
```

### 1.3 ItemService（饰品）

```python
class ItemService:
    def search_items(self, keyword: str = "", category: str|None = None,
                     wear: str|None = None, rarity: str|None = None,
                     min_price: float|None = None, max_price: float|None = None,
                     sort: str = "change_7d_desc", limit: int = 50, offset: int = 0) -> ItemPage
    def get_item_detail(self, market_hash_name: str) -> ItemDetail
        # {master, metadata, quotes:[按平台{sell,buy,counts,volume,quality,ts}],
        #  cross_platform_spread, statistics(存世量), lease(可选)}
    def get_item_kline(self, market_hash_name: str, platform: str = "BUFF",
                       period: str = "1d", days: int = 180) -> list[KlinePoint]
    def get_item_quotes(self, market_hash_name: str) -> list[QuoteView]
```

### 1.4 SignalService（信号）

```python
class SignalService:
    def generate_signals(self, scope: str = "watchlist") -> GenerateReport  # {count, buy, hold, sell}
    def get_signals(self, signal_filter: str|None = None,
                    risk_level: str|None = None, limit: int = 100) -> list[SignalView]
        # SignalView{ts, item{name,image}, item_score, market_score, final_score, signal,
        #   confidence, risk_level, suggested_position, model_version, data_quality}
    def get_signal_detail(self, signal_id: int) -> SignalDetail
        # + reason_json 解析（逐因子 分值/权重/贡献/原始值）+ input_snapshot_ref 追溯数据
    def get_signal_history(self, market_hash_name: str, days: int = 30) -> list[SignalView]
```

### 1.5 BacktestService

```python
class BacktestService:
    def run_backtest(self, strategy: str = "default", params: dict|None = None,
                     start: str|None = None, end: str|None = None,
                     universe: str = "watchlist") -> BacktestSummary
        # BacktestSummary{backtest_id, total_return, annualized, max_drawdown, sharpe, sortino,
        #   calmar, win_rate, profit_factor, trade_count, turnover, fee_cost, slippage_cost,
        #   benchmark_return, warnings[]}
    def get_backtest_result(self, backtest_id: str) -> BacktestDetail  # 含 equity 曲线与回撤
    def list_backtests(self, limit: int = 20) -> list[BacktestSummary]
    def get_backtest_trades(self, backtest_id: str) -> list[BacktestTradeView]
```

### 1.6 DataSourceService

```python
class DataSourceService:
    def get_health(self) -> list[SourceHealth]
        # {source, status(ok/degraded/down), success_rate_1h, latency_p50_ms, freshness_p50_sec,
        #  last_error, circuit_open}
    def trigger_collect(self, scope: str = "watchlist", source: str|None = None) -> CollectReport
    def trigger_metadata_sync(self) -> SyncReport
```

### 1.7 AlertService（V2+，契约预留）

`create_rule / list_rules / delete_rule / get_triggered` —— 规则：`{item|market, metric, op, threshold, channel(log|webhook)}`。

---

## Part 2 HTTP API 契约（FastAPI，实现版本标注 V2+）

统一响应包：

```json
{ "code": 0, "data": { }, "as_of": "2026-07-21T08:00:00Z",
  "quality": "A", "message": "ok" }
```

| 方法 | 路径 | 说明 | 版本 |
|---|---|---|---|
| GET | /api/v1/portfolio | 库存与KPI | V2 |
| GET | /api/v1/portfolio/nav?days=90 | 净值曲线 | V2 |
| POST | /api/v1/portfolio/sync | 触发库存同步 | V2 |
| POST | /api/v1/portfolio/positions | 手工建仓 | V2 |
| POST | /api/v1/portfolio/positions/{id}/sell | 卖出登记（二次确认头 `X-Confirm: yes`） | V2 |
| GET | /api/v1/market/overview | 大盘总览 | V2 |
| GET | /api/v1/market/score/history?days= | MarketScore历史 | V2 |
| GET | /api/v1/items?keyword=&category=&wear=&sort=&cursor= | 筛选器（cursor 分页，offset 在大表深翻页退化，论证采用 cursor） | V2 |
| GET | /api/v1/items/{market_hash_name} | 单品详情 | V2 |
| GET | /api/v1/items/{market_hash_name}/kline?platform=&period=&days= | K线 | V2 |
| GET | /api/v1/signals?signal=&risk=&cursor= | 信号列表 | V2 |
| GET | /api/v1/signals/{id} | 信号详情+追溯 | V2 |
| POST | /api/v1/signals/generate | 手动生成 | V2 |
| POST | /api/v1/backtests | 发起回测（body: strategy/params/start/end/universe） | V4 |
| GET | /api/v1/backtests/{id} | 回测结果 | V4 |
| GET | /api/v1/datasources/health | 数据源健康 | V2 |
| POST | /api/v1/datasources/collect | 手动补采 | V2 |

鉴权：本机 `Authorization: Bearer {LOCAL_API_TOKEN}`（.env）；限频：60 req/min/IP（中间件令牌桶）。

**响应示例** `GET /api/v1/signals?signal=BUY`：

```json
{ "code": 0,
  "data": { "items": [ { "signal_id": 1024, "ts": "2026-07-21T07:30:00Z",
      "market_hash_name": "AK-47 | Redline (Field-Tested)", "name_zh": "AK-47 | 红线 (久经沙场)",
      "image_url": "...", "item_score": 78.5, "market_score": 66.0, "final_score": 73.5,
      "signal": "BUY", "confidence": 0.71, "risk_level": "M", "suggested_position": 0.08,
      "model_version": "signal-v1.0.0" } ], "next_cursor": "eyJvZmZzZXQiOjEwMH0=" },
  "as_of": "2026-07-21T07:30:05Z", "quality": "A", "message": "ok" }
```

---

## Part 3 Provider 调用契约

调用方（采集器/因子）仅依赖 `data_sources.base.MarketDataProvider` 协议与 `capabilities` 声明；`unified.service` 负责按降级矩阵路由（DATA_SOURCE_SPEC §9）。`UnifiedQuote` 为 stdlib dataclass（字段同 DATA_SOURCE_SPEC §4），HTTP 层（V2）再由 pydantic 包装校验。

## Part 4 错误与降级规范

| 码段 | 含义 | 示例 |
|---|---|---|
| 0 | 成功 | — |
| 1xxx | 数据问题 | 1001 无数据；1002 数据陈旧(stale 返回+quality=C)；1003 映射缺失 |
| 2xxx | 计算问题 | 2001 因子输入不足（信号降级 confidence≤0.4）；2002 回测区间无数据 |
| 3xxx | 外部源 | 3001 CSQAQ 限频；3002 Token/IP白名单失效(熔断)；3003 Steam 429 |
| 4xxx | 配置 | 4001 缺密钥；4002 参数非法 |

外部源失败响应策略：有快照 → 返回陈旧数据 + `quality=C` + `message` 注明；无快照 → `code=1001`。超时：外部源 connect 5s / read 15s；内部服务无超时但单请求数据量上限 10k 行。

## Part 5 版本与兼容

- 信号/回测响应必含 `model_version` 与 `input_snapshot_ref`；
- HTTP 路径版本 `/api/v1`；破坏性变更升 v2，旧版保留 90 天；
- `model_version` 变更必须关联 `backtest_result.strategy_version`（防拍脑袋阈值上线）。
