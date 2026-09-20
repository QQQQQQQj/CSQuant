PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS item_master (
    item_uuid        TEXT PRIMARY KEY,
    app_id           INTEGER NOT NULL DEFAULT 730,
    market_hash_name TEXT NOT NULL,
    name_zh          TEXT,
    name_en          TEXT,
    category         TEXT,
    is_stattrak      INTEGER NOT NULL DEFAULT 0,
    is_souvenir      INTEGER NOT NULL DEFAULT 0,
    steam_name_id    INTEGER,
    buff_id          INTEGER,
    c5_id            TEXT,
    igxe_id          INTEGER,
    uuyp_id          INTEGER,
    csqaq_good_id    INTEGER,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (app_id, market_hash_name)
);

CREATE TABLE IF NOT EXISTS item_metadata (
    item_uuid     TEXT PRIMARY KEY REFERENCES item_master(item_uuid),
    weapon        TEXT,
    skin          TEXT,
    wear          TEXT,
    rarity        TEXT,
    rarity_color  TEXT,
    collection    TEXT,
    crate         TEXT,
    phase         TEXT,
    min_float     REAL,
    max_float     REAL,
    paint_index   INTEGER,
    def_index     INTEGER,
    image_url     TEXT,
    extra_json    TEXT,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quote_ticks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 TEXT NOT NULL,
    item_uuid          TEXT NOT NULL REFERENCES item_master(item_uuid),
    platform           TEXT NOT NULL,
    source             TEXT NOT NULL,
    sell_price         REAL,
    sell_count         INTEGER,
    buy_price          REAL,
    buy_count          INTEGER,
    volume_24h         INTEGER,
    reference_price    REAL,
    currency           TEXT NOT NULL DEFAULT 'CNY',
    source_update_time TEXT,
    data_quality       TEXT NOT NULL DEFAULT 'A',
    quality_flags      TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_ticks_item_ts ON quote_ticks(item_uuid, platform, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ticks_ts ON quote_ticks(ts DESC);

CREATE TABLE IF NOT EXISTS market_snapshot (
    item_uuid  TEXT NOT NULL REFERENCES item_master(item_uuid),
    platform   TEXT NOT NULL,
    source     TEXT NOT NULL,
    sell_price REAL, sell_count INTEGER, buy_price REAL, buy_count INTEGER,
    volume_24h INTEGER, reference_price REAL,
    currency   TEXT NOT NULL DEFAULT 'CNY',
    ts         TEXT NOT NULL,
    source_update_time TEXT,
    data_quality TEXT NOT NULL DEFAULT 'A',
    PRIMARY KEY (item_uuid, platform)
);

CREATE TABLE IF NOT EXISTS kline_daily (
    item_uuid TEXT NOT NULL REFERENCES item_master(item_uuid),
    platform  TEXT NOT NULL,
    period    TEXT NOT NULL DEFAULT '1d',
    ts        TEXT NOT NULL,
    open REAL NOT NULL, close REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    volume    INTEGER,
    source    TEXT NOT NULL,
    PRIMARY KEY (item_uuid, platform, period, ts)
);

CREATE TABLE IF NOT EXISTS portfolio_position (
    position_id TEXT PRIMARY KEY,
    item_uuid   TEXT NOT NULL REFERENCES item_master(item_uuid),
    asset_id    TEXT,
    quantity    INTEGER NOT NULL DEFAULT 1,
    buy_price   REAL,
    buy_time    TEXT,
    buy_platform TEXT,
    cost_basis  REAL,
    status      TEXT NOT NULL DEFAULT 'HOLDING',
    notes       TEXT,
    created_at  TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pos_item ON portfolio_position(item_uuid, status);

CREATE TABLE IF NOT EXISTS trade_record (
    trade_id    TEXT PRIMARY KEY,
    position_id TEXT REFERENCES portfolio_position(position_id),
    item_uuid   TEXT NOT NULL,
    side        TEXT NOT NULL,
    quantity    INTEGER NOT NULL,
    price       REAL NOT NULL,
    fee         REAL NOT NULL DEFAULT 0,
    platform    TEXT,
    currency    TEXT NOT NULL DEFAULT 'CNY',
    traded_at   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_snapshot (
    ts              TEXT PRIMARY KEY,
    total_value     REAL NOT NULL,
    total_cost      REAL NOT NULL,
    cash            REAL NOT NULL DEFAULT 0,
    unrealized_pnl  REAL NOT NULL,
    realized_pnl    REAL NOT NULL DEFAULT 0,
    return_rate     REAL,
    net_deposit     REAL NOT NULL DEFAULT 0,
    value_by_platform TEXT,
    currency        TEXT NOT NULL DEFAULT 'CNY'
);

CREATE TABLE IF NOT EXISTS market_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    index_name      TEXT NOT NULL,
    index_value     REAL NOT NULL,
    change_1d REAL, change_7d REAL, change_30d REAL,
    breadth_up INTEGER, breadth_down INTEGER,
    volume_score REAL, liquidity_score REAL, sentiment_score REAL,
    market_score    REAL,
    market_regime   TEXT,
    sub_indices_json TEXT,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_index_ts ON market_index(index_name, ts DESC);

CREATE TABLE IF NOT EXISTS signal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    item_uuid TEXT NOT NULL REFERENCES item_master(item_uuid),
    market_score REAL, item_score REAL, final_score REAL,
    signal TEXT NOT NULL,
    confidence REAL,
    risk_level TEXT,
    reason_json TEXT NOT NULL,
    suggested_position REAL,
    model_version TEXT NOT NULL,
    input_snapshot_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_signal_item ON signal(item_uuid, ts DESC);

CREATE TABLE IF NOT EXISTS backtest_result (
    backtest_id TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL, strategy_version TEXT NOT NULL,
    start_date TEXT NOT NULL, end_date TEXT NOT NULL,
    params_json TEXT NOT NULL,
    total_return REAL, annualized_return REAL, max_drawdown REAL,
    sharpe REAL, sortino REAL, calmar REAL,
    win_rate REAL, profit_factor REAL,
    trade_count INTEGER, turnover REAL,
    fee_cost REAL, slippage_cost REAL,
    benchmark_return REAL,
    equity_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_trade (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backtest_id TEXT NOT NULL REFERENCES backtest_result(backtest_id),
    item_uuid TEXT NOT NULL,
    side TEXT NOT NULL, quantity INTEGER NOT NULL,
    price REAL NOT NULL, fee REAL NOT NULL,
    traded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasource_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, endpoint TEXT NOT NULL, ts TEXT NOT NULL,
    latency_ms INTEGER, status TEXT NOT NULL,
    error_code TEXT, success_rate_1h REAL, freshness_p50_sec INTEGER
);

CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, level TEXT NOT NULL,
    module TEXT NOT NULL, message TEXT NOT NULL, detail_json TEXT
);

CREATE TABLE IF NOT EXISTS watchlist (
    item_uuid TEXT PRIMARY KEY REFERENCES item_master(item_uuid),
    tier TEXT NOT NULL DEFAULT 'L2',
    added_at TEXT NOT NULL, notes TEXT
);

CREATE TABLE IF NOT EXISTS sync_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- 告警规则（阈值/冷却页面可改）
CREATE TABLE IF NOT EXISTS alert_rule (
    rule_id      TEXT PRIMARY KEY,
    rule_type    TEXT NOT NULL,
    name         TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    threshold    REAL,
    cooldown_min INTEGER NOT NULL DEFAULT 60,
    params_json  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- 告警记录（可追溯）
CREATE TABLE IF NOT EXISTS alert_record (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    rule_id   TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    level     TEXT NOT NULL,
    target    TEXT,
    message   TEXT NOT NULL,
    pushed    INTEGER NOT NULL DEFAULT 0,
    error     TEXT
);

-- 全市场异动特征与评分（回测数据基础）
CREATE TABLE IF NOT EXISTS market_anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    item_uuid TEXT NOT NULL REFERENCES item_master(item_uuid),
    buy_count_change REAL, sell_count_change REAL,
    buy_price_change REAL, sell_price_change REAL, volume_change REAL,
    buy_count_zscore REAL, sell_count_zscore REAL, volume_zscore REAL,
    price_zscore REAL, spread_zscore REAL,
    cross_platform_sync REAL,
    relative_strength REAL,
    anomaly_score REAL, accumulation_score REAL, distribution_score REAL,
    order_flow_score REAL,
    liquidity_score REAL, data_quality_score REAL, confidence REAL
);
CREATE INDEX IF NOT EXISTS idx_anom_item_ts ON market_anomalies(item_uuid, ts DESC);
CREATE INDEX IF NOT EXISTS idx_anom_ts ON market_anomalies(ts DESC);

-- 全市场信号（STRONG BUY/BUY/WATCH/HOLD/REDUCE/SELL/RISK ALERT）
CREATE TABLE IF NOT EXISTS market_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    item_uuid TEXT NOT NULL REFERENCES item_master(item_uuid),
    signal TEXT NOT NULL,
    anomaly_score REAL, accumulation_score REAL, distribution_score REAL,
    order_flow_score REAL, relative_strength REAL, market_score REAL,
    confidence REAL, reason_json TEXT NOT NULL, model_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msignal_item ON market_signals(item_uuid, ts DESC);
CREATE INDEX IF NOT EXISTS idx_msignal_ts ON market_signals(ts DESC);

-- 事件生命周期（NEW/ONGOING/UPGRADED/DOWNGRADED/RESOLVED）
CREATE TABLE IF NOT EXISTS signal_events (
    event_id   TEXT PRIMARY KEY,
    item_uuid  TEXT NOT NULL REFERENCES item_master(item_uuid),
    event_type TEXT NOT NULL,            -- accumulation / distribution / anomaly
    state      TEXT NOT NULL,            -- NEW/ONGOING/UPGRADED/DOWNGRADED/RESOLVED
    severity   TEXT NOT NULL,            -- WATCH/MID/MAJOR/CRITICAL
    first_ts   TEXT NOT NULL,
    last_ts    TEXT NOT NULL,
    peak_score REAL,
    times_reported INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_item ON signal_events(item_uuid, event_type, state);

-- 双通道播报日志（router=inventory|market，路由审计）
CREATE TABLE IF NOT EXISTS qq_broadcast_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    router TEXT NOT NULL,
    target_qq_group TEXT,
    message_type TEXT NOT NULL,
    ref_id TEXT,
    message TEXT NOT NULL,
    pushed INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

-- 资金流水（注资/提现；净注资口径的唯一事实来源）
CREATE TABLE IF NOT EXISTS cash_flow (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    flow_type TEXT NOT NULL CHECK (flow_type IN ('DEPOSIT', 'WITHDRAWAL')),
    amount REAL NOT NULL CHECK (amount > 0),
    note TEXT
);

-- 信号不可变输入快照（只插不改；包含行情/因子/权重/阈值/版本）
CREATE TABLE IF NOT EXISTS signal_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
