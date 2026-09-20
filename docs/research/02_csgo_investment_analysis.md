# csgo_investment 深度代码分析报告

> 分析对象：`csgo_investment-main`（ShevonKuan/csgo_investment，CS 饰品库存管理与投资组合 Dashboard）
> 分析方式：逐文件阅读全部 Python/Go 源码（`app.py` 463 行、`api/__init__.py` 280 行、`crawler/*.go` 332 行），并直接读取两个 SQLite 数据库文件的二进制头部验证表结构。本文所有结论均来自真实代码，非 README 转述。
> 环境说明：分析时终端不可用，未能执行 `sqlite3` 查询行数，数据库表结构已通过读取 `.db` 文件原始字节确认（Schema 以 ASCII 明文存于文件头），行数标记为"待验证"。

---

## 1. 项目定位

这是一个**个人自用级别的 CS 饰品投资组合追踪工具**，核心场景：

- 手工维护一个"库存文件"（pickle），把买入的饰品按 **BUFF goods_id + 买入价** 录入；
- 打开 Dashboard 时逐个饰品**实时**抓 BUFF 最低在售价格与悠悠有品（UUYP）价格，计算浮盈浮亏；
- 支持饰品的四种状态流转：**在库 / 观望（cost=0）/ 租出 / 卖出**，统计总投资额、库存价值、总套现、盈利与收益率；
- 曾支持租赁收益分析（租售比、年化租金比例），因 UUYP 接口鉴权化而半残（README 明确说明租赁数据均为错误占位值）。

它不是行情采集系统，没有历史数据、没有定时任务、没有多用户；本质是"**手工台账 + 实时估值器**"。对 CSQuant 而言，其价值在于**投资组合指标体系设计**与 **Dashboard 交互模式**，而非数据采集能力。

## 2. 技术栈

| 层 | 技术 | 出处 |
|---|---|---|
| 前端/Dashboard | Streamlit 1.15.0（单页应用） | `requirements.txt` |
| 表格组件 | streamlit-aggrid 0.3.3（可编辑、多选、侧边栏） | `app.py` |
| 图表 | pyecharts 1.9.1 + streamlit-echarts 0.4.0（Pie×2、Bar×1） | `app.py` |
| 数据处理 | pandas 1.5.1 | `app.py` |
| 行情抓取（运行时） | requests 2.28.1 直连 BUFF / UUYP JSON API | `api/__init__.py` |
| 库存持久化 | **pickle**（序列化整个 `Goods` 对象字典） | `api/__init__.py` |
| ID 映射库构建 | Go 1.20：`net/http` + `tidwall/gjson` + `mattn/go-sqlite3`，产物为 SQLite | `crawler/go.mod` |
| 部署 | Docker（python:3.9.15-slim-bullseye，VOLUME 挂 data 目录，8501 端口） | `Dockerfile` |

## 3. 目录结构

```
csgo_investment-main/
├── app.py                  # Streamlit 主程序：全部 UI 与交互逻辑（463 行）
├── api/
│   └── __init__.py         # 领域模型：Goods（饰品）+ Inventory（库存），含行情抓取（280 行）
├── crawler/                # Go 写的"名称→平台ID"映射库爬虫（与运行时解耦）
│   ├── main.go             # 入口：翻页爬 UUYP 模板列表（BUFF 爬虫已被注释）
│   ├── buffId.go           # BUFF 商品列表爬虫 + buffDB 写入
│   ├── youpinId.go         # UUYP 模板列表爬虫 + youpinDB 写入 + 建表 SQL
│   ├── go.mod / go.sum
│   ├── buffDB.db           # BUFF 全量商品 ID 映射（约 3.5MB，行数待验证）
│   └── youpinDB.db         # UUYP 全量模板 ID 映射（约 1.4MB，行数待验证）
├── data/
│   └── PUT YOUR pkl FILE HERE   # 占位文件，用户库存 pkl 存放处
├── pictures/               # README 截图（5 张 jpg，内容未分析）
├── demo.gif                # 演示动画
├── requirements.txt / Dockerfile / README.md / README.html / LICENSE
```

注意：`Dockerfile` 只 COPY 了 `api/` 和 `app.py`，**没有 COPY `crawler/`**，但 `api/__init__.py` 运行时要读 `./crawler/youpinDB.db`——官方 Docker 镜像的 UUYP 查询功能实际上是坏的（除非镜像层里另有处理），这是一个部署缺陷。

