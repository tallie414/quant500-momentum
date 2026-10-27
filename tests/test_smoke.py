# -*- coding: utf-8 -*-
"""
tests/test_smoke.py —— 冒烟测试（纯标准库实现，无需 pytest）

用途：在答辩/交付前确认「产物齐全、数据没坏、模块能导入」，避免现场打开页面才发现缺文件。
用法：
    python tests/test_smoke.py            # 快速检查（默认，秒级）
    python tests/test_smoke.py --full     # 追加一次真实引擎回测（数分钟，需要 data/cache）
    python -m pytest tests/test_smoke.py  # 也兼容 pytest

退出码 0 = 全部通过；1 = 存在失败项。
"""
import os
import sys
import json
import argparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
OUT = os.path.join(BASE, "output")
PRESET_DIR = os.path.join(OUT, "web_presets")

VERSIONS = ["mo", "v2", "v2c", "v3a", "v3b"]
RESULTS = []


def check(name, fn):
    """执行一个检查项，记录 PASS/FAIL，异常视为 FAIL 但不中断。"""
    try:
        ok, msg = fn()
    except Exception as e:
        ok, msg = False, f"{type(e).__name__}: {e}"
    RESULTS.append((ok, name, msg))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {msg}" if msg else ""))
    return ok


def test_output_files_exist():
    missing = []
    for k in VERSIONS:
        for f in (f"nav_{k}.csv", f"metrics_{k}.csv", f"trade_log_{k}.csv",
                  f"holdings_log_{k}.csv"):
            if not os.path.exists(os.path.join(OUT, f)):
                missing.append(f)
    for f in ("compare_4v.csv", "sensitivity.csv"):
        if not os.path.exists(os.path.join(OUT, f)):
            missing.append(f)
    return (not missing), ("缺失：" + ", ".join(missing)) if missing else f"{len(VERSIONS)} 个版本产物齐全"


def test_nav_integrity():
    import pandas as pd
    bad = []
    for k in VERSIONS:
        df = pd.read_csv(os.path.join(OUT, f"nav_{k}.csv"), index_col=0, parse_dates=True)
        if not {"strategy", "benchmark"}.issubset(df.columns):
            bad.append(f"{k}: 缺少 strategy/benchmark 列")
            continue
        if len(df) < 500:
            bad.append(f"{k}: 交易日仅 {len(df)} 天")
        if df.isna().any().any():
            bad.append(f"{k}: 存在 NaN")
        if (df[["strategy", "benchmark"]] <= 0).any().any():
            bad.append(f"{k}: 存在非正净值")
        if not df.index.is_monotonic_increasing:
            bad.append(f"{k}: 日期非递增")
    return (not bad), ("；".join(bad)) if bad else "5 条净值序列无 NaN、无异常值、日期递增"


def test_metrics_tables():
    import pandas as pd
    bad = []
    expect = ["累计收益率", "年化收益率", "年化超额收益", "年化波动率",
              "夏普比率(RF=2%)", "最大回撤", "卡玛比率", "信息比率(IR)", "月度胜率"]
    for k in VERSIONS:
        m = pd.read_csv(os.path.join(OUT, f"metrics_{k}.csv"), index_col=0)
        miss = [e for e in expect if e not in m.index]
        if miss:
            bad.append(f"{k}: 缺指标 {miss}")
        if "全区间" not in m.columns:
            bad.append(f"{k}: 缺全区间列")
    return (not bad), ("；".join(bad)) if bad else "5 张指标表结构一致（9 指标 × 3 区间）"


