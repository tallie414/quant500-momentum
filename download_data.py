# -*- coding: utf-8 -*-
"""
download_data.py —— 数据下载脚本（第一步运行）
下载内容：
  1. 中证500指数日线（交易日历 + 基准）
  2. 每个调仓日的成分股快照 → 股票池全集
  3. 股票池全集的个股日线（后复权，含 isST/停牌/PB/pctChg）
支持断点续传：已有缓存的股票自动跳过。
用法：python download_data.py
"""
import time
import pandas as pd

import config as cfg
import data_loader as dl


def main():
    reb_dates = dl.get_rebalance_dates()
    print(f"[1/3] 调仓日数量: {len(reb_dates)}, 首日 {reb_dates[0].date()}, 末日 {reb_dates[-1].date()}")

    print("[2/3] 下载各调仓日中证500成分股快照 ...")
    codes = dl.get_universe_codes(reb_dates)
    print(f"      股票池全集（历史成分股并集）: {len(codes)} 只")

    print("[3/3] 下载个股日线（断点续传）...")
    ok, fail = 0, []
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        try:
            df = dl.get_stock_daily(code)
            if len(df) == 0:
                fail.append(code)
            else:
                ok += 1
        except Exception as e:
            fail.append(code)
            print(f"      {code} 失败: {e}")
        if i % 25 == 0 or i == len(codes):
            rate = i / (time.time() - t0)
            eta = (len(codes) - i) / max(rate, 0.01) / 60
            print(f"      进度 {i}/{len(codes)}  成功{ok}  失败{len(fail)}  "
                  f"速度{rate:.1f}只/秒  预计剩余{eta:.1f}分钟")
    print(f"完成。成功 {ok}，失败 {len(fail)}: {fail[:20]}")
    print("下一步：运行 python run_backtest.py")


if __name__ == "__main__":
    main()
