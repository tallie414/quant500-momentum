# -*- coding: utf-8 -*-
"""
strategy.py —— 策略信号模块
V1 选股逻辑（对应需求文档"一、V1 基线版策略"）：
  1. 在经过统一前置过滤的中证500成分股中，筛选股价创 250 个交易日新高的股票
  2. 使用 ROE、PB 做全局阈值粗筛（PB∈(0,10)；最新已公告报告期年化ROE≥8%）
  3. 等权买入，定期调仓

实现口径（报告注明）：
  - "创新高"采用 2% 缓冲（收盘价 ≥ 250日最高价×0.98）：严格新高在震荡市经常
    选不出股票，缓冲保证信号每月可用，对收益影响很小
  - ROE 用 pubDate ≤ 调仓日 过滤，严格避免未来函数
  - 全部按日期切片（daily.index < 调仓日），严禁行号切片
"""
import pandas as pd
import numpy as np

import config as cfg
import data_loader as dl
import filters


def screen_candidates(code, t, daily, liq_threshold=None):
    """
    判断个股在调仓日 t 是否通过 V1 筛选（统一前置过滤 + 动量新高 + PB）。
    返回 dict(momentum=250日涨幅, pb=PB) 若通过，否则 None
    """
    if not filters.passes_base_filter(code, daily, t, liq_threshold):
        return None
    sub = daily[daily.index < t]
    if len(sub) < cfg.MIN_HISTORY:
        return None                          # 上市不足250个交易日
    last = sub.iloc[-1]
    window = sub["close"].iloc[-cfg.LOOKBACK:]        # 过去250日（含T-1）
    mom_ret = last["close"] / window.iloc[0] - 1.0    # 250日涨幅（排序用）
    hi = window.max()
    if last["close"] < hi * cfg.NEW_HIGH_BUFFER:
        return None                          # 未接近250日新高
    return {"momentum": mom_ret, "pb": last["pbMRQ"]}


def build_portfolio(t, universe_codes, daily_data, liq_threshold=None, verbose=False):
    """
    在调仓日 t 构建 V1 目标持仓（等权 TOP_N）。
    返回 (picked, cands)：picked=[(code, momentum)]，cands=通过动量+PB的候选
    """
    cands = []
    for code in universe_codes:
        daily = daily_data.get(code)
        r = screen_candidates(code, t, daily, liq_threshold)
        if r is not None:
            cands.append((code, r["momentum"], r["pb"]))
    if not cands:
        return [], []
    # 按 250日涨幅 降序
    cands.sort(key=lambda x: x[1], reverse=True)
    picked = []
    for code, mom, pb in cands:
        if len(picked) >= cfg.TOP_N:
            break
        roe, stat = dl.get_roe_annualized(code, t)
        if roe is None or roe < cfg.ROE_THRESHOLD:
            continue                         # 基本面不达标 → 下一只补位
        picked.append((code, mom, pb, roe, stat))
        if verbose:
            print(f"    入选 {code} 动量{mom*100:.0f}% PB={pb:.1f} ROE(年化)={roe:.1f}% [{stat}]")
    return [(c[0], c[1]) for c in picked], cands