## 4. 核心模块

### 4.1 `api/__init__.py` — 领域模型（全项目唯一真正的"模块"）

**`Goods` 类（单个饰品）**，字段即数据模型（`__init__`，第 9–29 行）：

```python
self.index = 0            # 库存编号（= 加入时 inventory 长度）
self.id = goods_id        # BUFF goods_id，录入的唯一入口
self.youpin_id = 0        # UUYP 模板 ID（实际是 sqlite 查询结果元组，见 6.3）
self.name = ''            # 饰品名（来自 BUFF，用作关联 UUYP 的 key）
self.cost = cost          # 买入花费（手工输入，0 表示观望）
self.price = 0            # BUFF 当前最低在售价
self.steam_price = 0      # Steam 市场价（BUFF 接口返回的 steam_price_cny）
self.status = 0           # 0:在库 1:租出 2:卖出
self.on_sale_count / on_lease_count / lease_unit_price /
long_lease_unit_price / deposit   # UUYP 租赁字段（现已全部硬编码为 1）
self.youpin_price = 0     # UUYP 当前价
self.sell_price = 0       # 卖出成交价（手工输入）
```

关键方法：

- `__get_buff()`（第 31–46 行）：`GET https://buff.163.com/api/market/goods/sell_order?game=csgo&goods_id=<id>`，取 `data.items[0].price`（**最低在售价**）作为现价，`goods_infos[id].name` 与 `steam_price_cny` 作为名称与 Steam 价。无任何重试、限流、异常处理。
- `__get_youpin()`（第 48–83 行）：先用 `self.name` 在 `./crawler/youpinDB.db` 的 `youpinItem` 表里查 UUYP 模板 ID，再 POST `api.youpin898.com/api/homepage/es/commodity/GetCsGoPagedList`（`templateId` 精确匹配）取 `CommodityList[0].Price`。**租赁相关 5 个字段全部被注释掉、硬编码为 1**（README 已知 bug）。
- `refresh()` = 重新执行上述两个请求。
- `sell(price) / lease() / back()`：状态机流转。
- `__call__()`（第 109–171 行）：返回 20 余列派生指标字典（租售比、租金比例、年化短/长租比例、套现比例、Buff/有品价格比、理论收益与收益率），观望饰品（cost=0）与已购饰品返回两套不同列。

**`Inventory` 类（库存集合）**（第 174–274 行）：本质是一个 `{index: Goods}` 字典 + pickle 持久化 + 一组 `sum()` 聚合方法（详见第 6、9 节）。

### 4.2 `app.py` — 单页 Dashboard（详见第 6.4、9 节）

全部 UI 在一个 `main()` 里：侧边栏负责库存文件打开/保存与添加饰品；主区自上而下为「投资信息 metric 矩阵 → 资金组成饼图 → 追踪列表（可编辑 AgGrid）→ 已购列表（指标 AgGrid）→ 理论收益横向条形图 → 观望列表」。

### 4.3 `crawler/*.go` — ID 映射库构建器（一次性工具）

- `main.go`：`for i := 100; i < 300; i++` 翻页爬 UUYP 模板列表写入 `youpinDB.db`；每页一个事务批量 `INSERT OR IGNORE`，页间隔 sleep 1s，空页重试并 sleep 20s；BUFF 爬虫 goroutine 被整段注释（第 36–54 行）。
- `buffId.go`：`GET buff.163.com/api/market/goods?game=csgo&page_num=N`，gjson 解析 `data.items` 取 `{id, name, market_hash_name}`。
- `youpinId.go`：`POST api.youpin898.com/api/homepage/es/template/GetCsGoPagedList`（`listType:10, gameId:730, pageSize:40`），解析 `Data` 数组取 `{Id, CommodityName, CommodityHashName, IconUrlLarge}`；建表 SQL 也定义在此文件。

**重要代码气味**：`buffId.go` 的 `init()` 复用了 `youpinId.go` 里的包级变量 `schemaSQL`/`insertSQL`，导致 **`buffDB.db` 里的表也叫 `youpinItem`**（已通过二进制头部确认，见第 8 节）。爬 BUFF 数据却建 UUYP 表名，是明显的复制粘贴遗留。

## 5. 主数据流

