# -*- coding: utf-8 -*-
"""
tests/test_pages.py —— Web 应用页面级自检（基于 Streamlit 官方 AppTest 框架）

逐个切换 app.py 的六个页面并执行，确认无运行时异常（白屏是演示最大的风险）。
用法：
    python tests/test_pages.py          # 默认跳过耗时的「完整自定义」模式
    python tests/test_pages.py --full   # 追加验证在线回测的完整自定义模式（需已预热，耗时数分钟）
"""
import os
import sys
import argparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from streamlit.testing.v1 import AppTest   # noqa: E402

PAGES = ["四版本总览", "单版本详情", "参数敏感性",
         "在线回测（调参实验）", "答辩速查（一页纸）", "口径与说明"]


def run_page(at, page, timeout):
    at.sidebar.radio[0].set_value(page)
    at.run(timeout=timeout)
    errs = [e.value for e in at.exception]
    warns = [w.value for w in at.warning]
    return errs, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="追加验证在线回测完整自定义模式")
    args = ap.parse_args()

    at = AppTest.from_file(os.path.join(BASE, "app.py"), default_timeout=300)
    at.run()
    print(f"[启动] 默认页面执行完成，异常 {len(at.exception)} 项")

    failed = []
    for page in PAGES:
        errs, warns = run_page(at, page, timeout=300)
        status = "PASS" if not errs else "FAIL"
        print(f"[{status}] 页面：{page}" + (f" —— 警告 {len(warns)} 条" if warns else ""))
        for e in errs:
            print(f"       异常：{e[:400]}")
        if errs:
            failed.append(page)

    if args.full:
        # 切到在线回测 → 完整自定义模式（会真实加载数据缓存与跑引擎）
        at.sidebar.radio[0].set_value("在线回测（调参实验）").run(timeout=300)
        try:
            at.radio[0].set_value("🛠 完整自定义（现场重跑引擎）")
            at.run(timeout=1800)
            errs = [e.value for e in at.exception]
            print(f"[{'PASS' if not errs else 'FAIL'}] 在线回测 · 完整自定义模式加载")
            for e in errs:
                print(f"       异常：{e[:400]}")
            if errs:
                failed.append("在线回测-完整自定义")
        except Exception as e:
            print(f"[FAIL] 完整自定义模式：{type(e).__name__}: {e}")
            failed.append("在线回测-完整自定义")

    print(f"\n合计 {len(PAGES)} 个页面，失败 {len(failed)} 个" + (f"：{failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
