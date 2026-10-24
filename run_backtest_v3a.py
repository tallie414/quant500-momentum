# -*- coding: utf-8 -*-
"""
run_backtest_v3a.py —— V3-A 主运行脚本（V2选股 + 固定-12%止损 + 每月动量排名退出）
产出（output/ 目录，文件名带 v3a，不覆盖 V1/V2 任何结果）：
  nav_v3a.png / nav_v3a.csv     净值对比图 / 每日净值序列
  annual_v3a.png                年度收益对比
  metrics_v3a.csv / metrics_bench_v3a.csv
  trade_log_v3a.csv             调仓+风控卖出明细（type列区分 调仓/止损/排名退出）
  holdings_log_v3a.csv          每期持仓明细
用法：python run_backtest_v3a.py
"""
import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config as cfg
import data_loader as dl
import backtest_v3a
import metrics

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

TAG = "v3a"


def load_all(reb_dates):
    codes = dl.get_universe_codes(reb_dates)
    daily_data = {}
    for c in codes:
        fp = os.path.join(cfg.DAILY_DIR, f"{c}.csv")
        if os.path.exists(fp):
            df = pd.read_csv(fp, index_col=0, parse_dates=True)
            daily_data[c] = df[df.index <= pd.Timestamp(cfg.BT_END)]
        else:
            daily_data[c] = pd.DataFrame()
    return codes, daily_data


def build_bench_map(reb_dates):
    bench_map = {}
    for d in reb_dates:
        cons = dl.get_zz500_constituents(str(d.date()))
        if cons is not None and len(cons):
            bench_map[(d.year, d.month)] = cons["code"].tolist()
    return bench_map


