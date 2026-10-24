# -*- coding: utf-8 -*-
"""
backtest_v3a.py —— V3-A 原版风控回测引擎
对应需求文档："V3-A 原版风控版本（继承 V2 全部选股逻辑）"

继承 V2：选股、打分、均线过滤、行业约束、月度调仓（月末收盘信号→次日开盘执行）
新增逐日监控风控（文档原文）：
  1. 个股固定百分比止损：个股自买入后累计浮亏超过 -12%，无条件立刻卖出
  2. 动量排名退出：每月重新计算全池动量排名，持仓个股动量排名跌出全池前 50%，
     提前调出持仓

执行口径（报告注明）：
  1. 止损为逐日监控：T 日收盘价相对买入成本浮亏 > 12% → T+1 开盘卖出
     （文档"立刻卖出"在日线数据下落地为"次日开盘"，与月度调仓的执行口径一致，
       避免用到 T 日收盘无法成交的未来假设）
  2. 动量排名为每月一查：每月首个"日期 >= 15 日"的交易日（月中）收盘后重算全池
     复合动量排名（口径与选股完全一致：0.6×60日 + 0.4×120日 + 0.05×创120日新高），
     持仓排名百分位 > 50% → 次日开盘卖出
  3. 风控卖出后资金闲置（现金收益 0），持有到下一次月末调仓再配置
  4. 卖出当日若停牌/开盘跌停 → 无法成交，顺延到下一交易日再试
  5. 月末调仓执行日与风控卖出同日时：先执行风控卖出，再做调仓
  6. 成本口径与 V1/V2 完全一致（买 0.23% / 卖 0.33%）
"""
import numpy as np
import pandas as pd

import config as cfg
import data_loader as dl
import filters
import strategy_v2

_LIMIT_UP, _LIMIT_DOWN = 9.8, -9.8


def _limit_pcts(code):
    if code.startswith(("sz.30", "sh.68")):
        return 19.8, -19.8
    return _LIMIT_UP, _LIMIT_DOWN


def month_end_signal_dates(cal_days):
    """与 backtest_v2 完全一致：每月最后一个交易日 → [(信号日T, 次日E)]"""
    s = pd.Series(cal_days, index=cal_days)
    month_last = s.groupby([s.index.year, s.index.month]).last()
    out = []
    for T in month_last:
        i = cal_days.index(T)
        if i + 1 < len(cal_days) and T >= pd.Timestamp(cfg.BT_START):
            out.append((T, cal_days[i + 1]))
    return out


def build_momentum_matrix(universe_codes, daily_data, cal_days):
    """
    预计算全池复合动量得分矩阵（行=交易日，列=股票代码）。
    口径与 strategy_v2.momentum_score 完全一致（0.6×60日 + 0.4×120日 + 0.05×创120日新高），
    按个股自身交易日序列计算后对齐到交易日历；行 T = 用 ≤T 收盘数据的得分。
    """
    cols = {}
    for code, daily in daily_data.items():
        if daily is None or len(daily) < 121:
            continue
        c = daily["close"]
        r60 = c / c.shift(60) - 1.0
        r120 = c / c.shift(120) - 1.0
        newhigh = (c >= c.rolling(120).max()).astype(float)
        score = cfg.V2_MOM_W60 * r60 + cfg.V2_MOM_W120 * r120 + cfg.V2_NEWHIGH120_BONUS * newhigh
        cols[code] = score.reindex(cal_days)
    return pd.DataFrame(cols, index=pd.Index(cal_days, name="date"))


