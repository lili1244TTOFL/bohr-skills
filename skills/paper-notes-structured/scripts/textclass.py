#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""textclass.py — 行级角色判定的统一口径。

layout.py（按列切段）与 pdfparse.py（按坐标聚行）是两条解析路径，历史上各自
维护一份正则，结果同一条 "2. Related Work" 在两条路径上被判成不同角色：
旧版把「编号 + 点 + 空格」一律当参考文献条目，导致所有编号章节被吞进
reference，章节骨架缺节。本模块把判定收到一处，两条路径都从这里取。

判据（都不看语义，只看形态）：
  heading   编号短标题 / 章节词 / 短全大写 / 无数字的 Title Case / 短中文行
  reference 方括号编号，或「编号 + 多点 + 引文痕迹（逗号、et al、doi、页码区间）」
  caption   以 图/表/Figure/Fig/Table + 编号 开头
  formula   行尾带 (1)、(2a) 之类公式编号
  page_no   - 12 - 形式

用法（库）：from textclass import heading_reject, is_reference_text
"""

from __future__ import annotations

import re

HEAD_WORDS = re.compile(r"(?i)^(abstract|introduction|background|related work|methods?|"
                        r"materials and methods|experiments?|results?|discussion|conclusions?|"
                        r"references|acknowledge?ments?|appendix|摘要|引言|前言|方法|结果|"
                        r"讨论|结论|参考文献|致谢|附录)\b")
HEAD_NUM = re.compile(r"^\s*\d+(?:\.\d+)*[\s.、]\s*(?=\S)")
CAPTION = re.compile(r"^\s*(图|表|Figure|Fig\.?|Table)\s*[0-9A-Z]")
FORMULA_NO = re.compile(r"[（(]\s*(\d+[a-z]?)\s*[）)]\s*$")
PAGE_NO = re.compile(r"^\s*[-–—]\s*\d{1,4}\s*[-–—]\s*$")
REF_BRACKET = re.compile(r"^\s*\[\d{1,3}\]\s+\S")
REF_NUM = re.compile(r"^\s*\d{1,3}\.\s+\S")
CITE_CUE = re.compile(r"(?i)(,\s|\bet al\b|\bdoi\b|\bpp?\.\s*\d|\d{2,4}\s*[-–]\s*\d{2,4}|"
                      r"proceedings|journal|conference|arxiv)")
ALL_CAPS = re.compile(r"^[A-Z][A-Z0-9 \-:,]{4,}$")
ALPHA = re.compile(r"[A-Za-z\u4e00-\u9fff]")
CJK = re.compile(r"[\u4e00-\u9fff]")
NUM_DOM = re.compile(r"^[\s\d.,%±×^()+\-/–—:]*$")


def strip_markers(t: str) -> str:
    return re.sub(r"^[#*\->\s]+", "", t or "").strip()


def is_page_number(t: str) -> bool:
    return bool(PAGE_NO.match(t or ""))


def is_formula(t: str) -> bool:
    return bool(FORMULA_NO.search(t or ""))


def is_caption(t: str) -> bool:
    return bool(CAPTION.match(t or ""))


def heading_reject(t: str) -> str | None:
    """返回拒绝原因；None 表示形态上像标题。

    与旧版差异：先剥编号再判句末标点，避免 "3.3. Network Architectures" 被编号里
    的点误判成正文残片；编号后必须是大写/中文首字，避免 "8 images (i.e., ..." 这类
    正文行混进标题。
    """
    core = strip_markers(t)
    if not core:
        return "空标题"
    if not ALPHA.search(core):
        return "无字母（纯数值/符号）"
    digits = sum(c.isdigit() for c in core)
    if digits and digits / max(1, len(core)) > 0.35:
        return "数字占比过高（疑表格数值）"
    if NUM_DOM.match(core):
        return "纯数值行"
    if core.endswith(("-", "–")):
        return "行尾连字符（断行残片）"
    if len(core) > 80:
        return "过长"
    m = HEAD_NUM.match(core)
    if m:
        rest = core[m.end():].strip()
        if not rest:
            return "编号后无标题"
        if re.search(r"[.!?。！？]\s+[A-Z\u4e00-\u9fff]", rest):
            return "编号后含句末标点（正文残片）"
        if len(rest.split()) > 10:
            return "编号后词数过多（正文行）"
        if rest.rstrip().endswith((".", "。")):
            return "编号后以句号收尾（正文/图注残片）"
        first = rest[:1]
        if not (first.isupper() or first.isdigit() or CJK.search(first)):
            return "编号后非标题首字（正文行）"
        return None
    if HEAD_WORDS.match(core):
        first = core[:1]
        if not (first.isupper() or CJK.search(first)):
            return "章节词后续正文（句中小写起头）"
        return None if len(core) <= 40 and len(core.split()) <= 6 else "章节词后接正文（正文行）"
    if core.isupper() and len(core) >= 4 and not digits and core.replace("-", "").isalpha():
        return None
    words = core.split()
    if 2 < len(words) <= 12:
        caps = [w for w in words if w[:1].isalpha()]
        if caps and all(w[:1].isupper() for w in caps) and not digits \
                and not re.search(r"[^\w\s\-,'&/()\.]", core) \
                and not re.search(r"[.!?]\s", core) \
                and not core.endswith((",", "，", ":", "：", ".", "。")):
            return None
    if CJK.search(core) and len(core) <= 20 and HEAD_NUM.match(core):
        return None
    return "无编号且非标题形态"


def is_heading_text(t: str) -> bool:
    return heading_reject(t) is None


def is_reference_text(t: str) -> bool:
    """参考文献条目：方括号编号，或「编号 + 多点 + 引文痕迹」。

    关键点：编号章节 "2. Related Work" 不含引文痕迹，判 heading 而不是 reference。
    """
    core = strip_markers(t)
    if not core:
        return False
    if REF_BRACKET.match(core):
        return True
    if not REF_NUM.match(core):
        return False
    rest = core[REF_NUM.match(core).end():]
    if len(rest.split()) < 4:
        return False
    if heading_reject(core) is None:
        return False
    return bool(CITE_CUE.search(rest))


def classify_role(t: str) -> str:
    """行角色：公式/页码/图注/参考文献在前，标题只认形态通过的。

    标题不再看字号/加粗（两条解析路径拿不到一致的字体信息），只看形态：
    口径唯一，pdfparse.prune_headings 用的也是 heading_reject。
    """
    s = (t or "").strip()
    if is_formula(s):
        return "formula"
    if is_page_number(s):
        return "page_number"
    if is_caption(s):
        return "caption"
    if is_reference_text(s):
        return "reference"
    if len(s) <= 80 and heading_reject(s) is None:
        return "heading"
    return "body"
