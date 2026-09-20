# CS2/CSGO 饰品量化系统数据源 Web 调研笔记

> 调研时间：2026-07-21。调研方式：WebSearch + WebFetch 抓取官方文档原文。
> 所有接口字段均来自官方文档页面原文；社区信息均标注可信度；未能确认的标注「待验证」。

---

## 1. SteamDT（https://www.steamdt.com/）

### 1.1 结论：有官方开放 API

- 开放平台文档：https://doc.steamdt.com/ （Apifox 托管，接口权限列表页显示「修改于 2026-06-08」，仍在活跃维护）
- 服务地址：`https://open.steamdt.com`（正式环境，国内外均可）
- 注册 SteamDT 账号后，在「个人中心 → API 管理」申请 API_KEY，文档原文为「目前限时免费」。

### 1.2 认证方式

- 请求头：`Authorization: Bearer {YOU_API_KEY}`（文档原文："公共请求参数…目前为 Header 中的 Authorization，内容格式为 Bearer {YOU_API_KEY}"）
- 签名机制：无。
- 统一返回结构：`success / data / errorCode / errorMsg / errorData / errorCodeStr`。

### 1.3 接口清单与限频（来自「接口权限列表」官方页，2026-06-08 更新）

| 接口 | 说明 | 官方限频 |
|---|---|---|
| `GET /open/cs2/v1/base` | 获取 Steam 饰品基础信息（全量 marketHashName 映射） | 每日 1 次（文档强调需自行保存） |
| `GET /open/cs2/v1/price/single` | 通过 marketHashName 查询单品全平台价格 | 每分钟 60 次 |
| `POST /open/cs2/v1/price/batch` | 批量查询价格（Body：`marketHashNames` 数组，1–100 个） | 每分钟 1 次 |
| `POST /open/cs2/item/v1/kline` | 单品 K 线（不含成交数据） | 每分钟 120 次 |
| `POST /open/cs2/v1/wear`、`POST /open/cs2/v2/wear` | 检视链接 / ASMD 参数查磨损 | 每小时 36000 次 |
| `POST /open/cs2/v1/inspect`、`POST /open/cs2/v2/inspect` | 生成检视图 | 每日 100 次 |

以下接口存在于官方文档目录，但未出现在权限列表页，限频「待验证」：

- `GET /open/cs2/broad/v1/index`（查询大盘最新指数数据）
- `POST 查询大盘k线数据`（CS2 大盘数据目录下，doc id 450452402e0，路径未抓取，待验证）
- `通过 MarketHashName 查询所有平台近7天均价`（doc id 319748133e0，路径未抓取，待验证）

### 1.4 可获得字段（官方文档原文）

- **基础信息 base**：`name`（饰品名）、`marketHashName`、`platformList[{name, itemId}]`（各平台饰品 id 映射）。
- **单品/批量价格**：按平台数组返回 `platform`（平台）、`platformItemId`、`sellPrice`（在售价）、`sellCount`（在售数量）、`biddingPrice`（求购价）、`biddingCount`（求购数量）、`updateTime`。
- **K 线**：POST Body `marketHashName`、`type`（整数 1–3，含义文档未写明，待验证）、`platform`（`ALL/BUFF/YOUPIN/C5/STEAM/HALOSKINS`，默认 ALL）、`specialStyle`（特殊款式，可选）。返回二维数组：`[更新时间, 开盘, 收盘, 最高, 最低]`，**明确不含成交量**。
- **大盘指数 index**：`broadMarketIndex`（大盘指数）、`updateTime`、`diffYesterday`（较昨日涨跌值）、`diffYesterdayRatio`（涨跌幅）、`historyMarketIndexList[[时间, 指数]]`（历史记录）。

### 1.5 平台覆盖 / 实时性 / 历史数据

