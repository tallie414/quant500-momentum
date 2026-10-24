# -*- coding: utf-8 -*-
"""
data_loader.py —— 数据接入模块（基于 baostock，带本地缓存）
职责：
  1. 交易日历与指数行情
  2. 中证500历史成分股（按日期查询，避免幸存者偏差）
  3. 个股日线行情（含 isST / tradestatus / pbMRQ / pctChg）
  4. 财务数据 query_profit_data（roeAvg，含公告日期 pubDate，避免未来函数）
所有接口优先读本地缓存，未命中才联网请求。
"""
import os
import json
import time
import baostock as bs
import pandas as pd

import config as cfg

os.makedirs(cfg.DAILY_DIR, exist_ok=True)
os.makedirs(cfg.PROFIT_DIR, exist_ok=True)
os.makedirs(cfg.CACHE_DIR, exist_ok=True)


_IS_LOGGED_IN = False


def login():
    """登录，失败自动重试8次、间隔递增（最长共约3分钟），扛住服务器晚间限流"""
    global _IS_LOGGED_IN
    last = None
    for attempt in range(8):
        try:
            lg = bs.login()
            if lg.error_code == "0":
                _IS_LOGGED_IN = True
                return
            last = lg.error_msg
        except Exception as e:
            last = str(e)
        _IS_LOGGED_IN = False
        time.sleep(min(5 * (attempt + 1), 40))
    raise AssertionError(f"baostock 登录失败（已重试8次）: {last}")


def _force_reconnect():
    """连接异常时强制重置会话，下次请求会重新登录"""
    global _IS_LOGGED_IN
    try:
        bs.logout()
    except Exception:
        pass
    _IS_LOGGED_IN = False


def logout():
    global _IS_LOGGED_IN
    bs.logout()
    _IS_LOGGED_IN = False


def _ensure_login():
    """持久连接：已登录则直接复用，避免高频重复登录被服务端限流"""
    if not _IS_LOGGED_IN:
        login()


