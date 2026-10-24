# -*- coding: utf-8 -*-
"""
backtest_v2.py —— V2 回测引擎（月末收盘出信号，支持两种成交模式）
对应需求文档 V2 交易规则："每月月末收盘后计算信号，次日以开盘价调仓"

exec_mode 两种模式（默认 open = 文档要求；close 仅用于对照实验/敏感性分析）：
  open : 信号日 T（月末最后交易日）收盘算信号，次日 E 开盘价成交
  close: 信号日 T 收盘算信号，当日收盘价成交（与 V1 引擎同口径，用于隔离
         "开盘抢跑溢价"的影响——动量股月末常高开，开盘买入系统性买贵）

执行口径（报告注明，答辩要点）：
  1. 信号日 T 调用 strategy_v2.build_portfolio_v2(t=T+1)：
     内部按 daily.index < T+1 切片，即恰好用到 T 收盘及以前的数据，无未来函数
  2. open 模式执行日 E：
     - 隔夜段：持仓股按 前收→开盘 计收益（继续持有部分）
     - 开盘交易：卖出/买入后，持仓股按 开盘→收盘 计当日收益
  3. 开盘涨停（open/前收-1 ≥ +9.8%，创业板/科创板 +19.8%）→ 无法买入，跳过
     开盘跌停 → 无法卖出，保留持仓至下一调仓日再试
     （close 模式用 收盘/前收 做同样的涨跌停判定）
  4. 执行日停牌（无 open/close）→ 无法买卖；持仓股权重冻结，下一调仓日处理
  5. 交易成本：与 V1 相同的口径（买 0.23% / 卖 0.33%），按成交权重对净值打折，
     保证四版本成本口径一致可比
  6. 首个信号日为 2019-01 月末 → 首次建仓在 2019-02，此前组合为现金
"""
import numpy as np
import pandas as pd

import config as cfg
import data_loader as dl
import filters
import strategy_v2

_LIMIT_UP = 9.8      # 涨停判定（%），创业板/科创板在函数内替换
_LIMIT_DOWN = -9.8


def _limit_pcts(code):
    if code.startswith(("sz.30", "sh.68")):
        return 19.8, -19.8
    return _LIMIT_UP, _LIMIT_DOWN


def month_end_signal_dates(cal_days):
    """每月最后一个交易日作为信号日，返回 [(信号日T, 次日E), ...]"""
    s = pd.Series(cal_days, index=cal_days)
    month_last = s.groupby([s.index.year, s.index.month]).last()
    out = []
    for T in month_last:
        i = cal_days.index(T)
        if i + 1 < len(cal_days) and T >= pd.Timestamp(cfg.BT_START):
            out.append((T, cal_days[i + 1]))
    return out