def main():
    cal = dl.get_index_daily(cfg.DATA_START, cfg.BT_END)["close"]
    cal_days = list(cal.index)
    cal_index = {d: i for i, d in enumerate(cal_days)}

    reb_dates = dl.get_rebalance_dates()
    reb_dates = [d for d in reb_dates if d in cal_index]
    signal_pairs = backtest_v3a.month_end_signal_dates(cal_days)
    print(f"V3-A：V2选股 + 逐日-12%止损 + 每月动量排名退出（月中检查，跌出前50%次日开盘卖出）")
    print(f"信号日 {len(signal_pairs)} 个: {signal_pairs[0][0].date()} ~ {signal_pairs[-1][0].date()}")

    codes, daily_data = load_all(reb_dates)
    print(f"股票池全集 {len(codes)} 只，其中有效日线 {sum(1 for v in daily_data.values() if len(v))} 只")

    bench_map = build_bench_map(reb_dates)
    industry_map = dl.get_industry_map()
    print(f"行业记录 {len(industry_map)} 条")

    nav_df, trade_log, holdings_log = backtest_v3a.run_backtest_v3a(
        signal_pairs, codes, daily_data, cal_index, bench_map, industry_map)

    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)

    rows = [
        metrics.summary_stats(nav_df, label="全区间"),
        metrics.summary_stats(nav_df, start=cfg.BT_START, end=cfg.IN_SAMPLE_END, label="样本内(2019-2022)"),
        metrics.summary_stats(nav_df, start="2023-01-01", label="样本外(2023-2026)"),
    ]
    stats_df = pd.DataFrame(
        {r["区间"]: metrics.format_stats(r)["策略"].values for r in rows},
        index=metrics.format_stats(rows[0])["指标"].values)
    bench_df = pd.DataFrame({r["区间"]: metrics.format_stats(r)["中证500基准"].values
                             for r in rows}, index=metrics.format_stats(rows[0])["指标"].values)
    stats_df.to_csv(os.path.join(cfg.OUTPUT_DIR, f"metrics_{TAG}.csv"), encoding="utf-8-sig")
    nav_df.to_csv(os.path.join(cfg.OUTPUT_DIR, f"nav_{TAG}.csv"), encoding="utf-8-sig")
    bench_df.to_csv(os.path.join(cfg.OUTPUT_DIR, f"metrics_bench_{TAG}.csv"), encoding="utf-8-sig")
    trade_log.to_csv(os.path.join(cfg.OUTPUT_DIR, f"trade_log_{TAG}.csv"), index=False, encoding="utf-8-sig")
    holdings_log.to_csv(os.path.join(cfg.OUTPUT_DIR, f"holdings_log_{TAG}.csv"), index=False, encoding="utf-8-sig")

    print("\n===== V3-A 策略 vs 中证500 =====")
    for r in rows:
        print(f"\n[{r['区间']}]")
        print(metrics.format_stats(r).to_string(index=False))

    # ---- 净值图（叠加 V2 净值对照，若有）----
    v2_fp = os.path.join(cfg.OUTPUT_DIR, "nav_v2.csv")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(nav_df.index, nav_df["strategy"], label="V3-A（V2选股+止损+排名退出）", color="#c0392b", lw=1.4)
    if os.path.exists(v2_fp):
        v2 = pd.read_csv(v2_fp, index_col=0, parse_dates=True)["strategy"]
        ax1.plot(v2.index, v2, label="V2（无风控）", color="#e67e22", lw=1.1, alpha=0.85)
    ax1.plot(nav_df.index, nav_df["benchmark"], label="中证500指数", color="#7f8c8d", lw=1.2)
    ax1.axvline(pd.Timestamp(cfg.IN_SAMPLE_END), color="#2980b9", ls="--", lw=1, alpha=0.7)
    ax1.set_title(f"V3-A 策略净值 vs 中证500（{cfg.BT_START[:4]}~{cfg.BT_END[:4]}）")
    ax1.legend(); ax1.grid(alpha=0.3)
    dd = metrics.drawdown_series(nav_df["strategy"])
    db = metrics.drawdown_series(nav_df["benchmark"])
    ax2.fill_between(dd.index, dd * 100, 0, color="#c0392b", alpha=0.4, label="策略回撤")
    ax2.plot(db.index, db * 100, color="#7f8c8d", lw=1, label="基准回撤")
    ax2.set_ylabel("回撤 %"); ax2.legend(); ax2.grid(alpha=0.3)
    plt.tight_layout()
    fig.savefig(os.path.join(cfg.OUTPUT_DIR, f"nav_{TAG}.png"), dpi=150)
    plt.close(fig)

    # ---- 年度收益图 ----
    ys = nav_df["strategy"].resample("YE").last()
    yb = nav_df["benchmark"].resample("YE").last()
    ann_s = ys.pct_change(); ann_s.iloc[0] = ys.iloc[0] - 1
    ann_b = yb.pct_change(); ann_b.iloc[0] = yb.iloc[0] - 1
    fig, ax = plt.subplots(figsize=(11, 4.5))
    x = range(len(ys)); w = 0.38
    colors_s = ["#c0392b" if v >= 0 else "#27ae60" for v in ann_s]
    colors_b = ["#e6a09a" if v >= 0 else "#9fd6b3" for v in ann_b]
    ax.bar([i - w/2 for i in x], ann_s * 100, w, label="V3-A 策略", color=colors_s)
    ax.bar([i + w/2 for i in x], ann_b * 100, w, label="中证500", color=colors_b)
    ax.set_xticks(list(x)); ax.set_xticklabels([d.year for d in ys.index])
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("V3-A 年度收益率对比（红=涨，绿=跌）"); ax.legend(); ax.grid(alpha=0.3, axis="y")
    plt.tight_layout()
    fig.savefig(os.path.join(cfg.OUTPUT_DIR, f"annual_{TAG}.png"), dpi=150)
    plt.close(fig)

    print(f"\n结果已保存至 {cfg.OUTPUT_DIR}（{TAG} 标签，未触碰 V1/V2 文件）")


if __name__ == "__main__":
    main()
