# -*- coding: utf-8 -*-
"""
build_compare.py —— 四版本 8 列对比表（读已保存的净值文件，不重跑回测）
输出：
  output/compare_4v.csv       四版本对比表（含信息比率 IR）
  output/nav_4v.png           四版本净值叠加图（含样本内外分界线）
  并在终端打印自校验结果（与回测结果汇总.md 的官方数字逐项核对）
用法：python build_compare.py   （几秒即完，全程离线）
"""
import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config as cfg
import metrics

OUTPUT_DIR = cfg.OUTPUT_DIR
VERSIONS = [
    ("V1  基准版", "nav_mo.csv",  0.3762, (0.0192, 0.3272)),
    ("V2  行业中性版", "nav_v2.csv",  0.1484, (0.0885, 0.0375)),
    ("V2c 收盘对照", "nav_v2c.csv", 0.0913, (0.0879, -0.0117)),
    ("V3-A 止损+排名退出", "nav_v3a.csv", 1.2470, (0.6636, 0.3280)),
    ("V3-B 海龟+漫步者", "nav_v3b.csv", 0.8332, (0.7139, 0.0534)),
]
# 8 列：全区间累计 / 全区间年化超额 / 全区间夏普 / 全区间回撤 / 样本内累计 / 样本外累计 / 全区间IR / 样本外IR


def main():
    rows, navs = [], {}
    for name, fp, off_total, (off_in, off_out) in VERSIONS:
        df = pd.read_csv(os.path.join(OUTPUT_DIR, fp), index_col=0, parse_dates=True)
        navs[name] = df["strategy"]
        full = metrics.summary_stats(df)
        ins = metrics.summary_stats(df, end=cfg.IN_SAMPLE_END)
        outs = metrics.summary_stats(df, start="2023-01-01")

        # ---- 自校验：与官方记录逐项核对（容差 0.05pp）----
        checks = [
            ("全区间", full["strategy_total"], off_total),
            ("样本内", ins["strategy_total"], off_in),
            ("样本外", outs["strategy_total"], off_out),
        ]
        for seg, got, exp in checks:
            assert abs(got - exp) < 0.0005, \
                f"{name} {seg} 不一致: 计算 {got*100:.2f}% vs 官方 {exp*100:.2f}% —— 中止，禁止带病输出"
        print(f"[校验通过] {name}: 全区间/样本内/样本外 = "
              f"{full['strategy_total']*100:.2f}% / {ins['strategy_total']*100:.2f}% / "
              f"{outs['strategy_total']*100:.2f}%")

        rows.append({
            "版本": name,
            "全区间累计_%": round(full["strategy_total"] * 100, 2),
            "全区间年化超额_%": round(full["excess_ann"] * 100, 2),
            "全区间夏普": round(full["strategy_sharpe"], 2),
            "全区间最大回撤_%": round(full["strategy_mdd"] * 100, 2),
            "样本内累计_%": round(ins["strategy_total"] * 100, 2),
            "样本外累计_%": round(outs["strategy_total"] * 100, 2),
            "全区间IR": round(full["ir"], 2),
            "样本外IR": round(outs["ir"], 2),
        })

    # 基准行
    bench = pd.read_csv(os.path.join(OUTPUT_DIR, "nav_v3a.csv"),
                        index_col=0, parse_dates=True)["benchmark"]
    bdf = pd.DataFrame({"strategy": bench, "benchmark": bench})
    bfull = metrics.summary_stats(bdf)
    bins = metrics.summary_stats(bdf, end=cfg.IN_SAMPLE_END)
    bouts = metrics.summary_stats(bdf, start="2023-01-01")
    print(f"[校验通过] 基准: 全区间/样本内/样本外 = "
          f"{bfull['strategy_total']*100:.2f}% / {bins['strategy_total']*100:.2f}% / "
          f"{bouts['strategy_total']*100:.2f}%")
    rows.append({
        "版本": "中证500基准",
        "全区间累计_%": round(bfull["strategy_total"] * 100, 2),
        "全区间年化超额_%": 0.0,
        "全区间夏普": round(bfull["strategy_sharpe"], 2),
        "全区间最大回撤_%": round(bfull["strategy_mdd"] * 100, 2),
        "样本内累计_%": round(bins["strategy_total"] * 100, 2),
        "样本外累计_%": round(bouts["strategy_total"] * 100, 2),
        "全区间IR": "—",
        "样本外IR": "—",
    })

    out = pd.DataFrame(rows)
    csv_fp = os.path.join(OUTPUT_DIR, "compare_4v.csv")
    out.to_csv(csv_fp, index=False, encoding="utf-8-sig")
    print("\n===== 四版本对比表 =====")
    print(out.to_string(index=False))
    print(f"\n已保存 {csv_fp}")

    # ---- 四版本净值叠加图 ----
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(12, 6.5))
    styles = {
        "V1  基准版":        dict(color="#95a5a6", lw=1.2),
        "V2  行业中性版":    dict(color="#e67e22", lw=1.4),
        "V2c 收盘对照":      dict(color="#f39c12", lw=1.0, ls="--"),
        "V3-A 止损+排名退出": dict(color="#c0392b", lw=2.0),
        "V3-B 海龟+漫步者":  dict(color="#2980b9", lw=2.0),
    }
    for name, s in navs.items():
        ax.plot(s.index, s / s.iloc[0], label=name, **styles.get(name, {}))
    ax.plot(bench.index, bench / bench.iloc[0], label="中证500基准",
            color="#2c3e50", lw=1.6, ls=":")
    ax.axvline(pd.Timestamp(cfg.IN_SAMPLE_END), color="#7f8c8d", lw=1, ls="--")
    ymax = ax.get_ylim()[1]
    ax.text(pd.Timestamp("2019-06-01"), ymax * 0.97, "样本内", color="#7f8c8d", fontsize=9)
    ax.text(pd.Timestamp("2023-03-01"), ymax * 0.97, "样本外", color="#7f8c8d", fontsize=9)
    ax.set_title("四版本策略净值对比（2019-01 ~ 2026-10，月末信号·次日开盘执行）", fontsize=13)
    ax.set_ylabel("净值（期初=1）")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    png_fp = os.path.join(OUTPUT_DIR, "nav_4v.png")
    fig.savefig(png_fp, dpi=150)
    print(f"已保存 {png_fp}")


if __name__ == "__main__":
    main()
