# -*- coding: utf-8 -*-
"""
precompute_presets.py —— 答辩演示「预热」脚本（上台前跑一次即可）

做两件事，解决课堂上最容易翻车的两个问题：
  1) 数据预热：把回测所需的数据上下文（1109 只个股日线、交易日历、行业映射、
     成分股映射、月度信号日）序列化到 data/web_ctx.pkl。
     → Web 端启动后直接反序列化，数据加载从 1~2 分钟缩短到数秒。
  2) 结果预跑：预先跑好若干组「课堂演示用」参数组合，结果落盘到 output/web_presets/。
     → Web 端「快速演示模式」直接读盘展示，秒出图表，现场不卡、不怕算不完。

用法（在项目根目录执行）：
    python precompute_presets.py            # 全量：预热 + 预跑全部预设
    python precompute_presets.py --ctx-only # 只做数据预热
    python precompute_presets.py --skip 2   # 跳过前 2 组（续跑用）

结果只写入 data/web_ctx.pkl 与 output/web_presets/，绝不覆盖 output/ 下的正式产物。
"""
import argparse
import contextlib
import io
import json
import os
import pickle
import sys
import time

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

PRESET_DIR = os.path.join(BASE, "output", "web_presets")
CTX_PKL = os.path.join(BASE, "data", "web_ctx.pkl")

import config as cfg                      # noqa: E402
import data_loader as dl                  # noqa: E402
import backtest_v2                        # noqa: E402
import backtest_v3a                       # noqa: E402
import backtest_v3b                       # noqa: E402
import metrics                            # noqa: E402
from run_backtest_v3a import load_all, build_bench_map   # noqa: E402

# 参数顺序必须与 app.py 完全一致：
# (version, top_n, mom_w, exec_mode, stop_loss, rank_pct, atr_mult, keep_rank)
PRESETS = [
    dict(id="p1", name="① V3-A 正式参数（复现 124.70%）",
         params=("V3-A", 40, 0.60, "open", -0.12, 0.50, 2.0, True),
         note="与正式版产物同源参数，用于现场验证在线引擎口径一致（应≈124.70% / 回撤-25.53%）。"),
    dict(id="p2", name="② 止损收紧到 -8%",
         params=("V3-A", 40, 0.60, "open", -0.08, 0.50, 2.0, True),
         note="检验「止损越紧越好吗」——更频繁触发止损，交易成本与被洗出风险上升。"),
    dict(id="p3", name="③ 止损放宽到 -20%",
         params=("V3-A", 40, 0.60, "open", -0.20, 0.50, 2.0, True),
         note="与敏感性页 -15% 一脉相承：止损过宽 → 风控形同虚设，回撤放大。"),
    dict(id="p4", name="④ 持仓集中到 20 只",
         params=("V3-A", 20, 0.60, "open", -0.12, 0.50, 2.0, True),
         note="分散度检验：更少持仓=更高集中度，收益与波动同时放大。"),
    dict(id="p5", name="⑤ V2 无风控对照（收盘执行）",
         params=("V2", 40, 0.60, "close", -0.12, 0.50, 2.0, True),
         note="剥离风控与执行时点：同选股、无止损、当日收盘成交。"),
    dict(id="p6", name="⑥ V3-B 海龟 ATR×1.5（争议组）",
         params=("V3-B", 40, 0.60, "open", -0.12, 0.50, 1.5, True),
         note="敏感性测试中样本外最占优的一组，但属事后回看=过拟合，正式版保留先验 ×2.0。"),
]


def build_context():
    """一次性把重数据读进内存（耗时大头）。"""
    cal = dl.get_index_daily(cfg.DATA_START, cfg.BT_END)["close"]
    cal_days = list(cal.index)
    cal_index = {d: i for i, d in enumerate(cal_days)}
    reb_dates = [d for d in dl.get_rebalance_dates() if d in cal_index]
    signal_pairs = backtest_v3a.month_end_signal_dates(cal_days)
    print(f"交易日历 {len(cal_days)} 天，月度信号 {len(signal_pairs)} 期")
    codes, daily_data = load_all(reb_dates)
    print(f"股票池 {len(codes)} 只，有效日线 {sum(1 for v in daily_data.values() if len(v))} 只")
    bench_map = build_bench_map(reb_dates)
    industry_map = dl.get_industry_map()
    print(f"行业记录 {len(industry_map)} 条")
    return dict(codes=codes, daily_data=daily_data, cal_days=cal_days,
                cal_index=cal_index, bench_map=bench_map,
                industry_map=industry_map, signal_pairs=signal_pairs)


