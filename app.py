# -*- coding: utf-8 -*-
"""
中证500动量选股策略回测 — Streamlit Web 应用（答辩演示版 v2）

六个页面：四版本总览 / 单版本详情 / 参数敏感性 / 在线回测 / 答辩速查 / 口径与说明
设计目标（面向课堂演示）：
  · 启动即用：优先读取 data/web_ctx.pkl 预热缓存，避免现场解析 1109 只个股 CSV
  · 秒出结果：在线回测提供「快速演示模式」，直接读预跑结果（output/web_presets/）
  · 可解释性：持仓快照解析为明细表 + 权重条形图 + 行业分布 + 与上期变动
  · 可交付性：核心表格与图表均支持一键下载（CSV / PNG）
启动：python -m streamlit run app.py
"""
import json
import os
import pickle

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="中证500动量选股策略回测", page_icon="📈", layout="wide")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "output")
if not os.path.isdir(OUT):
    OUT = r"D:\quant500_momentum\output"          # 兜底：项目固定位于 D 盘
PRESET_DIR = os.path.join(OUT, "web_presets")
SPLIT = "2022-12-31"                              # 样本内/外分界
GITHUB = "https://github.com/tallie414/quant500-momentum"

VERSIONS = {
    "mo":  dict(label="V1 基线版",  desc="250日新高动量 + ROE/PB 全局阈值过滤"),
    "v2":  dict(label="V2 行业中性增强版", desc="复合动量60% + 行业中性质量40%，月度调仓、次日开盘执行"),
    "v2c": dict(label="V2 对照（收盘执行）", desc="与 V2 相同信号，改为当日收盘执行——用于分离执行时点影响"),
    "v3a": dict(label="V3-A 风控版（最优）", desc="V2 + 固定止损-12% + 动量排名退出（月中检查前50%）"),
    "v3b": dict(label="V3-B 海龟风控版", desc="V2 + ATR(10)×2 跟踪止损 + 分档止盈 + 100日强平"),
}
PALETTE = {"mo": "#7f8c8d", "v2": "#2980b9", "v2c": "#8e44ad", "v3a": "#c0392b", "v3b": "#16803c"}
BENCH_COLOR = "#b0b7bd"


# ==================== 通用工具 ====================
def label_key(label: str) -> str:
    """由版本显示名取内部 key；容错：未命中时返回 V3-A（避免 KeyError 白屏）。"""
    for k, d in VERSIONS.items():
        if d["label"] == label:
            return k
    return "v3a"


def csv_bytes(df: pd.DataFrame, index: bool = True) -> bytes:
    return df.to_csv(index=index).encode("utf-8-sig")


def dl_df(label: str, df: pd.DataFrame, fname: str, index: bool = True, key: str = None):
    """表格下载按钮"""
    st.download_button(label, data=csv_bytes(df, index), file_name=fname,
                       mime="text/csv", key=key, width='content')


def dl_fig(label: str, fig: go.Figure, fname: str, key: str = None):
    """图表 PNG 下载按钮（需 kaleido；缺失时给出提示而不是报错）"""
    try:
        png = fig.to_image(format="png", scale=2)
    except Exception as e:
        st.caption(f"（图表 PNG 导出不可用：{type(e).__name__}。可 `pip install kaleido` 后重试）")
        return
    st.download_button(label, data=png, file_name=fname, mime="image/png", key=key, width='content')


def fig_layout(fig: go.Figure, height: int, y_title: str = "", title: str = "") -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40 if title else 30, b=10),
                      yaxis_title=y_title, title=title, hovermode="x unified",
                      legend=dict(orientation="h", y=1.08, x=0))
    return fig


# ==================== 数据加载（带缓存） ====================
@st.cache_data
def load_nav(key: str) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(OUT, f"nav_{key}.csv"), index_col=0, parse_dates=True)
    df.index.name = "date"
    return df


@st.cache_data
def load_metrics(key: str) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(OUT, f"metrics_{key}.csv"), index_col=0)
    df.index.name = "指标"
    return df


@st.cache_data
def load_compare() -> pd.DataFrame:
    return pd.read_csv(os.path.join(OUT, "compare_4v.csv"))


@st.cache_data
def load_sensitivity() -> pd.DataFrame:
    return pd.read_csv(os.path.join(OUT, "sensitivity.csv"))


@st.cache_data
def load_trades(key: str) -> pd.DataFrame:
    return pd.read_csv(os.path.join(OUT, f"trade_log_{key}.csv"))


@st.cache_data
def load_holdings(key: str) -> pd.DataFrame:
    return pd.read_csv(os.path.join(OUT, f"holdings_log_{key}.csv"))


# ==================== 指标/图表计算 ====================
def annual_table(df: pd.DataFrame) -> pd.DataFrame:
    """按年计算策略与基准收益、年度超额（净值首年按首日净值起算）。"""
    yearly = df.resample("YE").last()
    prev = yearly.shift(1)
    prev.iloc[0] = df.iloc[0]
    ret = (yearly / prev - 1) * 100
    out = pd.DataFrame({"策略收益_%": ret["strategy"].round(2), "基准收益_%": ret["benchmark"].round(2)})
    out["年度超额_%"] = (out["策略收益_%"] - out["基准收益_%"]).round(2)
    out.index = out.index.year
    out.index.name = "年份"
    return out


def drawdown_series(nav: pd.Series) -> pd.Series:
    return nav / nav.cummax() - 1.0


def nav_figure(df: pd.DataFrame, label: str, height: int = 420) -> go.Figure:
    color = PALETTE.get(label_key(label), "#2980b9")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df["benchmark"], name="中证500基准",
                             line=dict(color=BENCH_COLOR, width=1.6)))
    fig.add_trace(go.Scatter(x=df.index, y=df["strategy"], name=label,
                             line=dict(color=color, width=2.2)))
    fig.add_vline(x=SPLIT, line_dash="dash", line_color="#e67e22",
                  annotation_text="样本内 | 样本外", annotation_position="top")
    return fig_layout(fig, height, y_title="净值（期初=1）")


def drawdown_figure(df: pd.DataFrame, label: str, height: int = 300) -> go.Figure:
    """水下曲线（回撤），策略与基准对照"""
    color = PALETTE.get(label_key(label), "#2980b9")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=drawdown_series(df["benchmark"]) * 100,
                             name="基准回撤", line=dict(color=BENCH_COLOR, width=1.2)))
    fig.add_trace(go.Scatter(x=df.index, y=drawdown_series(df["strategy"]) * 100,
                             name=f"{label}回撤", line=dict(color=color, width=1.6),
                             fill="tozeroy", fillcolor=color + "33"))
    return fig_layout(fig, height, y_title="回撤 %")


