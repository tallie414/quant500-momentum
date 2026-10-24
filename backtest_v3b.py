# -*- coding: utf-8 -*-
"""
backtest_v3b.py —— V3-B 海龟ATR + 漫步者分档止盈 + 100日强平 回测引擎
对应需求文档："V3-B 新增风控规则"（继承 V2 全部选股逻辑）

风控规则（文档原文，全部逐日监控）：
  1. 海龟 ATR 动态止损 + 移动跟踪止损：
     - 10 日 ATR（真实波幅均值）
     - 入场初始止损价 = 买入价 - 2 x ATR
     - 持仓过程中股价上涨，持续向上抬高移动跟踪止损（本实现：每日
       新止损 = max(旧止损, 当日收盘 - 2 x ATR)，只上移不下移）
     - 收盘价一旦跌破移动跟踪止损 → 次日开盘无条件卖出
  2. 漫步者动态分档止盈（以买入时刻股价分档）：
     - 买入价 <= 60 元：止盈倍数 1.4
     - 60 < 买入价 <= 80 元：1.3
     - 80 < 买入价 <= 100 元：1.2
     - 买入价 > 100 元：1.1
     - 收盘价 >= 买入价 x 对应倍数 → 次日开盘止盈平仓
  3. 单票最大持有 100 个交易日，无论盈亏强制平仓（交易所日历口径）

重要约束（文档原文）：V3-B 只复用风控逻辑，不引入海龟 20/50 突破信号、
不引入漫步者 3/6 突破信号作为选股条件。

实现口径（报告注明）：
  - "盘中价格跌破"在日线数据下落地为：T 日收盘触发 → T+1 开盘卖出
    （与 V3-A / 月度调仓的执行口径一致，避免未来假设）
  - 持有交易日计数按交易所日历（含停牌日），买入日计第 1 天
  - V3-A 的月度动量排名退出保留（cfg.V3B_KEEP_RANK_EXIT，可关）
  - 买入日 ATR 缺失的股票（上市过短），止损距离退化为 15% 固定
  - 停牌日 ATR 不更新、不触发（无价格）
  - 成本口径与 V1/V2/V3-A 完全一致（买 0.23% / 卖 0.33%）
"""
import numpy as np
import pandas as pd

import config as cfg
import data_loader as dl
import filters
import strategy_v2
from backtest_v3a import (_limit_pcts, month_end_signal_dates,
                          build_momentum_matrix)


def _tp_mult(buy_px):
    """按买入价分档的止盈倍数（漫步者）"""
    for cap, mult in cfg.V3B_TP_TIERS:
        if buy_px <= cap:
            return mult
    return cfg.V3B_TP_TIERS[-1][1]


def build_atr_map(daily_data, cal_days, n=None):
    """
    预计算每只股票的 10 日 ATR 序列（对齐到交易日历）。
    TR = max(高-低, |高-昨收|, |低-昨收|)，ATR = TR 的 n 日简单均值。
    """
    n = n or cfg.V3B_ATR_N
    atr = {}
    for code, daily in daily_data.items():
        if daily is None or len(daily) < n + 2:
            continue
        h, l, c = daily["high"], daily["low"], daily["close"]
        pc = c.shift(1)
        tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        atr[code] = tr.rolling(n).mean().reindex(cal_days)
    return atr


