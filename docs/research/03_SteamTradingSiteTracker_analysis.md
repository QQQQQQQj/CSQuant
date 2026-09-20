# SteamTradingSiteTracker 深度代码分析报告

> 分析对象：`SteamTradingSiteTracker-main`（"Steam 挂刀行情站" iflow.work 的开源数据采集框架）
> 分析方式：逐文件阅读 `scripts/` 下全部 7 个 Python 源文件（本文所有结论均来自真实代码，非 README 转述）
> 开源范围说明：仓库仅开源 `scripts/` 目录；`scripts/secrets/` 下有 3 个 cookie 占位文件（`buff_cookie.txt`、`c5_cookie.txt`、`uuyp_cookie.txt`，均为 0KB 空文件），本文仅引用其文件名与用途，不含任何密钥内容。

---

## 1. 项目定位

这是一个**面向"挂刀"（跨平台套利）场景的 Steam 饰品行情采集与比价流水线**。核心目标：

- 全天候追踪 BUFF / IGXE / C5 / UUYP 四个第三方平台 + Steam 市场本身，覆盖 CSGO（appid=730）与 DOTA2（appid=570）约 64000 个饰品；
- 计算"挂刀比例" = 第三方平台售价 / Steam 平台到手价（扣除 Steam 手续费后）；
- 通过**优先级调度**让"比例低（值得挂刀）、流动性高"的饰品保持约 10 分钟级更新，冷门饰品被降级到几乎不更新；
- 数据供前端行情站（iflow.work）查询展示。

它不是通用爬虫框架，而是一个高度特化的**增量快照采集系统**：没有完整的历史时间序列存储（只有"当前最新快照"），架构重点全部放在"如何用有限代理资源让 6 万个条目尽量新鲜"。

## 2. 技术栈

从 import 与依赖推断：

| 层 | 技术 |
|---|---|
| 语言 | Python 3（使用 f-string 风格 loguru 格式化、类型标注） |
| 异步采集 | `aiohttp` + `asyncio`（data_fetcher 每个进程一个 event loop） |
| 同步采集 | `requests` + `BeautifulSoup4`（meta_crawler 解析 IGXE/C5 的 HTML 搜索页） |
| 并发模型 | `multiprocessing.Process`（4 个采集进程）+ 进程内 asyncio 协程，跨进程用 `multiprocessing.Lock` 互斥 |
| 消息队列/任务态 | **Redis（redis-py 的 JSON 模块，`redis.json().set/get`）**，任务以 JSON 文档存于 db0，key 为 `buff_id` |
| 持久化 | **MongoDB（pymongo）**，库名 `steam`，集合 `meta` / `data` |
| 重试 | `retrying` 库（`@retry(stop_max_attempt_number, wait_fixed)`） |
| 其他 | `numpy`（洗牌、argmin、mean）、`loguru`（日志）、`locale`（解析带千分位的 Steam 成交量字符串） |

## 3. 目录结构

```
SteamTradingSiteTracker-main/
├── scripts/
│   ├── start_meta_crawler.py      # 元数据爬虫：维护饰品清单与各平台 ID（324 行）
│   ├── start_task_mapper.py       # 任务分发器：按优先级把饰品映射为 Redis 任务（115 行）
│   ├── start_data_fetcher.py      # 采集器：4 进程异步抓取各平台行情（280 行）
│   ├── start_result_collector.py  # 结果收集器：解析、算比例、回写 MongoDB（216 行）
│   ├── database.py                # MongoDB / Redis(TaskList) 两个封装类（122 行）
│   ├── url_formats.py             # 全部外部 URL 模板（16 行）
│   ├── utils.py                   # 代理加载、随机延迟、Steam 手续费计算（65 行）
│   └── secrets/
│       ├── buff_cookie.txt        # （0KB 占位）BUFF 登录 cookie，断言含 "session"
│       ├── c5_cookie.txt          # （0KB 占位）C5 登录 cookie，断言含 "C5Login"
│       └── uuyp_cookie.txt        # （0KB 占位）UUYP Bearer token，断言含 "Bearer"
├── framework.png                  # 架构图（与代码推断一致：mapper→fetcher→collector）
├── market_analysis.png / titlepage.png
├── LICENSE / README.md
```

四个 `start_*.py` 是**四个独立常驻进程**，各自 `while True` 主循环，通过 MongoDB + Redis 解耦通信，进程间无任何直接调用。

## 4. 核心模块

### 4.1 `start_meta_crawler.py` — 元数据爬虫（饰品主档维护者）
- 以 BUFF 为**主数据源**：`get_buff_index(page_num, game)` 翻页拉取 BUFF 商品列表（每页 80 条，按价格升序，价格区间 1–5000 元），直到 `total_page`。
- 对每个 BUFF 条目做**准入过滤**（见第 9 节），通过后逐一解析其余平台 ID：
  - `get_market_id(hash_name, appid)`：抓 Steam 市场页 HTML，正则 `Market_LoadOrderSpread\((.*?)\);` 提取 `item_nameid`；
  - `get_igxe_id(game, name)`：抓 IGXE 搜索页 HTML，BeautifulSoup 按 `class="name"` 精确匹配中文名，从 `/product/\d+/(\d+)` 提取 ID；
  - `get_c5_id(game, name)`：抓 C5 搜索页 HTML，匹配 `el-col el-col-4` 卡片里的中文名，从链接 `/(\d+)/` 提取 ID；
  - `get_uuyp_id(name)`：POST UUYP 搜索 JSON API，`CommodityName.strip() == name.strip()` 精确匹配取 `Id`（仅 CSGO）。
