# -*- coding: utf-8 -*-
"""
regen_metrics.py —— 用当前 metrics.py 统一重算各版本绩效表（含 IR 行）

背景：output/metrics_*.csv 是早期产物，缺少后加入的「信息比率(IR)」行，
     导致网页指标表与 compare_4v.csv / 在线回测结果口径不一致。
     本脚本只从 nav_*.csv 重算指标（不重跑回测），秒级完成，并与 compare_4v.csv 交叉校验。

用法：python regen_metrics.py
"""
import os
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
OUT = os.path.join(BASE, "output")

import config as cfg          # noqa: E402
import metrics                # noqa: E402

TAGS = ["mo", "v2", "v2c", "v3a", "v3b"]
# compare_4v.csv 中的版本名 → 用于交叉校验累计收益率
CHECK_MAP = {"mo": "V1", "v2": "V2  行业", "v2c": "V2c", "v3a": "V3-A", "v3b": "V3-B"}


def main():
    cmp_df = None
    fp = os.path.join(OUT, "compare_4v.csv")
    if os.path.exists(fp):
        cmp_df = pd.read_csv(fp)

    for tag in TAGS:
        nav_fp = os.path.join(OUT, f"nav_{tag}.csv")
        if not os.path.exists(nav_fp):
            print(f"[跳过] {tag}：无净值文件")
            continue
        nav = pd.read_csv(nav_fp, index_col=0, parse_dates=True)
        rows = [
            metrics.summary_stats(nav, label="全区间"),
            metrics.summary_stats(nav, start=cfg.BT_START, end=cfg.IN_SAMPLE_END,
                                  label="样本内(2019-2022)"),
            metrics.summary_stats(nav, start="2023-01-01", label="样本外(2023-2026)"),
        ]
        stats_df = pd.DataFrame(
            {r["区间"]: metrics.format_stats(r)["策略"].values for r in rows},
            index=metrics.format_stats(rows[0])["指标"].values)
        bench_df = pd.DataFrame(
            {r["区间"]: metrics.format_stats(r)["中证500基准"].values for r in rows},
            index=metrics.format_stats(rows[0])["指标"].values)
        stats_df.to_csv(os.path.join(OUT, f"metrics_{tag}.csv"), encoding="utf-8-sig")
        bench_df.to_csv(os.path.join(OUT, f"metrics_bench_{tag}.csv"), encoding="utf-8-sig")

        ir = stats_df.loc["信息比率(IR)", "全区间"] if "信息比率(IR)" in stats_df.index else "—"
        cum = stats_df.loc["累计收益率", "全区间"]
        note = ""
        if cmp_df is not None:
            kw = CHECK_MAP.get(tag)
            m = cmp_df[cmp_df["版本"].str.contains(kw, na=False)] if kw else None
            if m is not None and len(m):
                ref = float(m.iloc[0]["全区间累计_%"])
                cur = float(str(cum).rstrip("%"))
                note = " ✅一致" if abs(ref - cur) < 0.02 else f" ⚠与compare差异 {cur - ref:+.2f}pp"
        print(f"[{tag}] 累计 {cum}　IR(全) {ir}　→ metrics_{tag}.csv 已更新{note}")


if __name__ == "__main__":
    main()
