# -*- coding: utf-8 -*-
"""
中证500动量选股策略回测 — Streamlit Web 应用
数据全部来自 output/ 下的回测产物（本地缓存），运行时零联网。
启动：python -m streamlit run app.py
"""
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="中证500动量选股策略回测", page_icon="chart", layout="wide")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "output")
if not os.path.isdir(OUT):
    # 兜底：项目固定位于 D 盘（与报告/缓存同源）
    OUT = r"D:\quant500_momentum\output"
SPLIT = "2022-12-31"  # 样本内/外分界

VERSIONS = {
    "mo":  dict(label="V1 基线版",  desc="250日新高动量 + ROE/PB 全局阈值过滤"),
    "v2":  dict(label="V2 行业中性增强版", desc="复合动量60% + 行业中性质量40%，月度调仓、次日开盘执行"),
    "v2c": dict(label="V2 对照（收盘执行）", desc="与 V2 相同信号，改为当日收盘执行——用于分离执行时点影响"),
    "v3a": dict(label="V3-A 风控版（最优）", desc="V2 + 固定止损-12% + 动量排名退出（月中检查前50%）"),
    "v3b": dict(label="V3-B 海龟风控版", desc="V2 + ATR(10)×2 跟踪止损 + 分档止盈 + 100日强平"),
}


# ---------------- 数据加载（带缓存） ----------------
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


def annual_table(df: pd.DataFrame) -> pd.DataFrame:
    """按年计算策略与基准收益、年度超额（净值首年按首日净值起算）。"""
    yearly = df.resample("YE").last()
    prev = yearly.shift(1)
    prev.iloc[0] = df.iloc[0]  # 首年以区间首日净值为基期
    ret = (yearly / prev - 1) * 100
    out = pd.DataFrame({"策略收益_%": ret["strategy"].round(2), "基准收益_%": ret["benchmark"].round(2)})
    out["年度超额_%"] = (out["策略收益_%"] - out["基准收益_%"]).round(2)
    out.index = out.index.year
    out.index.name = "年份"
    return out


def nav_figure(df: pd.DataFrame, label: str, height: int = 420) -> go.Figure:
    colors = {"mo": "#7f8c8d", "v2": "#2980b9", "v2c": "#8e44ad", "v3a": "#c0392b", "v3b": "#16803c"}
    color = colors.get(label_key(label), "#2980b9")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df["benchmark"], name="中证500基准",
                             line=dict(color="#b0b7bd", width=1.6)))
    fig.add_trace(go.Scatter(x=df.index, y=df["strategy"], name=label,
                             line=dict(color=color, width=2.2)))
    fig.add_vline(x=SPLIT, line_dash="dash", line_color="#e67e22",
                  annotation_text="样本内 | 样本外", annotation_position="top")
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10),
                      yaxis_title="净值（期初=1）", hovermode="x unified",
                      legend=dict(orientation="h", y=1.06, x=0))
    return fig


def label_key(label: str) -> str:
    return next(k for k, d in VERSIONS.items() if d["label"] == label)


# ---------------- 在线回测（调参实验）引擎接入 ----------------
def _resolve_project() -> str:
    """返回带 data/cache 的项目根（本机正式项目在 D 盘）。"""
    for cand in (BASE, r"D:\quant500_momentum"):
        if os.path.isdir(os.path.join(cand, "data", "cache")):
            return cand
    raise RuntimeError("未找到数据缓存目录（data/cache），在线回测不可用")


def _load_bt_context():
    import sys
    proj = _resolve_project()
    if proj not in sys.path:
        sys.path.insert(0, proj)
    import config as cfg  # noqa: F401
    import data_loader as dl
    import backtest_v2
    import backtest_v3a
    import backtest_v3b
    import metrics
    from run_backtest_v3a import load_all, build_bench_map

    cal = dl.get_index_daily(cfg.DATA_START, cfg.BT_END)["close"]
    cal_days = list(cal.index)
    cal_index = {d: i for i, d in enumerate(cal_days)}
    reb_dates = [d for d in dl.get_rebalance_dates() if d in cal_index]
    signal_pairs = backtest_v3a.month_end_signal_dates(cal_days)
    codes, daily_data = load_all(reb_dates)
    bench_map = build_bench_map(reb_dates)
    industry_map = dl.get_industry_map()
    return dict(cfg=cfg, bt2=backtest_v2, bt3a=backtest_v3a, bt3b=backtest_v3b,
                metrics=metrics, signal_pairs=signal_pairs, codes=codes,
                daily_data=daily_data, cal_index=cal_index, cal_days=cal_days,
                bench_map=bench_map, industry_map=industry_map, proj=proj)


@st.cache_resource(show_spinner="加载本地数据缓存（首次约 1~2 分钟，之后秒开）...")
def load_bt_context():
    return _load_bt_context()


