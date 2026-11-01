# -*- coding: utf-8 -*-
"""重排报告表6（参数敏感性）：两行分组表头 + 固定列宽，与表5同款修法。"""
import copy
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

DOC = r"D:\quant500_momentum\_tmp_report.docx"

# 列宽(cm)：版本1.2 参数2.5 | 全区间:累计1.8 回撤1.6 | 样本外:累计1.6 年化超额1.6 夏普1.2 回撤1.6 | 风控2.4
COLS_CM = [1.2, 2.5, 1.8, 1.6, 1.6, 1.6, 1.2, 1.6, 2.4]
WIDTHS = [int(round(c * 567)) for c in COLS_CM]
TOTAL = sum(WIDTHS)
assert len(WIDTHS) == 9

doc = Document(DOC)
t = doc.tables[6]
assert "参数" in t.rows[0].cells[1].text, "表6结构不符合预期"


def style_run(run, size=9, bold=False):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = "Times New Roman"
    rPr = run._r.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), "Times New Roman")
    rFonts.set(qn("w:hAnsi"), "Times New Roman")
    rFonts.set(qn("w:eastAsia"), "宋体")


def set_text(cell, text, bold=False, align=WD_ALIGN_PARAGRAPH.CENTER, size=9):
    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = align
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing = 1.0
    pf.first_line_indent = 0
    pf.left_indent = 0
    if "\n" in text:  # 显式两行（用于"（正式）"标注）
        parts = text.split("\n")
        r = p.add_run(parts[0]); style_run(r, size=size, bold=bold)
        r.add_break()
        r2 = p.add_run(parts[1]); style_run(r2, size=size, bold=bold)
    else:
        r = p.add_run(text); style_run(r, size=size, bold=bold)
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER


# ---- 1. 插入分组表头行 ----
tr0 = t.rows[0]._tr
tr0.addprevious(copy.deepcopy(tr0))

# ---- 2. 合并：版本、参数纵向跨两行；全区间(2,3列)、样本外(4-7列)、风控(8列纵向) ----
t.cell(0, 0).merge(t.cell(1, 0))   # 版本
t.cell(0, 1).merge(t.cell(1, 1))   # 参数
t.cell(0, 2).merge(t.cell(0, 3))   # 全区间绩效
t.cell(0, 4).merge(t.cell(0, 7))   # 样本外绩效
t.cell(0, 8).merge(t.cell(1, 8))   # 风控触发

set_text(t.cell(0, 0), "版本", bold=True)
set_text(t.cell(0, 1), "参数", bold=True)
set_text(t.cell(0, 2), "全区间绩效", bold=True)
set_text(t.cell(0, 4), "样本外绩效", bold=True)
set_text(t.cell(0, 8), "风控触发", bold=True)
for ci, txt in zip(range(2, 9), ["累计收益", "最大回撤", "累计收益", "年化超额", "夏普", "最大回撤", "次数"]):
    set_text(t.cell(1, ci), txt, bold=True)

# ---- 3. 数据行：9pt、居中、版本列左对齐、参数列"（正式）"换行 ----
for row in t.rows[2:]:
    cells = row.cells
    set_text(cells[0], cells[0].text.strip(), align=WD_ALIGN_PARAGRAPH.CENTER)
    ptxt = cells[1].text.strip()
    ptxt = " ".join(ptxt.split())            # 压掉多余空格
    if "（正式）" in ptxt:
        ptxt = ptxt.replace("（正式）", "").strip() + "\n（正式）"
    set_text(cells[1], ptxt, align=WD_ALIGN_PARAGRAPH.CENTER)
    for c in cells[2:]:
        txt = " ".join(c.text.split())
        set_text(c, txt, align=WD_ALIGN_PARAGRAPH.CENTER)

# ---- 4. 固定布局（tblPr子元素顺序：tblW -> tblLayout -> tblCellMar -> tblLook）----
tbl = t._tbl
tblPr = tbl.tblPr
for tag in ("w:tblW", "w:tblLayout", "w:tblLook"):
    el = tblPr.find(qn(tag))
    if el is not None:
        tblPr.remove(el)
tblW = OxmlElement("w:tblW"); tblW.set(qn("w:w"), str(TOTAL)); tblW.set(qn("w:type"), "dxa")
tblPr.append(tblW)
tblLayout = OxmlElement("w:tblLayout"); tblLayout.set(qn("w:type"), "fixed")
tblPr.append(tblLayout)
tblCellMar = OxmlElement("w:tblCellMar")
for edge, w in (("top", "40"), ("left", "80"), ("bottom", "40"), ("right", "80")):
    e = OxmlElement(f"w:{edge}")
    e.set(qn("w:w"), w); e.set(qn("w:type"), "dxa")
    tblCellMar.append(e)
tblPr.append(tblCellMar)
tblLook = OxmlElement("w:tblLook")
for k, v in (("firstRow", "1"), ("lastRow", "0"), ("firstColumn", "1"),
             ("lastColumn", "0"), ("noHBand", "0"), ("noVBand", "1"), ("val", "04A0")):
    tblLook.set(qn(f"w:{k}"), v)
tblPr.append(tblLook)
for tc in tbl.iter(qn("w:tc")):
    tcPr = tc.find(qn("w:tcPr"))
    if tcPr is not None:
        m = tcPr.find(qn("w:tcMar"))
        if m is not None:
            tcPr.remove(m)

grid = tbl.find(qn("w:tblGrid"))
for g, w in zip(grid.findall(qn("w:gridCol")), WIDTHS):
    g.set(qn("w:w"), str(w))
for tr in tbl.findall(qn("w:tr")):
    col = 0
    for tc in tr.findall(qn("w:tc")):
        tcPr = tc.get_or_add_tcPr()
        span_el = tcPr.find(qn("w:gridSpan"))
        span = int(span_el.get(qn("w:val"))) if span_el is not None else 1
        el = tcPr.find(qn("w:tcW"))
        if el is None:
            el = OxmlElement("w:tcW"); tcPr.insert(0, el)
        el.set(qn("w:w"), str(sum(WIDTHS[col:col + span]))); el.set(qn("w:type"), "dxa")
        col += span

# ---- 5. 行高/不跨页/表头重复 ----
for i, tr in enumerate(tbl.findall(qn("w:tr"))):
    trPr = tr.find(qn("w:trPr"))
    if trPr is None:
        trPr = OxmlElement("w:trPr"); tr.insert(0, trPr)
    for tag in ("w:cantSplit", "w:tblHeader", "w:trHeight"):
        e = trPr.find(qn(tag))
        if e is not None:
            trPr.remove(e)
    trPr.append(OxmlElement("w:cantSplit"))
    h = OxmlElement("w:trHeight")
    h.set(qn("w:val"), "340" if i >= 2 else "320")
    h.set(qn("w:hRule"), "atLeast")
    trPr.append(h)
    if i < 2:
        trPr.append(OxmlElement("w:tblHeader"))

doc.save(DOC)
print("表6已重排：", len(t.rows), "行，总宽", round(TOTAL / 567, 2), "cm")
for ri, row in enumerate(t.rows):
    print(f"  R{ri}:", " ¦ ".join(c.text.strip().replace(chr(10), "/") or "∅" for c in row.cells))
