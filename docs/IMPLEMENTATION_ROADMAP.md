# CSQuant 实施路线（IMPLEMENTATION_ROADMAP）

> 原则：先数据底座，后量化大脑；每版可独立交付、可验证。V1 代码骨架已随本轮交付（纯 stdlib 核心可运行可测试）。

## V1 — 数据底座与库存资产（最先做）

| # | 任务 | 验收 |
|---|---|---|
| 1 | 脚手架：utils(config/logger/time/fees) + database(schema/db/dao) | schema 初始化成功，DAO 单测过 |
| 2 | items：ID-Mapper 五平台 730.json 导入 item_master；CSGO-API 元数据同步 | 34,417 行入库，抽样校验 AK-47 Redline 五平台 ID |
| 3 | data_sources：ratelimit/http_client/base + CSQAQ Provider + unified 降级 | 真实拉取自选 10 件报价落库（需 Token） |
| 4 | inventory：Steam 库存同步 + 手工录入 + valuation KPI + 每日净值快照 | Dashboard 库存页与手工台账一致 |
| 5 | scheduler + run.py CLI（collect/sync/nav） | 任务表跑通，event_log 有记录 |
| 6 | frontend：库存/资产/数据源状态/设置 4 页签 | streamlit 可打开 |

**V1 完成标准**：我有哪些饰品、值多少钱、涨跌多少、资产曲线——全部可答。

## V2 — 大盘与市场情绪

CSQAQ 指数/涨跌分布采集 → market_index 落库 → breadth 自算 → MarketScore 六因子 → 大盘页签 + 数据源健康页完善。验收：MarketScore 每日生成、五档输出、因子贡献可解释。

## V3 — 单品评分与信号

kline 增量采集 → indicators/八因子 → signal_engine（阈值+confidence+reason）→ risk 定级与建议仓位 → 信号页签 + 单品详情页 + 筛选器。验收：信号 100% 落库可追溯（reason_json+input_snapshot_ref+model_version）。

## V4 — 回测

engine（纯 python）→ 数据清洗管道 → 实验矩阵 E1-E4（基准对照/阈值网格/权重扰动/walk-forward）→ 回测页签 + 报告 → （可选）vectorbt 独立 venv 热切换。验收：默认策略 vs 大盘基准报告可复现，泄漏检查清单过审。

## V5 — 事件与 LLM

agents/：llm_client（复用 CSGOTrading 推理层）→ event_agent（Valve 更新/赛事解析→event_score）→ explainer（信号自然语言解释）→ Portfolio Manager 两段式建议仓位。验收：LLM 输出结构化落 event_log，不进入任何数值计算。

## 依赖与风险

- 前置依赖：CSQAQ Token（免费注册即得）→ 不阻塞 V1 编码（Provider 可 mock 开发）；
- 最大风险：历史数据积累期回测不可信 → V4 前至少积累 3 个月 tick 数据，期间回测报告强制显示局限声明；
- SteamDT Key、Steam 小号 Cookie 为 V2 前建议配置项。

## 下一步（立即执行）

**模块：数据底座三连** —— `database`（schema+dao）→ `items`（导入 34k 映射）→ `data_sources/csqaq`（真实拉通 10 件自选报价）。完成即拥有「可采集、可存储、可查」的最小闭环，随后接库存同步。
