# -*- coding: utf-8 -*-
"""
metrics.py —— 绩效评估模块
指标：累计/年化收益率、年化波动、夏普比率、信息比率IR、最大回撤、卡玛比率、
      对基准的超额收益与月度胜率
"""
import pandas as pd
import numpy as np

import config as cfg


def max_drawdown(nav):
    peak = nav.cummax()
    return float(((nav / peak) - 1.0).min())


def drawdown_series(nav):
    return nav / nav.cummax() - 1.0


def summary_stats(nav_df, start=None, end=None, label=""):
    """对给定区间的净值序列输出指标对比（策略 vs 基准）"""
    df = nav_df.loc[start:end] if (start or end) else nav_df
    out = {"区间": label}
    for col in ["strategy", "benchmark"]:
        nav = df[col]
        n = len(nav)
        total_ret = nav.iloc[-1] / nav.iloc[0] - 1.0
        ann_ret = (1 + total_ret) ** (cfg.TRADING_DAYS / n) - 1.0
        ann_vol = nav.pct_change().std() * np.sqrt(cfg.TRADING_DAYS)
        daily_rf = cfg.RF / cfg.TRADING_DAYS
        sharpe = ((nav.pct_change() - daily_rf).mean() / nav.pct_change().std()
                  * np.sqrt(cfg.TRADING_DAYS)) if nav.pct_change().std() > 0 else np.nan
        mdd = max_drawdown(nav)
        out[f"{col}_total"] = total_ret
        out[f"{col}_ann"] = ann_ret
        out[f"{col}_vol"] = ann_vol
        out[f"{col}_sharpe"] = sharpe
        out[f"{col}_mdd"] = mdd
        out[f"{col}_calmar"] = ann_ret / abs(mdd) if mdd < 0 else np.nan
    out["excess_ann"] = out["strategy_ann"] - out["benchmark_ann"]
    # 月度胜率：策略月收益 > 基准月收益 的比例
    ms = df["strategy"].resample("ME").last()
    mb = df["benchmark"].resample("ME").last()
    out["monthly_win"] = float(((ms.pct_change() - mb.pct_change()) > 0).mean())
    # 信息比率 IR：日度主动收益（策略-基准）均值/标准差，年化
    act = df["strategy"].pct_change() - df["benchmark"].pct_change()
    out["ir"] = (float(act.mean() / act.std() * np.sqrt(cfg.TRADING_DAYS))
                 if act.std() > 0 else np.nan)
    return out


def format_stats(s):
    def pct(x): return f"{x*100:.2f}%"
    def f2(x): return f"{x:.2f}"
    rows = [
        ("累计收益率", pct(s["strategy_total"]), pct(s["benchmark_total"])),
        ("年化收益率", pct(s["strategy_ann"]), pct(s["benchmark_ann"])),
        ("年化超额收益", pct(s["excess_ann"]), "—"),
        ("年化波动率", pct(s["strategy_vol"]), pct(s["benchmark_vol"])),
        ("夏普比率(RF=2%)", f2(s["strategy_sharpe"]), f2(s["benchmark_sharpe"])),
        ("最大回撤", pct(s["strategy_mdd"]), pct(s["benchmark_mdd"])),
        ("卡玛比率", f2(s["strategy_calmar"]), f2(s["benchmark_calmar"])),
        ("信息比率(IR)", f2(s["ir"]), "—"),
        ("月度胜率", pct(s["monthly_win"]), "—"),
    ]
    return pd.DataFrame(rows, columns=["指标", "策略", "中证500基准"])