- 产出 item 文档写入 MongoDB `meta` 集合（upsert by `buff_id`）。
- 主循环：全量页列表 `np.random.shuffle` 后逐页 `update_once`，每页之间 `random_delay()`（默认 15–17 秒随机）；一轮结束后删除 `created_at` 超过 `META_EXPIRE_TIME = 14 天` 未更新的条目。

### 4.2 `start_task_mapper.py` — 任务分发器（调度大脑）
- 先对账 `data` 与 `meta` 两集合：`meta - data` 的新饰品插入 data（`updated_at=0, weighted_ratio=0`，即最高优先级）；`data - meta` 的已下架饰品删除。
- 内层循环约 1 小时（200 × 20s），每 20 秒检查 Redis 中空闲任务数 `count_free()`：
  - 若 `< 800`，从 `data` 集合按 `weighted_ratio` **升序**（比例最低 = 最值得挂刀 = 最优先）取全量候选，切成 3 段：`[0,10%)`、`[10%,30%)`、`[30%,100%)`；
  - 每段内部再按 `updated_at` 升序（最久未更新的优先），各取最旧 600 个，调用 `parse_task(item)` 生成任务写入 Redis。
- `parse_task` 生成的任务结构（关键！）：
  ```python
  {"buff_id", "hash_name", "appid", "game", "market_id",
   "tasks": ["volume", "buff", ("igxe"), ("c5"), ("uuyp"), "order"],
   "complete": False, "running": False, "start": time.time(), "trials": 0}
  ```
  其中 `order`（Steam 求购/挂单 histogram）**固定排在最后**——注释明确说明该接口限流最严、代理成本最高，必须等其它子任务全部成功后才发，"只处理离完成一步之遥的任务"。

### 4.3 `start_data_fetcher.py` — 采集器（代理池消费者）
- `N_PROCESSES = 4` 个进程，每进程独立 event loop；启动时 `task_list.flush()` 清空 Redis 并等 20 秒让 mapper 先填任务。
- 每轮：`load_proxies()` 加载代理列表 → 取 `get_free_task_ids()` → 按 `get_priority()` **升序**排序（返回值规则：队首非 "volume" 的在途任务=0，队首是 "volume" 的新任务=1，空任务=2；升序后**在途任务最优先**，即优先把已开工的任务做完，与 mapper 把最贵请求排最后的贪心策略呼应）→ `zip(task_ids, proxies)` **一任务一代理**并发派发 `safe_fetch`。
- `fetch()` 主逻辑：
  1. `acquire(task_id)` 置 `running=True`；
  2. 取 `task["tasks"][0]`，通过 `fetch_adapters` 字典分发到对应平台采集函数（策略模式）；
  3. 成功后 `update_task(task_id, "<platform>_data", data)` 把原始响应写回 Redis，并 `tasks = tasks[1:]` 弹出队首；
  4. 若 `tasks` 空了 → `complete()`；若 `trials > 80`（`N_TRIALS`）→ 强制 `complete()` 并记 stale warning；
  5. `release(task_id)`。
- 各 adapter 细节（请求方式、断言、容错）：
  | adapter | 方法 | 成功断言 | 特殊处理 |
  |---|---|---|---|
  | `fetch_volume` | GET `volume_json_fmt` | `data["success"]` | 成功后若 24h 成交量 `< 2` 直接 `complete()`（低流动性提前终止，不浪费后续请求） |
  | `fetch_buff` | GET `buff_json_fmt` | `data["code"] == "OK"` | — |
  | `fetch_c5` | GET `c5_json_fmt`，带 header `"platform": "2"` | 200/404，`data["success"]` | **404 视为正常**，data=None（该饰品在 C5 无在售） |
  | `fetch_igxe` | GET `igxe_json_fmt` | 200/404，`data["succ"]` | 404 → data=None |
  | `fetch_uuyp` | **POST** `uuyp_json_fmt`，json body 固定 `listSortType=1, listType=10, pageIndex=1, pageSize=10, sortType=1, templateId` | 200/404，`data["Code"] == 0` | 404 → data=None |
  | `fetch_order` | GET `order_json_fmt` | `data["success"]` | — |
- 所有 adapter 的 `except` 分支**直接静默 return**——代理极不可靠是常态，失败就放弃本次，靠 trials 上限防死循环。
- 超时统一 `TIMEOUT = 12s`；`aiohttp.TCPConnector(ssl=False, limit=0)`（关 SSL 校验、不限制连接数）。
- 进程间通过 `multiprocessing.Lock` + `time.sleep(1)` 错开抢任务，避免同时刷 Redis。

### 4.4 `start_result_collector.py` — 结果收集器（计算与回写）
- 每 10 秒扫一遍 Redis 全部任务，对 `complete=True` 的执行 `collect(buff_id, results)` 然后 `delete_task`。
- `collect()` 的三态决策：
  - `volume_data` 缺失 → **ignore**（采集不完整）；
  - 24h 成交量 `< 2` → **skip**（低流动性）；
  - `order_data` 或 `buff_data` 缺失 → **ignore**；
  - 否则 → **parse**（完整解析）。
  - ignore/skip 都直接把 `weighted_ratio = 100`（打入最低更新优先级冷宫）。