- 覆盖平台：BUFF、悠悠有品（YOUPIN）、Steam、C5、HALOSKINS（皮肤熊）（来自 kline 接口 platform 枚举）。价格接口为「全平台」，具体平台列表以 base 接口 platformList 为准。
- 实时性：文档未写明采集频率，「待验证」；价格带 `updateTime` 字段可判断新鲜度。
- 历史数据：K 线 + 7 天均价 + 大盘历史指数。K 线历史长度文档未说明，「待验证」。
- 数据更新频率：未公开说明，「待验证」。

### 1.6 成本

- 文档原文「目前限时免费」，意味着后续可能转付费；未见公开付费档位表（2026-07 时点）。

### 1.7 稳定性与风险

- 开放平台为新上线服务（社区 MCP 封装项目 zkcaryq/steamdt-mcp 于 2025 年出现），接口仍可能迭代；「限时免费」政策存在转付费或调整限频的风险。
- 单日全量 base 接口只能调 1 次，必须落库，否则当日无法重建映射。
- 无成交量字段（K 线明确不含成交数据），量化策略若需要成交量需配合 CSQAQ 或 Steam 官方。

### 1.8 Fallback 建议

- 价格/求购兜底：CSQAQ（免费额度 1 次/秒）。
- 大盘指数兜底：CSQAQ `current_data` 指数接口。

### 1.9 信息来源

- https://doc.steamdt.com/6279815m0 （一分钟接入：认证、API_KEY 申请、限时免费、无签名）
- https://doc.steamdt.com/6369437m0 （接口权限列表，2026-06-08 更新）
- https://doc.steamdt.com/278832832e0 （base 接口字段）
- https://doc.steamdt.com/278832830e0 （price/single 字段）
- https://doc.steamdt.com/278832831e0 （price/batch：1–100 个/次）
- https://doc.steamdt.com/428124801e0 （kline 参数与平台枚举、不含成交数据）
- https://doc.steamdt.com/450452403e0 （大盘指数字段）
- https://doc.steamdt.com/llms.txt （完整接口目录，含大盘 K 线、7 天均价）
- https://glama.ai/mcp/servers/zkcaryq/steamdt-mcp （社区 MCP 封装，佐证平台活跃度）

---

## 2. CSQAQ（https://csqaq.com/）

### 2.1 结论：有官方数据开放 API

- 文档站：https://docs.csqaq.com/ （Apifox 托管，含 llms.txt 完整接口目录，2025–2026 持续更新）
- 服务地址：`https://api.csqaq.com/api/v1`（正式环境，国内外）
- 注册 CSQAQ 网站用户 → 点击用户头像获取 ApiToken → 免费使用（项目引言原文：「通过 CSQAQ 网站进行注册即可获取 API_TOKEN 免费使用本网站数据 API」）。
- 声明：数据「仅供参考和学习交流，严禁用于商业用途」；商业合作/高频需求需联系站长（QQ）。

### 2.2 认证方式

- 「令牌 + IP 绑定」双重验证：HTTP Header `ApiToken: {YOUR_API_TOKEN}`；并需为 Token 绑定白名单 IP（自动获取 / 手动绑定 / 调用「绑定本机白名单 IP」接口 api-342090738，适用于非固定 IP）。
- 状态码：400 用户不存在或 Token 未通过；401 Token 未通过；429 请求过多；503 网关异常或频繁。

### 2.3 限频与费用

- 官方接入指南原文：「请求次数：不限次；单 IP 请求频率：1 次/秒」。
- 免费档：注册即得，覆盖大部分非「企业合作用户接口」的接口。
- 企业档（付费/合作，价格未公开，需联系站长）：全量数据类接口。

### 2.4 接口清单（来自官方 llms.txt 目录，按权限分组）

**免费接口（ApiToken 即可）：**

