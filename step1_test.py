# -*- coding: utf-8 -*-
"""
step1_test.py —— 第 1 步验证脚本：统一前置过滤 + 行业数据 + 新成本口径
跑通并数字合理 → 地基完工，进入第 2 步（重跑 V1 基线）。
用法：python step1_test.py   （VPN 关闭）
"""
import os
import pandas as pd

import config as cfg
import data_loader as dl
import filters


def load_daily(codes):
    daily_data = {}
    for c in codes:
        fp = os.path.join(cfg.DAILY_DIR, f"{c}.csv")
        if os.path.exists(fp):
            daily_data[c] = pd.read_csv(fp, index_col=0, parse_dates=True)
        else:
            daily_data[c] = pd.DataFrame()
    return daily_data


def main():
    print("=" * 60)
    print(f"[新成本口径] 买入 {cfg.BUY_COST*100:.2f}%  卖出 {cfg.SELL_COST*100:.2f}%")
    print("  （对应文档：双边佣金万三 + 卖出印花税千一 + 滑点0.2%）")

    # 1) 行业分类数据（首次联网下载全市场，之后走缓存）
    ind = dl.get_industry_map()
    print(f"\n[行业数据] 全市场行业记录 {len(ind)} 条")

    reb_dates = dl.get_rebalance_dates()
    codes = dl.get_universe_codes(reb_dates)
    print(f"[股票池] 历史成分股并集 {len(codes)} 只")

    covered = sum(1 for c in codes if c in ind)
    print(f"[行业覆盖] 股票池内有行业分类的: {covered}/{len(codes)}"
          f" ({covered/len(codes)*100:.1f}%)")

    daily_data = load_daily(codes)
    n_valid = sum(1 for v in daily_data.values() if len(v))
    print(f"[本地日线] 有数据的股票 {n_valid} 只")

    # 2) 在三个调仓日上验证统一前置过滤（按日期切片，无未来函数）
    test_dates = [d for d in reb_dates
                  if d.year in (2019, 2022, 2025) and d.month == 6][:3]
    print(f"\n[统一前置过滤验证] 测试调仓日: {[str(d.date()) for d in test_dates]}")
    for d in test_dates:
        cons = dl.get_zz500_constituents(d)
        cons_codes = cons["code"].tolist()
        n_cons_data = sum(1 for c in cons_codes if len(daily_data.get(c, pd.DataFrame())))
        thr = filters.pool_liquidity_threshold(daily_data, d, codes, pct=5)
        n_ok = sum(1 for c in codes
                   if filters.passes_base_filter(c, daily_data.get(c), d, liq_threshold=thr))
        print(f"  {d.date()}: 当日成分股 {len(cons_codes)} 只（本地有数据 {n_cons_data}）"
              f"  淘汰线(20日均额5%分位)={thr/1e8:.2f}亿  全池通过 {n_ok} 只")

    print("\n" + "=" * 60)
    print("判读标准（你的电脑有全部1109只数据，应以这些为准）：")
    print("  1. 行业覆盖率应 > 95%")
    print("  2. 每个调仓日：当日成分股 500 只（本地有数据应≈500），"
          "全池通过过滤 350~450 只左右")
    print("两项都合理 → 把本输出发给 AI 助手，进入第 2 步（重跑 V1 基线）")


if __name__ == "__main__":
    main()
