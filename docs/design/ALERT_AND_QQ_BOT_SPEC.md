# 告警与 QQ 群推送设计指示文档（ALERT_AND_QQ_BOT_SPEC）

> 版本 v1.0｜定位：CSQuant 实时监控与主动汇报子系统。
> 目标：库存/自选饰品价格异动、买卖信号、大盘情绪切换、净值回撤、数据源故障——在发生时**主动推送到 QQ 群**，而不是等人看 Dashboard。

---

## 1. 总体方案

```
scheduler（每10分钟 evaluate）
  → AlertService.evaluate_all()
      → 规则引擎 alerter/rules.py（5类内置规则，纯函数可测）
      → 触发 → 去抖检查（同规则+同标的，冷却期内不重复）
      → QQBotClient.send_group_message()
          → NapCat/Lagrange 本地 OneBot HTTP API → QQ 群
      → alert_record 落库（可追溯）
```

**为什么选 NapCat/Lagrange（OneBot 协议）而不是 QQ 官方 Bot**：

| 方案 | 可行性结论 |
|---|---|
| QQ 官方开放平台 Bot（q.qq.com） | 需企业/个人开发者认证+审核，群聊能力受限，周期长——不适合个人即时告警 |
| **NapCat / Lagrange.Core（OneBot 11）** | ✅ 本地登录一个 QQ 小号即获得 HTTP/WebSocket API，社区活跃（go-cqhttp 停更后的主流继任者），消息直达任意该号所在群 |
| Server酱/企业微信/Telegram | 更稳但非 QQ，不符合需求 |

**部署形态**：用户在 Windows 本地跑 NapCat（登录专用 QQ 小号）→ 开放 `http://127.0.0.1:3000`（OneBot HTTP）→ CSQuant 告警服务 POST `/send_group_msg`。

**安全红线**：必须用小号（协议登录有封号风险）；HTTP 端口只监听 127.0.0.1；access_token 写入 `.env` 不进 Git；推送内容不含任何密钥。

## 2. 告警规则（V1 内置 5 类）

| rule_type | 名称 | 触发条件（阈值存 alert_rule 表，页面可改） | 默认冷却 |
|---|---|---|---|
| `price_change` | 价格异动 | 自选池单品 BUFF 在售价相邻两次采集涨跌幅 ≥ threshold% | 60 min |
| `signal_trigger` | 买卖信号 | 新信号 BUY/SELL 且 confidence ≥ threshold | 120 min/标的 |
| `market_regime_change` | 大盘情绪切换 | market_index 最新 regime ≠ 上一次 regime | 360 min |
| `nav_drawdown` | 净值回撤 | 净值序列当前回撤 ≥ threshold | 720 min |
| `datasource_down` | 数据源故障 | 健康状态 down（含网关拦截/熔断） | 60 min/源 |

- `price_change` 对比方式：`quote_ticks` 同 item+platform 最近两条 `sell_price IS NOT NULL` 记录（不为 0 纪律）。
- 评估范围：`price_change`/`signal_trigger` 只评估 watchlist L1/L2，避免全库扫描。
- 规则表字段：`rule_id, rule_type, name, enabled, threshold, cooldown_min, params_json, created_at, updated_at`；首次启动 `ensure_default_alert_rules()` 写入 5 条默认规则。

## 3. 去抖与分级

- 去抖键：`rule_id + target`（target = item_uuid / source / "*"）；`alert_record` 查最近一条时间，冷却期内跳过。
- 分级：INFO（信号/切换）与 ALERT（异动/回撤/故障），消息前缀区分；ALERT 失败重发 1 次。

## 4. QQ Bot 客户端（OneBot 11 HTTP）

```
POST {QQ_BOT_HTTP}/send_group_msg
Headers: Authorization: Bearer {QQ_BOT_TOKEN}（可选）
Body: {"group_id": 123456789, "message": "文本"}
响应: {"status": "ok", "retcode": 0, "data": {"message_id": 123}}
```

- 健康检查：`GET /get_status`（online==true）。
- 消息模板（`build_alert_message`）：`【CSQuant·ALERT】价格异动\nAK-47 | 红线 (久经沙场)\nBUFF 在售价 92.50 → 101.80（+10.1%）\n时间 2026-07-22 10:30`。
- 失败处理：网络错误/retcode≠0 → 记 `alert_record(pushed=0, error)`，不阻塞评估。

## 5. 配置

`.env`：`QQ_BOT_HTTP=http://127.0.0.1:3000`、`QQ_BOT_TOKEN=`（可选）、`QQ_GROUP_ID=`（群号）。
`config.json.alert`：`evaluate_interval_sec=600`、`enabled=true`（总开关）、`default_cooldown_min=60`。

## 6. 数据表

```sql
CREATE TABLE IF NOT EXISTS alert_rule (
    rule_id TEXT PRIMARY KEY, rule_type TEXT NOT NULL, name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, threshold REAL, cooldown_min INTEGER NOT NULL DEFAULT 60,
    params_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alert_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, rule_id TEXT NOT NULL,
    rule_type TEXT NOT NULL, level TEXT NOT NULL, target TEXT, message TEXT NOT NULL,
    pushed INTEGER NOT NULL DEFAULT 0, error TEXT);
```

## 7. 前端（新增 🔔 告警页签）

① QQ 连接卡：状态灯 + 「发送测试消息」按钮；② 规则管理：每规则 expander（开关/阈值/冷却/保存）；③ 最近 50 条告警记录（时间/级别/内容/推送状态）。

## 8. NapCat 接入步骤（用户手册）

1. 下载 NapCat（或 Lagrange.OneBot）Windows 版，登录专用 QQ 小号；
2. 配置 OneBot HTTP：监听 `127.0.0.1:3000`，可选设置 access_token；
3. 小号加入目标 QQ 群；
4. `.env` 填 `QQ_GROUP_ID`（群号）；
5. Dashboard 🔔告警页 → 「发送测试消息」看到群内消息即完成。

## 9. 风险与注意事项

- 协议登录有封号风险 → 必须小号；频繁推送可能被限制 → 冷却与合并推送；
- 公司网关不影响本地方案（NapCat 走 QQ 协议出网，与域名拦截无关）；
- 告警内容仅投研参考，不构成投资建议。