- parse 分支的数据标准化（**多源统一的核心**）：把各平台原始响应统一转成 `{platform}_sell_list = [(price, 0, 0), ...]` 元组列表（后两位是兼容位，代码里恒为 0）；Steam 侧从 `order_data` 提取 `buy_order_graph[:10]`（求购档）与 `sell_order_graph[:10]`（在售档）。
- 然后计算挂刀比例并回写 MongoDB `data` 集合（replace_one 整文档覆盖）。

### 4.5 `database.py` — 存储封装
- `MongoDB` 类：`__init__(collection, database="steam")`，CRUD 全部以 `buff_id` 为主键（`find/replace_one/delete_one({"buff_id": ...})`），`get_sorted_items(sort)` 支持升序排序，`update_item` 是**整文档 replace**。
- `TaskList` 类：基于 RedisJSON 命令封装任务生命周期——`create_task / acquire / release / complete / update_task / get_free_task_ids / get_priority / get_trials / update_trials / flush`。
- 注意：`MONGODB_PORT` / `REDIS_PORT` 在开源代码中是 `"YOUR_MONGODB_PORT"` 占位符，需自行填写。

### 4.6 `utils.py` — 工具
- `load_proxies()`：**开源版直接 `assert 0, "Not implemented!"`**——代理池是作者刻意保留的私有部分，部署必须自行实现（返回 `["ip:port", ...]`）。
- `random_delay(min=15, max=17)`：均匀随机 sleep，反爬节流。
- `calculate_after_fee(amount)`：**Steam 市场手续费逆运算**。Steam 费用结构为 steam_fee 5% + publisher_fee 10%（各向下取整、最低 1 分），该函数从买家支付价反推卖家实际到手钱包金额（迭代逼近，最多 10 轮）。这是挂刀比例计算的"分母修正器"，精度直接决定比例准确性。
- `default_header`：Chrome 103 UA。

## 5. 主数据流（任务生成 → 分发 → 采集 → 入库）

```
                    ┌─────────────────────────────────────────────────┐
                    │            MongoDB (db=steam)                    │
                    │  ┌───────────┐              ┌───────────┐       │
                    │  │ meta 集合  │              │ data 集合  │       │
                    │  │(饰品主档+ │              │(最新行情快 │       │
                    │  │ 各平台ID) │              │ 照+比例)  │       │
                    │  └─────┬─────┘              └─────▲─────┘       │
                    └────────│──────────────────────────│─────────────┘
   meta_crawler (常驻)       │  upsert by buff_id       │ replace_one
   翻页BUFF索引→过滤→        │                          │
   解析igxe/c5/uuyp/steam ID─┘                          │
                                                         │
   task_mapper (常驻)  ── 读取 meta/data 对账 ────────────┤
   按 weighted_ratio 升序 + updated_at 最旧优先           │
   每段(top10%/10-30%/30-100%)取600条                     │
        │ parse_task() → tasks=["volume","buff",...,"order"]
        ▼
                    ┌──────────────────────────────────┐
                    │   Redis (db0, RedisJSON)          │  ← 消息队列/任务态
                    │   key = buff_id, value = task JSON│
                    │   {tasks[], complete, running,    │
                    │    trials, volume_data, buff_data,│
                    │    order_data, c5_data, ...}      │
                    └──▲───────────────▲───────────▲───┘
                       │ get_free_task_ids          │ complete任务扫描
   data_fetcher ×4进程 │ (Lock互斥, 按priority排序)  │ (每10s)
   每进程asyncio并发    │                            │
   1任务1代理, 逐个弹出  ─┘                            │
   tasks[0], 结果写回Redis                             │
                                                      │
   result_collector (常驻)  ── collect(buff_id, task) ──┘
   ignore/skip → weighted_ratio=100 (降级)
   parse → 解析各平台sell_list → 算24种比例 → weighted_ratio
          → update_item 回写 data 集合 → delete_task
```

**调度机制要点**：
- 没有 Celery/RQ 等成熟队列，而是**用 RedisJSON 文档 + running/complete 布尔位**自造了一个轻量任务队列，状态机为 `free → running → complete → deleted`；
- 任务不是一次性原子完成，而是**多轮渐进式**：每轮 fetch 只弹一个子任务（tasks[0]），失败则原样留待下轮重试，直到 tasks 空或 trials>80；
- 更新频率完全由 `weighted_ratio` 涌现式决定：比例低 → 排在 mapper 前 10% → 最久未更新先被映射 → 高频更新；比例高/采集失败/低成交量 → `weighted_ratio=100` → 沉底，实际形成"重点饰品约 10 分钟一更，冷门饰品几小时到几天一更"的自适应分层；
- 容量控制：Redis 空闲任务 `< 800` 才补货，每段最多 600 个，防止任务积压打爆代理池。

## 6. 数据模型

### 6.1 MongoDB `steam.meta` 集合（饰品主档，由 meta_crawler 写入）
| 字段 | 类型 | 来源/含义 |
|---|---|---|
| `buff_id` | int | **主键**，BUFF goods id |
| `igxe_id` / `c5_id` / `uuyp_id` | int | 各平台商品 ID，找不到为 0 |
| `market_id` | int | Steam `item_nameid`（order histogram 接口入参） |
| `hash_name` | str | Steam market_hash_name（英文唯一名） |
| `short_name` / `name` | str | BUFF 短名 / 中文全名 |
| `buff_ratio` | float | sell_min_price / buy_max_price（无求购为 999），准入过滤用 |
| `buff_reference_price` | float | BUFF sell_reference_price |
| `buff_buy_num` / `buff_sell_num` | int | BUFF 求购数 / 在售数 |
| `appid` | int | 730 / 570 |
| `game` | str | "csgo" / "dota2" |
| `created_at` | int | 最近更新时间戳（14 天未更新被清退） |

