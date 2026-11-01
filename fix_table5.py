# -*- coding: utf-8 -*-
"""重排报告表5：两行分组表头 + 固定列宽 + 数字居中，解决长表头挤压导致的错行。"""
import copy, re
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

DOC = r"D:\quant500_momentum\中证500动量选股策略回测报告.docx"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# 列宽(cm)：版本 2.55 | 累计 1.95 | 年化超额 1.85 | 夏普 1.30 | 回撤 1.85 | 样本内 1.70 | 样本外 1.70 | IR全 1.30 | IR外 1.30
COLS_CM = [2.55, 1.95, 1.85, 1.30, 1.85, 1.70, 1.70, 1.30, 1.30]
WIDTHS = [int(round(c * 567)) for c in COLS_CM]
assert len(WIDTHS) == 9
TOTAL = sum(WIDTHS)

GROUP = {1: "全区间绩效", 5: "分段累计收益", 7: "信息比率 IR"}
SUB = ["累计收益", "年化超额", "夏普", "最大回撤", "样本内", "样本外", "全区间", "样本外"]

doc = Document(DOC)
t = doc.tables[5]


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
    pf.first_line_indent = 0   # 关键：清除继承自Normal的首行缩进，否则数字被挤折行
    pf.left_indent = 0
    r = p.add_run(text)
    style_run(r, size=size, bold=bold)
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER


# ---- 1. 在最上方插入一行（复制原表头行，保留粗顶线样式）----
tr0 = t.rows[0]._tr
new_tr = copy.deepcopy(tr0)
tr0.addprevious(new_tr)

# ---- 2. 合并表头 ----
t.cell(0, 0).merge(t.cell(1, 0))          # 版本：纵向跨两行
t.cell(0, 1).merge(t.cell(0, 4))          # 全区间绩效
t.cell(0, 5).merge(t.cell(0, 6))          # 分段累计收益
t.cell(0, 7).merge(t.cell(0, 8))          # 信息比率 IR

# ---- 3. 写表头文字 ----
set_text(t.cell(0, 0), "版本", bold=True)
for ci, txt in GROUP.items():
    set_text(t.cell(0, ci), txt, bold=True)
for i, txt in enumerate(SUB):
    set_text(t.cell(1, i + 1), txt, bold=True)

# 第二行表头底部线保持细线；第一行表头下沿改为细线（已有）
def set_borders(cell, **kw):
    tcPr = cell._tc.get_or_add_tcPr()
    b = tcPr.find(qn("w:tcBorders"))
    if b is None:
        b = OxmlElement("w:tcBorders")
        tcPr.append(b)
    for edge, sz in kw.items():
        e = b.find(qn(f"w:{edge}"))
        if e is None:
            e = OxmlElement(f"w:{edge}")
            b.append(e)
        if sz is None:
            e.set(qn("w:val"), "none")
        else:
            e.set(qn("w:val"), "single"); e.set(qn("w:color"), "000000")
            e.set(qn("w:sz"), str(sz)); e.set(qn("w:space"), "0")

# ---- 4. 数据行：统一 9pt、数字居中、首列左对齐 ----
data_rows = t.rows[2:]
for row in data_rows:
    for ci, cell in enumerate(row.cells):
        old = cell.text.strip()
        align = WD_ALIGN_PARAGRAPH.LEFT if ci == 0 else WD_ALIGN_PARAGRAPH.CENTER
        set_text(cell, old, bold=False, align=align, size=9)

# ---- 5. 固定列宽 + 固定布局（注意 tblPr 子元素顺序：tblW -> tblLayout -> tblLook）----
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
# 表级单元格边距收紧（左右 80dxa≈0.14cm）；OOXML顺序：tblCellMar须在tblLook之前
tblCellMar = OxmlElement("w:tblCellMar")
for edge, w in (("top", "40"), ("left", "80"), ("bottom", "40"), ("right", "80")):
    e = OxmlElement(f"w:{edge}")
    e.set(qn("w:w"), w); e.set(qn("w:type"), "dxa")
    tblCellMar.append(e)
tblPr.append(tblCellMar)
tblLook = OxmlElement("w:tblLook")
tblLook.set(qn("w:firstRow"), "1"); tblLook.set(qn("w:lastRow"), "0")
tblLook.set(qn("w:firstColumn"), "1"); tblLook.set(qn("w:lastColumn"), "0")
tblLook.set(qn("w:noHBand"), "0"); tblLook.set(qn("w:noVBand"), "1")
tblLook.set(qn("w:val"), "04A0")
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

# 逐行按 gridSpan 写 tcW
for tr in tbl.findall(qn("w:tr")):
    col = 0
    for tc in tr.findall(qn("w:tc")):
        tcPr = tc.get_or_add_tcPr()
        span_el = tcPr.find(qn("w:gridSpan"))
        span = int(span_el.get(qn("w:val"))) if span_el is not None else 1
        w = sum(WIDTHS[col:col + span])
        el = tcPr.find(qn("w:tcW"))
        if el is None:
            el = OxmlElement("w:tcW"); tcPr.insert(0, el)
        el.set(qn("w:w"), str(w)); el.set(qn("w:type"), "dxa")
        col += span

# ---- 6. 行高与不跨页、表头重复 ----
for i, tr in enumerate(tbl.findall(qn("w:tr"))):
    trPr = tr.find(qn("w:trPr"))
    if trPr is None:
        trPr = OxmlElement("w:trPr"); tr.insert(0, trPr)
    for tag in ("w:cantSplit", "w:tblHeader", "w:trHeight"):
        e = trPr.find(qn(tag))
        if e is not None:
            trPr.remove(e)
    cs = OxmlElement("w:cantSplit"); trPr.append(cs)
    h = OxmlElement("w:trHeight")
    h.set(qn("w:val"), "340" if i >= 2 else "320")
    h.set(qn("w:hRule"), "atLeast")
    trPr.append(h)
    if i < 2:
        trPr.append(OxmlElement("w:tblHeader"))

doc.save(DOC)
print("已重排表5：", len(t.rows), "行 ×", len(t.columns), "列，总宽", round(TOTAL / 567, 2), "cm")

# ---- 7. 顺带修复其余表格单元格的首行缩进（同样的病根，避免数字/文字错位）----
fixed_cells = 0
for tb in doc.tables:
    if tb._tbl is t._tbl:
        continue
    for row in tb.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                p.paragraph_format.first_line_indent = 0
                fixed_cells += 1
print("其余表格清除首行缩进的段落数:", fixed_cells)
for ri, row in enumerate(t.rows):
    print(f"  R{ri}:", " ¦ ".join(c.text.strip() or "∅" for c in row.cells))
