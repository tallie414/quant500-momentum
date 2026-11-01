# -*- coding: utf-8 -*-
"""报告补写明确的筛选标准：ROE>=8%（最新已公告报告期年化）、0<PB<=10。三处：摘要/需求对照/V1设计。"""
import shutil
from docx import Document

SRC = r"D:\quant500_momentum\中证500动量选股策略回测报告.docx"
TMP = r"D:\quant500_momentum\_tmp_report2.docx"
shutil.copy(SRC, TMP)

doc = Document(TMP)
ps = doc.paragraphs

# [15] 摘要：r15 ' ROE/PB ' → 明确阈值
p = ps[15]
assert p.runs[15].text.strip() == "ROE/PB", p.runs[15].text
p.runs[15].text = " 年化ROE≥8%、0<PB≤10 "

# [22] 需求对照：r15 'ROE/PB' → 明确阈值
p = ps[22]
assert p.runs[15].text == "ROE/PB", p.runs[15].text
p.runs[15].text = "年化ROE≥8%、0<PB≤10"

# [38] V1 设计说明：把"达到设定门槛"改为完整规则+设计理由
p = ps[38]
r = p.runs
assert "达到设定门槛" in r[7].text, r[7].text
r[3].text = "日新高；基本面过滤采用全局阈值：最新已公告报告期年化 ROE≥8%，且 0<PB≤10"
r[4].text = ""
r[5].text = ""
r[6].text = ""
r[7].text = "（PB>0 剔除资不抵债个股、PB≤10 剔除估值泡沫个股）；符合条件者按动量强度取前"

doc.save(TMP)

# 校验
d2 = Document(TMP)
for i in (15, 22, 38):
    print(f"[{i}]", d2.paragraphs[i].text.strip()[:150])
    print()