# ---------------- 交易日历与指数 ----------------
def get_index_daily(start=None, end=None):
    """基准指数日线（不复权收盘价即可，指数无复权问题），带缓存"""
    start = start or cfg.DATA_START
    end = end or cfg.BT_END
    fp = os.path.join(cfg.CACHE_DIR, f"index_{cfg.INDEX_CODE}.csv")
    if os.path.exists(fp):
        df = pd.read_csv(fp, index_col=0, parse_dates=True)
    else:
        login()
        rs = bs.query_history_k_data_plus(
            cfg.INDEX_CODE, "date,close", start_date=start, end_date=end, frequency="d")
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        logout()
        df = pd.DataFrame(rows, columns=["date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df["close"] = df["close"].astype(float)
        df.to_csv(fp)
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    return df


def get_trading_days(start=None, end=None):
    df = get_index_daily(start, end)
    return df.index


def get_rebalance_dates(freq=None):
    """每月/每季第一个交易日作为调仓日"""
    freq = freq or cfg.REBALANCE_FREQ
    days = get_trading_days(cfg.BT_START, cfg.BT_END)
    s = pd.Series(days, index=days)
    key = s.index.to_period("Q" if freq == "quarterly" else "M")
    return list(s.groupby(key).first())


# ---------------- 中证500历史成分股 ----------------
def get_zz500_constituents(date):
    """指定日期的中证500成分股（baostock 支持历史快照），带缓存"""
    date = pd.Timestamp(date).strftime("%Y-%m-%d")
    fp = os.path.join(cfg.CACHE_DIR, f"zz500_{date}.csv")
    if os.path.exists(fp):
        return pd.read_csv(fp, dtype=str)
    _ensure_login()
    rs = bs.query_zz500_stocks(date=date)
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    df = pd.DataFrame(rows, columns=["date", "code", "code_name"])
    df.to_csv(fp, index=False)
    return df


def get_universe_codes(rebalance_dates):
    """全部调仓日成分股的并集（回测需要的数据全集）"""
    fp = os.path.join(cfg.CACHE_DIR, "universe_codes.csv")
    if os.path.exists(fp):
        return pd.read_csv(fp, dtype=str)["code"].tolist()
    codes = set()
    for d in rebalance_dates:
        df = get_zz500_constituents(d)
        codes.update(df["code"].tolist())
    out = pd.DataFrame({"code": sorted(codes)})
    out.to_csv(fp, index=False)
    return out["code"].tolist()


# ---------------- 行业分类（V2 行业中性化用） ----------------
def get_industry_map():
    """
    全市场证监会行业分类。baostock 一次请求返回全部股票，本地缓存。
    返回 dict: code -> industry（如"证监会行业-二级：软件和信息技术服务业"取冒号后部分）
    """
    fp = os.path.join(cfg.CACHE_DIR, "industry_all.csv")
    if os.path.exists(fp):
        df = pd.read_csv(fp, dtype=str)
    else:
        _ensure_login()
        rs = bs.query_stock_industry()
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        df = pd.DataFrame(rows, columns=["updateDate", "code", "code_name",
                                         "industry", "industryClassification"])
        df.to_csv(fp, index=False)
    m = {}
    for _, r in df.iterrows():
        ind = str(r["industry"])
        if ":" in ind:
            ind = ind.split(":")[-1].split("：")[-1]
        m[r["code"]] = ind
    return m


# ---------------- 个股日线 ----------------
DAILY_FIELDS = ("date,open,high,low,close,volume,amount,"
                "tradestatus,pctChg,isST,pbMRQ,peTTM")


def get_stock_daily(code, start=None, end=None):
    """
    个股日线。close 为后复权（adjustflag=1），保证动量/新高计算不被分红除权打断。
    pctChg 由 baostock 按复权前收盘计算，可直接作为日收益率。
    """
    start = start or cfg.DATA_START
    end = end or cfg.BT_END
    fp = os.path.join(cfg.DAILY_DIR, f"{code}.csv")
    need_fetch = True
    if os.path.exists(fp):
        df = pd.read_csv(fp, index_col=0, parse_dates=True)
        if len(df) and df.index[-1] >= pd.Timestamp(end) - pd.Timedelta(days=10):
            need_fetch = False
    if need_fetch:
        _ensure_login()
        rs = bs.query_history_k_data_plus(
            code, DAILY_FIELDS, start_date=start, end_date=end,
            frequency="d", adjustflag="1")
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        cols = DAILY_FIELDS.split(",")
        df = pd.DataFrame(rows, columns=cols)
        if df.empty:
            return pd.DataFrame()
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        for c in ["open", "high", "low", "close", "volume", "amount",
                  "pctChg", "pbMRQ", "peTTM"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["tradestatus"] = pd.to_numeric(df["tradestatus"], errors="coerce")
        df["isST"] = pd.to_numeric(df["isST"], errors="coerce")
        df.to_csv(fp)
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    return df


# ---------------- 财务数据（ROE） ----------------
def _period_days(stat_date):
    """报告期对应的自然日天数，用于把 roeAvg 年化"""
    d = pd.Timestamp(stat_date)
    return (d - pd.Timestamp(f"{d.year}-01-01")).days + 1


def get_roe_annualized(code, asof_date, max_try=4):
    """
    取 asof_date 时点“已公告”的最新报告期 ROE 并年化（%）。
    用 pubDate <= asof_date 过滤，严格避免未来函数。
    从 asof_date 所在期往前最多尝试 max_try 个报告期。
    返回 (年化ROE%, 报告期) 或 (None, None) 表示无可用财务数据。
    """
    t = pd.Timestamp(asof_date)
    qfp = os.path.join(cfg.PROFIT_DIR, f"{code}_profit.csv")
    if os.path.exists(qfp):
        cache = pd.read_csv(qfp, dtype=str)
    else:
        cache = pd.DataFrame(columns=["pubDate", "statDate", "roeAvg"])
    result = (None, None)
    stat_of_q = {1: "-03-31", 2: "-06-30", 3: "-09-30", 4: "-12-31"}
    # 从 asof 当季往前找（当季报告肯定未披露，但季度边界日子可能刚好赶上披露期）
    tries = []
    yy, qq = t.year, (t.month - 1) // 3 + 1
    for _ in range(max_try + 1):
        tries.append((yy, qq))
        qq -= 1
        if qq == 0:
            yy, qq = yy - 1, 4

    def _lookup(stat_prefix):
        """在缓存里找该报告期；找到且已公告则返回年化ROE"""
        hit = cache[cache["statDate"] == stat_prefix]
        if len(hit):
            pub = pd.Timestamp(hit.iloc[0]["pubDate"])
            roe = pd.to_numeric(hit.iloc[0]["roeAvg"], errors="coerce")
            if pub <= t and pd.notna(roe):
                days = _period_days(stat_prefix)
                return roe * 365.0 / days * 100.0
        return None

    for (ry, rq) in tries:
        stat_prefix = f"{ry}{stat_of_q[rq]}"
        val = _lookup(stat_prefix)
        if val is not None:
            result = (val, stat_prefix)
            break
        # 缓存未命中，联网查询该报告期并写入缓存（断线自动重连重试）
        rows = None
        for attempt in range(3):
            try:
                _ensure_login()
                rs = bs.query_profit_data(code=code, year=ry, quarter=rq)
                rows = []
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
                break
            except Exception:
                _force_reconnect()
                time.sleep(2 * (attempt + 1))
                rows = None
        if rows:
            # query_profit_data 字段: code, pubDate, statDate, roeAvg, ...
            new = pd.DataFrame([r[1:4] for r in rows],
                               columns=["pubDate", "statDate", "roeAvg"])
            cache = pd.concat([cache, new], ignore_index=True)
            val = _lookup(stat_prefix)
            if val is not None:
                result = (val, stat_prefix)
                break
    if len(cache):
        cache.to_csv(qfp, index=False)
    return result


# ================= V2：季度财报（roeAvg + netProfit）=================

PROFIT_FULL_DIR = os.path.join(cfg.CACHE_DIR, "profit_full")
_QEND = {1: "-03-31", 2: "-06-30", 3: "-09-30", 4: "-12-31"}
_PF_COLS = ["pubDate", "statDate", "roeAvg", "netProfit"]


def _pf_path(code):
    return os.path.join(PROFIT_FULL_DIR, f"{code}.csv")


def _load_pf(code):
    fp = _pf_path(code)
    if os.path.exists(fp):
        return pd.read_csv(fp, dtype=str)
    return pd.DataFrame(columns=_PF_COLS)


def fetch_profit_quarter(code, y, q):
    """
    查询并缓存某 (年, 季度) 的 roeAvg + netProfit（query_profit_data 单次一行）。
    返回 dict(pubDate, statDate, roeAvg, netProfit)；无数据/未查出返回 None。
    """
    stat = f"{y}{_QEND[q]}"
    cache = _load_pf(code)
    hit = cache[cache["statDate"] == stat]
    if len(hit):
        r = hit.iloc[0]
        roe = pd.to_numeric(r["roeAvg"], errors="coerce")
        if pd.isna(roe):
            return None
        return {"pubDate": r["pubDate"], "statDate": stat, "roeAvg": float(roe),
                "netProfit": pd.to_numeric(r["netProfit"], errors="coerce")}
    rows = []
    query_ok = False  # 只有查询本身成功（error_code=0）才允许缓存空记录
    for attempt in range(3):
        try:
            _ensure_login()
            rs = bs.query_profit_data(code=code, year=y, quarter=q)
            if rs.error_code != "0":
                raise RuntimeError(f"query_profit_data error: {rs.error_msg}")
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            query_ok = True
            break
        except Exception:
            _force_reconnect()
            time.sleep(2 * (attempt + 1))
            rows = []
    if not rows:
        if query_ok:
            # 查询成功但确实无数据（如次新股早期季度）→ 缓存"空记录"，
            # 之后离线重跑不再对该季度发起网络请求（roeAvg 为空 → 上层判定 None）
            new = pd.DataFrame([["", stat, "", ""]], columns=_PF_COLS)
            cache = pd.concat([cache, new], ignore_index=True).drop_duplicates("statDate")
            os.makedirs(PROFIT_FULL_DIR, exist_ok=True)
            cache.to_csv(_pf_path(code), index=False)
        return None
    # query_profit_data 字段: code, pubDate, statDate, roeAvg, npMargin, gpMargin, netProfit, ...
    row = rows[0]
    pub, stat2, roe, npf = row[1], row[2], row[3], row[6]
    new = pd.DataFrame([[pub, stat2, roe, npf]], columns=_PF_COLS)
    cache = pd.concat([cache, new], ignore_index=True).drop_duplicates("statDate")
    os.makedirs(PROFIT_FULL_DIR, exist_ok=True)
    cache.to_csv(_pf_path(code), index=False)
    try:
        roev = float(roe)
        npv = float(npf)
    except (TypeError, ValueError):
        return None
    return {"pubDate": pub, "statDate": stat2, "roeAvg": roev, "netProfit": npv}


def get_quarterly_profits(code, t, n_need=6):
    """
    截至 t 已公告的最近 n_need 个季度的财报（roeAvg/netProfit），按 statDate 升序。
    roeAvg 为单季度 ROE(%)，TTM-ROE = 最近4个季度之和。
    """
    out = []
    yy, qq = t.year, (t.month - 1) // 3 + 1
    for _ in range(10):
        row = fetch_profit_quarter(code, yy, qq)
        if row is not None and pd.Timestamp(row["pubDate"]) <= t:
            out.append(row)
            if len(out) >= n_need:
                break
        qq -= 1
        if qq == 0:
            yy, qq = yy - 1, 4
    out.sort(key=lambda r: r["statDate"])
    return out