def monthly_heatmap(nav: pd.Series, label: str, height: int = 300):
    """月度收益热力图（红涨绿跌，A股习惯）"""
    m = nav.resample("ME").last()
    r = m.pct_change()
    r.iloc[0] = m.iloc[0] / nav.iloc[0] - 1
    piv = pd.DataFrame({"y": r.index.year, "m": r.index.month, "v": r.values * 100}) \
        .pivot(index="y", columns="m", values="v")
    fig = go.Figure(go.Heatmap(
        z=piv.values, x=[f"{c}月" for c in piv.columns], y=[str(i) for i in piv.index],
        colorscale="RdYlGn", zmid=0, colorbar=dict(title="%"),
        text=[[("" if pd.isna(v) else f"{v:.1f}") for v in row] for row in piv.values],
        texttemplate="%{text}", hovertemplate="%{y}年 %{x}　%{z:.2f}%<extra></extra>"))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40, b=10),
                      title=f"{label} 月度收益率（%）", xaxis_nticks=12)
    return fig, piv


def parse_holdings(s) -> pd.DataFrame:
    """把 'sh.600177:0.091,sz.000739:0.091' 解析为明细表。"""
    rows = []
    for part in str(s).split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        code, w = part.rsplit(":", 1)
        code = code.strip()
        try:
            w = float(w)
        except ValueError:
            continue
        rows.append((code, w))
    df = pd.DataFrame(rows, columns=["代码", "权重"])
    if len(df) == 0:
        return df
    df["市场"] = df["代码"].str[:2].map({"sh": "沪市", "sz": "深市"}).fillna("—")
    df["板块"] = df["代码"].map(lambda c: "科创板" if c.startswith("sh.68")
                               else ("创业板" if c.startswith("sz.30") else "主板"))
    df["权重_%"] = (df["权重"] * 100).round(2)
    df = df.sort_values(["权重", "代码"], ascending=[False, True]).reset_index(drop=True)
    df.insert(0, "序号", range(1, len(df) + 1))
    return df[["序号", "代码", "市场", "板块", "权重_%"]]


# ==================== 在线回测引擎 ====================
def _resolve_project() -> str:
    for cand in (BASE, r"D:\quant500_momentum"):
        if os.path.isdir(os.path.join(cand, "data", "cache")):
            return cand
    raise RuntimeError("未找到数据缓存目录（data/cache），在线回测不可用")


def _load_bt_context():
    """加载回测上下文。优先读预热的 pickle（数秒），否则现场解析 CSV（1~2 分钟）。"""
    import sys
    proj = _resolve_project()
    if proj not in sys.path:
        sys.path.insert(0, proj)
    import config as cfg                      # noqa: F401
    import data_loader as dl
    import backtest_v2
    import backtest_v3a
    import backtest_v3b
    import metrics

    pkl = os.path.join(proj, "data", "web_ctx.pkl")
    if os.path.exists(pkl):
        with open(pkl, "rb") as f:
            cached = pickle.load(f)           # 预热缓存：codes/daily_data/cal/bench/industry/signal_pairs
        ctx = dict(cached)
        print(f"[预热缓存] 已加载 {pkl}")
    else:
        from run_backtest_v3a import load_all, build_bench_map
        cal = dl.get_index_daily(cfg.DATA_START, cfg.BT_END)["close"]
        cal_days = list(cal.index)
        cal_index = {d: i for i, d in enumerate(cal_days)}
        reb_dates = [d for d in dl.get_rebalance_dates() if d in cal_index]
        ctx = dict(cal_days=cal_days, cal_index=cal_index,
                   signal_pairs=backtest_v3a.month_end_signal_dates(cal_days))
        codes, daily_data = load_all(reb_dates)
        ctx["codes"] = codes
        ctx["daily_data"] = daily_data
        ctx["bench_map"] = build_bench_map(reb_dates)
        ctx["industry_map"] = dl.get_industry_map()
    ctx.update(cfg=cfg, bt2=backtest_v2, bt3a=backtest_v3a, bt3b=backtest_v3b,
               metrics=metrics, proj=proj)
    return ctx


@st.cache_resource(show_spinner="加载本地数据缓存…（首次约 1~2 分钟，之后秒开）")
def load_bt_context():
    return _load_bt_context()


def _run_web_backtest(params: tuple) -> dict:
    """按参数元组跑一次回测（不写正式产物目录）。参数顺序：
    (version, top_n, mom_w, exec_mode, stop_loss, rank_pct, atr_mult, keep_rank)"""
    import contextlib
    import io
    ctx = load_bt_context()
    cfg = ctx["cfg"]
    version, top_n, mom_w, exec_mode, stop_loss, rank_pct, atr_mult, keep_rank = params

    cfg.V2_TOP_N = int(top_n)
    cfg.V2_SCORE_MOM_W = float(mom_w)
    cfg.V2_SCORE_QUAL_W = 1.0 - float(mom_w)
    cfg.V2_POS_SIZE = round(1.0 / int(top_n), 4)
    cfg.V3A_STOP_LOSS = float(stop_loss)
    cfg.V3A_RANK_EXIT_PCT = float(rank_pct)
    cfg.V3B_ATR_MULT = float(atr_mult)
    cfg.V3B_KEEP_RANK_EXIT = bool(keep_rank)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        common = (ctx["signal_pairs"], ctx["codes"], ctx["daily_data"],
                  ctx["cal_index"], ctx["bench_map"], ctx["industry_map"])
        if version == "V2":
            nav, tl, _hl = ctx["bt2"].run_backtest_v2(*common, exec_mode=exec_mode)
        elif version == "V3-A":
            nav, tl, _hl = ctx["bt3a"].run_backtest_v3a(*common)
        else:
            nav, tl, _hl = ctx["bt3b"].run_backtest_v3b(*common)

        m = ctx["metrics"]
        rows = [m.summary_stats(nav, label="全区间"),
                m.summary_stats(nav, start=cfg.BT_START, end=cfg.IN_SAMPLE_END,
                                label="样本内(2019-2022)"),
                m.summary_stats(nav, start="2023-01-01", label="样本外(2023-2026)")]
        stats_df = pd.DataFrame(
            {r["区间"]: m.format_stats(r)["策略"].values for r in rows},
            index=m.format_stats(rows[0])["指标"].values)
        bench_df = pd.DataFrame(
            {r["区间"]: m.format_stats(r)["中证500基准"].values for r in rows},
            index=m.format_stats(rows[0])["指标"].values)

    counts = tl["type"].value_counts() if ("type" in tl.columns and len(tl)) else pd.Series(dtype=int)
    return {"nav": nav, "stats": stats_df, "bench": bench_df,
            "counts": counts, "log_tail": buf.getvalue()[-1200:], "params": params}


@st.cache_data(show_spinner=False)
def run_web_backtest(params: tuple) -> dict:
    return _run_web_backtest(params)


