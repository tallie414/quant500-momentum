# -*- coding: utf-8 -*-
"""
v2_step_test.py —— V2 第1步验证：对几个调仓日跑一遍 V2 选股漏斗
验证点：漏斗各级数量是否合理、行业约束是否生效、耗时/断网重试是否正常
（本脚本只验证选股信号，不跑回测；财报数据首次查询较慢，之后永久缓存）
"""
import os
import time
import pandas as pd

import config as cfg
import data_loader as dl
import filters
import strategy_v2

# 抽查的调仓日（覆盖样本内早段/晚段、样本外）
TEST_DATES = ["2019-06-03", "2022-06-01", "2025-06-03"]


def load_all(universe):
    daily_data = {}
    for c in universe:
        fp = os.path.join(cfg.DAILY_DIR, f"{c}.csv")
        if os.path.exists(fp):
            daily_data[c] = pd.read_csv(fp, index_col=0, parse_dates=True)
        else:
            daily_data[c] = pd.DataFrame()
    return daily_data


def main():
    t0 = time.time()
    print("=" * 60)
    print("V2 第1步验证：选股信号漏斗（财报数据首次查询较慢，属一次性）")
    print("=" * 60)

    reb_dates = dl.get_rebalance_dates()
    cal = dl.get_index_daily(cfg.DATA_START, cfg.BT_END)["close"]
    reb_dates = [d for d in reb_dates
                 if pd.Timestamp(cfg.BT_START) <= d <= pd.Timestamp(cfg.BT_END)]
    universe = dl.get_universe_codes(reb_dates)
    print(f"调仓日 {len(reb_dates)} 个，股票池全集 {len(universe)} 只")

    daily_data = load_all(universe)
    industry_map = dl.get_industry_map()
    print(f"本地有效日线 {sum(1 for v in daily_data.values() if len(v))} 只，"
          f"行业覆盖 {sum(1 for c in universe if c in industry_map)/len(universe)*100:.1f}%\n")

    for ds in TEST_DATES:
        t = pd.Timestamp(ds)
        # 用 t 当日（或其后第一个）真实调仓日
        real = next((d for d in reb_dates if d >= t), None)
        if real is None:
            print(f"[{ds}] 不在调仓日范围内，跳过")
            continue
        print(f"---------- 调仓日 {real.date()} ----------")
        thr = filters.pool_liquidity_threshold(daily_data, real, universe, pct=5)
        # 基准行业权重：用当期 500 只成分股名单（快照已缓存）
        cons = dl.get_zz500_constituents(real.date().isoformat())
        bench_codes = cons["code"].tolist()
        t1 = time.time()
        picked, info = strategy_v2.build_portfolio_v2(
            real, universe, daily_data, industry_map, liq_threshold=thr,
            bench_codes=bench_codes, verbose=True)
        # 行业分布与偏离检查
        bench = {}
        for c in bench_codes:
            ind = industry_map.get(c, "未知")
            bench[ind] = bench.get(ind, 0.0) + 1.0 / len(bench_codes)
        port = {}
        for code, _, _ in picked:
            ind = industry_map.get(code, "未知")
            port[ind] = port.get(ind, 0.0) + cfg.V2_POS_SIZE
        max_over, max_under = 0.0, 0.0
        for i in set(port) | set(bench):
            dev = port.get(i, 0.0) - bench.get(i, 0.0)
            max_over = max(max_over, dev)
            max_under = min(max_under, dev)
        print(f"    最终持仓 {len(picked)} 只，最大超配偏离 {max_over*100:+.2f}%"
              f"（约束：超配 ≤ +{cfg.V2_IND_DEV*100:.0f}%），"
              f"最大低配 {max_under*100:.2f}%（候选不足所致，属正常），耗时 {time.time()-t1:.0f}s")
        for code, score, w in picked[:5]:
            print(f"      示例 {code}  综合分 {score:.3f}  权重 {w*100:.1f}%"
                  f"  行业 {industry_map.get(code, '?')}")
        print()

    print(f"总耗时 {time.time()-t0:.0f}s。全部通过 → 把本输出发给 AI 助手进第2步（回测引擎）")
    print("判断标准（你的电脑应有全量数据）：")
    print("  1. 漏斗逐级收窄且不为 0：全池 ~400 → 前30% ~120 → 均线 ~40-80 → 质量 ~20-60 → 持仓 30-40")
    print("  2. 最大超配偏离 ≤ +5%（低配不限，候选不足所致）")
    print("  3. 持仓示例带行业名称，无报错")


if __name__ == "__main__":
    main()
