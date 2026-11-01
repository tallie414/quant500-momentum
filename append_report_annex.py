# -*- coding: utf-8 -*-
"""
append_report_annex.py —— 在技术报告末尾追加"关键买卖点标注"附录（对照课程要求第5条）

只做纯追加（不动原文任何段落），可重复运行前请确认尚未追加过。
"""
import os

from docx import Document
from docx.shared import Inches, Pt

BASE = os.path.dirname(os.path.abspath(__file__))
DOCX = os.path.join(BASE, "中证500动量选股策略回测报告.docx")
IMG = os.path.join(BASE, "output", "nav_v3a_annotated.png")

d = Document(DOCX)

# 防重复：已含附录标题则跳过
if any("关键买卖点标注" in p.text for p in d.paragraphs):
    print("附录已存在，跳过")
    raise SystemExit

def para(text, bold=False, size=10.5, align=None):
    p = d.add_paragraph()
    r = p.add_run(text)
    r.bold = bold
    r.font.size = Pt(size)
    r.font.name = "宋体"
    if align:
        p.alignment = align
    return p

para("附录　净值曲线的关键买卖点标注（对照课程要求）", bold=True, size=14)
para(
    "课程要求净值曲线须标注关键买卖点，本附录以最优版本 V3-A 为例给出标注结果（见下图）。"
    "V3-A 正式版全区间共发生调仓买入 93 次（图中绿色▲，全部标注）；风控卖出合计 752 笔，"
    "其中固定止损 707 笔、动量排名退出 45 笔。止损笔数多且分散于 406 个交易日，逐日标注会遮蔽净值曲线，"
    "故在曲线上标注当日止损笔数最多的 10 个交易日（深蓝▼，旁注笔数），完整的卖出时点分布由下方"
    "“月度风控卖出笔数”柱状图呈现；排名退出 45 笔（31 个交易日）全部以橙色◆标注。"
)
para(
    "每一处买入标记均对应“月末信号 → 次日开盘执行”的一次调仓，每一笔卖出均可追溯到具体触发类型"
    "（固定止损 -12% 或动量排名跌出全池前 50%），与报告交易记录章节逐笔对应，可在提交的"
    " output/trade_log_v3a.csv 中复核。"
)
para(
    "基准选择理由：策略全部交易发生在中证500历史成分股域内，故选取中证500指数（sh.000905）作为业绩基准，"
    "与课程需求文档要求（6）一致；未选沪深300 等其他指数，是因为其成分域与本策略持仓不具备可比性。"
)

d.add_picture(IMG, width=Inches(6.3))
para("图 A-1　V3-A 净值曲线与关键买卖点标注（上：净值与买卖点；下：月度风控卖出笔数）",
     size=9, align=1)

d.save(DOCX)
print(f"附录已追加 → {DOCX}（现共 {len(d.paragraphs)} 段）")
