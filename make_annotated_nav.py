# -*- coding: utf-8 -*-
"""
make_annotated_nav.py —— 生成"标注关键买卖点"的净值对比图（对照课程要求第5条）

课程要求：净值曲线须"标注关键买卖点"。
做法（V3-A 正式版）：
  · 买入点：93 次"调仓买入"执行日，全部以绿色▲标在策略净值曲线上；
  · 止损卖出：707 笔止损发生在 406 个交易日——逐日全标会糊成一片，
    故标注当日止损笔数最多的前 10 天（红▼），完整分布由下面板的月度柱状图呈现；
  · 排名退出：45 笔、31 天，全部以橙色◆标注。
产出：output/nav_v3a_annotated.png（报告附录 / PPT / Web 均可直接使用）
"""
import os
import sys

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
OUT = os.path.join(BASE, "output")

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

nav = pd.read_csv(os.path.join(OUT, "nav_v3a.csv"), index_col=0, parse_dates=True)
tl = pd.read_csv(os.path.join(OUT, "trade_log_v3a.csv"))
SPLIT = pd.Timestamp("2022-12-31")

s = nav["strategy"]
b = nav["benchmark"]

buy_days = pd.to_datetime(tl.loc[tl["type"] == "调仓买入", "exec_date"].unique())
rank_days = pd.to_datetime(tl.loc[tl["type"] == "排名退出", "exec_date"].unique())
stop_cnt = tl.loc[tl["type"] == "止损"].groupby("exec_date").size().sort_values(ascending=False)
stop_top = pd.to_datetime(stop_cnt.index[:10])

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True,
                               gridspec_kw={"height_ratios": [3, 1.2]})

ax1.plot(s.index, s.values, label="V3-A 策略", color="#c0392b", lw=1.4, zorder=2)
ax1.plot(b.index, b.values, label="中证500基准", color="#7f8c8d", lw=1.1, zorder=2)
ax1.axvline(SPLIT, color="#2980b9", ls="--", lw=1, alpha=0.7)
ax1.text(SPLIT, ax1.get_ylim()[1], " 样本内 | 样本外", fontsize=8, color="#2980b9", va="top")

def y_at(days, series=s):
    days = pd.DatetimeIndex(days)
    return series.reindex(days).values

ax1.scatter(buy_days, y_at(buy_days), marker="^", s=26, color="#16803c",
            alpha=0.75, label=f"调仓买入（93 次，全标）", zorder=3)
ax1.scatter(rank_days, y_at(rank_days), marker="D", s=26, color="#e67e22",
            alpha=0.85, label=f"排名退出卖出（45 笔 / 31 天，全标）", zorder=4)
ax1.scatter(stop_top, y_at(stop_top), marker="v", s=60, color="#1a1a8c",
            label="止损卖出最集中 10 日（全期 707 笔 / 406 天）", zorder=5)
for d in stop_top:
    ax1.annotate(f"{stop_cnt.loc[str(d.date())]}笔", (d, s.reindex([d]).iloc[0]),
                 textcoords="offset points", xytext=(0, -14),
                 ha="center", fontsize=7, color="#1a1a8c")

ax1.set_title("V3-A 净值曲线与关键买卖点标注（2019-01 ~ 2026-10，期初=1）")
ax1.set_ylabel("净值（期初=1）")
ax1.legend(loc="upper left", fontsize=8, framealpha=0.9)
ax1.grid(alpha=0.3)

# 下面板：月度风控触发次数（止损 / 排名退出），完整呈现卖出时点分布
t2 = tl.copy()
t2["exec_date"] = pd.to_datetime(t2["exec_date"])
t2["月"] = t2["exec_date"].dt.to_period("M").astype(str)
m_stop = t2[t2["type"] == "止损"].groupby("月").size()
m_rank = t2[t2["type"] == "排名退出"].groupby("月").size()
months = sorted(set(m_stop.index) | set(m_rank.index))
m_stop = m_stop.reindex(months, fill_value=0)
m_rank = m_rank.reindex(months, fill_value=0)
# 关键：sharex 之下 x 必须用真实日期（与上面板同一坐标系），否则柱子会落到 1970 年
xs = pd.PeriodIndex(months, freq="M").to_timestamp(how="end")
ax2.bar(xs - pd.Timedelta(days=9), m_stop.values, width=16, color="#c0392b",
        alpha=0.8, label="止损卖出（笔/月）")
ax2.bar(xs + pd.Timedelta(days=9), m_rank.values, width=16, color="#e67e22",
        alpha=0.8, label="排名退出（笔/月）")
step = max(1, len(months) // 12)
ax2.set_xticks(list(xs[::step]))
ax2.set_xticklabels([months[i][2:] for i in range(0, len(months), step)], rotation=45, fontsize=7)
ax2.set_ylabel("风控卖出笔数")
ax2.legend(fontsize=8)
ax2.grid(alpha=0.3, axis="y")

plt.tight_layout()
fp = os.path.join(OUT, "nav_v3a_annotated.png")
fig.savefig(fp, dpi=150)
plt.close(fig)
print(f"已生成 {fp}")
print(f"买入点 {len(buy_days)} 个｜排名退出 {len(rank_days)} 天｜止损 top10 天："
      + ", ".join(str(d.date()) for d in stop_top))