def test_compare_table():
    import pandas as pd
    df = pd.read_csv(os.path.join(OUT, "compare_4v.csv"))
    need = ["版本", "全区间累计_%", "全区间年化超额_%", "全区间最大回撤_%", "全区间IR", "样本外IR"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        return False, f"缺列 {miss}"
    if len(df) != 6:
        return False, f"行数 {len(df)}（应为 4 版本 + V2c + 基准 = 6）"
    v3a = df[df["版本"].str.contains("V3-A", na=False)]
    if len(v3a) != 1:
        return False, "未唯一定位 V3-A 行"
    r = v3a.iloc[0]
    ok = abs(float(r["全区间累计_%"]) - 124.70) < 0.5 and abs(float(r["全区间最大回撤_%"]) + 25.53) < 0.5
    return ok, f"V3-A 累计 {r['全区间累计_%']}%、回撤 {r['全区间最大回撤_%']}%（基准基准线 124.70 / -25.53）"


def test_sensitivity_table():
    import pandas as pd
    df = pd.read_csv(os.path.join(OUT, "sensitivity.csv"))
    if len(df) != 6:
        return False, f"行数 {len(df)}（应为 3+3）"
    if df[["全区间_累计收益率_%", "样本外_累计收益率_%"]].isna().any().any():
        return False, "存在空值"
    return True, "V3-A 三档止损 × V3-B 三档 ATR，共 6 组"


def test_modules_importable():
    mods = ["config", "data_loader", "filters", "strategy", "strategy_v2",
            "metrics", "backtest_v2", "backtest_v3a", "backtest_v3b"]
    import importlib
    bad = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append(f"{m}({type(e).__name__})")
    return (not bad), ("导入失败：" + ", ".join(bad)) if bad else f"{len(mods)} 个核心模块可导入"


def test_app_compiles():
    import py_compile
    import tempfile
    try:
        py_compile.compile(os.path.join(BASE, "app.py"), doraise=True,
                           cfile=os.path.join(tempfile.gettempdir(), "_app_smoke.pyc"))
        return True, "app.py 语法检查通过"
    except Exception as e:
        return False, str(e)


def test_presets_if_any():
    idx = os.path.join(PRESET_DIR, "index.json")
    if not os.path.exists(idx):
        return True, "（跳过）尚未生成预跑方案，可执行 precompute_presets.py"
    with open(idx, encoding="utf-8") as f:
        items = json.load(f)
    bad = []
    for it in items:
        for suffix in ("_nav.csv", "_stats.csv", "_bench.csv", "_meta.json"):
            if not os.path.exists(os.path.join(PRESET_DIR, f"{it['id']}{suffix}")):
                bad.append(f"{it['id']}{suffix}")
    return (not bad), ("缺失：" + ", ".join(bad)) if bad else f"{len(items)} 组预跑方案文件齐全"


def test_engine_full():
    """真实跑一次 V2 回测（最轻的一档），验证引擎与数据缓存可用。"""
    import pickle
    pkl = os.path.join(BASE, "data", "web_ctx.pkl")
    if not os.path.exists(pkl):
        return False, "缺少 data/web_ctx.pkl，请先跑 precompute_presets.py"
    with open(pkl, "rb") as f:
        ctx = pickle.load(f)
    import config as cfg
    import backtest_v2
    import metrics
    nav, tl, _hl = backtest_v2.run_backtest_v2(
        ctx["signal_pairs"], ctx["codes"], ctx["daily_data"],
        ctx["cal_index"], ctx["bench_map"], ctx["industry_map"], exec_mode="open")
    s = metrics.summary_stats(nav, label="全区间")
    if len(nav) < 500 or len(tl) < 10:
        return False, f"结果过短：nav={len(nav)}, trades={len(tl)}"
    return True, f"V2 引擎跑通：{len(nav)} 个交易日、{len(tl)} 次调仓，累计 {s['strategy_total'] * 100:.2f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="追加一次真实引擎回测")
    args = ap.parse_args()

    print(f"项目根目录：{BASE}\n")
    check("产物文件齐全", test_output_files_exist)
    check("净值序列完整性", test_nav_integrity)
    check("指标表结构", test_metrics_tables)
    check("四版本对比表", test_compare_table)
    check("敏感性结果表", test_sensitivity_table)
    check("核心模块可导入", test_modules_importable)
    check("app.py 语法", test_app_compiles)
    check("预跑方案（可选）", test_presets_if_any)
    if args.full:
        check("引擎端到端回测", test_engine_full)

    n_fail = sum(1 for ok, _, _ in RESULTS if not ok)
    print(f"\n合计 {len(RESULTS)} 项，通过 {len(RESULTS) - n_fail} 项，失败 {n_fail} 项")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