### 6.2 MongoDB `steam.data` 集合（行情快照，由 collector 覆盖式更新）
在 meta 全部字段基础上追加：

| 字段 | 含义 |
|---|---|
| `updated_at` | 快照时间戳（int 秒） |
| `count_in_24` | Steam 24h 成交量（`volume` 字符串去千分位转 int） |
| `buy_order_list` / `sell_order_list` | Steam 求购/在售前 10 档 `[(price, num), ...]` |
| `buff_sell_list` / `igxe_sell_list` / `c5_sell_list` / `uuyp_sell_list` | 各平台在售列表 `[(price, 0, 0), ...]` |
| `{p}_optimal_price` / `{p}_safe_price` | p∈{buff,igxe,c5,uuyp}，见第 9 节定价规则；缺货为 9999999 |
| `optimal_buy_price` / `safe_buy_price` | Steam 求购价（已扣手续费） |
| `optimal_sell_price` / `safe_sell_price` | Steam 在售最低价（已扣费；safe_sell 是占位符=optimal） |
| `safe_transaction_price` / `optimal_transaction_price` | Steam 最近成交中位价（已扣费；optimal 是占位符=safe） |
| `{p}_{s}_{m}_ratio` | 4 平台 × 2 档(optimal/safe) × 3 模式(buy/sell/transaction) = **24 个挂刀比例** |
| `weighted_ratio` | 综合优先级分（越低越优先更新；100=冷宫） |

### 6.3 Redis 任务文档（见 4.2 parse_task 结构 + fetcher 追加的 `{platform}_data` 原始响应字段）

**注意：本项目没有历史时间序列表**——`data` 集合是"最新快照"整文档覆盖，`updated_at` 只有一个。行情站的历史曲线应来自未开源部分（可能有时序库或另设快照归档）。

## 7. API / 外部依赖（url_formats.py 完整摘录与请求方式）

```python
# ==== meta ====
steam_item_page_fmt  = "https://steamcommunity.com/market/listings/{appid:d}/{hash_name:s}"
buff_index_json_fmt  = "https://buff.163.com/api/market/goods?game={game:s}&page_num={page_num:d}&page_size=80&min_price=1&max_price=5000&sort_by=price.asc"
igxe_search_page_fmt = "https://www.igxe.cn/market/{game:s}?keyword={name:s}"
c5_search_page_fmt   = "https://www.c5game.com/{game:s}?marketKeyword={name:s}"
uuyp_search_page_fmt = "https://api.youpin898.com/api/homepage/es/template/GetCsGoPagedList"

# ==== data ====
index_page_fmt       = "https://steamcommunity.com/market/listings/{appid:d}/{hash_name:s}"  # 与 steam_item_page_fmt 相同，代码中实际未用
order_json_fmt       = "https://steamcommunity.com/market/itemordershistogram?country=HK&currency=23&language=english&item_nameid={market_id:d}&two_factor=0&norender=1"
volume_json_fmt      = "https://steamcommunity.com/market/priceoverview/?appid={appid:d}&currency=23&market_hash_name={hash_name:s}"
buff_json_fmt        = "https://buff.163.com/api/market/goods/sell_order?game={game:s}&goods_id={buff_id:d}"
igxe_json_fmt        = "https://www.igxe.cn/product/trade/{appid:d}/{igxe_id:d}"
c5_json_fmt          = "https://www.c5game.com/napi/trade/steamtrade/sga/sell/v3/list?itemId={c5_id:d}"
uuyp_json_fmt        = "https://api.youpin898.com/api/homepage/es/commodity/GetCsGoPagedList"
```

请求方式与字段映射：

| 接口 | 方法 | 关键入参 | 响应断言 | 采集字段（collector 中的映射） |
|---|---|---|---|---|
| Steam priceoverview | GET | appid, hash_name(URL quote), currency=23(CNY) | `data["success"]` | `volume`（千分位字符串→`count_in_24`）、`median_price`（去前两位货币符号转 float） |
| Steam itemordershistogram | GET | item_nameid=market_id, currency=23 | `data["success"]` | `buy_order_graph[:10]`、`sell_order_graph[:10]` → 前 2 列元组 |
| BUFF sell_order | GET | game, goods_id=buff_id | `data["code"]=="OK"` | `data["items"][i]["price"]` → buff_sell_list |
| IGXE trade | GET | appid, igxe_id（路径参数） | 200/404 + `data["succ"]` | `d_list[i]["unit_price"]` → igxe_sell_list |
| C5 sell v3 list | GET | itemId=c5_id，header `platform: 2` | 200/404 + `data["success"]` | `data["list"][i]["price"]` → c5_sell_list |
| UUYP GetCsGoPagedList | POST | json body 含 templateId=uuyp_id 等 6 个固定参数 | 200/404 + `data["Code"]==0` | `Data["CommodityList"][i]["Price"]` → uuyp_sell_list |
| BUFF goods index（meta） | GET | game, page_num, page_size=80, 价格区间1–5000 | `code=="OK"` | id/name/market_hash_name/short_name/quick_price/sell_min_price/buy_max_price/sell_num/buy_num/sell_reference_price |
| UUYP template 搜索（meta） | POST | gameId="730", keyWords=中文名 等 | `Code==0` | `Data[i]["CommodityName"]` 精确匹配 → `Id` |
| IGXE/C5 搜索页（meta） | GET | keyword=中文名 | HTTP 200 | **HTML 解析**，非 JSON API（脆弱点） |
| Steam listings 页（meta） | GET | appid, hash_name | HTTP 200 | 正则 `Market_LoadOrderSpread(id)` → market_id |

