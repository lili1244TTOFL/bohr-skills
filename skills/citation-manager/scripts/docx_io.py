#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docx_io.py — 按字符偏移在 docx 段落里插入/删除文字，标记可设为上标。

只在段落内操作 run，不重建段落，保留原有样式。
"""

from __future__ import annotations

from copy import deepcopy

from docx import Document
from docx.text.run import Run


def open_doc(path: str):
    return Document(path)


def paragraph_texts(doc) -> list[str]:
    return [p.text or "" for p in doc.paragraphs]


def _spans(paragraph) -> list[tuple[object, int, int]]:
    out, pos = [], 0
    for r in paragraph.runs:
        t = r.text or ""
        out.append((r, pos, pos + len(t)))
        pos += len(t)
    return out


def delete_range(paragraph, start: int, end: int) -> bool:
    if end <= start:
        return True
    ok = False
    for r, s, e in _spans(paragraph):
        if e <= start or s >= end:
            continue
        t = r.text or ""
        a = max(start, s) - s
        b = min(end, e) - s
        r.text = t[:a] + t[b:]
        ok = True
    return ok


def _new_run(paragraph, ref_run, text: str, superscript: bool = True):
    el = deepcopy(ref_run._element)
    ref_run._element.addnext(el)
    r = Run(el, paragraph._parent)
    r.text = text
    if superscript:
        r.font.superscript = True
    else:
        r.font.superscript = None
    return r


def insert_superscript(paragraph, offset: int, marker: str, superscript: bool = True) -> bool:
    """在段落字符偏移 offset 处插入 marker；superscript=False 时按正文样式写。"""
    runs = list(paragraph.runs)
    if not runs:
        r = paragraph.add_run(marker)
        r.font.superscript = True
        return True
    total = len(paragraph.text or "")
    offset = max(0, min(offset, total))
    pos = 0
    for idx, r in enumerate(runs):
        t = r.text or ""
        s, e = pos, pos + len(t)
        if offset == s and (t or idx == 0):
            el = deepcopy(r._element)
            r._element.addprevious(el)
            nr = Run(el, paragraph._parent)
            nr.text = marker
            nr.font.superscript = superscript
            return True
        if s < offset < e:
            cut = offset - s
            head, tail = t[:cut], t[cut:]
            r.text = head
            suf = _new_run(paragraph, r, tail, superscript=False)
            mid_el = deepcopy(r._element)
            suf._element.addprevious(mid_el)
            mid = Run(mid_el, paragraph._parent)
            mid.text = marker
            mid.font.superscript = superscript
            return True
        if offset == e and (idx == len(runs) - 1 or offset == total):
            _new_run(paragraph, r, marker, superscript=superscript)
            return True
        pos = e
    r = runs[-1]
    _new_run(paragraph, r, marker, superscript=True)
    return True


def superscript_markers(doc) -> list[dict]:
    """列出所有上标 run，用于自检。"""
    out = []
    for i, p in enumerate(doc.paragraphs):
        for r in p.runs:
            if r.font.superscript:
                txt = (r.text or "").strip()
                if txt:
                    out.append({"para_index": i, "text": txt})
    return out


def append_reference_section(doc, entries: list[str], heading: str = "参考文献") -> int:
    doc.add_paragraph(heading)
    n = 0
    for i, txt in enumerate(entries, 1):
        doc.add_paragraph(f"[{i}] {txt}")
        n += 1
    return n


def replace_reference_section(doc, entries: list[str], heading: str = "参考文献",
                              numbered: bool = True) -> tuple[int, int]:
    """删掉原有参考文献段落（标题之后到文末），按新编号重写。返回（删掉段数, 写入条数）。"""
    idx = None
    for i, p in enumerate(doc.paragraphs):
        t = (p.text or "").strip()
        if t == heading or t.startswith("参考文献"):
            idx = i
            break
    removed = 0
    if idx is not None:
        for p in list(doc.paragraphs)[idx:]:
            p._element.getparent().remove(p._element)
            removed += 1
    doc.add_paragraph(heading)
    n = 0
    for i, txt in enumerate(entries, 1):
        doc.add_paragraph(f"[{i}] {txt}" if numbered else txt)
        n += 1
    return removed, n

