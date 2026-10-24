# -*- coding: utf-8 -*-
"""
backtest.py —— 回测引擎（模块化：数据层(strategy/data_loader)与回测层分离）
交易规则：
  - 调仓日 = 每月第一个交易日；信号用 T-1 收盘数据，T 日收盘价成交
  - 等权持仓；买入成本 0.1%，卖出成本 0.15%（含印花税）
  - 组合日收益 = 成分股 pctChg 等权平均（停牌股收益记 0，持有至下一调仓日）
产出：组合与基准的日收益序列、净值曲线、调仓记录
"""
import pandas as pd
import numpy as np

import config as cfg
import data_loader as dl
import filters
import strategy


def run_backtest(rebalance_dates, universe_codes, daily_data, cal_index):
    """
    rebalance_dates: 调仓日列表（交易日历中的 Timestamp）
    cal_index: dict Timestamp -> 交易日历位置
    返回 (nav_df, trade_log, holdings_log)
      nav_df: index=date, columns=[strategy, benchmark] 日净值（起点=1.0）
      trade_log: 每次调仓的买卖明细
    """
    cal_days = list(cal_index.keys())
    nav_dates, strat_nav, bench_nav = [], [], []
    hold = {}            # 当前持仓 {code: weight}
    sv, bv = 1.0, 1.0    # 组合/基准净值
    trade_log, holdings_log = [], []

    # 预先整理每只股票的 pctChg 序列（按交易日历对齐，停牌记0）
    rets = {}
    for code, daily in daily_data.items():
        if daily is None or len(daily) == 0:
            continue
        r = daily["pctChg"].fillna(0.0) / 100.0
        rets[code] = r.reindex(cal_days).fillna(0.0)

    bench_ret = dl.get_index_daily()["close"].pct_change().fillna(0.0)
    bench_ret = bench_ret.reindex(cal_days).fillna(0.0)

    reb_set = set(rebalance_dates)
    n_reb = len(reb_set)
    done_reb = 0
    for i, d in enumerate(cal_days):
        if d in reb_set:
            # ---- 调仓：用 T-1 之前的数据选股 ----
            thr = filters.pool_liquidity_threshold(daily_data, d, universe_codes, pct=5)
            res = strategy.build_portfolio(d, universe_codes, daily_data, liq_threshold=thr)
            if isinstance(res, tuple):
                new_list, cands = res
            else:
                new_list, cands = res, []
            done_reb += 1
            print(f"[调仓 {done_reb}/{n_reb}] {d.date()}  "
                  f"候选 {len(cands)} 只 -> 入选 {len(new_list)} 只", flush=True)
            new_hold = {code: 1.0 / len(new_list) for code, _ in new_list} if new_list else {}
            # 换手与交易成本
            sell_side = sum(w for c, w in hold.items() if c not in new_hold)
            buy_side = sum(w for c, w in new_hold.items() if c not in hold)
            cost = sell_side * cfg.SELL_COST + buy_side * cfg.BUY_COST
            # 记录调仓明细
            trade_log.append({
                "date": d, "n_hold_new": len(new_list),
                "sold": ",".join(sorted(set(hold) - set(new_hold))),
                "bought": ",".join(sorted(set(new_hold) - set(hold))),
                "turnover_sell": round(sell_side, 3), "turnover_buy": round(buy_side, 3),
                "cost_pct": round(cost * 100, 3),
            })
            holdings_log.append({"date": d,
                                 "holdings": ",".join(f"{c}:{w:.3f}" for c, w in sorted(new_hold.items()))})
            hold = new_hold
            # 调仓日收益：当日先按旧持仓赚当日收益，收盘成交扣成本
            if hold:
                port_ret = np.mean([rets.get(c, pd.Series(0, index=cal_days)).loc[d] for c in hold])
            else:
                port_ret = 0.0
            sv *= (1 + port_ret) * (1 - cost)
        else:
            # ---- 非调仓日：持仓等权日收益 ----
            if hold:
                port_ret = np.mean([rets.get(c, pd.Series(0, index=cal_days)).loc[d] for c in hold])
            else:
                port_ret = 0.0
            sv *= 1 + port_ret
        bv *= 1 + bench_ret.loc[d]
        nav_dates.append(d)
        strat_nav.append(sv)
        bench_nav.append(bv)

    nav_df = pd.DataFrame({"strategy": strat_nav, "benchmark": bench_nav},
                          index=pd.Index(nav_dates, name="date"))
    return nav_df, pd.DataFrame(trade_log), pd.DataFrame(holdings_log)