```
用户手工输入 BUFF goods_id + 买入价
        │
        ▼
Goods.__init__ ──GET BUFF sell_order──► name / price(最低在售) / steam_price
        │
        ▼ 以 name 为 key
SELECT Id FROM youpinDB.db.youpinItem WHERE CommodityName = name
        │
        ▼ templateId
POST UUYP GetCsGoPagedList ──► youpin_price（租赁字段已废弃=1）
        │
        ▼
Goods 对象存入 Inventory.__data{index: Goods}
        │
        ├── 手动"保存库存"──► pickle.dump 整个对象字典到 ./data/data.pkl
        └── 每次打开库存──► 逐个 Goods.refresh() 串行刷价（带进度条）
                                    │
                                    ▼
        Inventory 聚合方法（total_cost / calc_price / sell_price ...）
                                    │
                                    ▼
        app.py：metric 矩阵 + Pie（资金组成）+ AgGrid（列表）+ Bar（收益分析）
```

特点：**无离线行情库、无快照、无历史**。价格只活在 Goods 对象内存里，刷新即覆盖；pkl 里保存的 price 是上次保存时的旧值，打开时全量重刷。库存 N 件饰品 = 打开时 2N 次串行 HTTP 请求，无缓存、无并发。

## 6. 数据模型

### 6.1 库存数据结构（专项问题 1）

库存 = **pickle 序列化的 `dict[int, Goods]`**：

```python
# api/__init__.py 第 177-202 行
class Inventory:
    def __init__(self, path):
        self.path = path
        if os.path.exists(path):
            self.__data = pickle.load(open(path, "rb"))
        else:
            self.__data = {}

    def add(self, good: Goods):
        good.index = len(self())          # 以当前长度当主键
        self.__data[good.index] = good

    def save(self):
        pickle.dump(self.__data, open(self.path, "wb"))
```

两个结构性缺陷：

1. `add` 用 `len()` 生成主键，**删除中间元素后再添加会发生主键覆盖**（`delete` 是 `del self()[good]`，长度回缩）。
2. pickle 直接序列化自定义类对象，**类定义一旦变更旧库存文件即报废**，且 pickle 反序列化本身有安全隐患。

### 6.2 买入成本与当前价格的关联（专项问题 2）

关联完全建立在**单个 Goods 对象的字段并置**上，没有任何外部价格表：

- `cost`：用户添加饰品时手工输入（`app.py` 第 101 行 `cost = eval(st.text_input("请输入购买价格..."))`），**一个饰品只有一条成本记录，不支持分批建仓/多数量**；
- `price` / `youpin_price` / `steam_price`：每次 `refresh()` 时从行情接口现取现覆；
- 跨平台关联键是**饰品中文名**：BUFF 接口给出 `name` → 本地 `youpinDB.db` 反查 UUYP 模板 ID。名称即外键，脆弱但简单有效（BUFF/UUYP 中文名基本一致）。

### 6.3 数据库表结构（详见第 8 节）

`youpinItem(Id, CommodityName, CommodityHashName, IconUrlLarge)`——纯静态映射表，无价格、无时间戳。运行时只读不写。

### 6.4 Portfolio 页面组织（专项问题 4）

`app.py` 单页结构（`st.set_page_config(layout="wide")`）：

| 区块 | 实现 | 内容 |
|---|---|---|
| 侧边栏 | `st.sidebar` + `st.form` | 库存路径输入（默认 `./data/data.pkl`）、打开/保存按钮、添加饰品表单（BUFF 代码 + 购买价） |
| 投资信息 | 3 行 × `st.columns(4)` 的 `st.metric`（第 116–175 行） | 总投资额 / 追踪总量 / 库存价值(Buff计) / 总套现 / 盈利与总收益率（Buff 计、UUYP 计两套）/ 持有饰品收益与收益率（两套） |
| 目前资金组成 | 2 个 pyecharts `Pie`（第 216–248 行） | 图1「库存资金组成」：出租 vs 在库（按 status 分组求和 price）；图2「盈利资金组成」：库存增值 vs 卖出收益 |
| 追踪列表 | 可编辑 AgGrid（第 254–313 行） | 全部饰品的编号/名称/状态/成本/卖出价；「购入花费」「卖出价格」两列可双击编辑；多选后出现删除/出售/租出/回仓四个按钮 |
| 已购列表 | 只读 AgGrid（第 319–369 行） | 22 列派生指标；「理论收益/收益率」两列用 JsCode 按正负着红绿色（A 股配色：红涨绿跌） |
| 理论收益分析 | pyecharts `Bar` + `reversal_axis()` + 三向 DataZoom（第 370–402 行） | 按收益率排序的横向条形图，收益率与收益额双系列 |
| 观望列表 | 只读 AgGrid（第 408–450 行） | cost=0 饰品的 19 列行情指标 |

