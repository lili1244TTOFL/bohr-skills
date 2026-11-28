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

import importlib.util
import re
from pathlib import Path

SPLIT = re.compile(r"[ \t]{5,}")
CAPTION = re.compile(r"^\s*(图|表|Figure|Fig\.?|Table)\s*[0-9A-Z]")
FORMULA_NO = re.compile(r"[（(]\s*(\d+[a-z]?)\s*[）)]\s*$")
PAGE_NO = re.compile(r"^\s*[-–—]\s*\d{1,4}\s*[-–—]\s*$")
HEAD_NUM = re.compile(r"^\s*\d+(\.\d+)*[\s.、]\s*\S")
ALL_CAPS = re.compile(r"^[A-Z][A-Z0-9 \-:,]{4,}$")


def _textclass():
    """行级角色判定与 pdfparse 共用一份口径（scripts/textclass.py）。"""
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("textclass", here / "textclass.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def page_text(page) -> str:
    """取版面文本；NBSP（中文排版常用）先折成空格，否则中文行会被切成碎段。"""
    try:
        raw = page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False) or ""
    except Exception:
        try:
            raw = page.extract_text() or ""
        except Exception:
            raw = ""
    return raw.replace("\u00a0", " ").replace("\u3000", "  ")


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


def classify(text: str, body_len: float = 0.0) -> str:
    """行角色统一走 textclass：编号章节不再被当成参考文献条目。"""
    return _textclass().classify_role(text)


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
