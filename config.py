# -*- coding: utf-8 -*-
"""
config.py —— 全局参数配置（策略与回测的所有可调参数集中在这里）
"""

# ---------- 路径 ----------
import os

# 清除代理环境变量（重要！）：Cursor/终端常继承系统代理设置（如 http://127.0.0.1:7890），
# 导致 Python 网络库把请求发给本地代理而报错——即使 VPN 开着"规则模式"也会中招。
# baostock 走国内直连，必须在无代理环境下运行。
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
           "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "data", "cache")          # 数据缓存目录
DAILY_DIR = os.path.join(CACHE_DIR, "daily")                  # 个股日线缓存
PROFIT_DIR = os.path.join(CACHE_DIR, "profit")                # 财务数据缓存
OUTPUT_DIR = os.path.join(BASE_DIR, "output")                 # 回测结果输出

INDEX_CODE = "sh.000905"                                      # 中证500指数（对比基准）
INDEX_NAME = "中证500"

# ---------- 时间区间 ----------
DATA_START = "2017-10-01"        # 数据起点（往前多取，保证回测起点前有250日动量窗口）
BT_START = "2019-01-01"          # 回测起点
BT_END = "2026-10-20"            # 回测终点（数据截止日）
IN_SAMPLE_END = "2022-12-31"     # 样本内 / 样本外 分界（样本外: 2023-01-01 起）

# ---------- 股票池与筛选 ----------
LOOKBACK = 250                   # 动量回看窗口（交易日）
NEW_HIGH_BUFFER = 0.98           # “创250日新高”允许的缓冲：收盘价 >= 过去250日最高价*0.98
                                 # 严格新高在某些月份选不出股票，2%缓冲保证信号每月可用
TOP_N = 20                       # 持仓数量（等权）
PB_MIN, PB_MAX = 0.0, 10.0       # PB 合理区间：>0 剔除资不抵债，<10 剔除估值泡沫
ROE_THRESHOLD = 8.0              # 最新报告期年化 ROE (%) 下限
MIN_HISTORY = 250                # 至少需要的历史交易日数（次新股自动剔除）

# ---------- 交易规则 ----------
REBALANCE_FREQ = "monthly"       # "monthly" 或 "quarterly"
# 需求文档统一口径：双边佣金万三 + 卖出印花税千一 + 滑点0.2%
BUY_COST = 0.0023                # 买入 = 佣金0.03% + 滑点0.2%
SELL_COST = 0.0033               # 卖出 = 佣金0.03% + 印花税0.1% + 滑点0.2%

# ---------- 评估 ----------
RF = 0.02                        # 无风险利率（夏普比率用），年化2%
TRADING_DAYS = 252               # 年化交易日数

# ---------- V2 参数（增强版，对应需求文档"二、V2"） ----------
V2_MOM_W60 = 0.6                 # 动量综合得分 = 0.6×60日收益率 + 0.4×120日收益率
V2_MOM_W120 = 0.4
V2_NEWHIGH120_BONUS = 0.05       # 当日收盘创120日新高 → 动量得分额外 +0.05
V2_CAND_TOP = 0.30               # 按动量得分从高到低取前30%作为候选
V2_SCORE_MOM_W = 0.6             # 综合得分 = 动量标准化分×0.6 + 质量标准化分×0.4
V2_SCORE_QUAL_W = 0.4
V2_TOP_N = 40                    # 持仓 40 只，等权
V2_POS_SIZE = 0.025              # 单票初始仓位 2.5%
V2_POS_CAP = 0.03                # 单票仓位上限 3%（月度调仓下等权即为2.5%，上限供V3使用）
V2_POS_CAP = 0.03                # （同上，供引用）

# ---------- V3-A 风控参数 ----------
V3A_STOP_LOSS = -0.12            # 个股固定止损线：自买入后累计浮亏超过 -12% 无条件卖出
V3A_RANK_EXIT_PCT = 0.50         # 动量排名退出：每月重算全池排名，持仓跌出前 50% 提前调出
V3A_RANK_CHECK_DAY = 15          # 每月排名检查日：当月首个"日期>=该日"的交易日（月中，报告注明假设）
# ---------- V3-B 风控参数（海龟ATR + 漫步者分档止盈 + 100日强平） ----------
V3B_ATR_N = 10                   # ATR 周期：10日真实波幅均值（文档原文）
V3B_ATR_MULT = 2.0               # 初始止损价 = 买入价 - 2 x ATR；跟踪止损 = max(旧止损, 收盘 - 2 x ATR)
V3B_TP_TIERS = [(60.0, 1.4), (80.0, 1.3), (100.0, 1.2), (float("inf"), 1.1)]
                                 # 漫步者分档止盈：(买入价上限, 止盈倍数)，股价 >= 买入价x倍数 触发
V3B_MAX_HOLD_DAYS = 100          # 单票最长持有 100 个交易日（交易所日历口径），强制平仓
V3B_KEEP_RANK_EXIT = True        # 保留 V3-A 的月度动量排名退出（假设，报告注明；可一键关闭）
V2_PB_INDCAP = 1.5               # PB ≤ 所属行业PB中位数 ×1.5
V2_IND_DEV = 0.05                # 单行业权重相对基准行业权重偏离 ≤ ±5%
V2_NP_MISSING_DROP = True        # 净利润数据不足无法判断同比 → 保守剔除（报告注明）

# 均线多头前置过滤：MA5≥MA20 且 MA50≥MA90
V2_MA_FAST1, V2_MA_FAST2 = 5, 20
V2_MA_SLOW1, V2_MA_SLOW2 = 50, 90
