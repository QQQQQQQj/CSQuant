# SteamTradingSite-ID-Mapper 深度分析（CSQuant 调研 04）

> 分析对象：`CS Coding/SteamTradingSite-ID-Mapper-main`（纯数据项目，无代码）
> 分析方式：README/LICENSE 精读 + 对全部 9 个 JSON 文件的实际抽样（未整读大文件）
> 许可证：**Creative Commons Attribution 4.0 International（CC BY 4.0）**——可自由使用/修改/再分发，**必须署名**。

---

## 1. 项目定位

社区维护的 **DOTA2（appid 570）与 CS2/CSGO（appid 730）可交易饰品** 在 Steam 市场与四大第三方平台之间的 **ID 对照表（静态 JSON 数据集）**：

- **BUFF**（buff.163.com）
- **IGXE**（igxe.cn）
- **C5**（c5game.com）
- **UUYP**（悠悠有品，youpin898.com）

仓库内**没有任何代码/脚本**，只有 5 个平台目录 × 各 1~2 个 JSON。README 明确表示"无法保证完整性、正确性与时效性，欢迎 PR"——即**靠社区 PR 驱动更新的快照数据**。

对 CSQuant 的意义：这是把"同一个饰品"在 5 个平台的异构 ID 体系统一起来的**现成主数据（master data）**，可直接作为 `item_master` 维表的初始化数据源，省去向各平台爬虫逐个建映射的巨量工作。

---

## 2. 数据规模与覆盖范围

### 2.1 文件清单与实测大小

| 平台 | 570 (DOTA2) | 730 (CS2) | 结构 |
|---|---|---|---|
| steam | 7,402.2 KB（228,412 行 ≈ 45,682 条） | 6,811.9 KB（172,088 行 ≈ 34,417 条） | 对象值 |
| buff | 2,013.9 KB（45,684 行 = 45,682 键） | 1,802.8 KB（34,419 行 = 34,417 键） | 整数值 |
| c5 | 2,126.5 KB | 2,004.7 KB（34,419 行） | 整数值 |
| igxe | 1,990.4 KB | 1,754.8 KB（34,419 行） | 整数值 |
| uuyp | **不存在** | 1,778.2 KB（34,419 行） | 整数值 |

实测记录数与 README 覆盖率表完全吻合（steam 570 = 45,682，steam 730 = 34,417），说明 README 可信。

### 2.2 覆盖率（引自 README，与实测一致）

|  | 570 (DOTA2) | 730 (CS2) |
|---|---|---|
| Steam Market | 45,682 | 34,417 |
| BUFF | 45,172（98.88%） | 34,402（99.96%） |
| IGXE | 28,768（62.97%） | 23,506（68.30%） |
| C5 | 39,767（87.05%） | 31,247（90.79%） |
| UUYP | N/A | 33,614（97.67%） |

要点：
- **四个第三方平台的 JSON 键集与 steam 完全一致**（均为全量 34,417 / 45,682 个 market_hash_name），未覆盖的饰品以哨兵值 `-1` 填充。实测佐证：探针 `"AK-47 | Redline (Field-Tested)"`、`"Souvenir AWP | Dragon Lore (Field-Tested)"`、`"StatTrak™ AK-47 | Redline (Field-Tested)"` 在 buff/c5/igxe/uuyp 四个文件中**行号完全相同**（230 / 8579 / 10929 行），证明键集与排序一致、由同一 steam 键集生成。
- 因此"-1"的数量 = Steam 数 − 覆盖数，例如 730：IGXE 有 10,911 个 -1，C5 有 3,170 个，UUYP 有 803 个，BUFF 仅 15 个。
- **数据较新**：570 含 `International 2024 Autograph`（TI2024 签名）系列；730 含 2025 年 CS2 新皮肤如 `AK-47 | Aphrodite`、`AK-47 | Breakthrough`、`AK-47 | Crane Flight`（在 igxe 中均为 -1，说明 Steam 侧清单新、IGXE 侧未收录）。

---

## 3. 数据结构与样本

### 3.1 steam/*.json（唯一的"富结构"文件）

```json
"AK-47 | Redline (Field-Tested)": {
  "en_name": "AK-47 | Redline (Field-Tested)",
  "cn_name": "AK-47 | 红线 (久经沙场)",
  "name_id": 7178002
}
```

