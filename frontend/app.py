"""CSQuant Dashboard · 深色金融终端风格。

启动：python -m streamlit run frontend/app.py --server.port 8504
规范：页面只调 services 层；红涨绿跌；价格带 ¥；缺失显示「待更新」不为 0。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import streamlit as st
except ImportError:  # pragma: no cover
    sys.exit("未安装 streamlit：pip install streamlit plotly")

import plotly.graph_objects as go  # noqa: E402

from run import get_context  # noqa: E402
from utils.timeutils import to_cn_str  # noqa: E402

st.set_page_config(page_title="CSQuant", page_icon="📊", layout="wide",
                   initial_sidebar_state="collapsed")

# ---------------- 全局样式 ----------------
st.markdown("""
<style>
  /* 品牌栏 */
  .brand-bar {display:flex;align-items:baseline;gap:14px;padding:4px 0 2px 0;}
  .brand-title {font-size:30px;font-weight:800;letter-spacing:1px;color:#e6e8ee;}
  .brand-title span {color:#00d4aa;}
  .brand-sub {font-size:12px;color:#8b93a7;}
  /* KPI 卡片 */
  .kpi-grid {display:flex;gap:12px;flex-wrap:wrap;margin:6px 0 10px 0;}
  .kpi-card {flex:1;min-width:150px;background:#161a26;border-radius:12px;
             padding:14px 16px;border-left:4px solid #00d4aa;}
  .kpi-card.warn {border-left-color:#f23645;}
  .kpi-card.neutral {border-left-color:#6b7280;}
  .kpi-label {font-size:12px;color:#8b93a7;margin-bottom:6px;}
  .kpi-value {font-size:24px;font-weight:700;color:#e6e8ee;line-height:1.15;}
  .kpi-delta {font-size:12px;margin-top:4px;}
  .up {color:#f23645;} .down {color:#089981;} .flat {color:#8b93a7;}
  /* 小节标题 */
  .sec-title {font-size:16px;font-weight:700;color:#e6e8ee;margin:14px 0 8px 0;
              padding-left:10px;border-left:3px solid #00d4aa;}
  /* 信号卡 */
  .signal-card {background:#161a26;border-radius:10px;padding:12px 16px;margin-bottom:10px;
                border-left:4px solid #6b7280;}
  .signal-card.buy {border-left-color:#00c853;}
  .signal-card.sell {border-left-color:#f23645;}
  .signal-top {display:flex;align-items:center;gap:10px;}
  .signal-badge {color:#04110a;font-weight:800;font-size:12px;border-radius:6px;
                 padding:2px 10px;background:#6b7280;}
  .signal-badge.buy {background:#00c853;} .signal-badge.sell {background:#f23645;color:#fff;}
  .signal-name {font-weight:700;color:#e6e8ee;font-size:15px;}
  .signal-score {margin-left:auto;font-size:22px;font-weight:800;color:#00d4aa;}
  .signal-meta {font-size:12px;color:#8b93a7;margin-top:6px;}
  /* 报价平台卡 */
  .quote-grid {display:flex;gap:10px;flex-wrap:wrap;}
  .quote-card {background:#161a26;border-radius:10px;padding:10px 14px;min-width:150px;flex:1;}
  .quote-plat {font-size:12px;color:#8b93a7;}
  .quote-price {font-size:18px;font-weight:700;color:#e6e8ee;}
  .quote-sub {font-size:11px;color:#8b93a7;margin-top:2px;}
  /* 其他 */
  div[data-testid="stMetric"] {background:#161a26;border-radius:12px;padding:10px 14px;}
  .block-container {padding-top:1.2rem;}
  #MainMenu {visibility:hidden;} footer {visibility:hidden;}
  /* —— optimus 模板设计语言：网格线背景 —— */
  [data-testid="stAppViewContainer"] {
    background-image:
      linear-gradient(to right, rgba(230,232,238,0.025) 1px, transparent 1px),
      linear-gradient(to bottom, rgba(230,232,238,0.025) 1px, transparent 1px);
    background-size: 56px 56px;
  }
  /* eyebrow 等宽标签（短横线装饰） */
  .eyebrow {font-family: 'JetBrains Mono', monospace; font-size: 11px;
    letter-spacing: 2px; text-transform: uppercase; color: #8b93a7;
    display: flex; align-items: center; gap: 10px; margin-bottom: 8px;}
  .eyebrow::before {content: ''; width: 32px; height: 1px; background: #3a3f52;}
  /* Live 呼吸点 */
  .live-dot {width: 8px; height: 8px; border-radius: 50%; background: #00c853;
    animation: pulse-dot 2s infinite; display: inline-block;}
  @keyframes pulse-dot {0%,100% {opacity: 1;} 50% {opacity: 0.25;}}
  /* hover-lift 悬浮 */
  .kpi-card, .signal-card, .quote-card, .top-card {
    transition: transform 0.35s cubic-bezier(0.34,1.56,0.64,1), box-shadow 0.35s;}
  .kpi-card:hover, .signal-card:hover, .quote-card:hover, .top-card:hover {
    transform: translateY(-4px); box-shadow: 0 8px 24px rgba(0,0,0,0.45);}
  /* TOP 榜卡片墙 */
  .top-card {background:#161a26;border-radius:10px;padding:14px 16px;
    border-top:2px solid #00d4aa; min-height:120px;}
  .top-card.risk {border-top-color:#f23645;}
  .top-card h4 {margin:0 0 8px 0;font-size:13px;color:#8b93a7;font-weight:600;}
  .top-row {display:flex;justify-content:space-between;gap:8px;padding:5px 0;
    font-size:12px;border-bottom:1px dashed #262a36;}
  .top-row:last-child {border-bottom:none;}
  .top-name {overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .top-score {color:#00d4aa;font-weight:700;font-family:monospace;}
  .top-card.risk .top-score {color:#f23645;}
  /* 异动跑马灯 */
  .ticker-wrap {overflow:hidden;background:#0a0d14;border-radius:8px;
    padding:8px 0;margin-bottom:12px;border:1px solid #1e2230;}
  .ticker {display:inline-block;white-space:nowrap;font-size:12px;color:#8b93a7;
    animation: ticker-scroll 36s linear infinite;}
  @keyframes ticker-scroll {0% {transform: translateX(0);} 100% {transform: translateX(-50%);}}
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def load_context():
    return get_context()


ctx = load_context()
svc = ctx["services"]
cfg = ctx["config"]

QUALITY_BADGE = {"A": "🟢A", "B": "🔵B", "C": "🟡C", "D": "🔴D"}
RISK_BADGE = {"L": "🟢低", "M": "🟡中", "H": "🔴高"}
REGIME_EMOJI = {"极度恐慌": "🔴", "偏空": "🟠", "震荡": "⚪",
                "偏多": "🟢", "极度贪婪": "🔥"}


def fmt_price(v) -> str:
    return f"¥{v:,.2f}" if v is not None else "待更新"


def fmt_pct(v) -> str:
    if v is None:
        return "-"
    return f"{'+' if v >= 0 else ''}{v * 100:.2f}%"


def kpi_cards(items: list[dict]) -> None:
    """items: [{label, value, delta, cls, delta_cls}]"""
    cards = "".join(
        f'<div class="kpi-card {it.get("cls", "")}">'
        f'<div class="kpi-label">{it["label"]}</div>'
        f'<div class="kpi-value">{it["value"]}</div>'
        + (f'<div class="kpi-delta {it.get("delta_cls", "flat")}">{it["delta"]}</div>'
           if it.get("delta") else "")
        + "</div>" for it in items)
    st.markdown(f'<div class="kpi-grid">{cards}</div>', unsafe_allow_html=True)


def sec(title: str) -> None:
    st.markdown(f'<div class="sec-title">{title}</div>', unsafe_allow_html=True)


def _dark_layout(fig, height=300):
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", height=height,
                      margin=dict(l=10, r=10, t=30, b=10),
                      xaxis=dict(gridcolor="#262a36"),
                      yaxis=dict(gridcolor="#262a36"))
    return fig


def area_fig(x, y, color="#00d4aa"):
    fig = go.Figure(go.Scatter(
        x=x, y=y, mode="lines", fill="tozeroy",
        line=dict(color=color, width=2), fillcolor="rgba(0,212,170,0.12)"))
    return _dark_layout(fig)


def gauge_fig(score: float, regime_zh: str | None):
    steps = [
        {"range": [0, 20], "color": "#d32f2f"}, {"range": [20, 40], "color": "#f57c00"},
        {"range": [40, 60], "color": "#4b5563"}, {"range": [60, 80], "color": "#84cc16"},
        {"range": [80, 100], "color": "#00c853"}]
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=score,
        number={"font": {"size": 46, "color": "#e6e8ee"}},
        title={"text": f"{REGIME_EMOJI.get(regime_zh, '')} {regime_zh or ''}",
               "font": {"size": 18, "color": "#8b93a7"}},
        gauge={"axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": "#444"},
               "bar": {"color": "#00d4aa", "thickness": 0.22},
               "bgcolor": "rgba(0,0,0,0)", "steps": steps,
               "threshold": {"line": {"color": "#e6e8ee", "width": 3},
                             "thickness": 0.8, "value": score}}))
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", height=250,
                      margin=dict(l=30, r=30, t=50, b=5))
    return fig


def kline_fig(klines: list[dict], title: str = ""):
    fig = go.Figure(go.Candlestick(
        x=[k["ts"][:10] for k in klines],
        open=[k["open"] for k in klines], high=[k["high"] for k in klines],
        low=[k["low"] for k in klines], close=[k["close"] for k in klines],
        increasing_line_color="#f23645", decreasing_line_color="#089981"))
    fig.update_layout(title=title, xaxis_rangeslider_visible=False)
    return _dark_layout(fig, height=340)


def factor_bar_fig(factors: list[dict], title: str = ""):
    names = [f["key"] for f in factors if f.get("available")]
    scores = [f["score"] for f in factors if f.get("available")]
    colors = ["#00c853" if s >= 60 else ("#f57c00" if s >= 40 else "#f23645")
              for s in scores]
    fig = go.Figure(go.Bar(x=scores, y=names, orientation="h",
                           marker_color=colors, text=scores, textposition="outside"))
    fig.update_layout(title=title, xaxis=dict(range=[0, 100]))
    return _dark_layout(fig, height=max(180, 40 * len(names) + 80))


def signal_card_html(s: dict) -> str:
    sig = s["signal"]
    cls = {"BUY": "buy", "SELL": "sell"}.get(sig, "")
    name = s.get("name_zh") or s["market_hash_name"]
    conf = f"{(s.get('confidence') or 0) * 100:.0f}%"
    pos = f"{(s.get('suggested_position') or 0) * 100:.0f}%"
    risk = RISK_BADGE.get(s.get("risk_level"), "-")
    ts = to_cn_str(s["ts"]) if s.get("ts") else ""
    return (f'<div class="signal-card {cls}"><div class="signal-top">'
            f'<span class="signal-badge {cls}">{sig}</span>'
            f'<span class="signal-name">{name}</span>'
            f'<span class="signal-score">{s.get("final_score")}</span></div>'
            f'<div class="signal-meta">置信度 {conf} · 风险 {risk} · '
            f'建议仓位 {pos} · {ts} · {s.get("model_version", "")}</div></div>')


# ===================== 品牌栏 =====================
st.markdown("""
<div class="brand-bar">
  <div class="brand-title">CS<span>Quant</span></div>
  <div class="brand-sub">CS2 饰品量化投研终端 · 数据仅供个人研究，不构成投资建议 · 交易执行需人工确认</div>
</div>
""", unsafe_allow_html=True)

tabs = st.tabs(["📦 我的库存", "💰 总资产与盈亏", "🌡️ 市场大盘", "🔍 单品详情",
                "🧰 饰品筛选器", "📡 买卖信号", "🧪 回测", "🔌 数据源状态",
                "⚙️ 系统设置", "🔔 告警", "🌐 全市场异动"])

# ===================== 📦 我的库存 =====================
with tabs[0]:
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("🔄 同步Steam库存", use_container_width=True):
        try:
            report = svc["inventory"].sync_steam_inventory()
            st.success(f"同步完成: 新增{report.added} 已有{report.existing} "
                       f"未匹配{len(report.unmatched)}")
        except Exception as e:  # noqa: BLE001
            st.error(f"同步失败: {e}")
    if c2.button("📸 生成净值快照", use_container_width=True):
        svc["inventory"].snapshot_nav()
        st.toast("净值快照已生成")

    pf = svc["inventory"].get_portfolio()
    pnl_cls = "up" if (pf["unrealized_pnl"] or 0) >= 0 else "down"
    kpi_cards([
        {"label": "总成本", "value": fmt_price(pf["total_cost"]), "cls": "neutral"},
        {"label": "现值", "value": fmt_price(pf["total_value"])},
        {"label": "浮动盈亏", "value": fmt_price(pf["unrealized_pnl"]),
         "delta": fmt_pct(pf["return_rate"]), "delta_cls": pnl_cls,
         "cls": "warn" if (pf["unrealized_pnl"] or 0) < 0 else ""},
        {"label": "已实现盈亏", "value": fmt_price(pf["realized_pnl"]),
         "cls": "neutral"},
    ])

    if not pf["positions"]:
        st.info("暂无持仓。点击「同步Steam库存」或到 ⚙️系统设置 手工录入。")
    else:
        rows = []
        for p in sorted(pf["positions"],
                        key=lambda x: (x["return_rate"] is not None,
                                       x["return_rate"] or 0), reverse=True):
            rows.append({
                "饰品": p.get("name_zh") or p.get("market_hash_name"),
                "数量": p["quantity"],
                "成本": fmt_price(p["cost_basis"]),
                "现价": fmt_price(p["current_price"]),
                "平台": p.get("price_platform") or "-",
                "盈亏": fmt_price(p["unrealized_pnl"]),
                "收益率": fmt_pct(p["return_rate"]),
                "状态": p["status"],
                "质量": QUALITY_BADGE.get(p["data_quality"], ""),
            })
        st.dataframe(rows, width="stretch", hide_index=True)
        st.caption(f"估值时间: {pf['as_of']}（BUFF→UUYP→STEAM 优先级取价）")

# ===================== 💰 总资产与盈亏 =====================
with tabs[1]:
    nav = svc["inventory"].get_nav_history(90)
    if not nav:
        st.info("暂无净值历史。先运行库存估值并生成净值快照（python run.py nav）。")
    else:
        latest = nav[-1]
        kpi_cards([
            {"label": "总资产", "value": fmt_price(latest["total_value"])},
            {"label": "总收益率", "value": fmt_pct(latest.get("return_rate")),
             "delta_cls": "up" if (latest.get("return_rate") or 0) >= 0 else "down"},
            {"label": "净注资", "value": fmt_price(latest.get("net_deposit")),
             "cls": "neutral"},
            {"label": "浮盈", "value": fmt_price(latest.get("unrealized_pnl"))},
        ])
        sec("净值曲线（90 天）")
        st.plotly_chart(area_fig([r["ts"][:10] for r in nav],
                                 [r["total_value"] for r in nav]),
                        width="stretch")
        if latest.get("value_by_platform"):
            sec("平台估值分布")
            dist = json.loads(latest["value_by_platform"])
            pie = go.Figure(go.Pie(labels=list(dist.keys()),
                                   values=list(dist.values()), hole=0.55,
                                   marker=dict(colors=["#00d4aa", "#5b8def", "#f57c00"])))
            pie.update_layout(paper_bgcolor="rgba(0,0,0,0)", height=260,
                              margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(pie, width="stretch")

# ===================== 🌡️ 市场大盘 =====================
with tabs[2]:
    ov = svc["market"].get_market_overview()
    score = ov.get("market_score")
    regime_zh = None
    if score is not None:
        for low, high, _, zh in cfg.strategy["market_regime_bands"]:
            if low <= score < high:
                regime_zh = zh
    g1, g2 = st.columns([1, 2])
    with g1:
        if score is not None:
            st.plotly_chart(gauge_fig(score, regime_zh), width="stretch")
        else:
            st.info("暂无 MarketScore（先采集大盘数据并计算）")
    with g2:
        kpi_cards([
            {"label": "大盘指数",
             "value": f"{ov['index_value']:,.1f}" if ov.get("index_value") else "暂无"},
            {"label": "1日涨跌", "value": fmt_pct(ov.get("change_1d")),
             "delta_cls": "up" if (ov.get("change_1d") or 0) >= 0 else "down"},
            {"label": "7日涨跌", "value": fmt_pct(ov.get("change_7d")),
             "delta_cls": "up" if (ov.get("change_7d") or 0) >= 0 else "down"},
            {"label": "宽度(涨/跌)",
             "value": (f"{ov['breadth']['up']}/{ov['breadth']['down']}"
                       if ov["breadth"]["up"] is not None else "暂无"),
             "cls": "neutral"},
        ])
        hist = svc["market"].get_market_score_history(90)
        if hist:
            sec("MarketScore 历史")
            st.plotly_chart(
                area_fig([h["ts"][:16] for h in hist],
                         [h["market_score"] for h in hist
                          if h.get("market_score") is not None][-len(hist):]
                         if any(h.get("market_score") for h in hist) else []),
                width="stretch")
    if ov.get("category_strength"):
        sec("品类强弱")
        cats = ov["category_strength"]
        fig = go.Figure(go.Bar(
            x=[c.get("change") for c in cats],
            y=[c.get("name") for c in cats], orientation="h",
            marker_color=["#f23645" if (c.get("change") or 0) >= 0 else "#089981"
                          for c in cats]))
        fig.update_layout(xaxis_title="涨跌幅")
        st.plotly_chart(_dark_layout(fig, height=max(200, 40 * len(cats))),
                        width="stretch")
    if st.button("🔄 立即计算 MarketScore"):
        r = svc["market"].compute_and_store_market_score()
        st.success(f"MarketScore={r.score} ({r.regime_zh}) 缺失因子: {r.missing_factors}")

# ===================== 🔍 单品详情 =====================
with tabs[3]:
    dao = ctx["dao"]
    query = st.text_input(
        "搜索饰品（支持中文/英文/部分名称，如：深海复仇、Aquamarine、Redline）",
        placeholder="输入关键词后从下拉框选择")
    name = None
    if query.strip():
        candidates = dao.search_items(query.strip(), limit=20)
        if not candidates:
            st.warning("未找到匹配饰品，换个关键词试试（支持中英文模糊搜索）")
        else:
            options = {
                f"{(c.get('name_zh') or c['market_hash_name'])} ｜ {c['market_hash_name']}":
                    c["market_hash_name"] for c in candidates}
            choice = st.selectbox(f"找到 {len(candidates)} 件匹配饰品，点击选择：",
                                  list(options.keys()))
            name = options[choice]
    if name:
        item = dao.get_item_by_hash_name(name)
        if not item:
            st.warning("未找到该饰品（item_master 无记录）")
        else:
            meta = dao.get_metadata(item["item_uuid"]) or {}
            snaps = dao.get_snapshots_for_item(item["item_uuid"])
            top = st.columns([1, 3])
            if meta.get("image_url"):
                top[0].image(meta["image_url"], width=180)
            with top[1]:
                st.markdown(f"### {item.get('name_zh') or name}")
                st.caption(f"{name}\n\n"
                           f"品类: {item.get('category') or '-'} ｜ "
                           f"磨损: {meta.get('wear') or '-'} ｜ "
                           f"稀有度: {meta.get('rarity') or '-'} ｜ "
                           f"收藏品: {meta.get('collection') or '-'}")
            sec("多平台报价")
            if snaps:
                quote_html = '<div class="quote-grid">'
                for s in snaps:
                    quote_html += (
                        f'<div class="quote-card">'
                        f'<div class="quote-plat">{s["platform"]} '
                        f'{QUALITY_BADGE.get(s.get("data_quality"), "")}</div>'
                        f'<div class="quote-price">{fmt_price(s.get("sell_price"))}</div>'
                        f'<div class="quote-sub">求购 {fmt_price(s.get("buy_price"))} · '
                        f'在售 {s.get("sell_count") or "-"} · '
                        f'{to_cn_str(s["ts"]) if s.get("ts") else "-"}</div></div>')
                st.markdown(quote_html + "</div>", unsafe_allow_html=True)
            else:
                st.info("暂无报价，加入自选后运行采集（python run.py collect）")
            klines = dao.get_klines(item["item_uuid"], "BUFF", "1d", 180)
            if klines:
                sec("K线（BUFF 日线）")
                st.plotly_chart(kline_fig(klines), width="stretch")
            c1, c2 = st.columns(2)
            if c1.button("⭐ 加入自选"):
                dao.add_watchlist(item["item_uuid"], tier="L2")
                dao.commit()
                st.toast("已加入自选池")
            if c2.button("📡 查看该饰品信号历史"):
                sigs = svc["signal"].get_signal_history(item["item_uuid"])
                st.dataframe(sigs if sigs else [{"提示": "暂无信号"}],
                             width="stretch", hide_index=True)

# ===================== 🧰 饰品筛选器 =====================
with tabs[4]:
    f1, f2, f3 = st.columns([2, 1, 1])
    keyword = f1.text_input("关键词", "", placeholder="中文/英文模糊搜索")
    category = f2.selectbox("品类", ["", "knife", "gloves", "weapon", "case",
                                     "sticker", "capsule", "music_kit", "patch"])
    limit = f3.slider("数量", 10, 200, 50)
    results = ctx["dao"].search_items(keyword, category or None, limit)
    st.dataframe([{
        "名称": r.get("name_zh") or r.get("market_hash_name"),
        "market_hash_name": r["market_hash_name"],
        "品类": r.get("category"),
        "磨损": r.get("wear"),
        "稀有度": r.get("rarity"),
        "BUFF价": fmt_price(r.get("buff_price")),
        "求购价": fmt_price(r.get("buff_buy_price")),
        "价格更新": to_cn_str(r["price_ts"]) if r.get("price_ts") else "待采集",
    } for r in results], width="stretch", hide_index=True)
    st.caption("价格为 BUFF 快照价；「待采集」表示行情尚未拉取（数据源加白后运行 python run.py collect）")

# ===================== 📡 买卖信号 =====================
with tabs[5]:
    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    sig_filter = c1.selectbox("信号", ["", "BUY", "HOLD", "SELL"])
    risk_filter = c2.selectbox("风险", ["", "L", "M", "H"])
    if c4.button("⚡ 立即生成信号"):
        closes = svc["market"].get_index_closes()
        mr = svc["market"].compute_and_store_market_score()
        report = svc["signal"].generate_signals(market_result=mr,
                                                market_closes=closes)
        st.success(f"生成 {report.total} 条: BUY={report.buy} "
                   f"HOLD={report.hold} SELL={report.sell}")
    st.caption(f"模型版本: {cfg.model_version}（信号仅供研究，不构成投资建议，执行需人工确认）")
    signals = svc["signal"].get_signals(sig_filter or None, risk_filter or None)
    if not signals:
        st.info("暂无信号。确保自选池有数据后点击「立即生成信号」。")
    for s in signals:
        st.markdown(signal_card_html(s), unsafe_allow_html=True)
        with st.expander("因子贡献与追溯"):
            reason = s.get("reason_json") or {}
            item_factors = reason.get("item_factors", [])
            if item_factors:
                st.plotly_chart(factor_bar_fig(item_factors), width="stretch")
            st.caption(f"信号时间: {s['ts']} ｜ 模型: {s['model_version']} ｜ "
                       f"追溯: {s.get('input_snapshot_ref')}")

# ===================== 🧪 回测 =====================
with tabs[6]:
    if st.button("▶️ 运行 E1 基准策略回测"):
        result = svc["backtest"].run_backtest()
        if result.get("error"):
            st.warning(result.get("message"))
        else:
            st.session_state["last_bt"] = result["backtest_id"]
            st.rerun()
    bts = svc["backtest"].list_backtests()
    if bts:
        bt = bts[0]
        sec(f"最近回测: {bt['strategy_name']} "
            f"（{bt['start_date'][:10]} ~ {bt['end_date'][:10]}）")
        kpi_cards([
            {"label": "总收益", "value": fmt_pct(bt.get("total_return")),
             "delta_cls": "up" if (bt.get("total_return") or 0) >= 0 else "down"},
            {"label": "最大回撤", "value": fmt_pct(-(bt.get("max_drawdown") or 0)),
             "cls": "warn"},
            {"label": "Sharpe", "value": bt.get("sharpe") or "-"},
            {"label": "Sortino", "value": bt.get("sortino") or "-"},
            {"label": "胜率", "value": fmt_pct(bt.get("win_rate"))},
            {"label": "交易次数", "value": bt.get("trade_count"), "cls": "neutral"},
        ])
        detail = svc["backtest"].get_backtest_result(bt["backtest_id"])
        if detail and detail.get("equity_json"):
            eq = detail["equity_json"]
            st.plotly_chart(area_fig(eq["dates"], eq["equity"]), width="stretch")
        with st.expander("更多指标与交易明细"):
            st.json({k: bt.get(k) for k in
                     ("annualized_return", "calmar", "profit_factor", "turnover",
                      "fee_cost", "slippage_cost", "benchmark_return")})
            trades = svc["backtest"].get_backtest_trades(bt["backtest_id"])
            st.dataframe(trades, width="stretch", hide_index=True)
        st.warning("⚠️ 数据积累期：回测区间短、bid/ask 为估计值，结论仅供参考。")
    else:
        st.info("暂无回测记录。K线积累 ≥30 天后可运行。")

# ===================== 🔌 数据源状态 =====================
with tabs[7]:
    c1, _ = st.columns([1, 4])
    if c1.button("🔄 手动补采自选池", use_container_width=True):
        report = svc["datasource"].trigger_collect()
        st.success(str(report) if report else "数据源未配置")
    health = svc["datasource"].get_health()
    if health:
        st.dataframe([{
            "数据源": h["source"], "端点": h["endpoint"].split("/")[-1][:30],
            "状态": h["status"], "延迟(ms)": h.get("latency_ms"),
            "时间": to_cn_str(h["ts"]) if h.get("ts") else "-",
        } for h in health], width="stretch", hide_index=True)
    else:
        st.info("暂无健康记录（运行一次采集后产生）")
    sec("最近事件")
    events = svc["datasource"].get_events(50)
    st.dataframe(events if events else [{"提示": "暂无事件"}],
                 width="stretch", hide_index=True)

# ===================== ⚙️ 系统设置 =====================
with tabs[8]:
    sec("密钥状态")
    st.write({
        "CSQAQ_API_TOKEN": "✅ 已配置" if cfg.csqaq_token else "❌ 未配置",
        "STEAMDT_API_KEY": "✅ 已配置" if cfg.steamdt_key else "❌ 未配置",
        "STEAM_ID_64": "✅ 已配置" if cfg.steam_id_64 else "❌ 未配置",
        "QQ Bot": "✅ 已配置" if cfg.qq_group_id else "（可选）未配置",
    })
    sec("手工建仓")
    with st.form("add_position"):
        name = st.text_input("market_hash_name（可到🔍单品详情搜索后复制）")
        qty = st.number_input("数量", min_value=1, value=1)
        price = st.number_input("买入单价(¥)", min_value=0.0, value=0.0)
        platform = st.selectbox("买入平台", ["BUFF", "UUYP", "STEAM", "C5", "其他"])
        if st.form_submit_button("建仓") and name:
            pid = svc["inventory"].add_manual_position(
                name, qty, price if price > 0 else None, None, platform)
            st.success(f"已建仓: {pid}")
    sec("策略参数（只读，修改请编辑 config.json 并递增 model_version）")
    st.json({"model_version": cfg.model_version,
             "market_weights": cfg.strategy["market_weights"],
             "item_weights": cfg.strategy["item_weights"],
             "signal_thresholds": cfg.strategy["signal_thresholds"]})

# ===================== 🔔 告警 =====================
with tabs[9]:
    dao = ctx["dao"]
    alert_svc = svc["alert"]
    status = alert_svc.qq_status()
    mkt_status = svc["market_bot"].status()
    q0, q1, q2, q3 = st.columns([1, 1, 1, 2])
    q0.metric("库存QQ Bot", "🟢 在线" if status.get("online") else "🔴 离线")
    q1.metric("全市场QQ Bot", "🟢 在线" if mkt_status.get("online") else "🔴 离线")
    if q2.button("📨 发送测试消息"):
        ok, err = alert_svc.send_test_message()
        (st.success("已发送，请到QQ群查收") if ok
         else st.error(f"发送失败: {err}"))
    if q3.button("⚡ 立即评估一次"):
        st.json(alert_svc.evaluate_all())
    if not status.get("online"):
        st.info(f"QQ Bot 未连通：{status.get('reason')}。"
                "请运行 NapCat（小号登录）并在 .env 配置 QQ_GROUP_ID，"
                "详见 docs/design/ALERT_AND_QQ_BOT_SPEC.md")

    sec("告警规则")
    for rule in dao.get_alert_rules():
        with st.expander(
                f"{'🟢' if rule['enabled'] else '⚪'} {rule['name']}（{rule['rule_type']}）"):
            r1, r2, r3, r4 = st.columns(4)
            enabled = r1.checkbox("启用", value=bool(rule["enabled"]),
                                  key=f"en_{rule['rule_id']}")
            threshold = r2.number_input("阈值", value=float(rule["threshold"] or 0.0),
                                        key=f"th_{rule['rule_id']}")
            cooldown = r3.number_input("冷却(分钟)", value=int(rule["cooldown_min"]),
                                       min_value=1, key=f"cd_{rule['rule_id']}")
            if r4.button("保存", key=f"sv_{rule['rule_id']}"):
                dao.update_alert_rule(rule["rule_id"], {
                    "enabled": enabled, "threshold": threshold,
                    "cooldown_min": int(cooldown)})
                dao.commit()
                st.toast(f"规则「{rule['name']}」已保存")

    sec("最近告警记录")
    records = dao.get_alert_records(50)
    st.dataframe(
        [{"时间": to_cn_str(r["ts"]), "级别": r["level"],
          "规则": r["rule_type"], "内容": r["message"].split("\n")[0],
          "推送": "✅" if r["pushed"] else "❌",
          "错误": r.get("error") or ""} for r in records]
        if records else [{"提示": "暂无告警记录"}],
        width="stretch", hide_index=True)

# ===================== 🌐 全市场异动 =====================
with tabs[10]:
    dao = ctx["dao"]
    ma_svc = svc["market_anomaly"]
    bot_svc = svc["market_bot"]
    st.markdown(
        '<div class="eyebrow"><span class="live-dot"></span>'
        'FULL MARKET MONITOR · 全市场异动监控（发现价格变化之前的供需异常）</div>',
        unsafe_allow_html=True)

    u_size = dao.get_meta("market_universe_size") or "0"
    u1, u2, u3, u4 = st.columns([1, 1, 1, 1])
    if u1.button("🔄 刷新Universe", use_container_width=True):
        st.success(f"Universe: {ma_svc.refresh_universe()} 件可量化饰品")
    if u2.button("⚡ 检测一轮", use_container_width=True):
        report = ma_svc.detect_round()
        st.json({k: v for k, v in report.items() if k != "emit_events"})
        if report["emit_events"]:
            dispatch = bot_svc.dispatch(report["emit_events"])
            st.caption(f"播报: {dispatch}")
    if u3.button("📊 发送TOP榜摘要", use_container_width=True):
        latest_mi = dao.get_latest_market_index("csquant_market_score") or {}
        ok, err = bot_svc.send_top_summary(
            ma_svc.top_lists(), latest_mi.get("market_score"),
            latest_mi.get("market_regime"))
        st.success("已发送") if ok else st.warning(f"未发送: {err}")
    u4.metric("Universe 规模", u_size)

    # 跑马灯：最新全市场信号
    msignals = dao.get_market_signals(limit=10)
    if msignals:
        ticker_text = " ｜ ".join(
            f"{s['signal']} {(s.get('name_zh') or s['market_hash_name'])}"
            f"({s['anomaly_score']:.0f})" for s in msignals)
        st.markdown(
            f'<div class="ticker-wrap"><div class="ticker">'
            f'{ticker_text} ｜ {ticker_text}</div></div>',
            unsafe_allow_html=True)

    # TOP 榜卡片墙
    tops = ma_svc.top_lists(5)
    if any(tops.values()):
        sec("异动 TOP 榜")
        top_cols = st.columns(5)
        for col, (title, rows) in zip(top_cols, tops.items()):
            is_risk = "出货" in title or "在售" in title
            rows_html = "".join(
                f'<div class="top-row"><span class="top-name">'
                f'{(r.get("name_zh") or r["market_hash_name"])[:16]}</span>'
                f'<span class="top-score">'
                f'{(r.get("accumulation_score") or r.get("distribution_score") or r.get("anomaly_score") or 0):.0f}</span></div>'
                for r in rows)
            col.markdown(
                f'<div class="top-card{" risk" if is_risk else ""}">'
                f'<h4>{title}</h4>{rows_html}</div>',
                unsafe_allow_html=True)
    else:
        st.info("暂无异动数据（行情采集接入后自动生成）。Universe 为空时请先运行 collect 拉取行情。")

    # 全市场信号
    sec("全市场量化信号（STRONG BUY/BUY/WATCH/REDUCE/SELL/RISK ALERT）")
    if not msignals:
        st.caption("暂无信号")
    for s in msignals:
        sig = s["signal"]
        cls = "buy" if "BUY" in sig else ("sell" if sig in ("SELL", "RISK ALERT", "REDUCE") else "")
        name = s.get("name_zh") or s["market_hash_name"]
        st.markdown(
            f'<div class="signal-card {cls}"><div class="signal-top">'
            f'<span class="signal-badge {cls}">{sig}</span>'
            f'<span class="signal-name">{name}</span>'
            f'<span class="signal-score">{s["anomaly_score"]:.0f}</span></div>'
            f'<div class="signal-meta">建仓 {s.get("accumulation_score") or 0:.0f} · '
            f'出货 {s.get("distribution_score") or 0:.0f} · '
            f'订单流 {(s.get("order_flow_score") or 0):+.0f} · '
            f'置信度 {(s.get("confidence") or 0):.2f} · '
            f'{to_cn_str(s["ts"])}</div></div>',
            unsafe_allow_html=True)
        with st.expander("判定依据（概率性推断）"):
            st.json(json.loads(s.get("reason_json") or "{}"))

    # 进行中事件（生命周期）
    sec("事件生命周期")
    open_events = dao.get_signal_events()
    st.dataframe(
        [{"饰品": (e.get("name_zh") or e["market_hash_name"]),
          "类型": e["event_type"], "状态": e["state"],
          "等级": e["severity"], "峰值分": e.get("peak_score"),
          "首发": to_cn_str(e["first_ts"]), "最近": to_cn_str(e["last_ts"]),
          "已播报": e["times_reported"]} for e in open_events]
        if open_events else [{"提示": "暂无进行中事件"}],
        width="stretch", hide_index=True)

    # 全市场播报日志
    sec("全市场播报日志（router=market）")
    mlogs = dao.get_broadcast_logs("market", 30)
    st.dataframe(
        [{"时间": to_cn_str(l["ts"]), "群": l.get("target_qq_group"),
          "类型": l["message_type"], "推送": "✅" if l["pushed"] else "❌",
          "错误": l.get("error") or ""} for l in mlogs]
        if mlogs else [{"提示": "暂无播报（配置 MARKET_QQ_GROUP_ID 后启用）"}],
        width="stretch", hide_index=True)