def _run_web_backtest(params: tuple) -> dict:
    """按参数元组跑一次回测（不写任何文件，只返回结果）。参数顺序：
    (version, top_n, mom_w, exec_mode, stop_loss, rank_pct, atr_mult, keep_rank)"""
    import contextlib
    import io
    ctx = load_bt_context()
    cfg = ctx["cfg"]
    version, top_n, mom_w, exec_mode, stop_loss, rank_pct, atr_mult, keep_rank = params

    # 共享选股参数（等权仓位 = 1/持仓数，保持近似满仓）
    cfg.V2_TOP_N = int(top_n)
    cfg.V2_SCORE_MOM_W = float(mom_w)
    cfg.V2_SCORE_QUAL_W = 1.0 - float(mom_w)
    cfg.V2_POS_SIZE = round(1.0 / int(top_n), 4)
    # 版本专属风控参数
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


def web_nav_figure(nav: pd.DataFrame, title: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=nav.index, y=nav["benchmark"], name="中证500基准",
                             line=dict(color="#b0b7bd", width=1.6)))
    fig.add_trace(go.Scatter(x=nav.index, y=nav["strategy"], name=f"自定义参数（{title}）",
                             line=dict(color="#c0392b", width=2.2)))
    fig.add_vline(x=SPLIT, line_dash="dash", line_color="#e67e22",
                  annotation_text="样本内 | 样本外", annotation_position="top")
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10),
                      yaxis_title="净值（期初=1）", hovermode="x unified",
                      legend=dict(orientation="h", y=1.06, x=0))
    return fig


# ---------------- 侧边栏 ----------------
st.sidebar.title("中证500动量选股策略")
st.sidebar.caption("大学量化投资课程作业 · baostock 数据 · 2019-01 ~ 2026-10")
page = st.sidebar.radio("页面", ["四版本总览", "单版本详情", "参数敏感性",
                                "在线回测（调参实验）", "口径与说明"])
st.sidebar.divider()
st.sidebar.caption("数据来源：本地回测缓存（output/），运行全程离线。")

# ---------------- 页面 1：四版本总览 ----------------
if page == "四版本总览":
    st.header("四版本总览")
    st.markdown("V1 基线 → V2 行业中性增强 → V3-A/V3-B 两种风控路线的迭代对比。")

    cmp_df = load_compare()
    st.dataframe(cmp_df, width='stretch', hide_index=True)

    st.subheader("净值曲线（交互，可缩放/悬停）")
    fig = go.Figure()
    palette = {"mo": "#7f8c8d", "v2": "#2980b9", "v2c": "#b39ddb", "v3a": "#c0392b", "v3b": "#16803c"}
    for k, info in VERSIONS.items():
        if k == "v2c":
            continue  # 对照版在总览中省略，避免干扰；详情页可看
        nav = load_nav(k)
        vis = True if k in ("v3a", "benchmark") else "legendonly"
        fig.add_trace(go.Scatter(x=nav.index, y=nav["strategy"], name=info["label"],
                                 line=dict(color=palette[k], width=1.8), visible=vis))
    nav_b = load_nav("mo")
    fig.add_trace(go.Scatter(x=nav_b.index, y=nav_b["benchmark"], name="中证500基准",
                             line=dict(color="#e67e22", width=2.0)))
    fig.add_vline(x=SPLIT, line_dash="dash", line_color="#555",
                  annotation_text="样本内 | 样本外", annotation_position="top")
    fig.update_layout(height=520, yaxis_title="净值（期初=1）", hovermode="x unified",
                      legend=dict(orientation="h", y=1.06, x=0),
                      margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, width='stretch')
    st.caption("提示：点击图例可显示/隐藏各版本；V3-A（红）默认显示。")

    c1, c2 = st.columns(2)
    with c1:
        st.info("**核心结论**：V3-A 是唯一双区间信息比率均为正的版本（全区间 0.24 / 样本外 0.18），"
                "累计 124.70%、回撤 -25.53%（基准的六成），收益-回撤-效率三角度交叉验证为最优。")
    with c2:
        st.warning("**风控路线权衡**：V3-B 回撤最小（-18.33%）但样本外 IR 转负（-0.23）；"
                   "ATR 倍数敏感性显示 ×1.5 事后占优——属事后优化，正式版保留先验 ×2.0（详见敏感性页）。")

