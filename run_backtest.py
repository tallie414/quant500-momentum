# -*- coding: utf-8 -*-
"""
run_backtest.py —— 主运行脚本（第二步运行，需先完成数据下载）
产出（output/ 目录）：
  nav.png            净值对比图（归一至1.0）+ 回撤曲线
  annual_returns.png 年度收益对比
  metrics.csv        全区间/样本内/样本外指标
  trade_log.csv      调仓记录    holdings_log.csv 持仓记录
用法：python run_backtest.py [monthly|quarterly]
"""
import sys
import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config as cfg
import data_loader as dl
import backtest
import metrics

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False


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


def main():
    if len(sys.argv) > 1:
        cfg.REBALANCE_FREQ = sys.argv[1]
    print(f"调仓频率: {cfg.REBALANCE_FREQ}")
    reb_dates = dl.get_rebalance_dates()
    cal = dl.get_index_daily(cfg.DATA_START, cfg.BT_END)["close"]
    cal_index = {d: i for i, d in enumerate(cal.index)}
    reb_dates = [d for d in reb_dates if d in cal_index]
    print(f"调仓日 {len(reb_dates)} 个: {reb_dates[0].date()} ~ {reb_dates[-1].date()}")

    codes, daily_data = load_all(reb_dates)
    print(f"股票池全集 {len(codes)} 只，其中有效日线 {sum(1 for v in daily_data.values() if len(v))} 只")

    nav_df, trade_log, holdings_log = backtest.run_backtest(reb_dates, codes, daily_data, cal_index)

    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    tag = cfg.REBALANCE_FREQ[:2]

    # ---- 指标输出 ----
    rows = [
        metrics.summary_stats(nav_df, label="全区间"),
        metrics.summary_stats(nav_df, start=cfg.BT_START, end=cfg.IN_SAMPLE_END, label="样本内(2019-2022)"),
        metrics.summary_stats(nav_df, start="2023-01-01", label="样本外(2023-2026)"),
    ]
    # ---- 指标输出（策略侧与基准侧各一张表）----
    stats_df = pd.DataFrame(
        {r["区间"]: metrics.format_stats(r)["策略"].values for r in rows},
        index=metrics.format_stats(rows[0])["指标"].values)
    bench_df = pd.DataFrame({r["区间"]: metrics.format_stats(r)["中证500基准"].values
                             for r in rows}, index=metrics.format_stats(rows[0])["指标"].values)
    stats_df.to_csv(os.path.join(cfg.OUTPUT_DIR, f"metrics_{tag}.csv"), encoding="utf-8-sig")
    nav_df.to_csv(os.path.join(cfg.OUTPUT_DIR, f"nav_{tag}.csv"), encoding="utf-8-sig")  # 每日净值序列（分年度分析用）
    bench_df.to_csv(os.path.join(cfg.OUTPUT_DIR, f"metrics_bench_{tag}.csv"), encoding="utf-8-sig")
    trade_log.to_csv(os.path.join(cfg.OUTPUT_DIR, f"trade_log_{tag}.csv"), index=False, encoding="utf-8-sig")
    holdings_log.to_csv(os.path.join(cfg.OUTPUT_DIR, f"holdings_log_{tag}.csv"), index=False, encoding="utf-8-sig")

    print("\n===== 策略 vs 中证500 =====")
    for r in rows:
        print(f"\n[{r['区间']}]")
        print(metrics.format_stats(r).to_string(index=False))

    # ---- 净值图 ----
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(nav_df.index, nav_df["strategy"], label="策略（动量+基本面）", color="#c0392b", lw=1.4)
    ax1.plot(nav_df.index, nav_df["benchmark"], label="中证500指数", color="#7f8c8d", lw=1.2)
    ax1.axvline(pd.Timestamp(cfg.IN_SAMPLE_END), color="#2980b9", ls="--", lw=1, alpha=0.7)
    ax1.text(pd.Timestamp(cfg.IN_SAMPLE_END), ax1.get_ylim()[1] * 0.95,
             " 样本内|样本外", color="#2980b9", fontsize=9)
    ax1.set_title(f"策略净值 vs 中证500（{cfg.BT_START[:4]}~{cfg.BT_END[:4]}，{cfg.REBALANCE_FREQ}调仓）")
    ax1.legend(); ax1.grid(alpha=0.3)
    dd = metrics.drawdown_series(nav_df["strategy"])
    db = metrics.drawdown_series(nav_df["benchmark"])
    ax2.fill_between(dd.index, dd * 100, 0, color="#c0392b", alpha=0.4, label="策略回撤")
    ax2.plot(db.index, db * 100, color="#7f8c8d", lw=1, label="基准回撤")
    ax2.set_ylabel("回撤 %"); ax2.legend(); ax2.grid(alpha=0.3)
    plt.tight_layout()
    fig.savefig(os.path.join(cfg.OUTPUT_DIR, f"nav_{tag}.png"), dpi=150)
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
    ax.bar([i - w/2 for i in x], ann_s * 100, w, label="策略", color=colors_s)
    ax.bar([i + w/2 for i in x], ann_b * 100, w, label="中证500", color=colors_b)
    ax.set_xticks(list(x)); ax.set_xticklabels([d.year for d in ys.index])
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("年度收益率对比（红=涨，绿=跌）"); ax.legend(); ax.grid(alpha=0.3, axis="y")
    plt.tight_layout()
    fig.savefig(os.path.join(cfg.OUTPUT_DIR, f"annual_{tag}.png"), dpi=150)
    plt.close(fig)

    print(f"\n结果已保存至 {cfg.OUTPUT_DIR}")


if __name__ == "__main__":
    main()