def run_backtest_v2(signal_pairs, universe_codes, daily_data, cal_index,
                    bench_map, industry_map, exec_mode="open"):
    """
    signal_pairs: [(信号日T, 次日E)]（来自 month_end_signal_dates）
    bench_map: {(年,月): 当期中证500成分股代码列表}（基准行业权重用）
    exec_mode: "open"（文档口径）或 "close"（对照实验）
    返回 (nav_df, trade_log, holdings_log)
    """
    assert exec_mode in ("open", "close")
    cal_days = list(cal_index.keys())
    cal_pos = {d: i for i, d in enumerate(cal_days)}

    # 预整理每只股票对齐到交易日历的开盘/收盘/前收（停牌日 open/close 为 NaN）
    opens, closes, prevc = {}, {}, {}
    for code, daily in daily_data.items():
        if daily is None or len(daily) == 0:
            continue
        opens[code] = daily["open"].reindex(cal_days)
        closes[code] = daily["close"].reindex(cal_days)
        prevc[code] = closes[code].shift(1)

    bench_ret = dl.get_index_daily()["close"].pct_change().fillna(0.0)
    bench_ret = bench_ret.reindex(cal_days).fillna(0.0)

    signal_set = set(t for t, _ in signal_pairs)
    n_sig = len(signal_pairs)

    hold = {}                              # {code: weight}，权重和 ≤ 1
    sv, bv = 1.0, 1.0
    pending = None                         # 待执行的 (执行日, 信号日, picked, info)
    done = 0
    nav_dates, strat_nav, bench_nav = [], [], []
    trade_log, holdings_log = [], []

    def _px(code, d, field):
        s = (opens if field == "open" else closes).get(code, pd.Series(dtype=float))
        return s.get(d, np.nan)

    def overnight_factor(code, d):
        """前收→开盘 的净值因子；停牌(无open)记 1（权重冻结）"""
        o, p = _px(code, d, "open"), prevc.get(code, pd.Series(dtype=float)).get(d, np.nan)
        if pd.isna(o) or pd.isna(p) or p <= 0:
            return 1.0
        return o / p

    def intraday_factor(code, d):
        """开盘→收盘 的净值因子；停牌记 1"""
        o, c = _px(code, d, "open"), _px(code, d, "close")
        if pd.isna(o) or pd.isna(c) or o <= 0:
            return 1.0
        return c / o

    def daily_factor(code, d):
        """普通日 收盘→收盘 因子；停牌记 0 收益"""
        c, p = _px(code, d, "close"), prevc.get(code, pd.Series(dtype=float)).get(d, np.nan)
        if pd.isna(c) or pd.isna(p) or p <= 0:
            return 1.0
        return c / p

    for d in cal_days:
        # ---- 信号日：用 ≤ T 收盘的数据选股（传 t=E 实现 "< E" 切片）----
        if d in signal_set:
            T, E = d, cal_days[cal_pos[d] + 1]
            thr = filters.pool_liquidity_threshold(daily_data, E, universe_codes, pct=5)
            ym = (d.year, d.month)
            picked, info = strategy_v2.build_portfolio_v2(
                E, universe_codes, daily_data, industry_map,
                liq_threshold=thr, bench_codes=bench_map.get(ym), verbose=True)
            exec_day = E if exec_mode == "open" else d
            pending = (exec_day, d, picked, info)
            done += 1
            print(f"[V2信号 {done}/{n_sig}] {d.date()}  "
                  f"漏斗 全池{info.get('pool')}→前30%{info.get('n_cand')}"
                  f"→均线{info.get('after_ma')}→质量{info.get('after_qual')}"
                  f"→入选 {info.get('picked')}", flush=True)

        # ---- 执行日：调仓 ----
        if pending is not None and d == pending[0]:
            _, T_sig, picked, info = pending
            pending = None
            target = {c: cfg.V2_POS_SIZE for c, _, _ in picked}
            kept, sold_list, bought_list, skipped = {}, [], [], []
            px_field = "open" if exec_mode == "open" else "close"

            # 卖出：不在目标里且能卖（当日有成交价且非跌停）
            for c, w in hold.items():
                if c in target:
                    kept[c] = w
                    continue
                px = _px(c, d, px_field)
                p = prevc.get(c, pd.Series(dtype=float)).get(d, np.nan)
                _, ld = _limit_pcts(c)
                if pd.isna(px):                                  # 停牌 → 无法卖出
                    kept[c] = w
                    skipped.append(f"{c}:停牌保留")
                elif pd.notna(p) and p > 0 and (px / p - 1) * 100 <= ld:
                    kept[c] = w                                  # 跌停 → 无法卖出
                    skipped.append(f"{c}:跌停保留")
                else:
                    sold_list.append(c)

            # 买入：目标里尚未持有的，按得分顺序（picked 已按综合分降序）
            budget = 1.0 - sum(kept.values())
            for c, _, _ in picked:
                if c in kept:
                    continue
                if budget <= 1e-6:
                    break
                px = _px(c, d, px_field)
                p = prevc.get(c, pd.Series(dtype=float)).get(d, np.nan)
                lu, _ = _limit_pcts(c)
                if pd.isna(px):
                    skipped.append(f"{c}:停牌未买入")
                    continue
                if pd.notna(p) and p > 0 and (px / p - 1) * 100 >= lu:
                    skipped.append(f"{c}:涨停未买入")
                    continue
                w_alloc = min(cfg.V2_POS_SIZE, budget)
                kept[c] = w_alloc
                budget -= w_alloc
                bought_list.append(c)

            sold_frac = sum(w for c, w in hold.items() if c in sold_list)
            bought_frac = sum(kept[c] for c in bought_list)
            cost = sold_frac * cfg.SELL_COST + bought_frac * cfg.BUY_COST

            if exec_mode == "open":
                # 隔夜段（前收→开盘，旧持仓）+ 成本折扣 + 开盘→收盘段（新持仓）
                on = 1.0
                for c, w in hold.items():
                    on += w * (overnight_factor(c, d) - 1.0)
                sv *= on * (1.0 - cost)
                intra = 1.0
                for c, w in kept.items():
                    intra += w * (intraday_factor(c, d) - 1.0)
                sv *= intra
            else:
                # close 模式：当日先按旧持仓赚 收盘→收盘 收益，收盘成交扣成本
                dr = 1.0
                for c, w in hold.items():
                    dr += w * (daily_factor(c, d) - 1.0)
                sv *= dr * (1.0 - cost)

            trade_log.append({
                "signal_date": T_sig,
                "exec_date": d,
                "n_target": len(picked), "n_bought": len(bought_list),
                "sold": ",".join(sorted(sold_list)),
                "bought": ",".join(sorted(bought_list)),
                "skipped": ";".join(skipped),
                "turnover_sell": round(sold_frac, 3), "turnover_buy": round(bought_frac, 3),
                "cost_pct": round(cost * 100, 3),
            })
            holdings_log.append({"exec_date": d,
                                 "holdings": ",".join(f"{c}:{w:.3f}" for c, w in sorted(kept.items())),
                                 "cash": round(1.0 - sum(kept.values()), 3)})
            print(f"    执行 {d.date()}({exec_mode}): 目标{len(picked)} 实买{len(bought_list)} "
                  f"卖出{len(sold_list)} 保留/跳过 {len(skipped)}", flush=True)
            hold = kept

        # ---- 普通日：收盘→收盘 ----
        else:
            dr = 1.0
            for c, w in hold.items():
                dr += w * (daily_factor(c, d) - 1.0)
            sv *= dr

        bv *= 1 + bench_ret.loc[d]
        nav_dates.append(d)
        strat_nav.append(sv)
        bench_nav.append(bv)

    nav_df = pd.DataFrame({"strategy": strat_nav, "benchmark": bench_nav},
                          index=pd.Index(nav_dates, name="date"))
    return nav_df, pd.DataFrame(trade_log), pd.DataFrame(holdings_log)
