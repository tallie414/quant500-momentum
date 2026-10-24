# -*- coding: utf-8 -*-
"""
run_sensitivity.py —— 参数敏感性测试（报告加分项：证明风控参数不是过拟合的幸运数字）
  V3-A：止损线 -10% / -12% / -15% 三组
  V3-B：ATR 倍数 1.5 / 2.0 / 2.5 三组
每组完整重跑对应引擎（数据全走本地缓存），输出 output/sensitivity.csv 汇总表。
用法：python run_sensitivity.py   （预计 20~40 分钟，全程离线）

v2 修复：样本外指标改为复用 metrics.summary_stats（区间内重新归一），
  旧版样本外累计收益率误用绝对净值终点，数值错误（等于全区间）。
"""
import os
import pandas as pd

import config as cfg
import data_loader as dl
import metrics
import backtest_v3a
import backtest_v3b


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


def perf(nav_df, start=None, end=None):
    """计算一段净值的指标（与正式版 metrics.summary_stats 完全同口径：
    子区间自动以区间首日重新归一，累计/年化/夏普/回撤均为该区间真实值）"""
    s = metrics.summary_stats(nav_df, start=start, end=end)
    return {"累计收益率_%": round(s["strategy_total"] * 100, 2),
            "年化收益率_%": round(s["strategy_ann"] * 100, 2),
            "年化超额_%": round(s["excess_ann"] * 100, 2),
            "夏普": round(s["strategy_sharpe"], 2),
            "最大回撤_%": round(s["strategy_mdd"] * 100, 2)}


def main():
    cal = dl.get_index_daily(cfg.DATA_START, cfg.BT_END)["close"]
    cal_days = list(cal.index)
    cal_index = {d: i for i, d in enumerate(cal_days)}

    reb_dates = dl.get_rebalance_dates()
    reb_dates = [d for d in reb_dates if d in cal_index]
    pairs = backtest_v3a.month_end_signal_dates(cal_days)

    codes, daily_data = load_all(reb_dates)
    bench_map = build_bench_map(reb_dates)
    industry_map = dl.get_industry_map()
    print(f"数据就绪：股票池 {len(codes)} 只，信号日 {len(pairs)} 个\n")

    rows = []
    base_stop = cfg.V3A_STOP_LOSS
    for stop in (-0.10, -0.12, -0.15):
        cfg.V3A_STOP_LOSS = stop
        print(f"\n========== V3-A 止损线 {stop*100:.0f}% ==========")
        nav, tl, _ = backtest_v3a.run_backtest_v3a(
            pairs, codes, daily_data, cal_index, bench_map, industry_map)
        row = {"版本": "V3-A", "参数": f"止损 {stop*100:.0f}%"}
        row.update({f"全区间_{k}": v for k, v in perf(nav).items()})
        row.update({f"样本外_{k}": v for k, v in perf(nav, start="2023-01-01").items()})
        n_stop = int((tl["type"] == "止损").sum()) if len(tl) else 0
        row["止损次数"] = n_stop
        rows.append(row)
    cfg.V3A_STOP_LOSS = base_stop

    base_mult = cfg.V3B_ATR_MULT
    for mult in (1.5, 2.0, 2.5):
        cfg.V3B_ATR_MULT = mult
        print(f"\n========== V3-B ATR 倍数 {mult} ==========")
        nav, tl, _ = backtest_v3b.run_backtest_v3b(
            pairs, codes, daily_data, cal_index, bench_map, industry_map)
        row = {"版本": "V3-B", "参数": f"ATR x{mult}"}
        row.update({f"全区间_{k}": v for k, v in perf(nav).items()})
        row.update({f"样本外_{k}": v for k, v in perf(nav, start="2023-01-01").items()})
        n_stop = int((tl["type"] == "跟踪止损").sum()) if len(tl) else 0
        row["跟踪止损次数"] = n_stop
        rows.append(row)
    cfg.V3B_ATR_MULT = base_mult

    df = pd.DataFrame(rows)
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    out = os.path.join(cfg.OUTPUT_DIR, "sensitivity.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print("\n===== 敏感性测试汇总 =====")
    print(df.to_string(index=False))
    print(f"\n已保存 {out}")


if __name__ == "__main__":
    main()