def save_context(ctx):
    os.makedirs(os.path.dirname(CTX_PKL), exist_ok=True)
    t0 = time.time()
    with open(CTX_PKL, "wb") as f:
        pickle.dump(ctx, f, protocol=pickle.HIGHEST_PROTOCOL)
    size_mb = os.path.getsize(CTX_PKL) / 1024 / 1024
    print(f"数据上下文已缓存 → {CTX_PKL}（{size_mb:.0f} MB，耗时 {time.time() - t0:.0f}s）")


def run_one(ctx, params):
    """按参数跑一次回测，返回 (nav, stats_df, bench_df, counts)。"""
    version, top_n, mom_w, exec_mode, stop_loss, rank_pct, atr_mult, keep_rank = params
    cfg.V2_TOP_N = int(top_n)
    cfg.V2_SCORE_MOM_W = float(mom_w)
    cfg.V2_SCORE_QUAL_W = 1.0 - float(mom_w)
    cfg.V2_POS_SIZE = round(1.0 / int(top_n), 4)
    cfg.V3A_STOP_LOSS = float(stop_loss)
    cfg.V3A_RANK_EXIT_PCT = float(rank_pct)
    cfg.V3B_ATR_MULT = float(atr_mult)
    cfg.V3B_KEEP_RANK_EXIT = bool(keep_rank)

    common = (ctx["signal_pairs"], ctx["codes"], ctx["daily_data"],
              ctx["cal_index"], ctx["bench_map"], ctx["industry_map"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        if version == "V2":
            nav, tl, _hl = backtest_v2.run_backtest_v2(*common, exec_mode=exec_mode)
        elif version == "V3-A":
            nav, tl, _hl = backtest_v3a.run_backtest_v3a(*common)
        else:
            nav, tl, _hl = backtest_v3b.run_backtest_v3b(*common)

        rows = [metrics.summary_stats(nav, label="全区间"),
                metrics.summary_stats(nav, start=cfg.BT_START, end=cfg.IN_SAMPLE_END,
                                      label="样本内(2019-2022)"),
                metrics.summary_stats(nav, start="2023-01-01", label="样本外(2023-2026)")]
        stats_df = pd.DataFrame(
            {r["区间"]: metrics.format_stats(r)["策略"].values for r in rows},
            index=metrics.format_stats(rows[0])["指标"].values)
        bench_df = pd.DataFrame(
            {r["区间"]: metrics.format_stats(r)["中证500基准"].values for r in rows},
            index=metrics.format_stats(rows[0])["指标"].values)

    counts = (tl["type"].value_counts()
              if ("type" in tl.columns and len(tl)) else pd.Series(dtype=int))
    return nav, stats_df, bench_df, counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctx-only", action="store_true", help="只做数据预热，不预跑")
    ap.add_argument("--skip", type=int, default=0, help="跳过前 N 组预设（续跑用）")
    ap.add_argument("--only", type=int, default=0, help="只跑第 N 组（从 1 开始）")
    args = ap.parse_args()

    t_all = time.time()
    ctx = build_context()
    save_context(ctx)

    if args.ctx_only:
        print(f"\n完成（仅预热），总耗时 {(time.time() - t_all) / 60:.1f} 分钟")
        return

    os.makedirs(PRESET_DIR, exist_ok=True)
    index = []
    todo = PRESETS[args.skip:]
    if args.only:
        todo = [PRESETS[args.only - 1]]

    for i, p in enumerate(todo, start=1):
        t0 = time.time()
        print(f"\n[{i}/{len(todo)}] 预跑 {p['name']} ...")
        try:
            nav, stats_df, bench_df, counts = run_one(ctx, p["params"])
        except Exception as e:  # 单组失败不影响其余组
            print(f"  !! 失败：{type(e).__name__}: {e}")
            continue
        nav.to_csv(os.path.join(PRESET_DIR, f"{p['id']}_nav.csv"), encoding="utf-8-sig")
        stats_df.to_csv(os.path.join(PRESET_DIR, f"{p['id']}_stats.csv"), encoding="utf-8-sig")
        bench_df.to_csv(os.path.join(PRESET_DIR, f"{p['id']}_bench.csv"), encoding="utf-8-sig")
        meta = dict(id=p["id"], name=p["name"], note=p["note"],
                    params=list(p["params"]),
                    counts={str(k): int(v) for k, v in counts.items()},
                    seconds=round(time.time() - t0, 1))
        with open(os.path.join(PRESET_DIR, f"{p['id']}_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        index.append(meta)
        # 每跑完一组就写一次索引，中途中断也能用上已有的
        with open(os.path.join(PRESET_DIR, "index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        print(f"  完成，耗时 {(time.time() - t0) / 60:.1f} 分钟 → {p['id']}")

    print(f"\n全部完成，总耗时 {(time.time() - t_all) / 60:.1f} 分钟")
    print(f"预设结果目录：{PRESET_DIR}")


if __name__ == "__main__":
    main()