| 接口 | 路径 / 文档 | 说明 |
|---|---|---|
| 获取首页相关数据 | `GET /api/v1/current_data?type=init/hours/kline/lease`（api-187131779） | 指数、涨跌分布、在线人数、市场情绪、饰品异动、美金卡价；文档标注该接口不需要 ApiToken |
| 获取指数详情数据 | api-230764015 | 指数今日涨跌、图表 |
| 获取指数 K 线图 | api-278085071 | 大盘指数 K 线 |
| 获取饰品的 ID 信息 | `POST /api/v1/info/get_good_id`（api-187131777） | good_id ↔ 中英文名 ↔ market_hash_name 映射，分页（page_size ≤ 500），支持模糊搜索 |
| 联想查询饰品 ID | api-189290586 | 名称联想 |
| 获取单件饰品详情 | `GET /api/v1/info/good?id={good_id}`（api-187131780） | 见 2.5 字段表 |
| 获取单件饰品图表数据 | api-187131781 | 多平台多周期图表：BUFF/悠悠/Steam/C5 的出售价、求购价、短/长租租金、收益率、在售/求购/在租数量、日成交量、租赁过户价 |
| 批量获取饰品出售价格 | api-283470032 | 按 marketHashName 批量 |
| 获取单件饰品存世量走势 | api-366480669 | 近 180 天 |
| 获取饰品列表信息 | `POST /api/v1/info/get_page_list`（api-187131775） | 全站分页列表 + 类型/品质/类别/磨损筛选 |
| 获取排行榜单信息 | api-187131776 | 价格、租赁、挂刀套现、成交量、存世量、总市值、在售/求购/在租数量等指标排行 |
| 热门系列列表/详情 | api-187131803 / api-187131804 | 热门系列 |
| 挂刀行情详情 | api-187131823 | 平台↔Steam 余额兑换行情 |
| 库存监控系列 | api-187131809/810/813/814/815、api-358158458、api-343919624 | 库存变动动态、任务列表、持有量排行、单用户全部库存、库存快照 |
| 武器箱数据系列 | api-187131788、api-294405260、api-327163706、api-187131799、api-187131826、api-187131825 | 开箱数量、回报率、收藏品包含物 |

**企业合作用户接口（需联系站长）：** 获取全量站内饰品 ID（api-337690892）、获取全量饰品价格数据（api-327138094）、获取全量饰品排行榜数据（api-337683199）、获取单件饰品 K 线数据（api-278065737，BUFF/悠悠，1hour/4hour/1day/7day，字段 `t/o/c/h/l/v` 含成交量，每次返回 150 条需自行分页回溯）、获取全量饰品热度排名（api-327157635）、获取饰品模板数据（api-366480732）。

**已暂停：** 实时成交数据系列（api-187131821/822）、Banana 数据系列。

### 2.5 单件饰品详情字段（api-187131780，官方文档原文，节选）

- 平台 ID 映射：`buff_id`、`yyyp_id`、`c5_id`、`igxe_id`、`eco_id/eco_sku_id`、`market_hash_name`。
- BUFF：`buff_sell_price/buff_buy_price/buff_sell_num/buff_buy_num`。
- 悠悠有品：`yyyp_sell_price/yyyp_buy_price/yyyp_sell_num/yyyp_buy_num/yyyp_lease_price(短租)/yyyp_long_lease_price(长租)/yyyp_lease_num/yyyp_transfer_price(过户底价)/yyyp_lease_annual/yyyp_long_lease_annual(年化)/yyyp_steam_price`。
- Steam：`steam_sell_price/steam_sell_num/steam_buy_price/steam_buy_num`、`turnover_number`(steam 成交量)、`turnover_avg_price`($)、`period_at`。
- C5/IGXE/ECO：各自在售价/量、求购价/量、租赁价等（`c5_*`、`igxe_*`、`eco_*`）。
- 衍生指标：`steam_buff_buy_conversion`（Steam 求购挂刀）、`steam_buff_sell_conversion`、`buff_steam_buy_conversion`（BUFF 求购套现）、`buff_steam_sell_conversion`。
- 基本面：`statistic`（存世量）、`min_float/max_float`、`def_index/paint_index`、品质/类别/磨损/大类、`rank_num`（热度排名）。
- 涨跌：`sell_price_rate_1/7/30/180`（%）、`sell_price_1/7/30/180`（量）。