- **键** = `market_hash_name`
- **en_name**：英文显示名。**注意：en_name 可能 ≠ 键**。实测：`"#CSGO_crate_musickit_masterminds2_capsule"` 的 en_name 是 `"Masterminds 2 Music Kit Box"`（键是未本地化的占位 token）。
- **cn_name**：中文显示名（与 BUFF 等中文平台名称口径一致，实测 `AK-47 | 红线 (久经沙场)`、`爪子刀（★） | 多普勒 (崭新出厂)`）。
- **name_id**：Steam `item_nameid`，可直接用于 `itemordershistogram` 等 Steam 官方 API。

### 3.2 第三方平台/*.json（纯映射）

`"market_hash_name": 平台ID`，未收录为 `-1`。

**真实样本（730.json 实测）：**

| market_hash_name | steam name_id | buff | c5 | igxe | uuyp |
|---|---|---|---|---|---|
| AK-47 \| Redline (Field-Tested) | 7178002 | 33960 | 22499 | 3796 | 1414 |
| StatTrak™ AK-47 \| Redline (Field-Tested) | 7180207 | 38220 | 22780 | 4126 | 4301 |
| ★ Karambit \| Doppler (Factory New) | 29217386 | 42998 | 22702 | 9294 | 1785 |
| AWP \| Dragon Lore (Field-Tested) | 14959400 | — | — | — | — |
| Souvenir AWP \| Dragon Lore (Field-Tested) | 17691555 | 44946 | 9225280 | 555680 | 55327 |
| #CSGO_crate_musickit_masterminds2_capsule | 176456108 | 967699 | 1310790108815106048 | 685168 | 109491 |
| StatTrak™ AK-47 \| Searing Rage (Battle-Scarred) | — | 1115901 | 1381097254447517696 | **-1** | 111072 |

**重要发现（C5 ID 大数坑）**：C5 的 ID 格式不统一，既有普通整数（如 `553486494`），也有 **19 位雪花式 ID**（如 `1310790108815106048` ≈ 1.3×10¹⁸），**超过 JavaScript `Number.MAX_SAFE_INTEGER`（2⁵³≈9×10¹⁵）**。在 JS/TS 中直接 `JSON.parse` 会丢精度，入库必须按 **BIGINT 或字符串** 处理。

---

## 4. 跨平台关联键分析

**结论：`market_hash_name` 是唯一且足够的跨平台关联键**，理由：

1. 本数据集全部 9 个文件都以它为键，四平台键集与 steam 完全一致（§2.1 行号证据）。
2. 它由 Steam 官方定义，天然涵盖区分交易条目的全部维度：武器 | 皮肤 （磨损）、`StatTrak™` 前缀、`Souvenir` 前缀、`★` 刀/手套标记——StatTrak/纪念品/普通版是**不同的 market_hash_name**，映射互不干扰。
3. 每个 hash name 在 steam 文件里还附带 `name_id`，可反向用 Steam 官方 API 校验。

但需注意 4 个工程细节：

- **特殊字符**：键含 `™`、`★`、`|`、括号、单引号，HTTP 拼接 URL 时必须 percent-encode；数据库比较时注意 Unicode 归一化（`™` 是 U+2122）。
- **占位键**：存在 `#CSGO_...` / `#Econ_...` 开头的未本地化 token 键，其 `en_name` 才是人类可读名；做展示/搜索时要用 en_name/cn_name 而非键本身。
- **键集合存在少量"裸名"条目**：如实测存在 `"AK-47"`（无皮肤后缀，name_id 1275401）这种非标准条目，igxe 中映射为 -1。入库时应容忍这类不带 `|` 的键。
- **磨损是键的一部分**：`(Factory New)`…`(Battle-Scarred)` 五档磨损各占一行，天然支持按外观维度建表。

---

## 5. 数据更新机制

- **仓库内无任何更新脚本/CI**（仅有 README、LICENSE、9 个 JSON、一张预览图）。
- README 声明：新饰品不断加入游戏，不保证完整性/正确性/时效性，**更新依赖社区 PR**。
- 从数据内容推断快照时点：含 TI2024（2024 年 9 月）签名与 2025 年 CS2 新皮肤（Aphrodite 等），即数据至少覆盖到 2025 年。
- 上游 GitHub 仓库（SteamTradingSite-ID-Mapper）有持续 PR 历史，**建议定期拉取上游新版并 diff**，而不是把本地快照当终态。

