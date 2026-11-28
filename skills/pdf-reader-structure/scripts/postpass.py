#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""postpass.py — 解析完之后的版面后处理，三条判据各自独立、都可回指坐标。

1. 脚注（footnote）：页面下部 20% 以内、字号 ≤ 正文 92%、行首带符号标记
   （`*` `†` `‡` `§` `¶` 或 1–2 位数字）的正文块。符号标记还要回正文里
   找一次引用位置：找得到记 `referenced_in_body=True`，找不到如实记 False；
   数字标记与正文上标无法区分，记 `None` 并写明原因，不猜。
2. 段落（paragraph）：同栏内相邻行、上一行不以句末/分号标点收尾的正文块归一段；
   跨页时若上页末行未收尾、本页首行以小写或 CJK 起头，则记 `continued_from`，
   段号继续——这就是"跨页续接"，只标关系、不拼接文本。
3. 图表链接（link_figures）：图片对象框与图类题注按垂直距离配对，
   60 pt 以内为同一条；只有题注没有图片对象的，标 `caption-only` 并写原因。

只做标记，不改原文、不补字。
"""

from __future__ import annotations

import re
from collections import Counter

MARK = re.compile(r"^\s*([\*\u2020\u2021\u00a7\u00b6]|\d{1,2})\s*[\.\)]?\s+\S")
SYMBOL_MARK = set("*\u2020\u2021\u00a7\u00b6")
TERMINAL = re.compile(r"[.。？?！!；;:：]$")
CAP_FIG = re.compile(r"^\s*(图|Figure|Fig\.?)\s*([0-9]+[A-Za-z]?|[IVXivx]+)")
CAP_TAB = re.compile(r"^\s*(表|Table)\s*([0-9]+[A-Za-z]?|[IVXivx]+)")
LOWER_START = re.compile(r"^[a-z\u4e00-\u9fff（(]")
PAGE_LIKE = re.compile(r"^[\s\-–—]*\d{1,4}[\s\-–—]*$")
KEEP = ("body", "heading", "title", "caption", "reference", "footnote")


def body_size(page: dict) -> float | None:
    """页面正文字号：区块实测字号的众数（layout 路径 page['body_size'] 是段长，不可用）。"""
    sizes = [round(b["size"], 1) for b in page["blocks"]
             if b.get("size") and b.get("role") in KEEP]
    if not sizes:
        return None
    return Counter(sizes).most_common(1)[0][0]


def _x0(b: dict) -> float:
    return float(b["bbox"][0])


def single_page_heads(pages: list[dict]) -> list[str]:
    """单页 PDF 的页眉页脚：重复判据需要 ≥2 页，单页只能靠位置 + 小字号 + 短文本。

    只在页顶 8% / 页底 6% 的窄带里生效，且要求字号 ≤ 正文 90% 或行尾是页码样式，
    行首带脚注标记的不动（那是脚注，交给 footnotes 判）。
    """
    out: list[str] = []
    if len(pages) != 1:
        return out
    p = pages[0]
    H = float(p.get("height") or 842.0)
    bs = body_size(p)
    for b in p["blocks"]:
        if b.get("role") != "body" or b.get("bbox_est"):
            continue
        if MARK.match(b.get("text") or ""):
            continue
        y0, y1 = float(b["bbox"][1]), float(b["bbox"][3])
        small = bool(bs and b.get("size") and b["size"] <= bs * 0.9)
        pageno = bool(PAGE_LIKE.match(b.get("text") or ""))
        if not (small or pageno) or len(b.get("text") or "") > 120:
            continue
        if y0 >= H * 0.92:
            b["role"] = "header"
            b["why"] = "header:single_page_band"
            out.append(b["text"])
        elif y1 <= H * 0.06:
            b["role"] = "footer"
            b["why"] = "footer:single_page_band"
            out.append(b["text"])
    return out


def footnotes(pages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for p in pages:
        H = float(p.get("height") or 842.0)
        bs = body_size(p)
        if not bs:
            continue
        for b in p["blocks"]:
            if b.get("role") != "body" or b.get("bbox_est"):
                continue
            if not b.get("size") or b["size"] > bs * 0.92:
                continue
            if b["bbox"][1] > H * 0.2:
                continue
            m = MARK.match(b.get("text") or "")
            if not m or len(b.get("text") or "") > 400:
                continue
            marker = m.group(1)
            ref = None
            if marker in SYMBOL_MARK:
                pat = re.compile(r"\S" + re.escape(marker))
                ref = any(pat.search(o.get("text") or "") for o in p["blocks"]
                          if o is not b and o.get("role") in ("body", "heading", "title"))
            b["role"] = "footnote"
            b["marker"] = marker
            b["why"] = "footnote:marker+low+small"
            out.append({"page": b["page"], "marker": marker, "text": b["text"],
                        "bbox": b["bbox"], "size": b.get("size"),
                        "size_ratio": round(float(b["size"]) / bs, 2),
                        "referenced_in_body": ref,
                        "note": "" if ref is not None
                                else "数字标记与正文上标无法区分，未判正文引用位置"})
    return out


def paragraphs(pages: list[dict]) -> list[dict]:
    """按栏内相邻行切段；跨页未收尾的段标 continued_from。返回段落清单并给块写 para。"""
    recs: list[dict] = []
    cur: dict | None = None
    last_by_col: dict[object, dict] = {}
    for p in pages:
        bs = body_size(p) or 10.0
        for b in p["blocks"]:
            if b.get("role") != "body":
                continue
            col = b.get("col", 0)
            prev = last_by_col.get(col)
            new_para = True
            if prev is not None and prev.get("role") == "body" and cur is not None:
                same_page = prev.get("page") == b.get("page")
                if same_page:
                    tail_ok = not TERMINAL.search((prev.get("text") or "").strip())
                    if "line" in b and "line" in prev:
                        adjacent = b["line"] == prev["line"] + 1
                    else:
                        gap = prev["bbox"][1] - b["bbox"][3]
                        adjacent = -0.2 * bs <= gap <= 0.9 * bs
                    near = abs(_x0(b) - _x0(prev)) <= 6
                    new_para = not (tail_ok and adjacent and near)
                else:
                    tail_ok = not TERMINAL.search((prev.get("text") or "").strip())
                    starts_low = bool(LOWER_START.match((b.get("text") or "").strip()))
                    if tail_ok and starts_low and cur is not None:
                        b["continued_from"] = prev.get("page")
                        new_para = False
            if new_para:
                cur = {"no": len(recs) + 1, "page_from": b["page"], "page_to": b["page"],
                       "col": col, "n_blocks": 0, "chars": 0, "prefix": "",
                       "cross_page": False, "para_bbox": list(b["bbox"])}
                recs.append(cur)
            b["para"] = cur["no"]
            cur["n_blocks"] += 1
            cur["chars"] += len(b.get("text") or "")
            if not cur["prefix"]:
                cur["prefix"] = (b.get("text") or "")[:60]
            cur["page_to"] = max(cur["page_to"], b["page"])
            cur["para_bbox"][2] = max(cur["para_bbox"][2], b["bbox"][2])
            cur["cross_page"] = cur["page_from"] != cur["page_to"]
            last_by_col[col] = b
    return recs


def link_figures(figures: list[dict], pages: list[dict]) -> list[dict]:
    """图片对象框 ↔ 图类题注配对；只有题注没有图对象的如实标 caption-only。"""
    caps = [b for p in pages for b in p["blocks"] if b.get("role") == "caption"]
    out: list[dict] = []
    used: set[str] = set()
    for f in figures:
        best = None
        for c in caps:
            if c.get("page") != f.get("page") or not CAP_FIG.match(c.get("text") or ""):
                continue
            if CAP_TAB.match(c.get("text") or ""):
                continue
            cy = float(c["bbox"][1])
            if cy >= f["bbox"][3] - 1:
                d, rel = cy - f["bbox"][3], "caption_above"
            elif float(c["bbox"][3]) <= f["bbox"][1] + 1:
                d, rel = f["bbox"][1] - float(c["bbox"][3]), "caption_below"
            else:
                continue
            if d > 60:
                continue
            if best is None or d < best[0]:
                best = (d, rel, c)
        item = {k: f[k] for k in ("page", "kind", "bbox", "width", "height", "why")}
        if best:
            used.add(best[2]["text"])
            item.update({"caption": best[2]["text"], "caption_relation": best[1],
                         "caption_gap": round(best[0], 1),
                         "caption_bbox": best[2]["bbox"]})
        else:
            item.update({"caption": "", "caption_relation": "", "caption_gap": None,
                         "note": "该图片对象 60 pt 内没有图类题注"})
        out.append(item)
    for c in caps:
        t = c.get("text") or ""
        if not CAP_FIG.match(t) or CAP_TAB.match(t) or t in used:
            continue
        out.append({"page": c["page"], "kind": "caption-only", "bbox": c["bbox"],
                    "width": 0, "height": 0, "caption": t, "caption_relation": "self",
                    "caption_gap": 0, "why": "figure:caption_only",
                    "note": "本页未检出图片对象（矢量图形或图片被拆成小块），只有题注可定位"})
    out.sort(key=lambda d: (d["page"], -float(d["bbox"][3])))
    return out


def table_orphans(pages: list[dict]) -> list[dict]:
    """落不进任何单元格的表格文字：逐条列出，供 gaps 与 result.json 回指。"""
    out = []
    for p in pages:
        for b in p["blocks"]:
            if b.get("role") != "table":
                continue
            for u in b.get("unassigned") or []:
                out.append({"page": b["page"], "text": u["text"],
                            "x": u.get("x"), "y": u.get("y"),
                            "table_bbox": b.get("bbox")})
    return out