### 2.6 实时性（官方「数据说明」页原文）

- 整站饰品数据：**5–15 分钟全量更新**；首页指数随饰品数据实时更新；涨跌分布每 2 分钟；排行榜 10 分钟；热门系列 3 分钟；存世量 25 分钟；在线人数 5 分钟；挂刀行情 15 分钟。
- 数据范围：**自 2021-07-26 起至今**。
- 指标口径：出售价=各平台寄售底价；求购价=求购最高价；Steam 求购挂刀=平台最低价/(steam 求购价×0.869)。

### 2.7 大盘数据

- 首页指数（日线/时线/K 线/租赁四类 type）、子指数（`sub_index_data`：名称、当前指数、涨跌、开/收/高/低）、涨跌分布（按类型/价格区间）、市场情绪监测（greedy）、在线人数。指数计算公式公开在网站帮助中心。

### 2.8 稳定性与风险

- 个人/小团队运营（站长 QQ 对接企业合作），无 SLA；「严禁商用」条款对量化商业化有合规限制。
- 部分高价值接口（全量、K 线含成交量）仅企业档；免费 K 线（api-187131781 图表）为分段图表数据。
- IP 白名单机制对动态 IP/多机部署不友好（虽有绑定接口）。

### 2.9 Fallback 建议

- 与 SteamDT 互为兜底：两边都做 marketHashName → 平台 ID 映射落库；CSQAQ 断供时切 SteamDT price/batch。

### 2.10 信息来源

- https://docs.csqaq.com/ （项目引言：免费、ApiToken、非商用声明）
- https://docs.csqaq.com/doc-4588854 （接入指南：api.csqaq.com/api/v1、Token+IP 白名单、1 次/秒不限次、状态码）
- https://docs.csqaq.com/doc-4589773 （数据说明：2021-07-26 至今、更新频率表、指标口径）
- https://docs.csqaq.com/api-187131779 （current_data 首页指数/涨跌/在线/情绪/异动）
- https://docs.csqaq.com/api-187131777 （get_good_id ID 映射）
- https://docs.csqaq.com/api-187131780 （单件详情全字段、7 平台覆盖）
- https://docs.csqaq.com/api-187131775 （get_page_list 列表与筛选）
- https://docs.csqaq.com/api-278065737 （企业 K 线：t/o/c/h/l/v、150 条/次）
- https://docs.csqaq.com/api-337683199 、 /api-327138094 （企业全量接口）
- https://docs.csqaq.com/api-187131813 （库存监控持有量排行）
- https://docs.csqaq.com/llms.txt （完整接口目录）

---

## 3. Steam 官方市场相关接口

> 说明：本调研环境直连 steamcommunity.com 失败（网络层超时），以下结论基于官方/半官方文档与社区近期反馈整理，标注可信度。字段描述为长期稳定结构，可信度高；限频数值为社区经验值，可信度中。

### 3.1 `GET /market/priceoverview/`

- URL：`https://steamcommunity.com/market/priceoverview/?appid=730&currency={n}&market_hash_name={name}`（currency=1 USD，23 CNY）。
- 返回：`success`、`lowest_price`、`volume`（24h 成交量）、`median_price`。无求购价。
- 认证：无需登录。
- 限频：Valve 未公开。社区经验：steamcommunity.com 全站约 20–30 次/分钟/IP，超限返回 429，IP 级临时封禁（数分钟至数小时）。2024–2026 年社区反馈 429 明显更易触发。
- 来源：https://steamcommunity.com/discussions/forum/1/601902348018676495/ 、https://dev.doctormckay.com/topic/699-rate-limit-on-steamcommunitycom/ 、https://www.steamwebapi.com/blog/steam-market-listing-too-many-requests-429

