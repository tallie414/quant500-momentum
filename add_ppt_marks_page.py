# -*- coding: utf-8 -*-
"""在27页PPT的P18(分段净值)之后插入一页"关键买卖点标注"，克隆P17版式，页码全量改 /28。"""
import copy, re, shutil, sys
from pptx import Presentation
from pptx.util import Inches, Emu

SRC = r"D:\xwechat_files\wxid_bp1009od5hbj12_b9e5\temp\RWTemp\2026-10\0747933ce7136b486e9bb31a24fd0964\中证500动量加基本面选股策略.pptx"
DST = r"D:\quant500_momentum\中证500动量加基本面选股策略_28页_买卖点版.pptx"
PNG = r"D:\quant500_momentum\output\nav_v3a_annotated.png"
INSERT_AT = 18  # 0-based：新页成为第19页

shutil.copy(SRC, DST)
prs = Presentation(DST)
tpl = prs.slides[16]  # P17 四版本净值页（图+右侧图例卡+底注，版式最接近）

# ---- 1. 新建空白页（同版式），清掉版式带来的占位符 ----
new = prs.slides.add_slide(tpl.slide_layout)
for ph in list(new.shapes):
    ph._element.getparent().remove(ph._element)

# ---- 2.5 复制P17的页面级深蓝背景（p:bg 必须是 cSld 第一个子元素）----
PML = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
bg17 = tpl._element.find(f"{PML}cSld/{PML}bg")
if bg17 is not None:
    cSld_new = new._element.find(f"{PML}cSld")
    cSld_new.insert(0, copy.deepcopy(bg17))
    print("已复制页面背景")
else:
    print("警告：P17无页面级背景")

# ---- 2. 从P17克隆文本形状（页眉/页码/标题/分隔线/底注/右侧卡片），按名字取 ----
def find(slide, name):
    for sh in slide.shapes:
        if sh.name == name:
            return sh
    raise KeyError(name)

def clone_to(shape, dst_slide, text=None, left=None, top=None, width=None, height=None):
    el = copy.deepcopy(shape._element)
    dst_slide.shapes._spTree.append(el)
    from pptx.shapes.autoshape import Shape
    ns = dst_slide.shapes[-1]
    if text is not None:
        tf = ns.text_frame
        paras = tf.paragraphs
        p0 = paras[0]
        if p0.runs:
            # 保留首个run的rPr(字号/加粗/颜色)，只换文字；删掉多余run与段落
            p0.runs[0].text = text
            for r in p0.runs[1:]:
                r._r.getparent().remove(r._r)
        else:
            p0.text = text
        for p in paras[1:]:
            p._p.getparent().remove(p._p)
    if left is not None: ns.left = left
    if top is not None: ns.top = top
    if width is not None: ns.width = width
    if height is not None: ns.height = height
    return ns

hdr    = find(tpl, "S16_O001")   # "回测结果" 页眉
pageno = find(tpl, "S16_O002")   # 页码
title  = find(tpl, "S16_O003")   # 大标题
rule   = find(tpl, "S16_O004")   # 分隔细线
note1  = find(tpl, "S16_O013")   # 底注1
note2  = find(tpl, "S16_O014")   # 底注2(数据来源)
card   = find(tpl, "S16_O007")   # 右侧图例卡样式

clone_to(hdr, new)
clone_to(pageno, new, text="19 / 28")
clone_to(title, new, text="净值曲线上的关键买卖点（V3-A 全区间）")
clone_to(rule, new)
clone_to(note1, new, text="口径：月末信号→次日开盘执行；止损全期 707 笔 / 406 天，图示最集中 10 日")
clone_to(note2, new, text="数据来源：回测报告附录 图 A-1（output/nav_v3a_annotated.png）")

# 右侧四张要点卡
cards = [
    "调仓买入 ▲ 93 次",
    "排名退出 ◆ 45 笔 / 31 天",
    "止损卖出 ▼ 707 笔",
    "图示为止损最集中 10 日",
]
ys = [1.90, 2.62, 3.34, 4.06]
for t, y in zip(cards, ys):
    clone_to(card, new, text=t, left=Inches(9.72), top=Inches(y), width=Inches(3.05), height=Inches(0.52))

# ---- 3. 插入标注图（原始比例1100x676 → 高5.25、宽8.54，居左）----
pic = new.shapes.add_picture(PNG, Inches(0.70), Inches(1.60), height=Inches(5.25))
print("图片尺寸(inch):", round(pic.width/914400,2), "x", round(pic.height/914400,2))

# ---- 4. 把新页移动到 P18 之后 ----
sldIdLst = prs.slides._sldIdLst
ids = list(sldIdLst)
new_id = ids[-1]
sldIdLst.remove(new_id)
sldIdLst.insert(INSERT_AT, new_id)

# ---- 5. 全量重编页码 "N / 27" -> "N / 28" ----
pat = re.compile(r"^\s*(\d+)\s*/\s*27\s*$")
fixed = 0
for i, s in enumerate(prs.slides, 1):
    for sh in s.shapes:
        if sh.has_text_frame:
            t = sh.text_frame.text
            m = pat.match(t.strip())
            if m:
                # 只替换第一个段落第一个run，保格式
                p = sh.text_frame.paragraphs[0]
                if p.runs:
                    p.runs[0].text = f"{i} / 28"
                    for r in p.runs[1:]:
                        r.text = ""
                    fixed += 1
                else:
                    sh.text_frame.text = f"{i} / 28"
                    fixed += 1

prs.save(DST)
print(f"页码更新 {fixed} 处；已保存: {DST}")

# ---- 6. 校验 ----
prs2 = Presentation(DST)
print("总页数:", len(prs2.slides))
for i in (17, 18, 19, 20, 28):
    s = prs2.slides[i-1]
    texts = [sh.text_frame.text.strip().replace("\n", " ")[:38]
             for sh in s.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
    pics = sum(1 for sh in s.shapes if sh.shape_type == 13)
    print(f"P{i:02d} [图{pics}]", " | ".join(texts[:3]))