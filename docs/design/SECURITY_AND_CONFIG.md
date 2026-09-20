# CSQuant 安全与配置设计（SECURITY_AND_CONFIG）

## 1. 密钥清单与存储

| 密钥 | 来源 | 存储 | 轮换/应急 |
|---|---|---|---|
| `CSQAQ_API_TOKEN` | csqaq.com 用户头像处获取 | `.env` | 泄露即网站重置 Token；需重新绑定 IP 白名单 |
| `STEAMDT_API_KEY` | steamdt.com 个人中心→API管理 | `.env` | 泄露即后台重新生成 |
| `STEAM_LOGIN_SECURE` | 浏览器登录 steamcommunity 后 Cookie | `.env`（可选，仅 pricehistory/私有库存用） | 定期重登刷新；泄露立即改 Steam 密码+撤销会话 |
| `STEAM_ID_64` | 自己的 64 位 ID（非密钥） | `.env` | — |
| `LOCAL_API_TOKEN` | 自生成随机串（HTTP API V2） | `.env` | 定期轮换 |
| LLM Key（V5） | OpenAI 兼容服务商 | `.env` | 按服务商控制台 |

纪律：`.env` 在 `.gitignore`；仓库只提交 `.env.example`（值全占位）；代码内 `utils/config.py` 统一读取，禁止散读 `os.environ`；日志/落库前对 token 值脱敏（`***` 后4位）；绝不回显到前端。

## 2. 三级配置

```
.env            # 密钥与本地路径（DB_PATH、LOG_LEVEL）
config.json     # 策略参数：market_weights/item_weights/signal_thresholds/
                # confidence_weights/risk_bands/fee_table/slippage_bands/
                # collect_intervals + model_version（改动阈值必须递增版本）
.env.example    # 模板
```

`config.json` 加载时 schema 校验（键缺失→启动报错并列出缺失键）；`model_version` 格式 `signal-vX.Y.Z`。

## 3. 账号与风控安全

1. **Steam 调用纪律**：priceoverview/库存 ≤1 req/2-3s + 抖动；429 → 指数退避+熔断 30min；带 Cookie 的 pricehistory 每日限量；**强烈建议使用专用小号**登录态，避免主号市场功能受限；
2. **CSQAQ IP 白名单**：动态 IP 用其「绑定本机白名单」接口启动时自检；宽带重拨后自动重绑（失败熔断告警，禁止重试风暴）；
3. **库存隐私**：库存必须公开才能匿名同步；不愿公开则用 Cookie 方案（自担账号风险，文档明示）。

## 4. 数据安全

- SQLite 每日备份（WAL checkpoint 后复制 db 文件，保留 14 份滚动）；
- `trade_record`/`signal`/`quote_ticks` 只插不改（审计）；
- 信号留痕：reason_json + input_snapshot_ref + model_version；
- V5 LLM prompt/响应全量落 `event_log`（借鉴 CSGOTrading 审计设计）。

## 5. 合规边界（红线）

- **CSQAQ 数据「严禁商用」**：本系统仅限个人投研学习；任何对外提供信号/收费功能前必须获得 CSQAQ 企业授权或更换合规数据源；
- SteamDT「限时免费」政策变化需跟踪；
- 不进行任何违反 Steam/BUFF/悠悠用户协议的自动化交易操作（V1 无自动下单）；
- 页面与报告标注「数据仅供参考，不构成投资建议」。

## 6. 误操作防护

- 卖出登记/删除持仓/清空缓存/重置库 → 二次确认（输入「确认」）+ 写 event_log；
- 手工录入价格超当前市场价 ±50% → 警告提示（可强制）；
- 配置修改 model_version 未递增 → 拒绝保存。

## 7. 上线安全检查清单

- [ ] `.env` 未提交、`.env.example` 无真实值
- [ ] 日志无 token 明文
- [ ] 密钥均从 config.py 读取
- [ ] Steam 调用速率 ≤ 配置上限（观察 event_log 无 429）
- [ ] CSQAQ 白名单生效（health=ok）
- [ ] 数据库备份任务运行
- [ ] 危险操作均有二次确认
- [ ] 信号落库含 model_version