# ==================== 预跑方案（快速演示模式） ====================
def list_presets() -> list:
    fp = os.path.join(PRESET_DIR, "index.json")
    if not os.path.exists(fp):
        return []
    try:
        with open(fp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


@st.cache_data
def load_preset(pid: str) -> dict:
    nav = pd.read_csv(os.path.join(PRESET_DIR, f"{pid}_nav.csv"), index_col=0, parse_dates=True)
    nav.index.name = "date"
    stats = pd.read_csv(os.path.join(PRESET_DIR, f"{pid}_stats.csv"), index_col=0)
    bench = pd.read_csv(os.path.join(PRESET_DIR, f"{pid}_bench.csv"), index_col=0)
    with open(os.path.join(PRESET_DIR, f"{pid}_meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    return {"nav": nav, "stats": stats, "bench": bench, "meta": meta,
            "counts": pd.Series(meta.get("counts", {}), dtype=int)}


def web_nav_figure(nav: pd.DataFrame, title: str, height: int = 420) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=nav.index, y=nav["benchmark"], name="中证500基准",
                             line=dict(color=BENCH_COLOR, width=1.6)))
    fig.add_trace(go.Scatter(x=nav.index, y=nav["strategy"], name=f"自定义参数（{title}）",
                             line=dict(color="#c0392b", width=2.2)))
    fig.add_vline(x=SPLIT, line_dash="dash", line_color="#e67e22",
                  annotation_text="样本内 | 样本外", annotation_position="top")
    return fig_layout(fig, height, y_title="净值（期初=1）")


def render_bt_result(res: dict, title: str, version: str, note: str = ""):
    """统一渲染一次回测结果（在线自定义 / 预跑方案共用）。"""
    if note:
        st.caption(note)
    tabA, tabB, tabC = st.tabs(["绩效指标", "净值曲线", "风控触发与运行日志"])
    with tabA:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**自定义参数**")
            st.dataframe(res["stats"], width='stretch')
        with col2:
            st.markdown("**中证500基准（同口径）**")
            st.dataframe(res["bench"], width='stretch')
        official = {"V2": ("v2", "V2 正式版（开盘执行）"),
                    "V3-A": ("v3a", "V3-A 正式版"), "V3-B": ("v3b", "V3-B 正式版")}
        if version in official:
            ok, olabel = official[version]
            st.markdown(f"**与正式版对照**：{olabel}（全区间列）")
            st.dataframe(load_metrics(ok)[["全区间"]], width='stretch')
        dl_df("⬇ 下载自定义参数绩效（CSV）", res["stats"], "custom_stats.csv", key="dl_stats")
    with tabB:
        fig = web_nav_figure(res["nav"], title)
        st.plotly_chart(fig, width='stretch')
        c1, c2 = st.columns(2)
        with c1:
            dl_df("⬇ 下载净值序列（CSV）", res["nav"], "custom_nav.csv", key="dl_nav")
        with c2:
            dl_fig("⬇ 下载净值图（PNG）", fig, "custom_nav.png", key="dl_nav_png")
        st.subheader("年度收益对比")
        at = annual_table(res["nav"])
        st.dataframe(at, width='stretch')
    with tabC:
        counts = res["counts"]
        if len(counts):
            st.bar_chart(counts)
            st.dataframe(counts.rename("次数").rename_axis("类型").reset_index(),
                         width='stretch', hide_index=True)
        else:
            st.caption("该版本引擎无逐笔风控记录（V2 系为调仓汇总）。")
        if res.get("log_tail"):
            with st.expander("运行日志（末尾 1200 字符）"):
                st.code(res["log_tail"], language=None)


def add_trade_markers(fig: go.Figure, nav: pd.DataFrame, tl: pd.DataFrame) -> go.Figure:
    """在净值图上标注关键买卖点（课程要求）：调仓买入▲ / 排名退出◆ / 止损最集中日▼。"""
    s = nav["strategy"]

    def _y(days):
        days = pd.DatetimeIndex(days)
        return s.reindex(days)

    if "type" in tl.columns:
        buys = pd.to_datetime(tl.loc[tl["type"] == "调仓买入", "exec_date"].unique())
        fig.add_trace(go.Scatter(x=buys, y=_y(buys), mode="markers",
                                 marker=dict(symbol="triangle-up", size=7, color="#16803c"),
                                 name=f"调仓买入（{len(buys)} 次，全标）"))
        ranks = pd.to_datetime(tl.loc[tl["type"] == "排名退出", "exec_date"].unique())
        if len(ranks):
            fig.add_trace(go.Scatter(x=ranks, y=_y(ranks), mode="markers",
                                     marker=dict(symbol="diamond", size=7, color="#e67e22"),
                                     name=f"排名退出（{len(ranks)} 天，全标）"))
        stop_cnt = tl.loc[tl["type"] == "止损"].groupby("exec_date").size()
        if len(stop_cnt):
            top = stop_cnt.sort_values(ascending=False).head(10)
            days = pd.to_datetime(top.index)
            fig.add_trace(go.Scatter(x=days, y=_y(days), mode="markers",
                                     marker=dict(symbol="triangle-down", size=11, color="#1a1a8c"),
                                     name=f"止损最集中10日（全期 {int(stop_cnt.sum())} 笔 / {len(stop_cnt)} 天）",
                                     text=[f"{v}笔止损" for v in top.values],
                                     hovertemplate="%{x}<br>%{text}<extra></extra>"))
    else:  # V1/V2 系：每期调仓一行
        dcol = "exec_date" if "exec_date" in tl.columns else "date"
        reb = pd.to_datetime(tl[dcol].unique())
        fig.add_trace(go.Scatter(x=reb, y=_y(reb), mode="markers",
                                 marker=dict(symbol="triangle-up", size=7, color="#16803c"),
                                 name=f"调仓执行日（{len(reb)} 次，全标）"))
    return fig


# ==================== 侧边栏 ====================
st.sidebar.title("中证500动量选股策略")
st.sidebar.caption("大学量化投资课程作业 · baostock 数据 · 2019-01 ~ 2026-10")
page = st.sidebar.radio("页面", ["四版本总览", "单版本详情", "参数敏感性",
                                 "在线回测（调参实验）", "答辩速查（一页纸）", "口径与说明"])
st.sidebar.divider()
with st.sidebar.expander("环境与状态"):
    try:
        proj = _resolve_project()
        warmed = os.path.exists(os.path.join(proj, "data", "web_ctx.pkl"))
    except Exception:
        proj, warmed = BASE, False
    n_preset = len(list_presets())
    st.write("数据缓存：", "✅ 已预热（在线回测秒开）" if warmed else "⚠ 未预热（首次在线回测需先解析数据）")
    st.write("预跑方案：", f"✅ {n_preset} 组可用（快速演示模式）" if n_preset else "⚠ 无（如需秒出请跑 precompute_presets.py）")
    st.caption("预热命令：`python precompute_presets.py`")
st.sidebar.divider()
st.sidebar.caption("数据来源：本地回测缓存（output/），运行全程离线。")