认证：meta_crawler 把三个 cookie 合并进同一 headers——`Cookie = buff_cookie + ";" + c5_cookie`，`authorization = uuyp_cookie(Bearer)`。data_fetcher 的采集请求**不带任何 cookie**（全部走匿名 + 代理）。

## 8. 数据库（表结构完整记录）

- **MongoDB**：host=localhost，端口需自填；库 `steam`；集合 `meta`（主档）与 `data`（快照），均以 `buff_id` 为业务主键（代码未显式建唯一索引，靠应用层 upsert 保证）。无 schema 校验、无历史表、无 TTL 索引（过期清理由 meta_crawler 应用层做）。
- **Redis**：host=localhost，端口需自填，db=0，`decode_responses=True`；依赖 **RedisJSON 模块**（`redis.json().set/get`）；key 为 str(buff_id)，value 为任务 JSON；fetcher 启动时 `flushdb()` 全清，即 Redis 只做**易失任务队列**，不承担持久化。

字段级结构见第 6 节三张表。

## 9. 核心算法

### 9.1 准入过滤（meta_crawler.update_once）
```python
quick_price < 1 → 丢弃                        # 太便宜没有挂刀价值
quick_price < 10 且 sell_num < 50 → 丢弃       # 低价且低流动性
buff_ratio = sell_min_price / buy_max_price   # 无求购则 999
buff_ratio > 1.64 → 丢弃                      # 溢价过高，挂刀无利
market_id == 0 → 丢弃                          # Steam 页解析失败
```
这一步把 BUFF 全量（约十几万）收敛到约 64000 个候选。

### 9.2 挂刀比例（result_collector）
定义（代码中的 lambda，虽未被直接调用但表达了公式）：
```python
parse_ratio = lambda buy, sell_raw: (sell_raw * 0.85, buy / (sell_raw * 0.85))
# 即：Steam到手 ≈ 售价 × 0.85；挂刀比例 = 第三方买价 / Steam到手价
```
实际计算用精确的 `calculate_after_fee()` 替代 0.85 近似：
- `ratio = {platform}_{safe}_price / {safe}_{mode}_price`，遍历 platform×{optimal,safe}×{buy,sell,transaction} 共 24 个比例。比例 < 1 表示第三方更便宜（可挂刀）。

### 9.3 平台定价规则（optimal vs safe）
- `quick_price > 8`（高价品）：`optimal = 在售列表第 1 低价`；`safe = 前 3 档中 (price, ?, ?) 第二列最小者`（本意是排除"只有 1 件在售的钓鱼低价"，但代码里第二列恒为 0，`np.argmin` 恒取下标 0，**safe 实际退化为 optimal**——一个现存 bug/半成品逻辑）；
- `quick_price <= 8`（低价品）：`optimal = 前 10 档均价`，`safe = optimal`；
- 该平台无在售：两者都置 `9999999`（参与 min 时自动被排除）。
- Steam 侧：`safe_buy_price` = 求购档中数量 ≥ 3 的最高档（排除散户小单），`optimal_buy_price` = 最高求购档；均经 `calculate_after_fee` 扣费。

### 9.4 更新优先级（weighted_ratio）
```python
if optimal_transaction_ratio > 0:
    weighted_ratio = 0.4*optimal_buy_ratio + 0.2*optimal_sell_ratio + 0.4*optimal_transaction_ratio
else:
    weighted_ratio = 0.6*optimal_buy_ratio + 0.4*optimal_sell_ratio
# 其中各 ratio 取 4 个平台的最小值（min = 最值得挂刀的平台）
# ignore/skip/空挂单 → weighted_ratio = 100
```
作者注释原话："I think the latest transaction price is the most informative :)"（成交价比买卖挂单价更能反映真实行情，故权重 0.4+0.4 偏向 buy 与 transaction）。

### 9.5 分层调度（task_mapper）
- 全量按 `weighted_ratio` 升序 → 切 top10% / 10–30% / 30–100% 三段 → 段内按 `updated_at` 最旧优先各取 600 → 入队；
- 效果：好比例饰品（top10%）几乎每轮必被映射（约 10 分钟一更）；中段次之；尾部 70% 只有队列空闲时才轮得到——**用同一字段同时编码"价值"与"更新频率"**。

### 9.6 Steam 手续费精确计算（utils.calculate_after_fee）
费用公式：`steam_fee = floor(max(5%, 1分))`，`publisher_fee = floor(max(10%, 1分))`。从买家支付金额反推卖家到手：初始估计 `amount/1.15`，迭代 ±1 分逼近直至 `received + fees == amount`，最多 10 轮，含 undershot 保护分支。比 0.85 粗算精确到分。

## 10. 最值得复用的设计

