# -*- coding: utf-8 -*-
"""
strategy_v2.py —— V2 增强版选股（对应需求文档"二、V2 增强版策略"）
在统一前置过滤（filters.py）基础上：
  1. 动量因子(60%)：动量综合得分 = 0.6×60日收益率 + 0.4×120日收益率；
     当日收盘创120日新高 → 额外 +0.05
  2. 质量因子(40%，行业中性)：TTM-ROE ≥ 行业中位数；PB ≤ 行业PB中位数×1.5；
     剔除最近两个季度净利润连续同比下滑
  3. 均线多头前置过滤：MA5≥MA20 且 MA50≥MA90（仅调仓日校验）
  4. 选股流程：动量得分前30% → 质量/均线过滤 → 综合得分(0-1百分位标准化：
     动量×0.6 + 质量×0.4)从高到低取 40 只等权
  5. 行业约束：单行业权重相对基准(当期成分股同行业等权合计)偏离 ≤ ±5%；
     超配时按综合得分从低到高剔除个股

实现口径（报告注明）：
  - 质量得分的 0-1 百分位：TTM-ROE 取高分位、PB 取低分位，各占 50% 平均
  - "行业中位数"在当期动量前30%候选池内计算（全池计算需为500只逐个查财报，
    成本过高且文档未指明口径）
  - TTM-ROE = 最近4个已公告季度的单季 roeAvg 之和（%）
  - 净利润同比下滑：最近两个已公告季度净利润均低于上年同期（数据不足则保守剔除）
  - 信号：月末最后交易日收盘计算（T-1 及以前数据），次日开盘执行（回测引擎实现）
"""
import numpy as np
import pandas as pd

import config as cfg
import data_loader as dl
import filters


# ---------- 动量 ----------

def momentum_score(daily, t):
    """动量综合得分；历史不足121日返回 None"""
    if daily is None or len(daily) == 0:
        return None
    sub = daily[daily.index < t]
    if len(sub) < 121:
        return None
    c = sub["close"]
    r60 = c.iloc[-1] / c.iloc[-61] - 1.0
    r120 = c.iloc[-1] / c.iloc[-121] - 1.0
    score = cfg.V2_MOM_W60 * r60 + cfg.V2_MOM_W120 * r120
    if c.iloc[-1] >= c.iloc[-120:].max():        # 收盘创120日新高
        score += cfg.V2_NEWHIGH120_BONUS
    return score


# ---------- 均线多头前置过滤 ----------

def ma_trend_ok(daily, t):
    """MA5≥MA20 且 MA50≥MA90；历史不足90日 → 不通过"""
    if daily is None or len(daily) == 0:
        return False
    sub = daily[daily.index < t]
    if len(sub) < cfg.V2_MA_SLOW2:
        return False
    c = sub["close"]
    ma5 = c.iloc[-cfg.V2_MA_FAST1:].mean()
    ma20 = c.iloc[-cfg.V2_MA_FAST2:].mean()
    ma50 = c.iloc[-cfg.V2_MA_SLOW1:].mean()
    ma90 = c.iloc[-cfg.V2_MA_SLOW2:].mean()
    return ma5 >= ma20 and ma50 >= ma90


# ---------- 质量 ----------

def quality_metrics(code, t):
    """
    返回 dict(ttm_roe, pb, np_double_decline) 或 None(数据不足)。
    pb 由调用方从日线传入更高效，这里只算财报侧。
    """
    qs = dl.get_quarterly_profits(code, t, n_need=6)
    if len(qs) < 6:
        return None
    roe4 = [r["roeAvg"] for r in qs[-4:]]
    if any(pd.isna(v) for v in roe4):
        return None
    ttm_roe = float(sum(roe4))
    np_last, np_prev = qs[-1]["netProfit"], qs[-2]["netProfit"]
    np_last_yoy, np_prev_yoy = qs[-5]["netProfit"], qs[-6]["netProfit"]
    if any(pd.isna(v) for v in (np_last, np_prev, np_last_yoy, np_prev_yoy)):
        if cfg.V2_NP_MISSING_DROP:
            return None
        decline = False
    else:
        decline = (np_last < np_last_yoy) and (np_prev < np_prev_yoy)
    return {"ttm_roe": ttm_roe, "np_double_decline": decline}