def run_backtest_v3b(signal_pairs, universe_codes, daily_data, cal_index,
                     bench_map, industry_map):
    """
    返回 (nav_df, trade_log, holdings_log)
    trade_log type 列：调仓 / 调仓买入 / 跟踪止损 / 止盈 / 强平 / 排名退出
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
    atr_map = build_atr_map(daily_data, cal_days)

    bench_ret = dl.get_index_daily()["close"].pct_change().fillna(0.0)
    bench_ret = bench_ret.reindex(cal_days).fillna(0.0)

    signal_set = {t for t, _ in signal_pairs}
    sig_of = {e: t for t, e in signal_pairs}
    n_sig = len(signal_pairs)

    # 每月排名检查日（可选保留）
    rank_check = set()
    if cfg.V3B_KEEP_RANK_EXIT:
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
    cost_px = {}              # {code: 买入价（实际成交开盘价）}
    buy_day_pos = {}          # {code: 买入日的 cal 序号}
    trail_stop = {}           # {code: 当前跟踪止损价}
    pending_sells = {}        # {code: 触发原因}，待次日开盘卖出
    sv, bv = 1.0, 1.0
    pending_reb = None
    done = 0
    n_stop = n_tp = n_max = n_rank = 0
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
        px = _px(code, d, "open")
        if pd.isna(px):
            return False
        p = prevc.get(code, pd.Series(dtype=float)).get(d, np.nan)
        _, ld = _limit_pcts(code)
        if pd.notna(p) and p > 0 and (px / p - 1) * 100 <= ld:
            return False
        return True

    def _atr(code, d):
        s = atr_map.get(code)
        if s is None:
            return np.nan
        return s.get(d, np.nan)

    for i_d, d in enumerate(cal_days):
        # ---- 月末信号日：选股（与 V2/V3-A 完全一致）----
        if d in signal_set:
            T, E = d, cal_days[cal_pos[d] + 1]
            thr = filters.pool_liquidity_threshold(daily_data, E, universe_codes, pct=5)
            picked, info = strategy_v2.build_portfolio_v2(
                E, universe_codes, daily_data, industry_map,
                liq_threshold=thr, bench_codes=bench_map.get((d.year, d.month)), verbose=True)
            pending_reb = (E, d, picked, info)
            done += 1
            print(f"[V3B信号 {done}/{n_sig}] {d.date()}  "
                  f"漏斗 全池{info.get('pool')}→前30%{info.get('n_cand')}"
                  f"→均线{info.get('after_ma')}→质量{info.get('after_qual')}"
                  f"→入选 {info.get('picked')}", flush=True)

        # ---- 收盘风控检查（逐日：跟踪止损 / 止盈 / 100日强平）----
        for c in list(hold.keys()):
            if c in pending_sells:
                continue
            cp, bd = cost_px.get(c), buy_day_pos.get(c)
            cl = _px(c, d, "close")
            if not cp or pd.isna(cl):
                continue
            # 1) 海龟跟踪止损（只上移）
            a = _atr(c, d)
            if pd.notna(a):
                new_stop = cl - cfg.V3B_ATR_MULT * a
                if c in trail_stop:
                    trail_stop[c] = max(trail_stop[c], new_stop)
                else:
                    trail_stop[c] = new_stop
                if cl < trail_stop[c]:
                    pending_sells[c] = "跟踪止损"
                    n_stop += 1
                    continue
            # 2) 漫步者分档止盈
            if cl >= cp * _tp_mult(cp):
                pending_sells[c] = "止盈"
                n_tp += 1
                continue
            # 3) 100 交易日强平（买入日计第 1 天）
            if bd is not None and (i_d - bd + 1) >= cfg.V3B_MAX_HOLD_DAYS:
                pending_sells[c] = "强平"
                n_max += 1

        # ---- 月中排名检查日（收盘后，可选）----
        if cfg.V3B_KEEP_RANK_EXIT and d in rank_check:
            if d in mom_mat.index and len(mom_mat.columns):
                scores = mom_mat.loc[d].dropna()
                if len(scores) > 10 and hold:
                    ranks = scores.rank(pct=True, ascending=False)
                    exits = [c for c in hold
                             if c in ranks.index and ranks[c] > cfg.V3A_RANK_EXIT_PCT]
                    for c in exits:
                        if c not in pending_sells:
                            pending_sells[c] = "排名退出"
                            n_rank += 1
                    if exits:
                        print(f"    [排名退出] {d.date()} 触发 {len(exits)} 只", flush=True)

        # ---- 当日开盘交易 ----
        sells_today = {c: r for c, r in pending_sells.items()
                       if c in hold and _sellable(c, d)}
        pending_sells = {c: r for c, r in pending_sells.items() if c not in sells_today}

        is_reb_day = pending_reb is not None and d == pending_reb[0]
        new_hold, bought_list, skipped = dict(hold), [], []
        sold_detail = []

        # 1) 风控卖出（先于调仓）
        for c, reason in sells_today.items():
            w = new_hold.pop(c)
            sold_detail.append((c, w, reason, _px(c, d, "open"), cost_px.pop(c, np.nan)))
            buy_day_pos.pop(c, None)
            trail_stop.pop(c, None)

        # 2) 月末调仓（逻辑与 V3-A 完全一致）
        T_sig = ""
        if is_reb_day:
            _, T_sig, picked, info = pending_reb
            pending_reb = None
            target = {c for c, _, _ in picked}
            for c in list(new_hold.keys()):
                if c in target:
                    continue
                if _sellable(c, d):
                    w = new_hold.pop(c)
                    sold_detail.append((c, w, "调仓", _px(c, d, "open"), cost_px.pop(c, np.nan)))
                    buy_day_pos.pop(c, None)
                    trail_stop.pop(c, None)
                else:
                    skipped.append(f"{c}:调仓卖出失败保留")
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
                buy_day_pos[c] = i_d
                # 初始跟踪止损 = 买入价 - 2 x ATR（当日 ATR 缺失则退化 15%）
                a = _atr(c, d)
                trail_stop[c] = px - cfg.V3B_ATR_MULT * a if pd.notna(a) else px * 0.85
                budget -= w_alloc
                bought_list.append(c)

        # ---- 净值结算（与 V3-A 完全一致）----
        if sells_today or is_reb_day:
            on = 1.0
            for c, w in hold.items():
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

    print(f"\n风控统计: 跟踪止损 {n_stop} 次, 止盈 {n_tp} 次, "
          f"强平 {n_max} 次, 排名退出 {n_rank} 次")
    nav_df = pd.DataFrame({"strategy": strat_nav, "benchmark": bench_nav},
                          index=pd.Index(nav_dates, name="date"))
    return nav_df, pd.DataFrame(trade_log), pd.DataFrame(holdings_log)
