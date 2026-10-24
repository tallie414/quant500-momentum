# -*- coding: utf-8 -*-
"""
filters.py —— 四版本统一的股票前置过滤模块
对应需求文档"通用前置过滤（全部版本统一，不可改动）"八条中的可计算项。
V1/V2/V3-A/V3-B 全部调用本模块，保证对比变量唯一。

⚠️ 一切按【日期】切片（daily.index < 调仓日），严禁按行号位置切片——
   2017年之后上市/退市的股票行号与交易日历不对齐，位置切片会错位甚至引入未来数据。

口径说明（答辩要点，写进报告）：
  - 涨跌停用 pctChg 近似判断：主板±10%，创业板(sz.30)/科创板(sh.68)±20%，
    收盘涨幅达 9.8%/19.8% 视为触板
  - "剔除当日涨停无法买入"：用 T-1 收盘涨停近似 T 日开盘一字板（无法以开盘价买入）
  - "剔除当日跌停无法卖出"：V3 逐日风控卖出时使用
  - 流动性过滤：T-1 往前 20 个交易日日均成交额，在全池内排后 5% 的剔除
  - 价格使用后复权收盘价：所有指标均为比值（收益率/均线比/新高/ATR），
    与前复权在数学上等价，报告中注明
"""
import numpy as np
import pandas as pd


def _limit_up_pct(code):
    """该股票的涨停判定阈值（%）"""
    return 19.8 if code.startswith(("sz.30", "sh.68")) else 9.8


def _limit_down_pct(code):
    return -19.8 if code.startswith(("sz.30", "sh.68")) else -9.8


def passes_base_filter(code, daily, t, liq_threshold=None):
    """
    个股在调仓日 t 是否通过统一前置过滤（信号日 = T-1 收盘，只用 < t 的数据）。
    daily: 该股日线 DataFrame；t: 调仓日 Timestamp
    liq_threshold: 全池20日日均成交额的5%分位线（None 则跳过流动性过滤）
    """
    if daily is None or len(daily) == 0:
        return False
    sub = daily[daily.index < t]          # 严格只用 T-1 及以前的数据
    if len(sub) < 20:
        return False                      # 上市不足/数据不足 → 剔除
    last = sub.iloc[-1]
    # 1) ST / *ST / 退市风险警示
    if last["isST"] == 1:
        return False
    # 2) 停牌
    if last["tradestatus"] != 1:
        return False
    # 3) 行情/财务数据缺失（价格或 PB 缺失视为财务数据不可用）
    if pd.isna(last["close"]) or last["close"] <= 0 or pd.isna(last["pbMRQ"]):
        return False
    # 4) T-1 收盘涨停 → T 日开盘无法买入
    pct = last["pctChg"]
    if pd.notna(pct) and pct >= _limit_up_pct(code):
        return False
    # 5) 流动性：近20日日均成交额全池后5%
    if liq_threshold is not None:
        amt = sub["amount"].iloc[-20:]
        if amt.isna().all():
            return False
        if amt.mean() < liq_threshold:
            return False
    return True


def limit_down_blocked(code, daily, t):
    """T-1 收盘跌停 → T 日无法卖出（V3 逐日风控卖出时用）"""
    if daily is None or len(daily) == 0:
        return True
    sub = daily[daily.index < t]
    if len(sub) == 0:
        return True
    pct = sub.iloc[-1]["pctChg"]
    return pd.notna(pct) and pct <= _limit_down_pct(code)


def pool_liquidity_threshold(daily_data, t, universe_codes, pct=5):
    """全池 [T-20, T-1] 日均成交额的 5% 分位数（流动性淘汰线）"""
    means = []
    for code in universe_codes:
        d = daily_data.get(code)
        if d is None or len(d) == 0:
            continue
        sub = d[d.index < t]
        if len(sub) < 20:
            continue
        amt = sub["amount"].iloc[-20:]
        if not amt.isna().all():
            means.append(amt.mean())
    return float(np.percentile(means, pct)) if means else 0.0