## 7. API / 外部依赖

| 依赖 | 端点 | 用途 | 现状 |
|---|---|---|---|
| BUFF 行情 | `GET buff.163.com/api/market/goods/sell_order?game=csgo&goods_id=` | 现价（最低在售）、名称、Steam 价 | 无鉴权直连（2022 年可用）；现今该接口有风控/登录要求，**大概率失效或极不稳定** |
| BUFF 列表（爬虫） | `GET buff.163.com/api/market/goods?game=csgo&page_num=` | 全量商品 ID 映射 | 代码注释里写明需要登录 cookie（`buffId.go` 第 89 行），现已被作者弃用（main.go 中注释掉） |
| UUYP 模板列表（爬虫） | `POST api.youpin898.com/api/homepage/es/template/GetCsGoPagedList` | 全量模板 ID 映射 | 无鉴权 POST，当时可用 |
| UUYP 商品行情 | `POST api.youpin898.com/api/homepage/es/commodity/GetCsGoPagedList` | UUYP 现价、在售/在租/租金/押金 | README 明确：租赁字段需鉴权（10 天 token），作者弃坑；代码中租赁 5 字段已硬编码为 1 |
| Steam 价格 | 不直连 Steam，复用 BUFF 返回的 `steam_price_cny` | 套现比例计算 | 随 BUFF 接口失效而失效 |

Python 依赖全部钉死在 2022 年版本（streamlit 1.15、pandas 1.5.1、protobuf 3.20.1 等），其中 `streamlit-aggrid 0.3.3` 的 `grid["selected_rows"]` 返回格式与新版不兼容，升级需改代码。

## 8. 数据库

两个 SQLite 文件均为**静态"名称→平台 ID"映射库**，由 Go 爬虫一次性构建，运行时只读：

```sql
-- crawler/youpinId.go 第 16-24 行（schemaSQL）
CREATE TABLE IF NOT EXISTS youpinItem (
    Id  VARCHAR(32) PRIMARY KEY,
    CommodityName  VARCHAR(32),
    CommodityHashName  VARCHAR(32),
    IconUrlLarge  VARCHAR(32)
);
-- 写入：INSERT OR IGNORE（按 Id 主键去重）
```

实测验证（直接读取 `.db` 文件头部字节，sqlite3 命令行/终端不可用，行数**待验证**）：

- `youpinDB.db`：含表 `youpinItem`，Schema 与上方 SQL 完全一致，并有主键自动索引 `sqlite_autoindex_youpinItem_1`，文件含数据（约 1.4MB）。
- `buffDB.db`：**表名同样是 `youpinItem`**（因 `buffId.go` 复用了 `youpinId.go` 的包级 `schemaSQL`），存的是 BUFF 的 `{goods_id, name, market_hash_name, ""}`，约 3.5MB。也就是说"buffDB"是个名不副实的文件。

运行期唯一 SQL（`api/__init__.py` 第 50–53 行）：

```python
conn = sqlite3.connect('./crawler/youpinDB.db')
cursor.execute("SELECT Id FROM youpinItem WHERE CommodityName=?", (self.name,))
```

**注意运行时从不查询 `buffDB.db`**——BUFF 的 ID 是用户手工输入的，`buffDB.db` 实际上是构建 youpinDB 的副产物/备用。

## 9. 核心算法（专项问题 3）

全部聚合算法在 `Inventory` 类，都是字典推导式求和（`api/__init__.py` 第 207–274 行）：

**总投资额**（含已卖出饰品的成本、含观望饰品的 0）：

```python
def total_cost(self):
    return sum([self()[good].cost for good in self()])
```

**库存价值（Buff 计，含租出）**：

```python
def calc_price(self):
    return sum([self()[good].price for good in self()
        if (self()[good].status == 0 and self()[good].cost != 0)
        or self()[good].status == 1])
```

**总套现与卖出收益**：

```python
def sell_price(self):   # 已卖出饰品的卖出总收入
    return sum([self()[good].sell_price for good in self() if self()[good].status == 2])
def sell_earn(self):    # 卖出净收益 = 卖出收入 - 已卖出饰品成本
    return sum(...sell_price...) - sum(...cost...)
```