### 3.2 `GET /market/pricehistory/`

- URL：`https://steamcommunity.com/market/pricehistory/?appid=730&market_hash_name={name}`。
- 认证：**需要登录态**（Cookie `steamLoginSecure`）。未登录返回空/错误。
- 返回：`prices: [["MMM dd yyyy HH: +0", 价格, "销量"], ...]`、`price_prefix`、`price_suffix`；为日级历史成交中位价与销量。
- 限频：与全站一致，且带登录 Cookie 的请求有账号关联风险（社区有账号市场交易功能被限制的报告，可信度中）。
- 来源：https://stackoverflow.com/questions/31961868/how-to-retrieve-steam-market-price-history 、https://github.com/HilliamT/scm-price-history

### 3.3 挂单页 JSON：`/market/listings/730/{market_hash_name}/render/`

- 参数：`query=&start=0&count=10&country=&language=&currency=`。
- 返回：`listinginfo`（在售列表：listingid、价格、手续费、asset 信息）、`buy_order_graph`（求购深度曲线，首点即最高求购价）、`sell_order_graph`、`highest_buy_order`、`lowest_sell_order`、`total_count` 等。
- 认证：无需登录（币种受 country/currency 参数控制）；count 上限 100。
- 求购深度补充接口：`/market/itemordershistogram?country=&language=&currency=&item_nameid={id}&two_factor=0`（item_nameid 需从挂单页 HTML 提取）。
- 来源：https://stackoverflow.com/questions/69789860/buy-and-sell-orders-of-an-item-in-steam-api-json 、https://github.com/Allyans3/steam-market-api-v2

### 3.4 Steam Web API：ISteamEconomy / GetAssetPrices 现状

- 官方接口 `ISteamEconomy/GetAssetPrices`、`GetAssetClassInfo` 在 Steamworks 文档仍存在，但**从未对 CS:GO/CS2（appid 730）提供有效市场价格**（主要服务 TF2 等老游戏，社区普遍认为对 730 不可用/返回陈旧）。结论：不可作为 CS2 价格源。可信度：高（长期社区共识），但 Valve 无正式「废弃公告」，标注「基本不可用，待验证」。
- 来源：https://partner.steamgames.com/doc/webapi/isteameconomy 、https://steamapi.xpaw.me/ISteamEconomy

### 3.5 库存接口：`/inventory/{steamid64}/730/2?l=schinese&count=2000`

- 返回：`assets`（assetid、classid、amount）、`descriptions`（market_hash_name、磨损外观、可交易/可上市标记）。分页用 `start_assetid`。
- 前提：目标库存必须**公开**。匿名（无 Cookie）访问公开库存在 2025 年社区反馈中仍可用，但限频显著收紧（429 频发，IP 级）；是否已全面强制登录「待验证」（未找到 Valve 官方公告，2025–2026 未见可靠变更报告）。
- 经验限频：同一 IP 每分钟数十次即触发；多账号轮询需代理池。
- 来源：https://steamcommunity.com/discussions/forum/1/1736595227843280036/ 、https://www.reddit.com/r/Steam/comments/zkgm9g/ 、https://dev.doctormckay.com/topic/699-rate-limit-on-steamcommunitycom/

### 3.6 综合风险评估

- **IP 级 429**：所有 steamcommunity.com 接口共享 IP 配额，是最大现实约束；数据中心 IP 更容易被预封。
- **账号风险**：携带登录 Cookie 的高频请求（pricehistory、库存）可能牵连账号市场功能。
- **无 SLA/无版本承诺**：Valve 可随时变更未文档化接口。
- 速率建议（社区实践）：每 IP ≤ 1 req/2–3s 并加随机抖动；429 后指数退避。

### 3.7 Fallback 建议