# ==================== 页面 1：四版本总览 ====================
if page == "四版本总览":
    st.header("四版本总览")
    st.markdown("V1 基线 → V2 行业中性增强 → V3-A/V3-B 两种风控路线的迭代对比。")

    cmp_df = load_compare()

    # —— 顶部关键指标卡（V3-A vs 基准）——
    def _row(keyword):
        m = cmp_df[cmp_df["版本"].str.contains(keyword, na=False)]
        return m.iloc[0] if len(m) else None
    r_a, r_b = _row("V3-A"), _row("基准")
    if r_a is not None and r_b is not None:
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("V3-A 全区间累计", f"{r_a['全区间累计_%']:.2f}%",
                  f"超额 {r_a['全区间累计_%'] - r_b['全区间累计_%']:+.2f}pp vs 基准")
        k2.metric("年化超额收益", f"{r_a['全区间年化超额_%']:+.2f}%",
                  f"夏普 {r_a['全区间夏普']:.2f}（基准 {r_b['全区间夏普']:.2f}）")
        k3.metric("最大回撤", f"{r_a['全区间最大回撤_%']:.2f}%",
                  f"较基准 {r_a['全区间最大回撤_%'] - r_b['全区间最大回撤_%']:+.2f}pp",
                  delta_color="off")
        k4.metric("信息比率 IR（全/样本外）", f"{r_a['全区间IR']} / {r_a['样本外IR']}",
                  "四版本中唯一双区间为正")

    st.dataframe(cmp_df, width='stretch', hide_index=True)
    dl_df("⬇ 下载四版本对比表（CSV）", cmp_df, "compare_4v.csv", index=False, key="dl_cmp")

    st.subheader("净值曲线（交互，可缩放/悬停）")
    fig = go.Figure()
    for k, info in VERSIONS.items():
        if k == "v2c":
            continue
        nav = load_nav(k)
        vis = True if k == "v3a" else "legendonly"
        fig.add_trace(go.Scatter(x=nav.index, y=nav["strategy"], name=info["label"],
                                 line=dict(color=PALETTE[k], width=1.8), visible=vis))
    nav_b = load_nav("mo")
    fig.add_trace(go.Scatter(x=nav_b.index, y=nav_b["benchmark"], name="中证500基准",
                             line=dict(color="#e67e22", width=2.0)))
    fig.add_vline(x=SPLIT, line_dash="dash", line_color="#555",
                  annotation_text="样本内 | 样本外", annotation_position="top")
    fig.update_layout(height=520, yaxis_title="净值（期初=1）", hovermode="x unified",
                      legend=dict(orientation="h", y=1.06, x=0),
                      margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, width='stretch')
    c1, c2, _ = st.columns([1, 1, 3])
    with c1:
        dl_fig("⬇ 净值图（PNG）", fig, "nav_4v.png", key="dl_nav4v")
    st.caption("提示：点击图例可显示/隐藏各版本；V3-A（红）默认显示。")

    st.subheader("样本内 / 样本外 累计收益对比")
    plot_df = cmp_df[~cmp_df["版本"].str.contains("基准")]
    fig2 = go.Figure()
    fig2.add_trace(go.Bar(x=plot_df["版本"], y=plot_df["样本内累计_%"], name="样本内 2019-2022",
                          marker_color="#5dade2"))
    fig2.add_trace(go.Bar(x=plot_df["版本"], y=plot_df["样本外累计_%"], name="样本外 2023-2026",
                          marker_color="#c0392b"))
    fig2.update_layout(barmode="group", height=360, yaxis_title="累计收益 %",
                       margin=dict(l=10, r=10, t=30, b=10),
                       legend=dict(orientation="h", y=1.1, x=0))
    st.plotly_chart(fig2, width='stretch')

    c1, c2 = st.columns(2)
    with c1:
        st.info("**核心结论**：V3-A 是唯一双区间信息比率均为正的版本（全区间 0.24 / 样本外 0.18），"
                "累计 124.70%、回撤 -25.53%（基准的六成），收益-回撤-效率三角度交叉验证为最优。")
    with c2:
        st.warning("**风控路线权衡**：V3-B 回撤最小（-18.33%）但样本外 IR 转负（-0.23）；"
                   "ATR 倍数敏感性显示 ×1.5 事后占优——属事后优化，正式版保留先验 ×2.0（详见敏感性页）。")


