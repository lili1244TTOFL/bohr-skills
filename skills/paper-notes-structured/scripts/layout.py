#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""layout.py — 用 layout 模式文本判栏与排序。

依据 pypdf `extract_text(extraction_mode="layout")` 的输出：同一视觉行内，
不同栏的文字被大段空格隔开。按"连续 ≥5 个空格"切段，段落的起始列位置
聚成栏，再按栏左→右、栏内上→下输出。

这条路径不依赖字符坐标，因此不受 TJ 数组字距偏移影响；代价是定位单位
从 bbox 变成"页 + 行号 + 列区间"。

用法（库）：
    from layout import parse_page_layout
    page_blocks = parse_page_layout(page, pno)
"""

from __future__ import annotations

import re

SPLIT = re.compile(r"[ \t]{5,}")
CAPTION = re.compile(r"^\s*(图|表|Figure|Fig\.?|Table)\s*[0-9A-Z]")
REF_ITEM = re.compile(r"^\s*(\[\d+\]|\d+\.)\s+\S")
FORMULA_NO = re.compile(r"[（(]\s*(\d+[a-z]?)\s*[）)]\s*$")
PAGE_NO = re.compile(r"^\s*[-–—]\s*\d{1,4}\s*[-–—]\s*$")
HEAD_NUM = re.compile(r"^\s*\d+(\.\d+)*[\s.、]\s*\S")
ALL_CAPS = re.compile(r"^[A-Z][A-Z0-9 \-:,]{4,}$")
HEAD_WORDS = re.compile(r"(?i)^(abstract|introduction|background|related work|methods?|"
                        r"materials and methods|experiments?|results?|discussion|conclusions?|"
                        r"references|acknowledge?ments?|appendix|摘要|引言|前言|方法|结果|"
                        r"讨论|结论|参考文献|致谢|附录)\b")


def page_text(page) -> str:
    try:
        return page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False) or ""
    except Exception:
        try:
            return page.extract_text() or ""
        except Exception:
            return ""


def segments(text: str) -> list[list[dict]]:
    """每行切成若干段，段带起始列。"""
    out = []
    for line in text.split("\n"):
        if not line.strip():
            out.append([])
            continue
        parts, pos = [], 0
        for m in SPLIT.finditer(line):
            chunk = line[pos:m.start()]
            if chunk.strip():
                parts.append({"col": pos + len(chunk) - len(chunk.lstrip()), "text": chunk.strip()})
            pos = m.end()
        tail = line[pos:]
        if tail.strip():
            parts.append({"col": pos + len(tail) - len(tail.lstrip()), "text": tail.strip()})
        out.append(parts)
    return out


def cluster_cols(rows: list[list[dict]], tol: int = 8) -> list[int]:
    """找主栏与副栏：主栏取出现最多的起始列，副栏需与主栏相距 ≥20 列且占比 ≥15%。"""
    from collections import Counter
    cnt: Counter = Counter()
    for r in rows:
        for seg in r:
            cnt[seg["col"] // tol * tol] += 1
    if not cnt:
        return []
    total = sum(cnt.values())
    main = cnt.most_common(1)[0][0]
    buckets = [main]
    for col, c in sorted(cnt.items()):
        if col >= main + 20 and c >= max(3, total * 0.15):
            buckets.append(col)
    return buckets


def order_rows(rows: list[list[dict]], buckets: list[int], tol: int = 8) -> list[dict]:
    """按栏左→右、栏内上→下输出；同一行内的多个段按列从左到右。"""
    if not buckets:
        return []
    out: list[dict] = []
    for bi, left in enumerate(buckets):
        right = buckets[bi + 1] if bi + 1 < len(buckets) else 10 ** 6
        for i, r in enumerate(rows):
            for p in r:
                if left - 1 <= p["col"] < right - tol // 2 or (bi == len(buckets) - 1 and p["col"] >= left - 1):
                    out.append({"col": bi, "text": p["text"], "line": i, "x": p["col"]})
    return out


def classify(text: str, body_len: float) -> str:
    t = text.strip()
    if FORMULA_NO.search(t):
        return "formula"
    if PAGE_NO.match(t):
        return "page_number"
    if CAPTION.match(t):
        return "caption"
    if REF_ITEM.match(t):
        return "reference"
    if len(t) <= 80 and HEAD_NUM.match(t):
        return "heading"
    if len(t) <= 80 and ALL_CAPS.match(t):
        return "heading"
    if len(t) <= 60 and HEAD_WORDS.match(t) and not t.endswith(("。", ".", ";", "；")):
        return "heading"
    return "body"


def heading_level(text: str) -> int:
    t = text.strip()
    m = re.match(r"^(\d+)(\.\d+)*[\s.、]", t)
    if m:
        return min(1 + t.count("."), 3)
    return 1 if ALL_CAPS.match(t) else 2


def parse_page_layout(page, pno: int) -> dict:
    text = page_text(page)
    rows = segments(text)
    buckets = cluster_cols(rows)
    ordered = order_rows(rows, buckets)
    body_len = sum(len(x["text"]) for x in ordered) / max(1, len(ordered))
    try:
        ph = float(page.mediabox.height)
        pw = float(page.mediabox.width)
    except Exception:
        ph, pw = 842.0, 595.0
    blocks = []
    for i, x in enumerate(ordered):
        role = classify(x["text"], body_len)
        y = round(ph - 12 * x["line"], 1)
        b = {"page": pno, "role": role, "text": x["text"],
             "line": x["line"], "col": x["col"],
             "bbox": [round(pw * x["col"] / 100, 1), y,
                      round(pw * (x["col"] + len(x["text"])) / 100, 1), round(y + 10, 1)]}
        if role == "heading":
            b["level"] = heading_level(x["text"])
        m = FORMULA_NO.search(x["text"])
        if m:
            b["formula_no"] = m.group(1)
        blocks.append(b)
    return {"page": pno, "n_lines": len(ordered), "columns": max(1, len(buckets)),
            "width": pw, "height": ph,
            "body_size": round(body_len, 1), "blocks": blocks, "n_tables": 0,
            "empty": not text.strip()}


def split_columns(rows: list[list[dict]]) -> tuple[list[int], int]:
    buckets = cluster_cols(rows)
    return buckets, max(1, len(buckets))