- Steam 价格/求购兜底：SteamDT price 接口、CSQAQ 单件详情（含 steam_* 字段与挂刀比例）。
- Steam 成交量兜底：CSQAQ（turnover_number 日成交 + 图表接口日成交量）。
- 库存监控兜底：CSQAQ 库存监控系列接口（免自己扛 Steam 限频）。

---

## 4. BUFF 与悠悠有品现状（2025–2026）及替代方案

### 4.1 网易 BUFF

- **官方开发者 API 存在**：https://buff.163.com/developer/documentation （网站页脚「开发者 API」入口）。页面需登录后查看，主要面向**卖家交易行为**（自动上架/改价/发货类），账号内可自助开通 API key（社区教程，2024-10 仍存在）。**不提供公开行情数据接口**。可信度：中高（文档页需登录，未能抓取接口列表，具体 scope 待验证）。
- **半公开行情接口**：`buff.163.com/api/market/goods?game=csgo&page_num=&page_size=` 等 web 端 JSON 接口仍被社区广泛使用，但需登录 session Cookie，反爬严格（频率、设备指纹），有账号封禁风险；无官方承诺。
- 来源：https://buff.163.com/developer/documentation 、https://zhuanlan.zhihu.com/p/182880739 、https://www.volcengine.com/article/1544782 、https://github.com/854771076/BUFF

### 4.2 悠悠有品（UUYP）

- **无任何公开行情 API**。`api.youpin898.com` 的行情端点自 2023 年起要求登录态（GitHub issue 佐证），2025–2026 未放松。
- 社区半公开方案：抓包 App/小程序 token 调用私有接口（Steamauto 等项目用于自动发货/改价），仅适合交易自动化，不适合做行情采集主源；有风控与账号风险。
- 来源：https://github.com/ShevonKuan/csgo_investment/issues/5 、https://github.com/Steamauto/Steamauto 、https://github.com/jiajiaxd/uuyoupinapi

### 4.3 社区推荐替代方案（结论）

1. **BUFF/悠悠行情一律经由聚合站获取**：SteamDT（BUFF/YOUPIN/C5/STEAM/HALOSKINS）与 CSQAQ（BUFF/悠悠/Steam/C5/IGXE/ECO/R8GAME）均已结构化提供，免登录、有官方许可，是 2025–2026 社区主流做法。
2. **补充源：C5GAME 官方开放平台**（调研附带发现）：交易类 https://opendoc.c5game.com/ （内测接口 5/s）；伙伴平台价格接口 https://partnerdoc.c5game.com/180852827e0 （批量 200 个/次、10 qps，需申请权限）——可作为 BUFF/悠悠之外的第三方校验源。
3. 交易自动化（非行情）才考虑 BUFF 开发者 API / UUYP token。

---

## 附：横向对比与接入优先级建议

| 维度 | SteamDT | CSQAQ | Steam 官方 | BUFF/UUYP 直连 |
|---|---|---|---|---|
| 官方授权 | ✅ 开放平台 | ✅ 开放 API（非商用） | 无正式行情 API | ❌（交易类除外） |
| 多平台比价 | ✅ 5 平台 | ✅ 7 平台 | 仅 Steam | 单平台 |
| 大盘指数 | ✅ | ✅（更丰富） | ❌ | ❌ |
| 历史 K 线 | ✅（无成交量） | 企业档含成交量 | pricehistory（需登录） | ❌ |
| 限频友好度 | 分钟级配额 | 1 次/秒不限量 | 严格、IP 级 | 严格、有封号风险 |
| 成本 | 限时免费 | 免费+企业合作 | 免费 | 免费但高风险 |

**建议接入顺序**：① CSQAQ 免费档（字段最全、含成交量与存世量、文档完整）→ ② SteamDT（官方开放平台兜底 + 磨损/检视图能力）→ ③ Steam 官方接口仅做低频校验/补漏 → ④ 不直连 BUFF/悠悠行情接口。