# ==================== 页面 2：单版本详情 ====================
elif page == "单版本详情":
    st.header("单版本详情")
    choice = st.selectbox("选择版本", [d["label"] for d in VERSIONS.values()], index=3)  # 默认 V3-A
    key = label_key(choice)
    info = VERSIONS[key]
    st.markdown(f"**设计要点**：{info['desc']}")

    nav = load_nav(key)

    tab1, tab2, tab3, tab4 = st.tabs(["绩效指标", "净值与年度收益", "交易记录", "持仓快照"])

    with tab1:
        st.dataframe(load_metrics(key), width='stretch')
        dl_df("⬇ 下载该版本绩效（CSV）", load_metrics(key), f"metrics_{key}.csv", key="dl_m")

    with tab2:
        show_marks = st.toggle("标注关键买卖点（课程要求）", value=True)
        fig_nav = nav_figure(nav, choice)
        if show_marks:
            fig_nav = add_trade_markers(fig_nav, nav, load_trades(key))
        st.plotly_chart(fig_nav, width='stretch')
        if show_marks:
            st.caption("买卖点口径：调仓买入=月末信号次日开盘的每次调仓；V3 系另标注风控卖出"
                       "（止损笔数多且分散，仅标当日止损最集中的 10 天；排名退出全部标注）。"
                       "完整逐笔记录见「交易记录」标签与 output/trade_log_*.csv。")
        st.plotly_chart(drawdown_figure(nav, choice), width='stretch')
        st.caption("水下曲线：策略与基准的逐日回撤深度，用于判断「赚的钱是否用不可承受的回撤换来」。")
        st.subheader("年度收益对比")
        at = annual_table(nav)
        st.dataframe(at, width='stretch')
        dl_df("⬇ 下载年度收益（CSV）", at, f"annual_{key}.csv", key="dl_a")
        st.subheader("月度收益热力图")
        hm, piv = monthly_heatmap(nav["strategy"], choice)
        st.plotly_chart(hm, width='stretch')
        st.caption("红=上涨，绿=下跌（A股习惯配色）。可快速识别策略的强势/失效月份是否集中。")
        dl_df("⬇ 下载月度收益表（CSV）", piv, f"monthly_{key}.csv", key="dl_mh")

    with tab3:
        tl = load_trades(key)
        if "type" in tl.columns:
            c1, c2 = st.columns([1, 2])
            with c1:
                st.metric("退出记录总数", len(tl))
            with c2:
                type_counts = tl["type"].value_counts().rename_axis("退出/执行类型").reset_index(name="次数")
                st.bar_chart(type_counts.set_index("退出/执行类型"))
            st.subheader("按类型统计")
            st.dataframe(type_counts, width='stretch', hide_index=True)
            st.subheader("盈亏分布（逐笔退出）")
            if "pnl_pct" in tl.columns:
                pnl = pd.to_numeric(tl["pnl_pct"], errors="coerce").dropna()
                if len(pnl):
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("平均单笔收益", f"{pnl.mean() * 100:.2f}%")
                    c2.metric("中位数", f"{pnl.median() * 100:.2f}%")
                    c3.metric("盈利占比", f"{(pnl > 0).mean() * 100:.1f}%")
                    c4.metric("最好 / 最差", f"{pnl.max() * 100:.1f}% / {pnl.min() * 100:.1f}%")
                    figp = go.Figure(go.Histogram(x=pnl * 100, nbinsx=60,
                                                  marker_color="#c0392b"))
                    figp.update_layout(height=300, xaxis_title="单笔收益 %",
                                      yaxis_title="笔数", margin=dict(l=10, r=10, t=30, b=10))
                    st.plotly_chart(figp, width='stretch')
            with st.expander("查看原始交易记录（前 200 行）"):
                st.dataframe(tl.head(200), width='stretch')
        else:
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("调仓期数", len(tl))
            with c2:
                n_bought = pd.to_numeric(tl.get("n_bought", tl.get("n_hold_new")), errors="coerce")
                st.metric("平均新买入数", f"{n_bought.mean():.1f}")
            with c3:
                st.metric("平均单边换手", f"{tl['turnover_sell'].mean():.1%}")
            show_cols = [c for c in ["date", "signal_date", "exec_date", "n_hold_new", "n_target",
                                     "n_bought", "turnover_sell", "turnover_buy", "cost_pct"] if c in tl.columns]
            st.dataframe(tl[show_cols], width='stretch', hide_index=True)
            st.caption("每次调仓一行：新买入/目标持仓数、双边换手率与交易成本。")
        dl_df("⬇ 下载交易记录（CSV）", tl, f"trade_log_{key}.csv", index=False, key="dl_t")

    with tab4:
        hl = load_holdings(key)
        dcol = "exec_date" if "exec_date" in hl.columns else ("date" if "date" in hl.columns else hl.columns[0])
        dates = hl[dcol].unique().tolist()
        date_pick = st.select_slider("调仓日", options=dates, value=dates[-1])
        rows = hl[hl[dcol] == date_pick].reset_index(drop=True)

        if len(rows) > 1 and "type" in hl.columns:
            opts = [f"{i + 1}. {r['type']}" for i, r in rows.iterrows()]
            pick = st.selectbox("当日事件（同日可能含调仓与风控卖出）", opts, index=len(rows) - 1)
            row = rows.iloc[opts.index(pick)]
        else:
            row = rows.iloc[0]

        hdf = parse_holdings(row["holdings"])
        if len(hdf) == 0:
            st.warning("该期持仓为空（当月信号未选出满足条件的股票）。")
        else:
            # 汇总卡
            wsum = hdf["权重_%"].sum()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("持仓数量", f"{len(hdf)} 只")
            c2.metric("权重合计", f"{wsum:.2f}%")
            c3.metric("单票权重", f"{hdf['权重_%'].max():.2f}%")
            c4.metric("剩余现金", f"{(100 - wsum):.2f}%" if "cash" not in hl.columns
                      else f"{float(row['cash']) * 100:.2f}%")
            st.caption(f"执行日 **{row[dcol]}**" + (f"　事件类型 **{row['type']}**" if "type" in hl.columns else ""))

            # 与上期变动
            idx = dates.index(date_pick)
            if idx > 0:
                prev_codes = set(parse_holdings(hl[hl[dcol] == dates[idx - 1]].iloc[-1]["holdings"])["代码"])
                cur_codes = set(hdf["代码"])
                added, removed = sorted(cur_codes - prev_codes), sorted(prev_codes - cur_codes)
                ca, cr = st.columns(2)
                ca.info(f"**本期新进 {len(added)} 只**：" + ("、".join(added[:12]) + ("…" if len(added) > 12 else "")
                                                            if added else "无"))
                cr.warning(f"**本期调出 {len(removed)} 只**：" + ("、".join(removed[:12]) + ("…" if len(removed) > 12 else "")
                                                                 if removed else "无"))

            # 明细表 + 权重条形图
            cl, cr = st.columns([1, 1])
            with cl:
                st.markdown("**持仓明细**")
                st.dataframe(hdf, width='stretch', hide_index=True, height=520)
                dl_df("⬇ 下载本期持仓（CSV）", hdf, f"holdings_{key}_{date_pick}.csv",
                      index=False, key="dl_h")
            with cr:
                st.markdown("**权重分布**")
                figw = go.Figure(go.Bar(x=hdf["权重_%"], y=hdf["代码"], orientation="h",
                                        marker_color="#2980b9",
                                        text=hdf["权重_%"], texttemplate="%{text:.2f}%"))
                figw.update_layout(height=max(320, 20 * len(hdf)),
                                   margin=dict(l=10, r=10, t=20, b=10),
                                   xaxis_title="权重 %", yaxis=dict(autorange="reversed"))
                st.plotly_chart(figw, width='stretch')

            # 行业分布（需数据缓存中的行业映射）
            if st.button("查看本期行业分布（读取行业映射）", width='content'):
                try:
                    imap = load_bt_context().get("industry_map", {})
                    hdf2 = hdf.copy()
                    hdf2["行业"] = hdf2["代码"].map(imap).fillna("未知")
                    g = hdf2.groupby("行业")["权重_%"].sum().sort_values(ascending=False).reset_index()
                    figi = go.Figure(go.Pie(labels=g["行业"], values=g["权重_%"], hole=0.42,
                                            textinfo="label+percent"))
                    figi.update_layout(height=460, margin=dict(l=10, r=10, t=20, b=10))
                    st.plotly_chart(figi, width='stretch')
                    st.dataframe(g, width='stretch', hide_index=True)
                    st.caption(f"覆盖 {len(g)} 个行业；行业中性约束为单行业权重偏离基准 ≤ ±5%。")
                except Exception as e:
                    st.caption(f"行业分布不可用：{type(e).__name__}（需 data/web_ctx.pkl 或 data/cache）")