1. **"比例即优先级"的自适应调度**：用一个 `weighted_ratio` 字段把"业务价值"直接翻译成"调度顺序"，配合分桶 + 最旧优先，无需单独调度器即实现热点高频、冷门低频。
2. **渐进式子任务链**：`tasks = ["volume","buff",...,"order"]` 列表即 DAG，每轮弹一个、结果落 Redis，失败自动留待下轮；把限流最贵/代理成本最高的请求（Steam order）放最后，失败成本最小化——这是稀缺代理资源下的经典贪心。
3. **成交量前置熔断**：volume 永远是第一个子任务，24h 成交 `< 2` 直接 complete，省掉后续 4–5 个请求。
4. **404 即正常语义**：C5/IGXE/UUYP 无在售返回 404 → data=None → 比例置 9999999，把"缺货"编码进数据而非异常流。
5. **四进程 × asyncio × 一任务一代理**的水平扩展结构，配合 RedisJSON 原生状态位（running/complete）实现无锁（应用层）协作。
6. **精确的 Steam 手续费逆运算**（calculate_after_fee），挂刀/搬砖类项目的必备组件。
7. **meta / data 双集合分离**：主档（慢变，14 天 TTL）与快照（快变，分钟级）解耦，对账式增删（meta−data 集合差）保证一致性。
8. **trials 计数 + 上限强杀**（80 次）防僵尸任务，配合 `start` 时间戳可统计采集延迟。

## 11. 可直接复用的代码（具体文件与函数）

| 文件 | 函数/片段 | 复用方式 |
|---|---|---|
| `utils.py` | `calculate_after_fee(amount)`、`calculate_fee_helper(received_amount)` | **零改动直接用**，Steam 扣费精确计算 |
| `utils.py` | `random_delay(min, max)`、`default_header` | 反爬节流模板 |
| `url_formats.py` | 全部 12 个 URL 模板 | 直接作为 CSQuant 各平台 connector 的 endpoint 配置（注意时效性，见 13 节） |
| `database.py` | `TaskList` 类（RedisJSON 任务队列封装） | 整体搬运，改 key 命名空间即可 |
| `database.py` | `MongoDB` 类 | CRUD 骨架可参考，建议补唯一索引与 schema |
| `start_data_fetcher.py` | `fetch_adapters` 字典 + `fetch()` 的 tasks 弹链模式 | 策略模式 + 渐进式任务链的范本，建议抽象为基类 |
| `start_result_collector.py` | 24 比例的 3 层 for 循环生成器（`{p}_{s}_{m}_ratio`） | 字段命名规范可直接沿用 |
| `start_task_mapper.py` | `parse_task()` 与分桶映射循环 | 调度逻辑模板 |
| `start_meta_crawler.py` | `get_market_id()` 的 `Market_LoadOrderSpread` 正则 | 取 Steam item_nameid 的标准做法 |
| `start_meta_crawler.py` | 准入过滤参数（1/10/50/1.64 阈值） | 作为 CSQuant universe 筛选的初始超参 |

## 12. 只能借鉴的部分

1. **代理体系**：`load_proxies()` 是 `assert 0` 空壳，一任务一代理、失败静默重试的**思想**可借鉴，但代理池实现、质量分层、成本核算必须自建。
2. **HTML 解析找 ID**（get_igxe_id / get_c5_id）：CSS 选择器（`list list`、`el-col el-col-4`、`ellipsis pointer li-btm-title mb10`）与前端 DOM 强耦合，网站改版即失效，只能借鉴"搜索→精确名匹配→提取链接 ID"的三段式思路。
3. **整文档 replace_one 覆盖**：简单粗暴，对快照够用，但 CSQuant 若要历史序列必须改为"快照表 + 最新表"双写。
4. **weighted_ratio 权重**（0.4/0.2/0.4）：是作者的领域直觉（挂刀场景），CSQuant 若做趋势/波动研究，优先级函数应换成波动率/成交量加权。
5. **cookie 合并进同一 headers 的写法**：`Cookie = buff + ";" + c5` 同时发给所有站点有凭证泄露风险（BUFF 能收到 C5 的 cookie），只能借鉴"凭证外置 secrets 文件"，不能照抄实现。
6. **`eval()` 解析数字**（`eval(item.get("quick_price","0"))`、`eval(order["price"])`）：能用但有注入风险，应替换为 `float()`。

## 13. 已过时/不适用部分

1. **ECO 平台缺失**：背景提到五平台含 ECO，但代码中完全没有 ECO 的 adapter 与 URL——该接口为闭源或后加，开源版不适用。
2. **C5 接口路径**：`c5_json_fmt` 用的是 `/napi/trade/steamtrade/sga/sell/v3/list`，C5 近年多次改版（sga→新商品体系），大概率已失效或需新参数；`c5_search_page_fmt` 的 HTML 结构同理。
3. **IGXE HTML 搜索**：当前 IGXE 已是 SPA 前端，`/market/{game}?keyword=` 返回的 HTML 未必还有 `class="list list"` 静态节点，需改用其 XHR JSON 接口。
4. **Steam currency=23 / country=HK**：CNY + HK 区的组合可用性随 Steam 区域政策变化；priceoverview 的 `median_price` 字符串切片 `[2:]` 依赖货币符号宽度，换币种即错。
5. **Chrome 103 UA**：2022 年的 UA 字符串，容易被指纹风控标记。
6. **`retrying` 库**：已停止维护，应换 `tenacity`。
7. **无历史数据模型**：对 CSQuant 的量化研究场景，"只存最新快照"本身就不适用，必须扩展时序层。
8. **`locale.setlocale("en_US.UTF-8")`**：在 Windows 上不可用（该项目面向 Linux 部署），跨平台需处理。
9. **DOTA2 不走 UUYP**（`if game == "csgo"` 才取 uuyp_id）：UUYP 只做 CSGO 是业务事实，但映射代码硬编码，扩展新游戏需改源码。