**盈利与总收益率**（`app.py` 第 133–144 行）：

```python
earn = inventory.calc_price() + inventory.sell_price() - inventory.total_cost()
# 即：盈利 = 库存现值 + 卖出回款 - 历史总投入
总收益率 = earn / total_cost * 100
```

**持有饰品收益（浮盈）**：`calc_price() - total_cost_in_inventory()`，分母为在库+租出饰品的成本（第 210–218 行）。

**单饰品指标**（`Goods.__call__`，第 155–170 行）：

```python
TheoreticalCurrentEarnings     = price - cost            # 理论收益
TheoreticalCurrentEarningsRate = (price - cost)/cost*100 # 理论收益率
CashRatio = price / steam_price * 100                    # 套现比例（≈挂刀比例的变形）
BuffYouyouRatio = price / youpin_price                   # 跨平台价差比
AnnualizedShortTermLeaseRatio = 192 * lease_unit_price / price * 100  # 年化短租
AnnualizedLongTermLeaseRatio  = 264 * long_lease_unit_price / price * 100
```

（年化系数 192/264 是 UUYP 短租/长租的年出租天数经验值，README 第 11–12 行有定义。）

**发现的算法缺陷**：

1. `calc_buff_earn` / `calc_youpin_earn`（第 220–236 行）条件写成了位运算 `cost != 0 & status == 0`。由于 Python 中 `&` 优先级高于比较运算，实际等价于 `cost != 0`，**已卖出饰品（status=2）被错误计入"持有收益"**。目前这两个方法在 UI 中已被注释，影响未暴露，但复用时必须修复。
2. 多处除零保护判断的是 `total_cost()==0`，实际除数却是 `total_cost_in_inventory()`（`app.py` 第 163、173 行），分母用错保护对象，全部饰品卖出后会除零崩溃。
3. 收益全部按"最低在售价"估值，未计 BUFF 2.5% 交易手续费与提现成本，盈利系统性高估。

## 10. 最值得复用的设计

1. **投资组合指标体系**：投入/现值/回款三分解 —— `盈利 = 库存现值 + 卖出回款 − 总投入`，并区分"总收益率（含已实现）"与"持有收益率（纯浮盈）"两个口径；再按 Buff 计 / UUYP 计双平台各算一遍。这套口径可以直接搬进 CSQuant。
2. **饰品状态机**：在库(0)/租出(1)/卖出(2) + 观望(cost=0 的隐式状态)，四态覆盖饰品投资全生命周期，聚合时用 status 过滤。
3. **饰品级衍生指标矩阵**：套现比例（第三方价/Steam 价）、Buff/有品价格比、租售比、年化租金比例——与 CSQuant 的量化因子思路天然契合。
4. **Dashboard 布局范式**：metric 矩阵（顶部 KPI）→ 饼图（资金结构）→ 可编辑主表（台账）→ 只读指标表（分析）→ 排序条形图（横向对比），是投资台账类页面的成熟模板。
5. **红涨绿跌的单元格着色 JsCode**（`app.py` 第 67–83 行）与收益列 pinned 列配置，细节体验好。
6. **Go 构建静态映射库、Python 运行时只读** 的离线字典思路（构建期与运行期解耦）——思路正确，但 CSQuant 应直接用社区维护的 SteamTradingSite-ID-Mapper JSON 替代自建爬虫。

## 11. 可直接复用的代码

| 代码 | 位置 | 复用方式 |
|---|---|---|
| 盈利/收益率聚合公式组 | `api/__init__.py` 第 207–274 行 | 公式直接移植（换成 dataclass + 数据库查询），注意修掉第 9 节的 `&` bug |
| 单饰品派生指标计算 | `api/__init__.py` 第 109–171 行 `__call__` | 指标字典结构可作 CSQuant 因子计算输出 schema 参考 |
| 资金组成双饼图 | `app.py` 第 216–248 行 | pyecharts Pie 配置原样可用 |
| 收益率排序横向条形图 + 三向 DataZoom | `app.py` 第 370–402 行 | 原样可用 |
| 盈亏红绿着色 JsCode | `app.py` 第 67–83 行 | 原样可用 |
| 可编辑台账 AgGrid 配置（可编辑列+多选+批量操作按钮） | `app.py` 第 269–313 行 | 模式可搬（升级 aggrid 版本后需适配 selected_rows API） |
| Go SQLite 批量事务写入模式（prepare + tx + INSERT OR IGNORE + 分页 sleep） | `crawler/youpinId.go` 第 40–86 行 | 写 CSQuant 落库工具时的参考模板 |

