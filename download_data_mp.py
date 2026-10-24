# -*- coding: utf-8 -*-
"""
download_data_mp.py —— 多进程并行数据下载（速度更快，断点续传）
将股票池均分给 N 个工作进程，每个进程独立登录 baostock。
用法：python download_data_mp.py [workers=6]
"""
import os
import sys
import time
import glob
import pandas as pd
from multiprocessing import Pool

import config as cfg
import data_loader as dl

_universe = None
_done_set = set()


def _init():
    global _universe, _done_set
    dl.login()
    # 加载股票池全集与已完成集合
    reb_dates = dl.get_rebalance_dates()
    _universe = dl.get_universe_codes(reb_dates)
    _done_set = {os.path.basename(p)[:-4] for p in
                 glob.glob(os.path.join(cfg.DAILY_DIR, "*.csv"))}


def _fetch(code):
    if code in _done_set:
        return None
    try:
        df = dl.get_stock_daily(code)
        return code if len(df) == 0 else None
    except Exception:
        return code


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    t0 = time.time()
    # 先在主进程里确保 universe_codes.csv 生成
    reb_dates = dl.get_rebalance_dates()
    codes = dl.get_universe_codes(reb_dates)
    print(f"股票池全集 {len(codes)} 只，开始 {workers} 进程并行下载 ...")
    done = 0
    fails = []
    with Pool(workers, initializer=_init) as pool:
        for r in pool.imap_unordered(_fetch, codes, chunksize=4):
            done += 1
            if r:
                fails.append(r)
            if done % 50 == 0 or done == len(codes):
                rate = done / (time.time() - t0)
                eta = (len(codes) - done) / max(rate, 0.01) / 60
                print(f"进度 {done}/{len(codes)}  失败{len(fails)}  "
                      f"速度{rate:.1f}只/秒  剩余约{eta:.1f}分钟", flush=True)
    print(f"完成。失败 {len(fails)}: {fails[:30]}")


if __name__ == "__main__":
    main()