## 14. 对 CSQuant 的具体价值

1. **现成的五源（四源+Steam）行情字段规范**：`{platform}_sell_list`、`optimal/safe_price`、`count_in_24`、`buy/sell_order_list` 前十档——可直接成为 CSQuant 标准行情结构（StandardQuote）的字段蓝本。
2. **多平台 ID 映射表的生产工艺**：以 BUFF 为锚（buff_id 主键 + market_hash_name 国际键），用"搜索接口 + 精确名匹配"批量解析他平台 ID，14 天 TTL 保鲜——CSQuant 的 instrument_master 表可照搬此流水线，也可复用仓库 `SteamTradingSite-ID-Mapper` 的静态映射做冷启动。
3. **调度即研究假设**：weighted_ratio 分桶调度证明"6 万条全量等频更新不可行，必须按价值分层"，CSQuant 可换成波动率/流动性因子驱动。
4. **手续费模型**：calculate_after_fee 是跨平台净价对齐的前提，没有它所有价差信号都有 15% 量级的系统误差。
5. **工程基线**：4 进程 × 异步 × 代理池 × Redis 队列已被生产验证能支撑 64000 标的、10 分钟级热点更新，CSQuant 采集层的容量规划可直接以此为基准。

## 15. 推荐集成方式

1. **不要整体 fork**，按组件吸收：新建 `csquant.ingest` 包，把 `url_formats.py` 变成 YAML/配置文件，`fetch_adapters` 抽象为 `PlatformAdapter` 基类（`fetch(task) -> RawQuote`）。
2. **任务队列**：保留 RedisJSON TaskList 设计，但 key 加命名空间（`task:{buff_id}`），并加结果 TTL；中长期可换 Celery/RQ + Redis Streams。
3. **存储分层**：
   - `instrument_master`（≈ meta 集合）：buff_id 主键 + 各平台 ID + hash_name，14 天 TTL 巡检；
   - `quote_latest`（≈ data 集合）：沿用其字段规范；
   - 新增 `quote_ticks`（时序表，TimescaleDB/ClickHouse/Parquet 分区）：collector 每次 update 同时 append 一条 `(ts, buff_id, platform, optimal_price, safe_price, steam_buy, steam_sell, volume_24h, ratios...)`——这是对它"只存最新"缺陷的关键改造。
4. **调度器**：复用"分桶 + 最旧优先"，优先级函数参数化（`priority = f(ratio, volume, volatility)`），把 0.4/0.2/0.4 权重做成配置。
5. **凭证管理**：沿用 secrets/ 外置文件思路，但每平台独立 header 注入（修正其 cookie 混发问题），并接入环境变量。
6. **灰度路径**：先只接 Steam（volume+order）与 BUFF 两源跑通"mapper→fetcher→collector→ticks"闭环，再逐个补 IGXE/C5/UUYP（需先验证接口时效性）。

## 16. 风险和注意事项

1. **法律/合规风险**：
   - 五个平台均无公开授权的行情 API，采集违反其 ToS；BUFF/C5/UUYP 接口带登录 cookie，封号风险高；
   - Steam 社区市场接口（itemordershistogram/priceoverview）有严格速率限制，大规模代理抓取可能触发 Valve 的 IP 封禁甚至法律行动；
   - 数据商用（行情站、收费 API）有著作权与不正当竞争风险，CSQuant 应仅限内部研究。
2. **反爬技术风险**：免费代理极不可靠（代码注释原话 "highly-unreliable proxies"），失败静默 + trials 上限意味着**数据缺失是常态**；cookie 凭证会过期（meta_crawler 启动时 assert 即崩）；UUYP Bearer token 有效期短。
3. **接口时效风险**：C5 v3 路径、IGXE HTML、UUYP es 接口均为逆向所得，随时可能变更或加签（UUYP 后续版本已加签名参数的先例很多）。接入前必须逐一手工验证。
4. **数据质量风险**：
   - `median_price` 按 `[2:]` 切片去货币符号，格式变化会静默出错；
   - 大量使用 `eval()` 解析数字，恶意响应可造成代码注入；
   - safe_price 选择逻辑因第二列恒 0 而退化为 optimal（现存 bug）；
   - 整文档 replace 无并发保护，collector 与 mapper 同时写 data 集合存在竞态（mapper 插入新条目依赖 meta，collector 覆盖依赖旧文档，极端时序下会丢字段）。
5. **架构风险**：Redis flushdb 启动清空 = 采集中断即丢全部在途任务；单点 MongoDB/Redis 无高可用；`N_TRIALS=80` 后强杀任务但 data 集合旧快照仍在，前端可能展示陈旧数据（age 只靠日志 warning，无自动下架）。
6. **凭证隔离缺陷**：buff 与 c5 cookie 拼接后发给所有请求方（含 Steam/UUYP），造成跨站凭证泄露，复用时必须修复。

---

## 附：10 个必须回答的问题

**1. 多数据源如何统一？**
以 BUFF 为主键锚点（buff_id 为全系统主键，market_hash_name 为跨平台语义键）；采集层各平台原始响应原样写回 Redis（`{platform}_data`），由 result_collector 统一做**字段映射归一化**：各平台在售列表统一为 `[(price,0,0), ...]` 元组列表，Steam 统一为 buy/sell 前十档；价格层面用 `calculate_after_fee` 把 Steam 价换算为"卖家到手净价"后再与他平台比价，实现口径统一。