def run_backtest_v3a(signal_pairs, universe_codes, daily_data, cal_index,
                     bench_map, industry_map):
    """
    返回 (nav_df, trade_log, holdings_log)
    trade_log 额外带 type 列：调仓 / 止损 / 排名退出
    """
    cal_days = list(cal_index.keys())
    cal_pos = {d: i for i, d in enumerate(cal_days)}

    opens, closes, prevc = {}, {}, {}
    for code, daily in daily_data.items():
        if daily is None or len(daily) == 0:
            continue
        opens[code] = daily["open"].reindex(cal_days)
        closes[code] = daily["close"].reindex(cal_days)
        prevc[code] = closes[code].shift(1)

    mom_mat = build_momentum_matrix(universe_codes, daily_data, cal_days)

    bench_ret = dl.get_index_daily()["close"].pct_change().fillna(0.0)
    bench_ret = bench_ret.reindex(cal_days).fillna(0.0)

    signal_set = {t for t, _ in signal_pairs}
    exec_set = {e for _, e in signal_pairs}
    sig_of = {e: t for t, e in signal_pairs}
    n_sig = len(signal_pairs)

    # 每月排名检查日：当月首个 日期>=15 的交易日（且不是信号日）
    rank_check = set()
    seen_months = set()
    for d in cal_days:
        if d < pd.Timestamp(cfg.BT_START):
            continue
        ym = (d.year, d.month)
        if ym not in seen_months and d.day >= cfg.V3A_RANK_CHECK_DAY:
            seen_months.add(ym)
            if d not in signal_set:
                rank_check.add(d)

    hold = {}                 # {code: weight}
    cost_px = {}              # {code: 买入成本价}
    pending_sells = {}        # {code: 触发原因"止损"/"排名退出"}，待次日开盘卖出
    sv, bv = 1.0, 1.0
    pending_reb = None        # (执行日, 信号日, picked, info)
    done = 0
    n_stop = n_rank = 0
    nav_dates, strat_nav, bench_nav = [], [], []
    trade_log, holdings_log = [], []

    def _px(code, d, field):
        s = (opens if field == "open" else closes).get(code, pd.Series(dtype=float))
        return s.get(d, np.nan)

    def overnight_factor(code, d):
        o, p = _px(code, d, "open"), prevc.get(code, pd.Series(dtype=float)).get(d, np.nan)
        if pd.isna(o) or pd.isna(p) or p <= 0:
            return 1.0
        return o / p

    def intraday_factor(code, d):
        o, c = _px(code, d, "open"), _px(code, d, "close")
        if pd.isna(o) or pd.isna(c) or o <= 0:
            return 1.0
        return c / o

    def daily_factor(code, d):
        c, p = _px(code, d, "close"), prevc.get(code, pd.Series(dtype=float)).get(d, np.nan)
        if pd.isna(c) or pd.isna(p) or p <= 0:
            return 1.0
        return c / p

    def _sellable(code, d):
        """当日开盘能否卖出（有价且非开盘跌停）"""
        px = _px(code, d, "open")
        if pd.isna(px):
            return False
        p = prevc.get(code, pd.Series(dtype=float)).get(d, np.nan)
        _, ld = _limit_pcts(code)
        if pd.notna(p) and p > 0 and (px / p - 1) * 100 <= ld:
            return False
        return True

    for d in cal_days:
        # ---- 月末信号日：选股（与 V2 完全一致）----
        if d in signal_set:
            T, E = d, cal_days[cal_pos[d] + 1]
            thr = filters.pool_liquidity_threshold(daily_data, E, universe_codes, pct=5)
            picked, info = strategy_v2.build_portfolio_v2(
                E, universe_codes, daily_data, industry_map,
                liq_threshold=thr, bench_codes=bench_map.get((d.year, d.month)), verbose=True)
            pending_reb = (E, d, picked, info)
            done += 1
            print(f"[V3A信号 {done}/{n_sig}] {d.date()}  "
                  f"漏斗 全池{info.get('pool')}→前30%{info.get('n_cand')}"
                  f"→均线{info.get('after_ma')}→质量{info.get('after_qual')}"
                  f"→入选 {info.get('picked')}", flush=True)

        # ---- 月中排名检查日（收盘后）----
        if d in rank_check:
            if d in mom_mat.index and len(mom_mat.columns):
                scores = mom_mat.loc[d].dropna()
                if len(scores) > 10 and hold:
                    ranks = scores.rank(pct=True, ascending=False)   # 1=最强
                    exits = [c for c in hold
                             if c in ranks.index and ranks[c] > cfg.V3A_RANK_EXIT_PCT]
                    for c in exits:
                        if c not in pending_sells:
                            pending_sells[c] = "排名退出"
                            n_rank += 1
                    if exits:
                        print(f"    [排名退出] {d.date()} 触发 {len(exits)} 只: "
                              f"{','.join(sorted(exits))}", flush=True)

        # ---- 收盘止损检查（逐日）----
        for c in list(hold.keys()):
            if c in pending_sells:
                continue
            cp = cost_px.get(c)
            cl = _px(c, d, "close")
            if cp and pd.notna(cl) and cl <= cp * (1.0 + cfg.V3A_STOP_LOSS):
                pending_sells[c] = "止损"
                n_stop += 1

        # ---- 当日开盘交易 ----
        sells_today = {c: r for c, r in pending_sells.items()
                       if c in hold and _sellable(c, d)}
        # 顺延卖不掉的（停牌/跌停）
        pending_sells = {c: r for c, r in pending_sells.items() if c not in sells_today}

        is_reb_day = pending_reb is not None and d == pending_reb[0]
        new_hold, bought_list, skipped = dict(hold), [], []
        sold_detail = []                      # (code, weight, reason, sell_px, cost)

        # 1) 风控卖出（先于调仓）
        for c, reason in sells_today.items():
            w = new_hold.pop(c)
            sold_detail.append((c, w, reason, _px(c, d, "open"), cost_px.pop(c, np.nan)))

        # 2) 月末调仓
        if is_reb_day:
            _, T_sig, picked, info = pending_reb
            pending_reb = None
            target = {c for c, _, _ in picked}
            # 卖出：不在目标里的
            for c in list(new_hold.keys()):
                if c in target:
                    continue
                if _sellable(c, d):
                    w = new_hold.pop(c)
                    sold_detail.append((c, w, "调仓", _px(c, d, "open"), cost_px.pop(c, np.nan)))
                else:
                    skipped.append(f"{c}:调仓卖出失败保留")
            # 买入
            budget = 1.0 - sum(new_hold.values())
            for c, _, _ in picked:
                if c in new_hold:
                    continue
                if budget <= 1e-6:
                    break
                px = _px(c, d, "open")
                if pd.isna(px):
                    skipped.append(f"{c}:停牌未买入")
                    continue
                p = prevc.get(c, pd.Series(dtype=float)).get(d, np.nan)
                lu, _ = _limit_pcts(c)
                if pd.notna(p) and p > 0 and (px / p - 1) * 100 >= lu:
                    skipped.append(f"{c}:涨停未买入")
                    continue
                w_alloc = min(cfg.V2_POS_SIZE, budget)
                new_hold[c] = w_alloc
                cost_px[c] = px
                budget -= w_alloc
                bought_list.append(c)

        # ---- 净值结算 ----
        if sells_today or is_reb_day:
            on = 1.0
            for c, w in hold.items():                    # 旧持仓含当天将卖出的，享受隔夜段
                on += w * (overnight_factor(c, d) - 1.0)
            sold_frac = sum(w for _, w, _, _, _ in sold_detail)
            bought_frac = sum(new_hold[c] for c in bought_list)
            cost = sold_frac * cfg.SELL_COST + bought_frac * cfg.BUY_COST
            sv *= on * (1.0 - cost)
            intra = 1.0
            for c, w in new_hold.items():
                intra += w * (intraday_factor(c, d) - 1.0)
            sv *= intra

            for c, w, reason, spx, cpx in sold_detail:
                pnl = (spx / cpx - 1.0) if (cpx and pd.notna(cpx) and cpx > 0 and pd.notna(spx)) else np.nan
                trade_log.append({
                    "type": reason, "exec_date": d, "signal_date": sig_of.get(d, ""),
                    "code": c, "weight": round(w, 3),
                    "sell_px": round(spx, 3) if pd.notna(spx) else "",
                    "cost_px": round(cpx, 3) if pd.notna(cpx) else "",
                    "pnl_pct": round(pnl * 100, 2) if pd.notna(pnl) else "",
                    "bought": "", "skipped": ";".join(skipped) if c == sold_detail[0][0] else "",
                    "cost_pct": round(w * cfg.SELL_COST * 100, 3)})
            if is_reb_day and bought_list:
                trade_log.append({
                    "type": "调仓买入", "exec_date": d, "signal_date": T_sig,
                    "code": ",".join(sorted(bought_list)), "weight": round(bought_frac, 3),
                    "sell_px": "", "cost_px": "", "pnl_pct": "",
                    "bought": len(bought_list), "skipped": ";".join(skipped),
                    "cost_pct": round(bought_frac * cfg.BUY_COST * 100, 3)})
            holdings_log.append({
                "exec_date": d, "type": "调仓" if is_reb_day else "风控卖出",
                "holdings": ",".join(f"{c}:{w:.3f}" for c, w in sorted(new_hold.items())),
                "cash": round(1.0 - sum(new_hold.values()), 3)})
            print(f"    执行 {d.date()}: {'调仓' if is_reb_day else ''}"
                  f"{'+' if is_reb_day and sells_today else ''}"
                  f"风控卖出{len(sells_today)} 买入{len(bought_list)} 跳过{len(skipped)}",
                  flush=True)
            hold = new_hold
        else:
            dr = 1.0
            for c, w in hold.items():
                dr += w * (daily_factor(c, d) - 1.0)
            sv *= dr

        bv *= 1 + bench_ret.loc[d]
        nav_dates.append(d)
        strat_nav.append(sv)
        bench_nav.append(bv)

    print(f"\n风控统计: 止损触发 {n_stop} 次, 排名退出 {n_rank} 次")
    nav_df = pd.DataFrame({"strategy": strat_nav, "benchmark": bench_nav},
                          index=pd.Index(nav_dates, name="date"))
    return nav_df, pd.DataFrame(trade_log), pd.DataFrame(holdings_log)