---

## 6. 对 CSQuant item_master 设计的具体价值

1. **零成本主数据**：34,417 个 CS2 + 45,682 个 DOTA2 饰品的中英文名 + 5 平台 ID，一次性导入即得 `item_master` 全量维度行，免去逐站爬取。
2. **天然连接键**：所有行情/价差模块只需存 `market_hash_name`，即可 JOIN 出任一平台 ID 去拼 API/URL（配合调研 03 的 `url_formats.py`）。
3. **Steam name_id 白送**：可直接调用 Steam `itemordershistogram` 获取买卖盘深度，无需再做 hash→nameid 的爬取。
4. **双语名称**：`cn_name` 与 BUFF/UUYP 等中文平台口径一致，可直接用于中文平台搜索/校验。
5. **覆盖率量化**：-1 哨兵让"某饰品在某平台是否可交易"成为可查询字段，价差计算时可自动跳过无报价平台。

---

## 7. 可直接复用的数据

| 数据 | 复用方式 |
|---|---|
| 全量 market_hash_name 清单（570/730） | `item_master` 主键/唯一约束 |
| en_name / cn_name | 展示名、中文平台搜索、国际化 |
| steam `name_id` | Steam 官方 API（itemordershistogram / priceoverview 的备用参数） |
| buff 整数 ID | BUFF goods_id，拼 `/goods/{id}` 及 API |
| igxe 整数 ID | IGXE product_id |
| c5 混合整数 ID | C5 商品 ID（**必须 BIGINT/VARCHAR 存储**） |
| uuyp 整数 ID | 悠悠有品 templateId |
| -1 哨兵 | "平台未收录"布尔标记 |

不可复用/需注意：无图片、无稀有度、无收藏品/箱子归属、无品质颜色等元数据——需配合 `CSGO-API-main`（调研 01 方向）补齐。

---

## 8. 局限与风险

1. **时效性无保障**：无自动更新机制，新饰品依赖社区 PR；IGXE 覆盖率仅 68.3%（730），大量新皮肤为 -1（实测：`AK-47 | Aphrodite` 全部 5 档磨损在 igxe 均为 -1）。
2. **UUYP 无 570.json**：README 未说明原因（覆盖率表标 N/A）。**推断**（非 README 明示）：悠悠有品只做 CS2 交易、不做 DOTA2，故无 DOTA2 映射。CSQuant 若做 DOTA2 需排除 UUYP。
3. **特殊饰品粒度**：
   - **StatTrak™ / Souvenir**：是独立 market_hash_name、独立行、独立平台 ID（实测样本见 §3.2），**已妥善处理**。
   - **Doppler 相位**（P1–P4、红宝石/蓝宝石等）：**不在数据粒度内**——只有 `★ Karambit | Doppler (Factory New)` 一行，相位是单品实例的 paint seed/pattern 属性，需实例级数据（inspect 数据）处理，本数据集帮不上。
   - **特殊磨损/特殊模板**（如蓝顶淬火、红锁 IBP 贴纸枪）：同理不在粒度内，贴纸/印花附加值完全无法体现。
4. **C5 大整数 ID 精度陷阱**：超出 JS 安全整数（§3.2），传输/存储链路任何一环用 JS Number 或 32 位 int 都会出错。
5. **占位键与显示名不一致**：`#CSGO_...` 类键的 en_name 才是真名（§3.1），直接拿键做搜索会失败。
6. **许可义务**：CC BY 4.0 要求署名，产品中需标注数据来源。
7. **无校验字段**：没有更新时间戳、来源标记，无法判断单条映射的新旧。

---

## 9. 推荐集成方式（初始化 CSQuant item_master）

**建议表结构：**

```sql
CREATE TABLE item_master (
  id                BIGSERIAL PRIMARY KEY,        -- 物理主键（内部 JOIN 用）
  app_id            SMALLINT  NOT NULL,           -- 730 / 570
  market_hash_name  TEXT      NOT NULL,           -- 自然键（跨平台关联键）
  en_name           TEXT,
  cn_name           TEXT,
  steam_name_id     BIGINT,                       -- steam/*.json 的 name_id
  buff_goods_id     BIGINT,                       -- -1 → NULL
  igxe_id           BIGINT,
  c5_id             TEXT,                         -- C5 19 位 ID，用 TEXT 最稳（或 BIGINT）
  uuyp_id           BIGINT,
  is_stattrak       BOOLEAN GENERATED ALWAYS AS (market_hash_name LIKE 'StatTrak™%') STORED,
  is_souvenir       BOOLEAN GENERATED ALWAYS AS (market_hash_name LIKE 'Souvenir %') STORED,
  created_at        TIMESTAMPTZ DEFAULT now(),
  updated_at        TIMESTAMPTZ DEFAULT now(),
  UNIQUE (app_id, market_hash_name)
);
```