## 12. 只能借鉴的部分

- **Goods/Inventory 的 OOP 结构**：思路可借鉴，实现不可直接用——pickle 持久化、`len()` 当主键、`__call__` 返回大字典代替显式 schema、私有方法名混淆（`__get_buff` 是 name mangling 而非约定），都需要重写为 dataclass/pydantic + 显式存储层。
- **UUYP 名称→ID 反查模式**：思路（本地映射表避免全量搜索）正确，但 `self.youpin_id = result` 存的是 sqlite Row 元组而非 ID 本身（第 75 行，另一处小 bug），且按中文名精确匹配无法处理改名/繁简差异。CSQuant 应以 `market_hash_name` 为主键做映射。
- **刷新流程的进度条 UX**（`app.py` 第 44–58 行）：模式可借鉴，但串行 2N 次请求的实现必须改为异步/批量。

## 13. 已过时/不适用部分（专项问题 6）

1. **BUFF `sell_order` 无鉴权直连**：2022 年后 BUFF 加强了风控，该接口如今需要登录 cookie 且频控严格，按现状裸奔基本不可用；即使可用，`items[0].price` 只取最低在售，无深度、无求购价。
2. **UUYP 租赁数据链路已死**（README 自认）：`on_sale_count / on_lease_count / lease_unit_price / long_lease_unit_price / deposit` 五个字段硬编码为 1，导致租售比、租金比例、年化租金、押金比例 8 个派生指标全部输出垃圾值（1/1、1/price…），前端却照常展示——**误导性 UI**。
3. **Go 爬虫整体过时**：BUFF 爬虫被作者自己注释弃用；UUYP 模板列表接口的分页参数（`listType:10`）与字段结构时隔两年大概率已变；且无代理、无重试、无 UA 池，不适合 2026 年的反爬环境。两个 .db 映射库数据停留在 2023 年前后，新饰品缺失。
4. **依赖版本全面过期**：streamlit 1.15（现 1.3x+）、pandas 1.5、protobuf 3.20.1；aggrid 0.3.3 API 不兼容新版。
5. **官方 Docker 镜像缺 `crawler/` 目录**（见第 3 节），UUYP 功能在容器内必然报错。
6. **CSGO 已更名 CS2**：接口里的 `game=csgo`、`gameId:730` 中 appid 仍有效，但 BUFF 的 game 参数现已用 `cs2`。

## 14. 对 CSQuant 的具体价值

- **提供了一套经过实战的饰品投资 KPI 口径**（双平台计价的总收益/持有收益、套现比例、年化租金），CSQuant 不必从零设计指标，直接修正后采纳。
- **提供了一个 280 行的"最小投资台账"参考实现**，界定了 CSQuant 组合模块的最小功能集：录入（id+成本）、状态流转、估值刷新、聚合报表。
- **反例价值同样大**：pickle 持久化、手工输入 ID、串行刷新、无历史数据——CSQuant 的数据层设计可以对照这张"错误清单"逐一规避。
- 其衍生指标（套现比例、Buff/有品价格比）本质就是**跨平台价差因子**，可直接进入 CSQuant 的因子库。

## 15. 推荐集成方式（专项问题 7、8、9）

**（7）替换数据源为 SteamDT / CSQAQ：**

- 用 SteamDT API（`market_hash_name` 为键返回多平台价格）替换 `__get_buff`/`__get_youpin` 两个方法。映射关系：BUFF 价→`price.buff`、UUYP 价→`price.uuyp`、Steam 价→`price.steam`；CSQAQ 作备份与历史数据源。
- 主键从"手工输入 BUFF id"改为 `market_hash_name`（配合工作区已有的 `SteamTradingSite-ID-Mapper-main` 的 `buff/730.json`、`uuyp/730.json` 做平台 ID 反查，比本项目自爬的 .db 更新、更全）。
- 废弃全部租赁指标或标记为"UUYP 鉴权后恢复"。

**（8）手工库存 → Steam 库存自动同步：**

- 通过 Steam Web API `IEconItems_730/GetPlayerItems` 或库存页 JSON（`steamcommunity.com/inventory/{steamid64}/730/2`，需 Steam 登录 cookie 或公开库存）定期拉取 `market_hash_name + amount`；
- 同步逻辑：Steam 库存与本地持仓对账——新出现→标记"待补成本"（用户补录买入价）；消失→标记"疑似卖出"待确认；实现 `status` 状态机的自动迁移。
- 保留手工补录通道：成本、卖出价永远需要人工确认（Steam 不返回人民币成交价）。

