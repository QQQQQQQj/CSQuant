# CSGO-API 深度分析（CS 饰品元数据静态 API 生成器）

> 分析对象：`c:\Users\Jie Qiao\Desktop\Coding\CS Coding\CSGO-API-main\CSGO-API-main`
> 上游项目：[ByMykel/CSGO-API](https://github.com/ByMykel/CSGO-API)（非官方 Counter-Strike 2 JSON API）
> 分析日期：2026-07-21；所有结论均基于本地实际代码。

## 1. 项目定位

CSGO-API 是一个**纯生成器（generator）项目**，不是服务端：它周期性抓取 Valve 游戏文件的社区解析版（`items_game.txt`、各语言 `csgo_*.txt` 的 JSON 化版本），在本地加工成一组按语言分目录的**静态 JSON 文件**，直接提交到 GitHub 仓库 `public/api/{lang}/`，通过 `raw.githubusercontent.com` 免费托管分发（README.md L10）：

```http
GET https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/skins.json
```

一句话定位：**把 CS2 的 items_game 游戏配置 + 本地化文本，转换成 13 类饰品元数据的静态 JSON API**。覆盖 skin、sticker、sticker slab、keychain(charm)、collection、crate、key、collectible、agent、patch、graffiti、music kit、base weapon、highlight（Major 集锦挂件）。更新通过 GitHub Actions 定时跑 `update.js` + `group.js`，靠 `manifestId.txt` / `images.json` 的 SHA 变化决定是否重跑（update.js L42-77）。

## 2. 技术栈

- **Node.js（ESM，`"type": "module"`）**，无 TypeScript、无框架、无测试。
- 运行时依赖仅 2 个（package.json）：`axios`（HTTP 抓取上游 JSON）、`sha1`（从图标路径生成 skin 的稳定 ID）。
- devDependency：`prettier`。
- 无数据库、无服务器：产物是文件系统上的 JSON；分发靠 GitHub 仓库 + GitHub Actions。
- 文档站：`scripts/generate-docs.js` 用 `public/docs/template.html` 渲染出 `public/docs/index.html`（含 favicon.ico、image-meta.png）。

## 3. 目录结构

```
CSGO-API-main/
├── update.js            # 主入口：检查上游变更 → loadData → 逐语言生成13类JSON + inventory.json
├── group.js             # 次入口：把13个分类JSON按 id 合并成 all.json
├── constants.js         # 上游URL常量 + 28种语言配置（folder代码↔Valve语言名）
├── manifestId{Update,Group}.txt / imagesSha{Update,Group}.txt  # 增量更新缓存戳
├── services/
│   ├── main.js          # 核心：state 全局状态 + 全部 loader（items_game解析、稀有度、
│   │                    #   箱子↔皮肤↔收藏品互相反查、Stattrak白名单、图片CDN映射）
│   ├── translations.js  # $t/$tTag/$tc 翻译函数 + loadTranslations
│   ├── skins.js / skinsNotGrouped.js  # 皮肤（按磨损聚合 / 按磨损+状态展开，含market_hash_name）
│   ├── crates.js / collections.js / keys.js
│   ├── stickers.js / stickerSlabs.js / patches.js / graffiti.js
│   ├── agents.js / keychains.js / musicKits.js / collectibles.js
│   ├── baseWeapons.js / tools.js / highlights.js
│   └── inventory.js     # 生成 inventory.json：以 weapon_id+paint_index / def_index 为键的查询表
├── utils/
│   ├── index.js         # 武器清单、weaponIDMapping、刀具清单、磨损分档、Doppler相位、
│   │                    #   market_hash_name 拼接、稀有度颜色等
│   ├── rareSpecial.js   # 硬编码：每个箱子的"极其稀有特殊物品"(刀/手套)清单
│   ├── languages.js     # --languages 参数解析
│   ├── saveDataJson.js  # 写 JSON（缩进1格，便于 git diff）
│   ├── specialNotes.json    # 个别物品的人工备注（如咆哮变违禁品）
│   └── translations.json    # 各语言名称模板（★/StatTrak™/纪念品 拼接格式）
├── scripts/generate-docs.js
└── public/
    ├── api/en/  api/zh-CN/   # 各18个JSON（托管语言仅 en、zh-CN）
    └── docs/    # favicon.ico, image-meta.png, index.html, template.html
```

`public/api/en` 实测 18 个文件，量级：`all.json` ≈74MB、`skins_not_grouped.json` ≈36MB、`sticker_slabs.json` ≈17MB、`stickers.json` ≈18MB、`inventory.json` ≈11MB、`crates.json` ≈7.6MB、`skins.json` ≈5.3MB，其余（agents/keys/tools/base_weapons/keychains/patches/music_kits/highlights/graffiti/collectibles/collections）较小。

## 4. 核心模块

### 4.1 `services/main.js` —— 数据装配中枢

- 导出一个全局 `state = {}`（main.js L16），`loadData()`（L857-880）按序执行 18 个 loader，把 `items_game.json` 的各子表（prefabs、items、item_sets、sticker_kits、keychain_definitions、paint_kits、music_definitions、client_loot_lists、revolving_loot_lists、highlight_reels、pro_teams、pro_players）全部预处理进内存。
- **物品键约定**：items_game 中的物品统一写作 `[pattern_name]entity_type`，如 `[hy_ddpat_urb]weapon_deagle`、`[kat2014_titan]sticker`。`getItemFromKey()`（L630-830）是这个键格式的总解析器，按 type 分派到 sticker/patch/spray/musickit/keychain/weapon 各类。
- **稀有度推导**（`loadRarities` L192-237）：从 `client_loot_lists` 的键名后缀（common/uncommon/rare/mythical/legendary/ancient）反推每个 `[pattern]weapon` 的稀有度，外加 8 条硬编码修正（如 M4A4 Howl = contraband）。
- **三向反查表**（整个项目的灵魂）：
  - `loadSkinsByCrates()`（L239-330）：`revolving_loot_lists` → 递归展开 `client_loot_lists` 得"箱子→皮肤列表"；`rare--{crate}` 键专门存刀/手套。
  - `loadyCratesBySkins()`（L332-377）：反转为"皮肤→出自哪些箱子"。
  - `loadSkinsByCollections()` / `loadCratesByCollections()` / `loadCollectionsBySkins()` / `loadCollectionsByStickers()`：收藏品维度同样的正/反查。
- **Stattrak 白名单**（`loadStattrakSkins` L530-568）：只有"武器箱（prefab 含 weapon_case/volatile_pricing）产出的收藏品皮肤"才有 StatTrak 版；刀全部默认有。
- 变更检测：`getManifestId()` / `getImagesJsonSha()` 通过 GitHub Contents API 读上游 `manifestId.txt` 与 `images.json` 的 sha（L832-855）。
- 注意：`alternate_icons2.weapon_icons` 被**整体替换**为 image-tracker 的 `default_generated.json`（L64-86）：只保留 `_light` 变体，key 为 `sha1(path).slice(0,12)`——这就是 skin id（`skin-e757fd7191f9`）的来源。

### 4.2 `services/skins.js` / `skinsNotGrouped.js` —— 皮肤生成

- 皮肤不是直接遍历 items_game 的物品表，而是**遍历 `alternate_icons2.weapon_icons`**（即 default_generated 图标清单）：图标路径 `econ/default_generated/{weapon}_{pattern}_light` 反推武器与涂装（skins.js L21-40）。`getWeaponName` 用 includes 匹配 utils 里的 65 个武器名。
- `skins.js` 按"武器+涂装"聚合：一条记录含全部可出现的磨损档（`wears` 数组）、`min_float/max_float`、稀有度、Stattrak/Souvenir 布尔、所属收藏品与箱子。
- `skinsNotGrouped.js` 把每个皮肤**展开成 磨损档 × 状态（普通/StatTrak/纪念品）** 的独立条目，id 形如 `skin-xxx_2_st`，并用 `skinMarketHashName()` 生成 Steam 市场 hash 名（`StatTrak™ AK-47 | Redline (Field-Tested)`）。特殊规则：`hy_labrat_mp5` 只有纪念品版（L111）。
- 两类 Souvenir 逻辑在 2026 年已更新：因 IEM Cologne 2026 的 Souvenir-O-Matic，所有枪械皮肤 `souvenir = !isNotWeapon(weapon)`（skins.js L132-134）。

### 4.3 `services/inventory.js` —— 库存查询表生成器

**澄清一个常见误解**：它并不解析某个具体 Steam 用户的库存响应；它把刚生成的 13 个分类 JSON 重新索引成一张**按游戏内部 def_index 组织的查询表** `inventory.json`（L6-173）：

- skins：`skins[weapon_id][paint_index] = {name, rarity, marketable, image}`（weapon_id 来自 `weaponIDMapping`，paint_index 来自 paint_kits）。
- 其他 12 类：`crates[def_index]`、`stickers[def_index]`、`agents[def_index]`…值结构同上。
- 用途：当你从 Steam 库存 + inspect link（或第三方 float API）拿到 `defindex`/`paintindex` 时，可 O(1) 反查名称/稀有度/图片/可交易性。`waitForFile()`（L175-183）带重试，因为 update.js 是逐语言串行写文件后再读。

### 4.4 `utils/rareSpecial.js` —— 刀/手套硬编码表

775 行的纯数据文件：`{ 箱子loot_list键: { "[pattern]weapon_xxx": 1, ... } }`。因为 items_game 的 loot list 里刀/手套只写了一个"特殊稀有物品"占位符，真实内容需要人工维护。`loadSkinsByCrates` 的 `extractRareItems()`（main.js L263-273）用它填充每个箱子的 `contains_rare`。vanilla（无涂装原皮刀）用 `[vanilla]weapon_knife_kukri` 表示。

### 4.5 `services/translations.js` —— 多语言层

- 上游语言文件是 Valve `csgo_{lang}.txt` 的 JSON 化：`{ "lang": { "Tokens": { key: value } } }`；加载时全部 key 转小写（L67-69）。
- `$t(key, useDefault)`：先查当前语言，缺失回落英文；`useDefault=true` 强制英文（用于生成 market_hash_name，因为 Steam 市场只认英文名）。
- `$tTag`：处理 `xxx_tag` 描述键的回退（向前找最近的非 `_tag` 键）。
- `$tc(key, data)`：用 `utils/translations.json` 里**按语言自定义的模板**拼名，例如英语 `"skin_stattrak": "StatTrak™ {item_name} | {pattern} ({wear})"`，解决不同语言"★/StatTrak™/纪念品"词序不同的问题。这是名称本地化的关键设计。

## 5. 主数据流

```
上游 GitHub 仓库（均为原始游戏文件的解析结果，非 Steam WebAPI）：
  ByMykel/counter-strike-file-tracker
    ├─ static/items_game.json        ← Valve items_game.txt 的 JSON 化（物品/涂装/箱子/loot表/语言键）
    ├─ static/csgo_english.json      ← 英文语言Tokens
    ├─ static/csgo_{schinese,...}.json ← 其余27种语言
    └─ static/manifestId.txt         ← 版本戳
  ByMykel/counter-strike-image-tracker
    ├─ static/images.json            ← 图标路径 → Steam CDN URL 映射
    ├─ static/default_generated.json ← 皮肤图标路径清单（决定有哪些皮肤）
    └─ static/panorama/images/**     ← 图片本体

update.js --languages en,zh-CN
  1. getManifestId() + getImagesJsonSha() 与本地 txt 比对，无变化直接退出
  2. loadData()（services/main.js）
       下载 items_game.json / images.json / default_generated.json
       → 18 个 loader 构建 state（含 cratesBySkins / collectionsBySkins /
         skinsByCrates / stattTrakSkins / rarities 等反查表）
  3. 对每种语言：
       loadTranslations(英文默认 + 当前语言)
       → getAgents/getCollectibles/getCollections/getCrates/getGraffiti/getKeys/
         getMusicKits/getPatches/getSkins/getSkinsNotGrouped/getStickers/
         getStickerSlabs/getKeychains/getTools/getBaseWeapons/getHighlights
       → 各自写 public/api/{folder}/*.json（saveDataJson）
       → getInventory() 读回13个文件，重索引写 inventory.json
  4. 更新 manifestIdUpdate.txt / imagesShaUpdate.txt

group.js --languages ...
  每语言读13个分类JSON → { [item.id]: item } 合并 → all.json
```

## 6. 数据模型（skin 元数据字段全集）

真实示例（`public/api/en/skins.json` 首条）：

```json
{
  "id": "skin-e757fd7191f9",
  "name": "★ Hand Wraps | Spruce DDPAT",
  "description": "Preferred by hand-to-hand fighters...",
  "weapon":   { "id": "leather_handwraps", "weapon_id": 5032, "name": "Hand Wraps" },
  "category": { "id": "sfui_invpanel_filter_gloves", "name": "Gloves" },
  "pattern":  { "id": "handwrap_camo_grey", "name": "Spruce DDPAT" },
  "min_float": 0.06, "max_float": 0.8,
  "rarity":   { "id": "rarity_ancient", "name": "Extraordinary", "color": "#eb4b4b" },
  "stattrak": false, "souvenir": false,
  "paint_index": "10010",
  "wears":    [ { "id": "SFUI_InvTooltip_Wear_Amount_0", "name": "Factory New" }, ... 共5档 ],
  "collections": [ { "id": "collection-...", "name": "...", "image": "..." } ],
  "crates":      [ { "id": "crate-4288", "name": "Glove Case", "image": "..." } ],
  "team":     { "id": "both", "name": "Both Teams" },
  "legacy_model": false,
  "image": "https://community.akamai.steamstatic.com/economy/image/...",
  "original": { "name": "leather_handwraps" }
}
```

字段要点：
- `id`：`skin-` + sha1(图标路径)前12位；原皮刀为 `skin-vanilla-{weapon}`。
- `weapon.weapon_id`：Valve 物品 defindex（AK-47=7、AWP=9、刀具500+、手套4725-5035，见 `weaponIDMapping`，utils/index.js L67-143）。
- `min_float/max_float`：来自 paint_kits 的 `wear_remap_min/max`，缺省 0.06/0.8（main.js L160-161）。
- `wears`：由 float 区间与五档区间（0-0.07/0.07-0.15/0.15-0.38/0.38-0.45/0.45-1.0）求交得出（`getWears`，utils/index.js L359-369）。
- `phase`（可选）：Doppler/Gamma Doppler 相位，由 paint_index 硬编码映射（415-421、568-572、617-619、852-855、1119-1123，`getDopplerPhase`）。
- `special_notes`（可选）：人工备注（`utils/specialNotes.json`）。
- `legacy_model`：是否旧模型（paint_kits 的 `use_legacy_model`）。

`skins_not_grouped.json` 额外有：`skin_id`（回指聚合条目）、单数 `wear`、`market_hash_name`、`style`（涂装工艺 id/名称/链接）。

其他类别的公共模式：`{type}-{def_index}` 作 id、`rarity{id,name,color}`、`market_hash_name`（不可交易则为 null）、`image`、`original`（保留 items_game 原始键）。crates 有 `contains/contains_rare/first_sale_date/type/rental/loot_list`；stickers 有 `tournament/team/player/effect/type`；collections 有 `contains/crates`。

## 7. API/外部依赖（上游数据源）

**不直接调用任何 Steam WebAPI**。全部数据来自两个"游戏文件追踪"GitHub 仓库（constants.js）：

| 依赖 | URL | 用途 |
|---|---|---|
| items_game.json | `raw.githubusercontent.com/ByMykel/counter-strike-file-tracker/main/static/items_game.json` | 核心：物品/涂装/箱子/loot/收藏品定义 |
| csgo_english.json + csgo_{lang}.json | 同上 repo `/static/` | 28 语言 Tokens |
| manifestId.txt | 同 repo，经 GitHub Contents API 读取 | 变更检测 |
| images.json | `ByMykel/counter-strike-image-tracker/main/static/images.json` | 图标路径→Steam CDN 大图 |
| default_generated.json | 同 repo | 皮肤枚举清单 |
| 图片本体 | 同 repo `/static/panorama/images/**`（fallback） | `getImageUrl()` 拼 PNG 地址 |
| 集锦视频 | `cdn.steamstatic.com/apps/csgo/videos/highlightreels/...` | highlights.json 的 video |

npm 依赖：axios、sha1。GitHub Actions 定时任务即全部"后端"。

## 8. 数据库

**没有数据库**。方案是"内存 state + 静态 JSON 文件"：
- 构建期：`services/main.js` 的 `state` 对象在内存里保存全部中间索引（skinsByCrates、cratesBySkins、rarities……）。
- 存储期：`public/api/{lang}/*.json` 即最终"数据库"，由 Git 版本控制、GitHub raw 分发。
- 变更检测靠两个文本戳文件（`manifestIdUpdate.txt`、`imagesShaUpdate.txt`），而非任何 DB 元数据。
- 代价：产物巨大（all.json 74MB），且 `skins_not_grouped.json` 这类展开表完全靠体积换查询便利。

## 9. 核心算法

### 9.1 物品分类
- 每个 service 一个 `isXxx` 过滤器，基于 items_game 的 `prefab`/`item_name`/`sticker_material` 等字段，如 `isAgent = item.prefab === "customplayertradable"`（agents.js L7）、`isCrate` 用 `attributes["set supply crate series"]` + `#CSGO_crate` 前缀 + 排除规则（crates.js L8-41）。
- 皮肤分类（手枪/步枪/重型/冲锋枪/近战/手套）是硬编码 switch（`getCategory`，utils/index.js L264-357）。

### 9.2 磨损处理
- 五档区间硬编码（FN 0-0.07、MW 0.07-0.15、FT 0.15-0.38、WW 0.38-0.45、BS 0.45-1.0）；皮肤的 float 区间取自 paint_kits 的 `wear_remap_min/max`，与五档求交得到该皮肤实际存在的磨损档（`getWears`）。
- 图片按磨损分 light/medium/heavy 三档（`formatIconPath`，utils/index.js L537-553）。

### 9.3 特殊物品识别
- 刀/手套判定：`isNotWeapon()`（不含 `weapon_`，或含 knife/bayonet）；刀强制 `rarity_ancient_weapon`（Covert），手套 `rarity_ancient`（Extraordinary）。
- 箱子里的"极其稀有"内容完全靠 `utils/rareSpecial.js` 硬编码表，经 `extractRareItems` 挂到 `rare--{crate}` 键下。
- 违禁品（Contraband）等特殊稀有度用硬编码修正表（main.js L193-218 的 `hardCoded`，如 `[cu_m4a1_howling]weapon_m4a1` → contraband）。
- StatTrak 资格 = 武器箱产出皮肤的集合（loadStattrakSkins）；Souvenir 资格 = 全部枪械（2026 Cologne 后）。

### 9.4 库存解析匹配
- 生成侧：inventory.js 建 `skins[weapon_id][paint_index]` 与其他类 `*[def_index]` 两级索引。
- 消费侧（CSQuant 要自己做）：Steam 用户库存 JSON（`GetInventoryContents`）只给 `classid/instanceid/market_hash_name`；要拿到 `defindex/paintindex` 需 inspect link 经 float API（如 csfloat）解析，或直接用 `market_hash_name` 匹配 `skins_not_grouped.json`。inventory.json 正是为前一路径准备的 O(1) 查找表。

## 10. 最值得复用的设计

1. **反查表构建模式**：loot_list 递归展开（`extractItems`）+ 正/反双向索引（skinsByCrates ↔ cratesBySkins、skinsByCollections ↔ collectionsBySkins），一套数据支撑"皮肤出自哪些箱子"和"箱子含哪些皮肤"两种查询。
2. **翻译三层函数 `$t/$tTag/$tc`**：英文回落 + 强制英文（给市场名）+ 每语言名称模板（解决 ★/StatTrak™ 词序）。
3. **磨损区间求交**：float 区间 ∩ 五档区间 → 实际存在的外观档，简单且正确。
4. **静态 JSON 即 API**：零服务器成本、Git 即版本史、CDN 分发；增量更新靠上游 manifestId/SHA 双戳。
5. **inventory.json 的 def_index 索引思路**：把元数据按游戏内部 ID 预索引，让库存匹配变成纯查表。

## 11. 可直接复用的代码/数据

| 内容 | 位置 | 复用方式 |
|---|---|---|
| `weaponIDMapping`（武器名↔defindex，含刀/手套/手雷） | utils/index.js L67-143 | 直接拷贝为 CSQuant 常量 |
| 五档磨损区间 + `getWears` | utils/index.js L359-369 | 直接移植 |
| `getDopplerPhase`（paint_index→相位） | utils/index.js L371-409 | 直接拷贝 |
| `getRarityColor`（稀有度→官方颜色码） | utils/index.js L491-524 | 直接拷贝 |
| `skinMarketHashName`（市场名拼接，含★/StatTrak™/Souvenir） | utils/index.js L415-449 | 直接移植 |
| `rareSpecial.js`（每个箱子的刀/手套清单） | utils/rareSpecial.js | 直接作为数据文件 |
| 生成产物本身 | `public/api/en|zh-CN/*.json` | **最有价值**：可直接下载/同步为 CSQuant 元数据层快照 |
| 语言 folder↔Valve 语言名映射 | constants.js LANGUAGES_URL | 拷贝 |
| 模板翻译 translations.json | utils/translations.json | 中文名拼接直接可用（zh-CN 模板） |

## 12. 只能借鉴的部分

- **items_game.json 解析流水线**（main.js 的 18 个 loader）：与 Node/axios 耦合，且依赖 ByMykel 上游仓库的格式；CSQuant 是 Python 栈，只能借鉴其"先建反查表再渲染"的思路。
- **`$tc` 模板机制**：思路可借鉴（Python 用 `str.format` 重做），文件本身只覆盖约 10 个键。
- **update.js 的双戳增量更新**：思路（上游版本戳 + 本地戳比对）可用于 CSQuant 的元数据同步任务。
- **hardCoded 修正表模式**：稀有度、违禁品、不可交易名单等"数据补丁层"思路值得保留，但具体条目需按当时数据核验。

## 13. 已过时/不适用部分

- **托管语言只有 en / zh-CN**（README L15）：其余 26 种语言需自跑生成器。
- `IMAGES_BASE_URL` 指向 `steamdatabase/gametracking-csgo` 的某个固定 commit（constants.js L16-17），代码中已无实际引用，属遗留。
- 大量硬编码补丁与特定时间点绑定：如 Cologne 2026 胶囊不可交易（crates.js L114-120）、Budapest 2025 缩略图无中文版（highlights.js L17）、选手名纠错表（utils/index.js L703-717），随时间必然腐化。
- `baseWeapons.js`（887 行）与 `tools.js` 全为手写条目，新武器需人工维护。
- GitHub raw 作为"API 托管"有速率与缓存限制，不适合高频生产调用，只适合定期同步。
- CS:GO 命名残留（market_hash_name 里 `CS:GO Case Key` 等）是 Valve 侧历史遗留，并非 bug，但做名称匹配时要注意。

## 14. 对 CSQuant 的具体价值

1. **元数据层可整体落地**：CSQuant 需要"饰品的标准中文名/英文名、稀有度、类别、外观档、图片、可交易性、箱子/收藏品归属"——本项目 `public/api/zh-CN/*.json` 与 `en/*.json` 已是成品，一次同步即可入库，无需自己解析 items_game。
2. **库存同步的关键拼图**：`inventory.json` 的 `skins[weapon_id][paint_index]` 模式 + `weaponIDMapping`，正是把 inspect/float API 返回的 `defindex+paintindex` 翻译成标准饰品的桥；没有 float 数据时，可用 `skins_not_grouped.json` 的 `market_hash_name`（英文，与市场一致）做二级匹配。
3. **name 规范化经验**：StatTrak™/Souvenir/★ 前缀、磨损后缀 `(Factory New)`、以及 `$tc` 的中英文双模板，为 CSQuant 建立"标准名 ↔ 市场 hash 名 ↔ 中文显示名"三轨命名提供了现成规则。
4. **分类体系对齐**：rarity id（rarity_*_weapon）、category id（csgo_inventory_weapon_category_*）、crate type、sticker effect 等枚举可直接作为 CSQuant 的维度字典，保证与社区/数据源口径一致。

## 15. 推荐集成方式

1. **首选：定期同步静态 JSON**。写一个 Python 定时任务（如每日）下载
   `raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/{en,zh-CN}/` 下的 `skins.json`、`skins_not_grouped.json`、`crates.json`、`collections.json`、`stickers.json`、`agents.json`、`keychains.json`、`music_kits.json`、`collectibles.json`、`inventory.json`，解析后写入 CSQuant 的元数据表。用 `manifestIdUpdate.txt`（或其 GitHub commit sha）做变更检测，避免全量重导。
2. **库存匹配流水线**：
   - 路径 A（有 inspect/float 数据）：`defindex → weaponIDMapping 反查 weapon_id`，再 `inventory.json["skins"][weapon_id][paint_index]` 命中；非皮肤类直接 `inventory.json[category][defindex]`。
   - 路径 B（只有市场名）：用 `skins_not_grouped.json` 建 `market_hash_name → skin` 索引（仅 en 文件有效）。
3. **常量本地化**：把 `weaponIDMapping`、磨损区间、Doppler 相位、稀有度颜色从 utils/index.js 移植为 Python 常量模块。
4. **不建议**：在生产环境直接高频请求 GitHub raw；也不建议 fork 整个 Node 生成器（上游 items_game 解析已由 ByMykel 维护，重复造轮子成本高）。

## 16. 风险和注意事项

- **上游单点依赖**：本 API 依赖 `counter-strike-file-tracker` / `counter-strike-image-tracker` 两个仓库持续更新；任一停更，元数据即开始腐化。CSQuant 应缓存快照而非每次实时拉取。
- **硬编码腐化**：rareSpecial 表、违禁品表、不可交易名单、选手名纠错等都是人工维护，新版本（新箱子/新刀）出来到表更新之间存在窗口期，可能漏识别。
- **名称匹配陷阱**：
  - `market_hash_name` 只有英文可靠（`$t(..., true)` 强制英文）；中文 name 不可用于市场匹配。
  - 存在 Valve 命名不一致被代码特判修正的例子（`#StickerKit_dhw2014_dignitas_gold` → `teamdignitas`，stickers.js L167-169），说明名称本身有坑。
- **体积**：all.json 74MB、skins_not_grouped 36MB，同步时应按类别增量处理，不要整文件入内存库。
- **库存匹配的固有缺口**：Steam 库存响应本身不带 paintindex/float，inventory.json 只有在配合 inspect 类 API 时才能发挥作用；普通库存条目可能只能匹配到"武器+涂装"级别，无法区分磨损与 StatTrak（需 float API 或 market_hash_name 辅助）。
- **许可证**：项目为 MIT（LICENSE 文件存在），代码与数据复用需保留版权声明；上游游戏数据本身属 Valve 所有。
- **时效性注释**：文档撰写时本地代码包含 2026（IEM Cologne 2026 Souvenir-O-Matic、Budapest 2025 集锦）逻辑，说明该仓库处于活跃维护状态，可信度较高。