**导入流程：**

1. 读 `steam/{app}.json` 建基表（hash_name, en, cn, name_id）；
2. 依次 LEFT JOIN 4 个第三方 JSON，`-1` 统一转 `NULL`（语义更清晰，且便于 `WHERE buff_goods_id IS NULL` 统计覆盖率）；
3. C5 ID 全程按字符串/BIGINT 解析（Python `json` 默认 int 无损；JS 需 `json-bigint`）；
4. 从 hash name 解析出 weapon / skin / exterior 冗余列（按 ` | ` 与尾部 `(…)` 切分），便于按武器聚合查询；
5. 建 `mapping_source` / `mapping_updated_at` 元表记录每次上游同步的版本与时间。

**持续同步**：定期 `git pull` 上游仓库 → 对 9 个文件做键级 diff（新增/删除/ID 变更三类事件）→ 增量 upsert，并保留变更日志用于审计。

---

## 附：5 个专项问题的回答

**Q1：用哪个字段做 CSQuant 统一主键？**
内部物理主键用自增 `id`（短、稳定、JOIN 快）；业务上 `(app_id, market_hash_name)` 作唯一约束，充当跨平台"统一键"。**不要**用任何平台 ID（含 steam name_id）当主键——平台 ID 会变、会缺（-1），而 hash name 是 Steam 官方、五平台共用的稳定标识。

**Q2：market_hash_name 是否足够作为跨平台关联键？**
足够，且是本数据集验证过的实践：四平台键集与 steam 完全一致（行号级对齐）。前提是做三点防御：① Unicode/URL 编码正确处理（™、★）；② 容忍 `#CSGO_` 占位键并用 en_name 兜底；③ 接受个别平台 -1 缺失。

**Q3：Doppler、特殊磨损、StatTrak、Souvenir 如何处理？**
数据证据：StatTrak™ 与 Souvenir 是**独立 hash name 行**（`StatTrak™ AK-47 | Redline (Field-Tested)`、`Souvenir AWP | Dragon Lore (Field-Tested)` 各有独立五平台 ID），直接在 item_master 成行，加 `is_stattrak`/`is_souvenir` 标志位即可。Doppler 相位、特殊模板/磨损**不在本数据粒度**，它们是单品实例属性（paint seed、float），须另建 item_instance 层数据（如要支持，需接 inspect/浮点数数据源），item_master 层一律按 hash name 归并。

**Q4：名称改变或平台映射缺失怎么办？**
① 缺失：-1 → NULL，查询时跳过该平台；② 改名：hash name 变了即新键，通过 diff 上游数据发现（旧键消失+新键出现），用 `(en_name, 武器, 皮肤, 磨损)` 启发式匹配旧行并迁移平台 ID；③ 平台侧 ID 变更：以 diff 中"值变更"事件捕获，全量校验时调平台 API 按 ID 反查名称比对。

**Q5：如何自动校验错误映射？**
五层校验可全部自动化：
1. **键集对齐校验**：四个第三方文件键集必须等于 steam 键集（本次抽样行号完全一致，若上游 PR 出错会立刻暴露）；
2. **哨兵比例监控**：各平台 -1 数量应与 README 覆盖率一致（如 730：BUFF 15、IGXE 10,911、C5 3,170、UUYP 803），突变即告警；
3. **Steam 侧活性校验**：抽样用 `name_id` 调 `itemordershistogram`，返回失败说明该条目已下架；
4. **跨平台名称回查**：抽样按平台 ID 调各平台公开接口/BUFF 页面，比对其返回的中文名与 `cn_name`（中文平台）或英文名是否一致；
5. **同一性交叉验证**：同一 hash name 在 ≥3 个平台均有 ID 时，任一新 PR 改动其中 1 个都进入人工 review 队列。