def build_portfolio_v2(t, universe_codes, daily_data, industry_map,
                       liq_threshold=None, bench_codes=None, verbose=False):
    """
    V2 调仓日选股。返回 (picked, info)
      picked: [(code, composite_score, weight)]；info 为过程统计 dict
      bench_codes: 当期中证500成分股名单（基准行业权重按它计算）；
                   None 则退化为 universe_codes（不精确，正式回测必须传）
    """
    # ---- 第0步：统一前置过滤 ----
    pool = []
    for code in universe_codes:
        if filters.passes_base_filter(code, daily_data.get(code), t, liq_threshold):
            pool.append(code)

    # ---- 第1步：动量得分前30% ----
    scored = []
    for code in pool:
        s = momentum_score(daily_data.get(code), t)
        if s is not None:
            scored.append((code, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    n_cand = max(1, int(round(len(scored) * cfg.V2_CAND_TOP)))
    cands = scored[:n_cand]

    # ---- 第2步：均线多头过滤 ----
    after_ma = [(c, s) for c, s in cands if ma_trend_ok(daily_data.get(c), t)]

    # ---- 第3步：质量过滤（行业中性） ----
    # 先取每只候选的 TTM-ROE / PB / 净利下滑
    qual = {}
    for code, _ in after_ma:
        q = quality_metrics(code, t)
        if q is None:
            continue
        daily = daily_data.get(code)
        sub = daily[daily.index < t]
        q["pb"] = float(sub.iloc[-1]["pbMRQ"])
        q["ind"] = industry_map.get(code, "未知")
        qual[code] = q
    # 行业中位数（在候选池内计算）
    ind_roe, ind_pb = {}, {}
    qdf = pd.DataFrame(qual).T
    for ind, g in qdf.groupby("ind"):
        ind_roe[ind] = g["ttm_roe"].median()
        ind_pb[ind] = g["pb"].median()
    passed = []
    for code, s in after_ma:
        q = qual.get(code)
        if q is None:
            continue
        ind = q["ind"]
        if q["ttm_roe"] < ind_roe[ind]:
            continue
        if q["pb"] > ind_pb[ind] * cfg.V2_PB_INDCAP:
            continue
        if q["np_double_decline"]:
            continue
        passed.append((code, s, q))

    # ---- 第4步：综合得分（0-1百分位标准化） ----
    if not passed:
        return [], {"pool": len(pool), "n_cand": n_cand, "after_ma": len(after_ma),
                    "after_qual": 0, "picked": 0}
    df = pd.DataFrame([
        {"code": c, "mom": s, "ttm_roe": q["ttm_roe"], "pb": q["pb"], "ind": q["ind"]}
        for c, s, q in passed])
    df["mom_pct"] = df["mom"].rank(pct=True)
    df["roe_pct"] = df["ttm_roe"].rank(pct=True)
    df["pb_pct"] = df["pb"].rank(pct=True)
    df["qual_pct"] = 0.5 * df["roe_pct"] + 0.5 * (1.0 - df["pb_pct"])
    df["score"] = cfg.V2_SCORE_MOM_W * df["mom_pct"] + cfg.V2_SCORE_QUAL_W * df["qual_pct"]
    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    # ---- 第5步：行业偏离约束 + 选40只 ----
    # 基准行业权重：当期中证500成分股等权，按行业加总（用户拍板口径）
    if bench_codes is None:
        bench_codes = universe_codes
    bench_ind = {}
    n_bench = max(1, len(bench_codes))
    for code in bench_codes:
        ind = industry_map.get(code, "未知")
        bench_ind[ind] = bench_ind.get(ind, 0.0) + 1.0 / n_bench
    # 贪心：综合得分从高到低，加入该行业不超配则选入
    ind_cnt = {}
    picked = []
    for _, r in df.iterrows():
        if len(picked) >= cfg.V2_TOP_N:
            break
        ind = r["ind"]
        cur_w = ind_cnt.get(ind, 0) * cfg.V2_POS_SIZE
        if cur_w + cfg.V2_POS_SIZE > bench_ind.get(ind, 0.0) + cfg.V2_IND_DEV + 1e-9:
            continue                                    # 该行业已超配 → 跳过（等效从低分剔除）
        ind_cnt[ind] = ind_cnt.get(ind, 0) + 1
        picked.append((r["code"], float(r["score"]), cfg.V2_POS_SIZE))

    info = {"pool": len(pool), "n_cand": n_cand, "after_ma": len(after_ma),
            "after_qual": len(df), "picked": len(picked)}
    if verbose:
        print(f"    过滤漏斗: 全池{len(pool)} -> 动量前30% {n_cand} -> 均线{len(after_ma)}"
              f" -> 质量{len(df)} -> 行业约束后 {len(picked)}")
    return picked, info