**2. 不同平台 ID 如何处理？**
meta_crawler 以 BUFF 索引为入口，对每个饰品的**中文全名**做精确匹配搜索，解析出 igxe_id（HTML 链接正则 `/product/\d+/(\d+)`）、c5_id（HTML 卡片链接 `/(\d+)/`）、uuyp_id（JSON API `CommodityName` 精确匹配取 `Id`，仅 CSGO）、market_id（Steam 页 `Market_LoadOrderSpread` 正则）；找不到记 0 且 task_mapper 跳过对应子任务；meta 集合 14 天 TTL 滚动保鲜。

**3. 数据采集任务如何调度（更新优先级机制）？**
三级机制：(a) collector 计算 `weighted_ratio`（各平台最小挂刀比例的加权：buy×0.4 + sell×0.2 + transaction×0.4），失败/低流动性置 100；(b) mapper 每 20s 检查队列，<800 时按 weighted_ratio 升序切 top10%/10–30%/30–100% 三桶，桶内按 `updated_at` 最旧优先各取 600 入队；(c) fetcher 内按 `get_priority` 升序——队首非 volume 的在途任务(0)最优先、新任务(1)次之、空任务(2)最后，即优先完成已开工任务；每任务最多 80 次尝试。净效果：热点约 10 分钟一更，冷门沉底。

**4. 如何保存价格历史快照？**
**开源版不保存历史**——`data` 集合是整文档 `replace_one` 覆盖，只有最新快照 + `updated_at`。它的"快照"是滚动当前态；历史曲线依赖未开源部分。CSQuant 必须在 collector 落库处追加时序写入（quote_ticks）。

**5. 哪些字段适合成为 CSQuant 标准行情结构？**
推荐：`instrument{buff_id, hash_name, appid, game, igxe_id, c5_id, uuyp_id, market_id}`；`quote{ts, platform, optimal_price, safe_price, sell_list_top10}`；`steam{ts, buy_order_top10, sell_order_top10, volume_24h, median_price, optimal_buy/safe_buy/optimal_sell/transaction（均扣费后净价）}`；派生 `{platform}_{optimal|safe}_{buy|sell|transaction}_ratio` 与 `weighted_ratio`（更名为 priority_score）。

**6. 如何处理接口失败、延迟和异常？**
分层容错：请求层 12s 超时 + 状态码/业务码双断言，失败静默返回（代理差是常态）；任务层失败不弹子任务、trials+1，>80 次强杀 complete；collector 层按数据完整度分 ignore/skip/parse 三态，ignore 记 warning 并降级优先级；meta 层用 `@retry`（2 次、定间隔 2–20s）+ 每页随机 15–17s 延迟 + 页级异常后 300–360s 长冷却；所有子任务结果带 `start` 时间戳可核算端到端延迟（日志中 age/elapsed）。

**7. 如何检测跨平台异常价格？**
本项目思路是**比例异常即信号**而非告警系统：(a) 准入时 `buff_ratio = sell_min/buy_max > 1.64` 直接剔除（买卖价倒挂异常）；(b) 无求购时 ratio=999、无在售时 price=9999999 的哨兵值把异常编码进数据；(c) safe_price 本意排除"前 3 档中仅 1 件的钓鱼低价"（虽有实现 bug）；(d) `count_in_24 > 10 但挂单列表为空` 会打 warning 日志（热门品数据缺失=接口异常信号）。CSQuant 可在此基础上加 z-score/IQR 的跨平台价差离群检测。

**8. 如何对高流动性和低流动性饰品采用不同更新频率？**
流动性直接参与调度：(a) 准入过滤砍掉 `quick_price<10 且 sell_num<50`；(b) 采集时 volume 是首个子任务，24h 成交 `<2` 立即 complete 跳过后续请求，collector 把 weighted_ratio 置 100 永久沉底；(c) 高流动性（=通常也是低挂刀比例的热门品）天然落在 mapper 的 top10% 桶，享受最旧优先的分钟级更新；低流动性品靠 30–100% 桶在队列空闲时低速轮询。

**9. 哪些爬虫思想仍值得使用？**
① 任务链式弹栈 + 最贵请求排最后的成本贪心；② 成交量前置熔断；③ 一任务一代理 + 失败静默重试 + trials 上限；④ 40x 语义化（404=无在售）；⑤ 搜索页精确名匹配做跨平台实体对齐；⑥ 准入过滤先行压缩 universe（十几万→6.4 万）；⑦ 随机延迟 + 页面洗牌（np.random.shuffle 更新顺序）降低行为指纹；⑧ meta/data 双层 TTL 保鲜。

**10. 哪些旧接口不应该继续依赖？**
① C5 `/napi/trade/steamtrade/sga/sell/v3/list`（改版高风险）；② IGXE/C5 的 HTML 搜索页（SPA 化后 DOM 选择器失效）；③ UUYP 无签名的 es 接口（随时可能加签）；④ Steam `priceoverview` 的 `median_price` 字符串切片解析（区域/币种相关）；⑤ 固定 `currency=23&country=HK` 组合；⑥ `retrying` 库（停更）；⑦ Chrome 103 固定 UA。接入 CSQuant 前每个 endpoint 都需重新抓包验证。