# ==================== 页面 3：参数敏感性 ====================
elif page == "参数敏感性":
    st.header("参数敏感性测试")
    st.markdown("对两个正式版本的关键风控参数各做三档测试（其余口径完全一致，净值区间内归一）。")

    sen = load_sensitivity()
    v3a = sen[sen["版本"].str.contains("V3-A")]
    v3b = sen[sen["版本"].str.contains("V3-B")]

    st.subheader("V3-A：止损线（-10% / -12% / -15%）")
    st.dataframe(v3a, width='stretch', hide_index=True)
    st.success("**结论**：越紧越好，但 -10% 与 -12% 仅差 1.5pp，-15% 明显劣化 → 正式版 -12% 处于稳健平台区，"
               "不属过拟合的幸运数字，予以保留。")

    st.subheader("V3-B：ATR 跟踪止损倍数（×1.5 / ×2.0 / ×2.5）")
    st.dataframe(v3b, width='stretch', hide_index=True)
    st.warning("**结论（重要反转）**：原假设“×2.0 过紧导致震荡市被反复洗出”被数据推翻——真相相反，"
               "×2.0 相对过松、×2.5 是灾难、×1.5 连样本外都全面占优（累计 26.08%、超额 +2.69%）。"
               "但 ×1.5 属事后回看，事后改参数即过拟合，正式版保留文档先验的海龟经典 ×2.0，并在此如实披露。")
    dl_df("⬇ 下载敏感性结果（CSV）", sen, "sensitivity.csv", index=False, key="dl_s")

    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure(go.Bar(x=v3a["参数"].astype(str), y=v3a["全区间_累计收益率_%"],
                               marker_color="#c0392b", name="全区间累计"))
        fig.add_trace(go.Bar(x=v3a["参数"].astype(str), y=v3a["样本外_累计收益率_%"],
                             marker_color="#e59866", name="样本外累计"))
        fig.update_layout(title="V3-A 止损线 vs 累计收益", barmode="group", height=380,
                          yaxis_title="%", margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(fig, width='stretch')
    with c2:
        fig = go.Figure(go.Bar(x=v3b["参数"].astype(str), y=v3b["全区间_累计收益率_%"],
                               marker_color="#16803c", name="全区间累计"))
        fig.add_trace(go.Bar(x=v3b["参数"].astype(str), y=v3b["样本外_累计收益率_%"],
                             marker_color="#a9dfbf", name="样本外累计"))
        fig.update_layout(title="V3-B ATR 倍数 vs 累计收益", barmode="group", height=380,
                          yaxis_title="%", margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(fig, width='stretch')

    st.subheader("止损触发次数（风控活跃度）")
    fig3 = go.Figure()
    fig3.add_trace(go.Bar(x=v3a["参数"].astype(str), y=v3a["止损次数"], name="V3-A 止损次数",
                          marker_color="#c0392b"))
    fig3.add_trace(go.Bar(x=v3b["参数"].astype(str), y=v3b["跟踪止损次数"], name="V3-B 跟踪止损次数",
                          marker_color="#16803c"))
    fig3.update_layout(barmode="group", height=340, yaxis_title="触发次数",
                       margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig3, width='stretch')
    st.caption("触发次数越多 → 风控越活跃、换手与成本越高。可与累计收益结合判断「风控是否有效而非过度交易」。")


# ==================== 页面 4：在线回测 ====================
elif page == "在线回测（调参实验）":
    st.header("在线回测 · 调参实验")
    presets = list_presets()
    mode = st.radio("运行模式", ["⚡ 快速演示（预跑方案，秒出）", "🛠 完整自定义（现场重跑引擎）"],
                    horizontal=True,
                    help="课堂演示强烈建议用快速演示模式：结果是答辩前预跑好的，点击即出，不受现场机器性能影响。")
    st.divider()

    # ---------- 模式 A：快速演示 ----------
    if mode.startswith("⚡"):
        if not presets:
            st.error("尚未生成预跑方案。请在项目目录执行：\n\n"
                     "`python precompute_presets.py`\n\n"
                     "（约 30~40 分钟，只需跑一次；结果写入 output/web_presets/）")
        else:
            st.markdown(f"已预跑 **{len(presets)} 组**方案（口径与正式版完全一致）：")
            names = [p["name"] for p in presets]
            pick = st.selectbox("选择方案", names, index=0)
            meta = next(p for p in presets if p["name"] == pick)
            res = load_preset(meta["id"])
            st.success(f"已载入：{meta['name']}")
            st.markdown(
                f"**参数**：版本 {meta['params'][0]}｜持仓 {meta['params'][1]}｜动量权重 {meta['params'][2]:.2f}"
                f"｜执行 {meta['params'][3]}｜止损 {meta['params'][4]:.0%}"
                f"｜排名退出前 {meta['params'][5]:.0%}｜ATR×{meta['params'][6]:g}")
            render_bt_result(res, meta["name"], meta["params"][0], note=meta["note"])
            st.caption(f"预跑耗时记录：{meta.get('seconds', '-')} 秒（现场读取耗时 <1 秒）")

    # ---------- 模式 B：完整自定义 ----------
    else:
        st.markdown(
            "在下方调整策略参数，点击 **运行回测** 后在服务器端完整重跑一遍回测引擎"
            "（93 个月度信号 × 全池日线；**已预热缓存时约 3~6 分钟**，未预热则需先解析数据 1~2 分钟）。"
            "结果仅保存在本页缓存，**不会覆盖正式版产物**；口径与正式版完全一致"
            "（相同数据/成本/统一预处理），可直接对照。")
        ctx_ok = True
        try:
            load_bt_context()
        except Exception as e:
            ctx_ok = False
            st.warning(f"在线回测不可用：{e}")

        if ctx_ok:
            c0, c1, c2, c3 = st.columns(4)
            with c0:
                version = st.radio("策略版本", ["V2", "V3-A", "V3-B"], index=1,
                                   help="V3-A/V3-B 在 V2 选股基础上叠加不同风控")
            with c1:
                top_n = st.slider("持仓数量（等权）", 10, 60, 40, step=5)
                mom_w = st.slider("动量得分权重", 0.30, 0.90, 0.60, step=0.05,
                                  help="综合得分 = 动量×该权重 + 质量×(1-该权重)")
            with c2:
                if version == "V2":
                    exec_mode = st.selectbox("执行时点", ["open", "close"],
                                             format_func=lambda x: {"open": "次日开盘（文档口径）",
                                                                    "close": "当日收盘（对照）"}[x])
                    stop_loss, rank_pct = -0.12, 0.50
                    atr_mult, keep_rank = 2.0, True
                elif version == "V3-A":
                    exec_mode = "open"
                    stop_loss = st.slider("个股止损线", -0.20, -0.05, -0.12, step=0.01, format="%.2f")
                    rank_pct = st.slider("排名退出阈值（跌出全池前 X%）", 0.30, 0.80, 0.50, step=0.05)
                    atr_mult, keep_rank = 2.0, True
                else:
                    exec_mode = "open"
                    stop_loss, rank_pct = -0.12, 0.50
                    atr_mult = st.slider("ATR 跟踪止损倍数", 1.0, 3.0, 2.0, step=0.25)
                    keep_rank = st.checkbox("保留月度排名退出", value=True)
            with c3:
                st.caption("固定不变（文档统一口径）：\n\n"
                           "- 成本：买 0.23% / 卖 0.33%\n\n"
                           "- 统一预处理（ST/停牌/涨跌停/流动性后5%）\n\n"
                           "- V3-B 分档止盈与 100 日强平\n\n"
                           "- 月末信号 → 次日开盘执行")

            params = (version, top_n, mom_w, exec_mode, stop_loss, rank_pct, atr_mult, keep_rank)
            pretty = (f"{version}｜持仓{top_n}｜动量权重{mom_w:.2f}" +
                      (f"｜{exec_mode}执行" if version == "V2" else "") +
                      (f"｜止损{stop_loss:.0%}｜排名退出前{rank_pct:.0%}"
                       if version in ("V3-A",) or (version == "V3-B" and keep_rank) else "") +
                      (f"｜ATR×{atr_mult:g}" if version == "V3-B" else ""))
            st.markdown(f"当前参数：**{pretty}**")

            if st.button("运行回测", type="primary", width='content'):
                with st.spinner(f"回测运行中（{version}），请勿关闭页面……"):
                    res = run_web_backtest(params)
                st.session_state["web_bt"] = res
                st.session_state["web_bt_pretty"] = pretty

            res = st.session_state.get("web_bt")
            if res is not None:
                pretty_done = st.session_state.get("web_bt_pretty", "")
                st.success(f"完成：{pretty_done}")
                render_bt_result(res, pretty_done, res["params"][0])


# ==================== 页面 5：答辩速查 ====================
elif page == "答辩速查（一页纸）":
    st.header("答辩速查 · 一页纸")
    st.markdown("汇报时可直接照着这张纸讲：先结论、再证据、最后风险自陈。")

    st.subheader("① 一句话结论")
    st.success("在中证500成分股上，「复合动量选股 + 行业中性质量过滤」单独用并不能稳定战胜指数；"
               "真正把策略救回来的是**风控**——V3-A（固定止损-12% + 月度动量排名退出）是四个版本中"
               "**唯一在样本内、样本外两个区间信息比率都为正**的版本（0.24 / 0.18），"
               "累计收益 124.70%（基准 65.57%），最大回撤 -25.53%（基准 -41.81%）。")

    st.subheader("② 四版本演进与关键数字")
    st.dataframe(load_compare(), width='stretch', hide_index=True)

    st.subheader("③ 三条最可能被追问的证据")
    e1, e2, e3 = st.columns(3)
    with e1:
        st.info("**未来函数（实证案例）**\n\nV1 初版用「行号切片」取历史数据，收益虚高到 90.1%；"
                "改为**日期切片**（daily.index < t）后修正为 37.62%。约 10% 的中后期上市/退市股票受影响。")
    with e2:
        st.info("**执行时点对照**\n\nV2 次日开盘 14.84% vs 当日收盘 9.13%。"
                "差距 5.7pp 说明：结果差异来自策略本身，不是执行细节的偶然。")
    with e3:
        st.info("**参数稳健性**\n\nV3-A 止损 -10%/-12% 仅差 1.5pp，-15% 明显劣化 → -12% 处于**平台区**；"
                "关键参数仅 4 个且事前设定，非网格搜索出来的最优解。")

    st.subheader("④ 高频提问 · 标准答法")
    QA = [
        ("为什么选动量因子？",
         "中证500是中盘股池、个人投资者占比高、信息扩散慢，动量效应在这类股票上比大盘股更显著；"
         "且动量是公开文献（Jegadeesh-Titman 1993）中跨市场最稳健的因子之一，选它有理论依据而非拍脑袋。"),
        ("怎么保证没有未来函数？",
         "三道闸：①月末信号只用 T-1 及更早的行情（日期切片而非行号切片）；"
         "②财务数据按**公告日 pubDate**对齐，不用报告期直接可用；③信号次日开盘成交，"
         "不使用当日收盘价成交。V1 的事故（90.1%→37.62%）就是第三道闸缺失的实证。"),
        ("V2 加了一堆增强反而更差，为什么？",
         "V2 的行业中性 + 质量约束是**降低波动、而非提高收益**的设计；2019-2022 成长风格极端，"
         "质量/估值约束系统性剔除了涨幅最大的高估值成长股，因此收益被压、回撤却因缺乏止损仍达 -44%。"
         "这也正是必须做 V3 的原因：选股负责「买什么」，风控负责「什么时候认错」。"),
        ("V3-A 为什么会更好？是不是运气？",
         "机制上解释得通：止损截断左尾亏损、排名退出淘汰动量衰减的个股，两者都直接改善收益分布形状。"
         "证据上不是单点：全区间与样本外 IR 都为正、回撤降到基准六成、止损线处于平台区，"
         "三条证据互相独立，不是同一个数字的重复表述。"),
        ("有没有过拟合？",
         "有风险，但我们做了三件事控制：①参数数量少（持仓数、动量权重、止损线、排名阈值共 4 个）且**事前设定**；"
         "②严格留出 2023-2026 作样本外，从未用它调参；③敏感性测试披露不利结果——V3-B 的 ATR×1.5 事后更优，"
         "我们**拒绝**改用它，正式版保留先验 ×2.0，并在报告与此处如实披露。"),
        ("交易成本够真实吗？",
         "双边佣金万三 + 卖出印花税千一 + 滑点 0.2%，合计买 0.23% / 卖 0.33%，属偏保守的零售口径。"
         "V3-A 换手明显高于 V2（止损与排名退出会更频繁卖出），在扣除这些成本后仍取得 +4.45% 年化超额，"
         "说明收益不是靠不切实际的零成本换来的。"),
        ("基准用价格指数，会不会高估超额？",
         "方向相反：中证500**价格指数不含股息再投资**，而策略持有的是股票（含分红除权），"
         "因此我们的超额收益估计是**偏保守**的，真实超额只会更高不会更低。"),
        ("数据从哪来，可靠吗？",
         "baostock 开放接口，含**历史**成分股与日线行情（1109 只、93 个月度信号期）。"
         "使用历史成分股而非当前成分回溯，已避免最典型的幸存者偏差。"),
        ("最大局限是什么？",
         "四条：①单一市场、单一周期（2019-2026），未跨牛熊完整循环；②未建模涨跌停无法成交与冲击成本；"
         "③行业中性用证监会分类近似，权重基准为当前成分等权；④未做因子正交化，动量与质量可能共线。"),
        ("实盘能直接用吗？",
         "不建议直接上。可作为**策略骨架**：月度调仓、40 只等权、单票 2.5%，容量尚可、换手可控；"
         "但需补充涨跌停/停牌的成交可行性判断、资金容量下的冲击成本模型与更长时间样本的验证。"),
    ]
    for i, (q, a) in enumerate(QA):
        with st.expander(f"Q{i + 1}. {q}", expanded=(i < 2)):
            st.write(a)

    st.subheader("⑤ 与量化机构做法的差距（自评）")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown(
            "**机构标准流程**：多因子正交化 → 风险模型约束（行业/市值/风格暴露）→ 组合优化器求解权重 → "
            "冲击成本模型 → 回测与实盘一致性校验 → 滚动样本外与因子衰减监控。")
        st.markdown(
            "**我们的做法（课程作业级）**：单因子动量 + 质量过滤 → 等权 40 只 → 规则化风控（止损/排名退出）"
            " → 固定成本口径回测。")
    with g2:
        st.markdown(
            "**已经做到的（方法论纪律）**：历史成分股防幸存者偏差、日期切片 + 公告日对齐防未来函数、"
            "严格留出 2023-2026 样本外、关键参数敏感性测试、主动披露不利结果（ATR×1.5 事后更优但拒绝采用）。")
        st.markdown(
            "**没做到的（能力边界）**：风险模型与组合优化、冲击成本与涨跌停不可成交、实盘一致性、"
            "跨市场跨周期验证、因子正交化（动量与质量可能共线）。")
    st.caption("一句话定位：**结论的可靠性来自方法论纪律，而不是模型复杂度**——这是课程作业能做到、也必须做到的部分。")

    st.subheader("⑥ 汇报节奏建议（20 分钟）")
    st.markdown(
        "1. **0-2'** 一句话结论（上面 ①）\n"
        "2. **2-6'** 数据与口径：股票池、统一预处理、成本、防未来函数三道闸\n"
        "3. **6-12'** 四版本演进：V1→V2→V3-A/V3-B，每版讲清「改了什么 / 为什么改 / 结果如何」\n"
        "4. **12-16'** 证据：敏感性测试、样本外验证、过拟合自陈\n"
        "5. **16-19'** 现场演示 Web 应用（快速演示模式，切换 2~3 组预跑方案）\n"
        "6. **19-20'** 局限与展望"
    )


# ==================== 页面 6：口径与说明 ====================
else:
    st.header("口径与说明")
    st.markdown(
        """
### 数据与股票池
- **数据源**：baostock 开放接口（免费、国内直连），含历史中证500成分股、日线行情（open/high/low/close、
  isST、tradestatus、pbMRQ、pctChg、amount）、季度财务（roeAvg、netProfit，按公告日 pubDate 对齐防未来函数）。
- **股票池**：各调仓日的历史中证500成分股（非当前成分回溯），共 1109 只股票、93 个月度信号期。

### 统一预处理与成本
- 剔除 ST / 停牌 / 涨跌停 / 流动性后 5% 股票；
- 成本：双边佣金 0.03% + 卖出印花税 0.1% + 滑点 0.2%（买入 0.23% / 卖出 0.33%）。

### 策略规则（筛选标准）
- **V1 基线版**：250 日新高动量；基本面全局阈值——**最新已公告报告期年化 ROE≥8%，且 0<PB≤10**
  （PB>0 剔除资不抵债、PB≤10 剔除估值泡沫），按动量强度取前 20 只，月度调仓；
- **V2 行业中性增强版**：复合动量（0.6×60日 + 0.4×120日收益 + 5% 新高奖励）；行业中性质量因子
  （TTM-ROE≥行业中位数、PB≤行业 PB 中位数×1.5、近两季净利润不连续下滑）；均线多头过滤；
- **V3-A**：V2 + 固定止损 -12% + 动量排名退出（月中检查排名前 50%）；
- **V3-B**：V2 + ATR(10)×2 跟踪止损 + 分档止盈 + 100 日强平。
- 注：本策略使用 ROE 与 PB 两个基本面指标，**未使用 PE**。

### 回测框架
- **信号生成**：每月末用 T-1 及更早数据计算（严格无未来函数）；
- **执行**：V1/V2 系列次日开盘成交（V2 另设收盘执行对照 v2c）；
- **样本划分**：样本内 2019-01 ~ 2022-12-31，样本外 2023-01-01 ~ 2026-10（区间内净值重新归一）。

### 指标定义
- 夏普比率无风险利率取 2%；卡玛 = 年化收益 / 最大回撤；
- **信息比率 IR** = 日度主动收益（策略-基准）均值 / 标准差 × √252，衡量单位跟踪误差的超额收益效率。

### 已知局限
1. 基准为中证500**价格指数**，未含股息再投资 → 超额收益估计偏保守；
2. 行业中性以证监会行业 + 当前成分等权近似基准行业权重；
3. V3-B 的 ATR 倍数为高敏感参数（×1.5 事后占优），存在参数选择偏差风险；
4. 未建模涨跌停无法成交、大额资金冲击成本等微观约束。

### 开发过程中的教训（报告实证案例）
V1 初版因“行号切片”引入未来函数，收益虚高至 90.1%；改为**日期切片**（daily.index < t）后修正为
37.62%——约 10% 的中后期上市/退市股票受影响。所有版本均已按日期切片重跑验证。
        """
    )

    st.divider()
    st.subheader("项目结构与复现步骤")
    st.markdown(f"**代码仓库**：[{GITHUB}]({GITHUB})　（含全部源码、回测产物与文档）")
    st.code(
        "quant500_momentum/\n"
        "├── config.py                 全局参数（含 RF=2%、成本、风控阈值）\n"
        "├── data_loader.py            数据层：成分股/日线/财务/行业，本地缓存\n"
        "├── filters.py                统一预处理：ST/停牌/涨跌停/流动性\n"
        "├── strategy.py / strategy_v2.py   选股与打分（V1 / V2 复合动量+质量）\n"
        "├── backtest.py                V1 引擎\n"
        "├── backtest_v2.py             V2 引擎（支持 open/close 执行对照）\n"
        "├── backtest_v3a.py            V3-A：固定止损 + 动量排名退出\n"
        "├── backtest_v3b.py            V3-B：ATR 跟踪止损 + 分档止盈 + 100日强平\n"
        "├── metrics.py                 绩效评估（夏普/回撤/卡玛/IR）\n"
        "├── run_backtest*.py           各版本主运行脚本（产出 output/）\n"
        "├── run_sensitivity.py         参数敏感性（V3-A 止损 × V3-B ATR）\n"
        "├── build_compare.py           四版本横向对比表（compare_4v.csv）\n"
        "├── app.py                     Streamlit Web 应用（本页）\n"
        "├── precompute_presets.py      演示预热：数据缓存 + 预跑方案\n"
        "├── tests/test_smoke.py        冒烟测试（产物完整性 + 引擎可跑）\n"
        "├── data/cache/                原始数据缓存（baostock 下载，244 MB）\n"
        "└── output/                    回测产物（净值/指标/交易/持仓/图）\n",
        language=None)

    st.markdown(
        "**复现步骤**（依赖见 `requirements.txt`）：\n\n"
        "```bash\n"
        "python download_data.py          # 1. 下载原始数据（约 1~2 小时）\n"
        "python run_backtest.py           # 2. V1 基线\n"
        "python run_backtest_v2.py        # 3. V2 / V2c\n"
        "python run_backtest_v3a.py       # 4. V3-A\n"
        "python run_backtest_v3b.py       # 5. V3-B\n"
        "python run_sensitivity.py        # 6. 参数敏感性\n"
        "python build_compare.py          # 7. 四版本对比表\n"
        "python precompute_presets.py     # 8. 演示预热（可选，建议答辩前跑）\n"
        "python -m streamlit run app.py   # 9. 启动 Web 应用\n"
        "```\n\n"
        "单元测试/冒烟：`python tests/test_smoke.py`（无需 pytest，纯标准库实现）。"
    )

st.divider()
st.caption("回测区间 2019-01 ~ 2026-10 ｜ 样本内外分界 2022-12-31 ｜ 本页数据与《中证500动量选股策略回测报告》同源")