# ---------------- 页面 2：单版本详情 ----------------
elif page == "单版本详情":
    st.header("单版本详情")
    choice = st.selectbox("选择版本", [d["label"] for d in VERSIONS.values()],
                          index=3)  # 默认 V3-A
    key = label_key(choice)
    info = VERSIONS[key]
    st.markdown(f"**设计要点**：{info['desc']}")

    nav = load_nav(key)

    tab1, tab2, tab3, tab4 = st.tabs(["绩效指标", "净值与年度收益", "交易记录", "持仓快照"])

    with tab1:
        st.dataframe(load_metrics(key), width='stretch')

    with tab2:
        st.plotly_chart(nav_figure(nav, choice), width='stretch')
        st.subheader("年度收益对比")
        st.dataframe(annual_table(nav), width='stretch')

    with tab3:
        tl = load_trades(key)
        if "type" in tl.columns:
            # V3-A / V3-B：逐笔退出记录，含风控触发类型
            c1, c2 = st.columns([1, 2])
            with c1:
                st.metric("退出记录总数", len(tl))
            with c2:
                type_counts = tl["type"].value_counts().rename_axis("退出/执行类型").reset_index(name="次数")
                st.bar_chart(type_counts.set_index("退出/执行类型"))
            st.subheader("按类型统计")
            st.dataframe(type_counts, width='stretch', hide_index=True)
            with st.expander("查看原始交易记录（前 200 行）"):
                st.dataframe(tl.head(200), width='stretch')
        else:
            # V1 / V2 系：每次调仓一行的汇总记录
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

    with tab4:
        hl = load_holdings(key)
        dates = hl["exec_date"].unique().tolist()
        date_pick = st.select_slider("调仓日", options=dates, value=dates[-1])
        row = hl[hl["exec_date"] == date_pick].iloc[0]
        try:
            h = row["holdings"]
            # 格式为 "sh.600177:0.091,sz.000739:0.091,..." 的代码:权重串
            n_hold = len([s for s in str(h).split(",") if s.strip()]) if isinstance(h, str) else len(h)
        except Exception:
            n_hold = "-"
        st.markdown(f"**执行日**：{row['exec_date']}　**类型**：{row['type']}　**持仓数**：{n_hold}")
        st.caption("holdings 字段为选股结果快照（代码:权重 逗号分隔），完整明细见 output/holdings_log_*.csv")

# ---------------- 页面 3：参数敏感性 ----------------
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

# ---------------- 页面 4：在线回测（调参实验） ----------------
elif page == "在线回测（调参实验）":
    st.header("在线回测 · 调参实验")
    st.markdown(
        "在下方调整策略参数，点击 **运行回测** 后在服务器端完整重跑一遍回测引擎"
        "（93 个月度信号 × 全池日线，**单次约 5~8 分钟**）。"
        "结果仅保存在本页缓存，**不会覆盖正式版产物**；口径与正式版完全一致"
        "（相同数据/成本/统一预处理），可直接对照。")
    try:
        load_bt_context()
        ctx_ok = True
    except RuntimeError as e:
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
                exec_mode = st.selectbox("执行时点", ["open", "close"], format_func=lambda x: {"open": "次日开盘（文档口径）", "close": "当日收盘（对照）"}[x])
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
                  (f"｜止损{stop_loss:.0%}｜排名退出前{rank_pct:.0%}" if version in ("V3-A",) or (version == "V3-B" and keep_rank) else "") +
                  (f"｜ATR×{atr_mult:g}" if version == "V3-B" else ""))
        st.markdown(f"当前参数：**{pretty}**")

        if st.button("运行回测", type="primary", width='content'):
            with st.spinner(f"回测运行中（{version}，约 5~8 分钟），请勿关闭页面……"):
                res = run_web_backtest(params)
            st.session_state["web_bt"] = res
            st.session_state["web_bt_pretty"] = pretty

        res = st.session_state.get("web_bt")
        if res is not None:
            pretty_done = st.session_state.get("web_bt_pretty", "")
            st.success(f"完成：{pretty_done}")
            tabA, tabB, tabC = st.tabs(["绩效指标", "净值曲线", "风控触发与运行日志"])
            with tabA:
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**自定义参数**")
                    st.dataframe(res["stats"], width='stretch')
                with col2:
                    st.markdown("**中证500基准（同参数运行口径）**")
                    st.dataframe(res["bench"], width='stretch')
                # 与正式版对照
                official_key = {"V2": ("v2", "V2 正式版（开盘执行）"), "V3-A": ("v3a", "V3-A 正式版"), "V3-B": ("v3b", "V3-B 正式版")}
                ok, olabel = official_key[res["params"][0]]
                st.markdown(f"**与正式版对照**：{olabel}（全区间列）")
                st.dataframe(load_metrics(ok)[["全区间"]], width='stretch')
            with tabB:
                st.plotly_chart(web_nav_figure(res["nav"], pretty_done), width='stretch')
            with tabC:
                if len(res["counts"]):
                    st.bar_chart(res["counts"])
                    st.dataframe(res["counts"].rename("次数").rename_axis("类型").reset_index(), width='stretch', hide_index=True)
                else:
                    st.caption("该版本引擎无逐笔风控记录（V2 系为调仓汇总）。")
                with st.expander("运行日志（末尾 1200 字符）"):
                    st.code(res["log_tail"], language=None)

# ---------------- 页面 5：口径与说明 ----------------
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
st.caption("回测区间 2019-01 ~ 2026-10 ｜ 样本内外分界 2022-12-31 ｜ 本页数据与《中证500动量选股策略回测报告》同源")
