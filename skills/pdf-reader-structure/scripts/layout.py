#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""layout.py — 用 layout 模式文本判栏与排序，并回填 visitor 测量框。

依据 pypdf `extract_text(extraction_mode="layout")` 的输出：同一视觉行内，
不同栏的文字被大段空格隔开。按"连续 ≥5 个空格"切段，段落的起始列位置
聚成栏，再按栏左→右、栏内上→下输出。

排序不依赖字符坐标，因此不受 TJ 数组字距偏移影响；但 bbox 需要坐标，
所以另从 `page_chunks`（visitor_text 的 tm[4]/tm[5]）按段文本做最长匹配，
匹配成功的块给测量框，匹配不上的块保留推算框并置 `bbox_est=True`。

表格单元格的段先按 cell_texts 剔除，再聚栏 —— 否则单元格起始列会被当成
第三、第四栏，把正文顺序拆散。

用法（库）：
    from layout import parse_page_layout
    page_blocks = parse_page_layout(page, pno, chunks, cell_texts)
"""

from __future__ import annotations

import re

SPLIT = re.compile(r"[ \t]{5,}")
CAPTION = re.compile(r"^\s*(图|表|Figure|Fig\.?|Table)\s*[0-9A-Z]")
# 题注后面若紧跟小写动词（"Table 1 reports ..."），那是正文句，不是题注
CAPTION_SENT = re.compile(r"^\s*(图|表|Figure|Fig\.?|Table)\s+[0-9IVXivx]+[.:]?\s+[a-z]")
REF_ITEM = re.compile(r"^\s*\[\d+\]\s+\S")
NUM_HEAD = re.compile(r"^\s*(\d+(?:\.\d+)*)[\s.、]\s*(\S.*)$")
FORMULA_NO = re.compile(r"[（(]\s*(\d+[a-z]?)\s*[）)]\s*$")
PAGE_NO = re.compile(r"^\s*[-–—]\s*\d{1,4}\s*[-–—]\s*$")
ALL_CAPS = re.compile(r"^[A-Z][A-Z0-9 \-:,]{4,}$")
HEAD_WORDS = re.compile(r"(?i)^(abstract|introduction|background|related work|methods?|"
                        r"materials and methods|experiments?|results?|discussion|conclusions?|"
                        r"references|acknowledge?ments?|appendix|摘要|引言|前言|方法|结果|"
                        r"讨论|结论|参考文献|致谢|附录)\b")
CJK = re.compile(r"[\u4e00-\u9fff]")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _w(s: str, size: float) -> float:
    cjk = len(CJK.findall(s))
    return cjk * size + (len(s) - cjk) * size * 0.52


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
    """找栏：主栏取出现最多的起始列；副栏需与主栏相距 ≥20 列、占比 ≥15%，
    且与主栏在同一视觉行共现 ≥2 次（否则居中标题的缩进会被误当成一栏）。"""
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
        if col == main or abs(col - main) < 20 or c < max(3, total * 0.15):
            continue
        co = sum(1 for r in rows
                 if any(abs(p["col"] - col) <= tol for p in r)
                 and any(abs(p["col"] - main) <= tol for p in r))
        if co >= 2:
            buckets.append(col)
    return sorted(buckets)


def order_rows(rows: list[list[dict]], buckets: list[int], tol: int = 8) -> list[dict]:
    """按栏左→右、栏内上→下输出；同一行内的多个段按列从左到右。"""
    if not buckets:
        return []
    if len(buckets) == 1:
        return [{"col": 0, "text": p["text"], "line": i, "x": p["col"]}
                for i, r in enumerate(rows) for p in r]
    out: list[dict] = []
    for bi, left in enumerate(buckets):
        right = buckets[bi + 1] if bi + 1 < len(buckets) else 10 ** 6
        for i, r in enumerate(rows):
            for p in r:
                if left - 1 <= p["col"] < right - tol // 2 or (bi == len(buckets) - 1 and p["col"] >= left - 1):
                    out.append({"col": bi, "text": p["text"], "line": i, "x": p["col"]})
    return out


def classify(text: str, body_len: float, size: float | None = None,
             body_size: float | None = None) -> str:
    t = text.strip()
    if FORMULA_NO.search(t):
        return "formula"
    if PAGE_NO.match(t):
        return "page_number"
    if CAPTION.match(t) and not CAPTION_SENT.match(t) and len(t) <= 160:
        if not t.endswith(("。",)) and (not t.endswith(".") or ":" in t):
            return "caption"
    if REF_ITEM.match(t):
        return "reference"
    # 编号标题先于参考文献：真实期刊写 "1. Introduction"，不能被 "\d+\." 抢走
    if len(t) <= 90:
        m = NUM_HEAD.match(t)
        if m:
            rest = m.group(2)
            if (HEAD_WORDS.match(rest) or (len(t) <= 60 and not t.endswith((".", "。", "；", ";")))):
                return "heading"
        if ALL_CAPS.match(t):
            return "heading"
        if len(t) <= 60 and HEAD_WORDS.match(t) and not t.endswith(("。", ".", ";", "；")):
            return "heading"
    # 字号门槛：标题比正文大 1.5 倍记为 title，大 1.15 倍记为 heading
    if size and body_size:
        if size >= body_size * 1.5 and len(t) <= 200 and not t.endswith(("。", ".")):
            return "title"
        if size >= body_size * 1.15 and len(t) <= 120 and not t.endswith(("。", ".")):
            return "heading"
    return "body"


def heading_level(text: str) -> int:
    t = text.strip()
    m = re.match(r"^(\d+(?:\.\d+)*)", t)
    if m:
        return min(m.group(1).count(".") + 1, 3)
    if HEAD_WORDS.match(t) or ALL_CAPS.match(t):
        return 1
    return 2


def _groups(chunks: list[dict]) -> list[dict]:
    """把 visitor 字符块按基线聚成视觉行，供段文本回填测量框。"""
    out: list[dict] = []
    for c in sorted(chunks or [], key=lambda c: (-c["y"], c["x"])):
        for g in out:
            if abs(g["y"] - c["y"]) <= max(1.5, c["size"] * 0.35):
                g["chunks"].append(c)
                g["size"] = max(g["size"], c["size"])
                break
        else:
            out.append({"y": c["y"], "size": c["size"], "chunks": [c]})
    for g in out:
        g["chunks"].sort(key=lambda c: c["x"])
        g["text"] = _norm(" ".join(c["text"] for c in g["chunks"]))
    return out


def _locate(t: str, groups: list[dict]) -> tuple[list[float] | None, float | None]:
    nt = _norm(t)
    if not nt or not groups:
        return None, None
    for g in groups:                                  # 整行匹配
        if nt in g["text"]:
            cs = [c for c in g["chunks"] if _norm(c["text"]) and _norm(c["text"]) in nt]
            cs = cs or g["chunks"]
            x0 = min(c["x"] for c in cs)
            x1 = max(c["x"] + _w(c["text"], c["size"]) for c in cs)
            return ([round(x0, 1), round(g["y"], 1), round(x1, 1),
                     round(g["y"] + g["size"], 1)], g["size"])
    for g in groups:                                  # 段被拆开时的逐块匹配
        cs = [c for c in g["chunks"] if _norm(c["text"]) and _norm(c["text"]) in nt]
        if cs:
            x0 = min(c["x"] for c in cs)
            x1 = max(c["x"] + _w(c["text"], c["size"]) for c in cs)
            return ([round(x0, 1), round(g["y"], 1), round(x1, 1),
                     round(g["y"] + g["size"], 1)], g["size"])
    return None, None


def _body_size(chunks: list[dict]) -> float:
    from collections import Counter
    cnt = Counter(round(c["size"], 1) for c in (chunks or []) if c.get("size"))
    return cnt.most_common(1)[0][0] if cnt else 10.0


def _in_cells(t: str, cells: list[str]) -> bool:
    nt = _norm(t)
    return bool(nt) and any(nt == c or nt in c for c in cells)


def parse_page_layout(page, pno: int, chunks: list[dict] | None = None,
                      cell_texts: list[str] | None = None) -> dict:
    text = page_text(page)
    rows = segments(text)
    cells = [_norm(c) for c in (cell_texts or []) if _norm(c)]
    if cells:
        rows = [[p for p in r if not _in_cells(p["text"], cells)] for r in rows]
    buckets = cluster_cols(rows)
    ordered = order_rows(rows, buckets)
    n_rows = sum(1 for r in rows if r)
    body_len = sum(len(x["text"]) for x in ordered) / max(1, len(ordered))
    groups = _groups(chunks or [])
    bsize = _body_size(chunks or [])
    try:
        ph = float(page.mediabox.height)
        pw = float(page.mediabox.width)
    except Exception:
        ph, pw = 842.0, 595.0
    blocks = []
    for x in ordered:
        bbox, size = _locate(x["text"], groups)
        est = bbox is None
        if est:
            y = round(ph - 12 * x["line"], 1)
            bbox = [round(pw * x["col"] / 100, 1), y,
                    round(pw * (x["col"] + len(x["text"])) / 100, 1), round(y + 10, 1)]
        role = classify(x["text"], body_len, size, bsize)
        b = {"page": pno, "role": role, "text": x["text"],
             "line": x["line"], "col": x["col"], "bbox": bbox,
             "size": round(size, 1) if size else None}
        if est:
            b["bbox_est"] = True
        if role == "heading":
            b["level"] = heading_level(x["text"])
        m = FORMULA_NO.search(x["text"])
        if m:
            b["formula_no"] = m.group(1)
        blocks.append(b)
    # 合并：独立成段的公式编号并入同行的公式；跨行的大标题并成一块
    merged: list[dict] = []
    for b in blocks:
        prev = merged[-1] if merged else None
        if (b["role"] == "formula"
                and re.fullmatch(r"[（(]\s*\d+[a-z]?\s*[）)]", b["text"].strip())
                and prev is not None and prev.get("line") == b.get("line")
                and prev["role"] in ("heading", "body", "formula")):
            prev["formula_no"] = b["text"].strip("（）() ")
            prev["role"] = "formula"
            prev["text"] = (prev["text"] + " " + b["text"]).strip()
            prev["bbox"][2] = max(prev["bbox"][2], b["bbox"][2])
            continue
        if (b["role"] == "title" and prev is not None and prev["role"] == "title"
                and b.get("line", 0) - prev.get("line", 0) <= 1):
            prev["text"] = (prev["text"] + " " + b["text"]).strip()
            prev["bbox"][2] = max(prev["bbox"][2], b["bbox"][2])
            prev["bbox"][3] = max(prev["bbox"][3], b["bbox"][3])
            continue
        merged.append(b)
    return {"page": pno, "n_lines": n_rows, "columns": max(1, len(buckets)),
            "width": pw, "height": ph,
            "body_size": round(body_len, 1), "blocks": merged, "n_tables": 0,
            "empty": not text.strip()}


def split_columns(rows: list[list[dict]]) -> tuple[list[int], int]:
    buckets = cluster_cols(rows)
    return buckets, max(1, len(buckets))
