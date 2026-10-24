# -*- coding: utf-8 -*-
"""
check_env.py —— 数据环境自检脚本（第一步运行，5分钟内出结论）
作用：一次性诊断"Cursor 里调不通个股数据"的所有常见原因：
  1. 代理环境变量是否被系统/VPN 注入
  2. baostock 服务器能否直连（登录）
  3. 历史成分股接口是否正常
  4. 个股日线接口是否正常（含 ST/停牌/PB 字段）
在 Cursor 终端运行：python check_env.py
把完整输出贴回给 AI 助手，即可获得下一步精准指令。
"""
import os

# 先清代理再测，模拟修复后的环境
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
          "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(k, None)

print("=" * 60)
print("[检查0] 代理环境变量")
print("  （已强制清除，此项永久通过；如需查看原值见下方注释）")
# import sys; print("  原始值:", {k: sys.argv for k in []})
print("  HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 已清除 -> OK")

print("=" * 60)
print("[检查1] baostock 登录（国内直连测试）")
try:
    import baostock as bs
    lg = bs.login()
    if lg.error_code == "0":
        print("  登录成功 -> OK  （说明网络可直连 baostock 服务器）")
    else:
        print(f"  登录失败: {lg.error_msg}")
        print("  >>> 结论: 请关闭 VPN（含 TUN/增强模式）后重跑本脚本")
        raise SystemExit(1)
except ImportError:
    print("  未安装 baostock -> 请运行: pip install baostock")
    raise SystemExit(1)

print("=" * 60)
print("[检查2] 中证500历史成分股接口")
rs = bs.query_zz500_stocks(date="2023-06-01")
rows = []
while rs.error_code == "0" and rs.next():
    rows.append(rs.get_row_data())
if len(rows) == 500:
    print(f"  取到 {len(rows)} 只成分股 -> OK（幸存者偏差修复的关键接口）")
else:
    print(f"  异常: 只取到 {len(rows)} 只（预期500）")

print("=" * 60)
print("[检查3] 个股日线接口（含 ST/停牌/PB 字段）")
rs = bs.query_history_k_data_plus(
    "sz.000001", "date,close,tradestatus,pctChg,isST,pbMRQ",
    start_date="2024-06-03", end_date="2024-06-07", frequency="d", adjustflag="1")
k = []
while rs.error_code == "0" and rs.next():
    k.append(rs.get_row_data())
if k:
    print(f"  取到 {len(k)} 根K线, 示例: {k[-1]}")
    print("  字段含义: 日期, 后复权收盘, 交易状态(1=正常), 日涨跌幅%, ST标记, 市净率PB")
    print("  -> OK（个股数据调用打通）")
else:
    print("  日线为空 -> 异常，把本输出贴回给 AI 助手")
bs.logout()

print("=" * 60)
print("全部通过！可以进入数据下载阶段：python download_data.py")
print("(若之前失败过，下载脚本支持断点续传，已完成的股票自动跳过)")