**（9）库存历史净值与收益曲线：**

- 本项目最大缺口即"只有现值没有历史"。CSQuant 应加一张快照表：
  `portfolio_snapshot(date, total_cost, market_value, sell_proceeds, profit, profit_rate)`，
  每日定时任务（复用 SteamTradingSiteTracker 的调度思路）对全部持仓刷价后落一行；
- 另建 `price_history(market_hash_name, platform, date, price)` 明细表（CSQAQ 提供历史价接口，可回填）；
- 前端用 pyecharts `Line` 替换一个 Pie 的位置，画净值曲线与累计收益率曲线，并与大盘指数（如 CSQAQ 大盘指数）叠加对比。

**架构落位建议**：`api/__init__.py` 拆为 `models.py`（dataclass）+ `datasource.py`（SteamDT/CSQAQ 客户端，带限流重试）+ `portfolio.py`（纯函数聚合）；pickle 换 SQLite（`holdings` 表）；Streamlit 层保留本项目页面骨架。

## 16. 风险和注意事项

1. **安全红线：`eval()` 直接作用于用户输入与接口返回**（`app.py` 第 25、101 行；`api/__init__.py` 第 39、41、82 行）。BUFF 返回的价格字符串、用户输入的成本都过 `eval`，配合 pickle 反序列化，是典型的代码注入面。任何复用必须替换为 `float()`。
2. **pickle 库存文件不可跨版本兼容**，且不能放任何不可信来源的 pkl 文件；CSQuant 必须用 SQLite/Parquet 替代。
3. **算法 bug 复用风险**：`calc_buff_earn` 的 `&` 优先级 bug、除零保护对象错误（第 9 节），照抄即踩雷。
4. **租赁指标全是占位垃圾值**（=1），界面却不标注，若照搬 UI 会误导用户决策。
5. **BUFF/UUYP 接口均有合规与账号风险**：高频抓取会触发风控甚至封号；SteamDT/CSQAQ 为付费 API，需评估配额成本。
6. **估值口径偏乐观**：按最低在售价估值、不计 2.5% 手续费与提现损耗、不计 Steam 市场 15% 手续费（套现比例未扣除），实际到手收益低于显示值。
7. **库存与行情耦合在一次请求生命周期内**：页面每次打开都全量串行刷价，饰品一多（>50 件）打开耗时不可接受，且刷新中任意一个接口异常（如 UUYP 改名导致查不到 ID，`result[0]` 直接 TypeError）整个添加/打开流程失败。
8. 数据库行数与内容时效**待验证**（终端不可用未能执行 sqlite3 查询）；建议用 DB Browser 打开确认记录数与最新饰品年份后再决定是否还有残余价值。

---

## 附：9 个专项问题速查

| # | 问题 | 结论定位 |
|---|---|---|
| 1 | 库存数据结构 | pickle 序列化的 `dict[int, Goods]`，Goods 17 个字段 → 第 6.1 节 |
| 2 | 成本与现价关联 | 同对象字段并置：`cost` 手工录入，`price` 刷新覆盖；中文名作跨平台外键 → 第 6.2 节 |
| 3 | 盈亏收益率计算 | 盈利=现值+回款−总投入；收益率=盈利/总投入 → 第 9 节 |
| 4 | Portfolio 页面组织 | 侧边栏台账 + metric 矩阵 + 双饼图 + 可编辑 AgGrid + 排序 Bar + 观望表 → 第 6.4 节 |
| 5 | 值得复用的 UI/模型 | 指标体系、状态机、页面布局范式、着色/图表配置 → 第 10、11 节 |
| 6 | 不适合继续用的旧接口 | BUFF sell_order 裸连、UUYP 租赁链路、Go 爬虫、旧依赖 → 第 13 节 |
| 7 | 换 SteamDT/CSQAQ | 以 market_hash_name 为主键重写 datasource 层 → 第 15 节 |
| 8 | Steam 库存自动同步 | IEconItems_730/库存 JSON 对账 + 人工补成本 → 第 15 节 |
| 9 | 历史净值曲线 | 每日快照表 + 价格历史表 + Line 图 → 第 15 节 |
