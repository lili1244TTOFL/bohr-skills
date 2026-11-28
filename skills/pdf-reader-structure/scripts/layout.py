#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""layout.py — 用 layout 模式文本判栏与排序，并回填 visitor 测量框。

依据 pypdf `extract_text(extraction_mode="layout")` 的输出：同一视觉行内，
不同栏的文字被大段空格隔开。按"连续 ≥5 个空格"切段，段落的起始列位置
聚成栏，再按栏左→右、栏内上→下输出。

排序不依赖字符坐标，因此不受 TJ 数组字距偏移影响；但 bbox 需要坐标，
所以另从 `page_chunks`（visitor_text 的 tm[4]/tm[5]）按段文本做最长匹配，
匹配成功的块给测量框，匹配不上的块保留推算框并置 `bbox_est=True`。

公式在字符块层面重建：公式体与右对齐编号之间有大间距，行内又带小字号上下标，
按空格切段会把它拆成 "E = m c" / "2" / "+ k T" / "(2)" 四块，编号还会被并到
错误的前一块上。这里先找"公式体 + 右对齐编号"的视觉行，再用字符块的
字号与基线还原上下标，输出 `original`（逐字原样）与 `inline`（带 ^{} _{} 标记）。

表格单元格的段先按 cell_texts 剔除，再聚栏 —— 否则单元格起始列会被当成
第三、第四栏，把正文顺序拆散。

用法（库）：
    from layout import parse_page_layout
    page_blocks = parse_page_layout(page, pno, chunks, cell_texts)
"""

from __future__ import annotations

import re
import unicodedata

SPLIT = re.compile(r"[ \t]{5,}")
CAPTION = re.compile(r"^\s*(图|表|Figure|Fig\.?|Table)\s*[0-9A-Z]")
# 题注后面若紧跟小写动词（"Table 1 reports ..."），那是正文句，不是题注
CAPTION_SENT = re.compile(r"^\s*(图|表|Figure|Fig\.?|Table)\s+[0-9IVXivx]+[.:]?\s+[a-z]")
REF_ITEM = re.compile(r"^\s*\[\d+\]\s+\S")
NUM_HEAD = re.compile(r"^\s*(\d+(?:\.\d+)*)[\s.、]\s*(\S.*)$")
FORMULA_NO = re.compile(r"[（(]\s*(\d+[a-z]?)\s*[）)]\s*$")
FORMULA_NO_CHUNK = re.compile(r"^[（(]\s*(\d{1,2}[a-z]?)\s*[）)]$")
PAGE_NO = re.compile(r"^\s*[-–—]\s*\d{1,4}\s*[-–—]\s*$")
ALL_CAPS = re.compile(r"^[A-Z][A-Z0-9 \-:,]{4,}$")
HEAD_WORDS = re.compile(r"(?i)^(abstract|introduction|background|related work|methods?|"
                        r"materials and methods|experiments?|results?|discussion|conclusions?|"
                        r"references|acknowledge?ments?|appendix|摘要|引言|前言|方法|结果|"
                        r"讨论|结论|参考文献|致谢|附录)\b")
CJK = re.compile(r"[\u4e00-\u9fff]")
TERMINAL = (".", "。", "；", ";", ",", "，", "、")
SYM_SPACE = re.compile(r"[^\x00-\x7f\s]")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _sq(s: str) -> str:
    """去空白后的形式：段被空格切碎后，仍能和公式原文对上。"""
    return re.sub(r"\s+", "", s or "")


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


def classify2(text: str, body_len: float, size: float | None = None,
              body_size: float | None = None) -> tuple[str, str]:
    """返回 (角色, 依据)。依据是稳定的判据名，写进每个块的 why 字段，供复核。"""
    t = text.strip()
    if FORMULA_NO.search(t):
        return "formula", "formula:tail_no"
    if PAGE_NO.match(t):
        return "page_number", "page_number:dash_no"
    if CAPTION.match(t) and not CAPTION_SENT.match(t) and len(t) <= 160:
        if not t.endswith(("。",)) and (not t.endswith(".") or ":" in t):
            return "caption", "caption:fig_table_head"
    if REF_ITEM.match(t):
        return "reference", "reference:bracket_no"
    # 编号标题先于参考文献：真实期刊写 "1. Introduction"，不能被 "\d+\." 抢走
    if len(t) <= 90:
        m = NUM_HEAD.match(t)
        if m:
            rest = m.group(2)
            if (HEAD_WORDS.match(rest) or (len(t) <= 60 and not t.endswith(TERMINAL))):
                return "heading", "heading:num_head"
        if ALL_CAPS.match(t):
            return "heading", "heading:caps"
        if len(t) <= 60 and HEAD_WORDS.match(t) and not t.endswith(TERMINAL):
            return "heading", "heading:wordlist"
    # 字号门槛：标题比正文大 1.5 倍记为 title，大 1.15 倍记为 heading
    if size and body_size:
        if size >= body_size * 1.5 and len(t) <= 200 and not t.endswith(("。", ".")):
            return "title", "title:size_ge_1.5"
        if size >= body_size * 1.15 and len(t) <= 120 and not t.endswith(("。", ".")):
            return "heading", "heading:size_ge_1.15"
    return "body", "body:default"


def classify(text: str, body_len: float, size: float | None = None,
             body_size: float | None = None) -> str:
    return classify2(text, body_len, size, body_size)[0]


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


def sym_inventory(text: str) -> list[dict]:
    """非 ASCII 字符清单：字符、码位、Unicode 名、出现次数。用来证明符号没失真。"""
    cnt: dict[str, int] = {}
    for ch in text or "":
        if ord(ch) < 128 or ch.isspace():
            continue
        cnt[ch] = cnt.get(ch, 0) + 1
    out = []
    for ch, n in cnt.items():
        try:
            name = unicodedata.name(ch)
        except ValueError:
            name = ""
        out.append({"char": ch, "cp": "U+%04X" % ord(ch), "name": name, "count": n})
    out.sort(key=lambda d: d["cp"])
    return out


def _runs(chunks: list[dict], main: float, main_y: float) -> list[dict]:
    """按字号与基线把字符块分成 正文 / 上标 / 下标 三类连续段。"""
    kind = []
    for c in chunks:
        if not c["text"].strip():
            continue
        if c["size"] <= main * 0.88 and c["y"] - main_y >= 0.18 * main:
            k = "sup"
        elif c["size"] <= main * 0.88 and c["y"] - main_y <= -0.10 * main:
            k = "sub"
        else:
            k = "main"
        kind.append((k, c))
    runs: list[dict] = []
    for k, c in kind:
        if runs and runs[-1]["kind"] == k:
            prev = runs[-1]["chunks"][-1]
            gap = c["x"] - prev["x"] - _w(prev["text"], prev["size"])
            space = k == "main" and (c.get("space_before") or gap > 0.3 * c["size"])
            runs[-1]["text"] += (" " if space else "") + c["text"]
            runs[-1]["chunks"].append(c)
            runs[-1]["x1"] = c["x"] + _w(c["text"], c["size"])
        else:
            runs.append({"kind": k, "text": c["text"], "chunks": [c],
                         "x0": c["x"], "x1": c["x"] + _w(c["text"], c["size"])})
    return runs


def _absorb(g: dict, groups: list[dict], num: dict, main: float) -> list[dict]:
    """把公式行上下的上下标字符块收进来。

    上下标的基线比公式体高/低几个点，按基线聚行时会被分到别的行；这里以公式
    体基线为中心开窗口取字符块：字号小于公式体 90% 的（候选上下标）允许偏
    0.85×字号，正常字号的只允许偏 0.35×字号（那就是同一视觉行，不是邻行）。
    """
    body = [c for c in g["chunks"] if c is not num]
    if not body:
        return [num]
    lo = min(c["x"] for c in body) - 2
    hi = num["x"] - 1

    def in_window(c: dict) -> bool:
        dy = abs(c["y"] - g["y"])
        if c["size"] <= main * 0.9:
            return dy <= 0.85 * main
        return dy <= 0.35 * main

    pool = [c for gg in groups for c in gg["chunks"]
            if lo <= c["x"] <= hi and in_window(c)]
    pool.sort(key=lambda c: (c["x"], -c["y"]))
    seen: set[tuple] = set()
    out = []
    for c in pool:
        key = (round(c["x"], 1), round(c["y"], 1), c["text"])
        if key in seen:
            continue
        seen.add(key)
        out.append({**c, "text": c["text"].strip(),
                    "space_before": bool((c["text"] or "")[:1].isspace())})
    out.append({**num, "text": num["text"].strip(), "formula_no_chunk": True})
    return out


def formula_runs(chunks: list[dict], bsize: float) -> list[dict]:
    """在字符块层面找"公式体 + 右对齐编号"的视觉行，并重建上下标。

    判据（三条同时成立才算公式行，避免把正文里顺带出现的 "(2)" 当公式）：
      1. 该视觉行最后一个字符块形如 `(1)` / `（2a）`；
      2. 编号块与公式体末端的水平间距 ≥ 3×正文字号（公式编号右对齐）；
      3. 公式体长度 ≤ 140 字符，且不以句末标点结尾。
    """
    groups = _groups(chunks or [])
    out = []
    for g in groups:
        chs = [c for c in g["chunks"] if c["text"].strip()]
        if len(chs) < 2:
            continue
        num = chs[-1]
        m = FORMULA_NO_CHUNK.match(_norm(num["text"]))
        if not m:
            continue
        body = chs[:-1]
        gap = num["x"] - max(c["x"] + _w(c["text"], c["size"]) for c in body)
        if gap < 3 * max(bsize, 1.0):
            continue
        main = max(c["size"] for c in body)
        main_y = max((c["y"] for c in body if c["size"] >= main * 0.99), default=body[0]["y"])
        line = _absorb(g, groups, num, main)
        body = [c for c in line if not c.get("formula_no_chunk")]
        body_text = "".join(c["text"] for c in body).strip()
        if not body_text or len(body_text) > 140 or body_text.endswith(TERMINAL):
            continue
        runs = _runs(body, main, main_y)
        plain, inline = "", ""
        for r in runs:
            if r["kind"] == "main":
                plain += (" " if plain and not plain.endswith((" ", "-")) else "") + r["text"]
                inline += (" " if inline and not inline.endswith((" ", "^{", "_{")) else "") + r["text"]
            elif r["kind"] == "sup":
                plain += r["text"]
                inline += "^{" + r["text"] + "}"
            else:
                plain += r["text"]
                inline += "_{" + r["text"] + "}"
        plain = (plain + " " + _norm(num["text"])).strip()
        inline = (inline + " " + _norm(num["text"])).strip()
        x0 = min(c["x"] for c in line)
        x1 = max(c["x"] + _w(c["text"], c["size"]) for c in line)
        out.append({"y": g["y"], "x0": round(x0, 1), "x1": round(x1, 1),
                    "main_size": main, "no": m.group(1), "original": plain,
                    "inline": inline, "text": _norm(g["text"]),
                    "scripts": [{"text": r["text"], "kind": r["kind"],
                                 "bbox": [round(r["x0"], 1), round(r["chunks"][0]["y"], 1),
                                          round(r["x1"], 1),
                                          round(r["chunks"][0]["y"] + main, 1)],
                                 "size": round(r["chunks"][0]["size"], 1),
                                 "size_ratio": round(r["chunks"][0]["size"] / main, 2)}
                                for r in runs if r["kind"] != "main"],
                    "symbols": sym_inventory(plain),
                    "chunks": [{"text": c["text"].strip(), "size": round(c["size"], 1),
                                "x": round(c["x"], 1), "y": round(c["y"], 1)}
                               for c in line]})
    return out


def _body_size(chunks: list[dict]) -> float:
    from collections import Counter
    cnt = Counter(round(c["size"], 1) for c in (chunks or []) if c.get("size"))
    return cnt.most_common(1)[0][0] if cnt else 10.0


def _in_cells(t: str, cells: list[str]) -> bool:
    nt = _norm(t)
    return bool(nt) and any(nt == c or nt in c for c in cells)


def _formula_block(fr: dict, pno: int) -> dict:
    return {"page": pno, "role": "formula", "text": fr["original"],
            "line": -1, "col": 0,
            "bbox": [fr["x0"], round(fr["y"], 1), fr["x1"], round(fr["y"] + fr["main_size"], 1)],
            "size": round(fr["main_size"], 1), "formula_no": fr["no"],
            "original": fr["original"], "inline": fr["inline"],
            "scripts": fr["scripts"], "symbols": fr["symbols"],
            "chunks": fr["chunks"], "why": "formula:no_and_gap"}


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
    fruns = formula_runs(chunks or [], bsize)
    # 段的实测框只算一次；同一段在"公式行定位"和"常规出块"两处都要用
    loc = [_locate(x["text"], groups) for x in ordered]
    # 先把每个公式行落到它出现在阅读顺序里的那个行号：上下标可能被 layout 分成
    # 上一行（抬高的上标）或下一行（压低的下载），要靠这个行号窗口把它们吃掉。
    for fr in fruns:
        for i, x in enumerate(ordered):
            bb = loc[i][0]
            near = (bb is not None
                    and abs(bb[1] - fr["y"]) <= max(2.0, 0.45 * fr["main_size"])
                    and bb[0] >= fr["x0"] - 3 and bb[2] <= fr["x1"] + 3)
            if near or (bb is None and _sq(_norm(x["text"])) in _sq(fr["original"])):
                fr["line"] = x["line"]
                break
    taken_runs: set[int] = set()
    blocks = []
    for i, x in enumerate(ordered):
        bbox, size = loc[i]
        nx = _norm(x["text"])
        consumed = False
        for k, fr in enumerate(fruns):
            if k not in taken_runs:
                continue
            # 公式行重建时已吃掉这一段：编号段 / 公式体段都不能再当正文或再并一次
            if bbox is not None and abs(bbox[1] - fr["y"]) <= max(2.0, 0.45 * fr["main_size"]) \
                    and bbox[0] >= fr["x0"] - 3 and bbox[2] <= fr["x1"] + 3:
                consumed = True
                break
            if (nx and fr.get("line") is not None and abs(x["line"] - fr["line"]) <= 1
                    and _sq(nx) in _sq(fr["original"])):
                consumed = True
                break
        if consumed:
            continue
        hit = None
        for k, fr in enumerate(fruns):
            if k in taken_runs:
                continue
            near = (bbox is not None
                    and abs(bbox[1] - fr["y"]) <= max(2.0, 0.45 * fr["main_size"])
                    and bbox[0] >= fr["x0"] - 3 and bbox[2] <= fr["x1"] + 3)
            sub = (bbox is None and nx and nx in fr["text"])
            if near or sub:
                hit = k
                break
        if hit is not None:
            taken_runs.add(hit)
            fruns[hit]["line"] = x["line"]
            fb = _formula_block(fruns[hit], pno)
            fb["col"] = x["col"]
            fb["line"] = x["line"]
            blocks.append(fb)
            continue
        est = bbox is None
        if est:
            y = round(ph - 12 * x["line"], 1)
            bbox = [round(pw * x["col"] / 100, 1), y,
                    round(pw * (x["col"] + len(x["text"])) / 100, 1), round(y + 10, 1)]
        role, why = classify2(x["text"], body_len, size, bsize)
        b = {"page": pno, "role": role, "text": x["text"],
             "line": x["line"], "col": x["col"], "bbox": bbox,
             "size": round(size, 1) if size else None, "why": why}
        if est:
            b["bbox_est"] = True
        if role == "heading":
            b["level"] = heading_level(x["text"])
        m = FORMULA_NO.search(x["text"])
        if m:
            b["formula_no"] = m.group(1)
        blocks.append(b)
    # 兜底：未被公式行重建吃掉的公式块（例如编号与公式体之间间距太小）
    for b in blocks:
        if b["role"] != "formula":
            continue
        b.setdefault("original", b["text"])
        b.setdefault("inline", b["text"])
        b.setdefault("scripts", [])
        b.setdefault("symbols", sym_inventory(b["text"]))
        b.setdefault("why", "formula:tail_no")
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
            prev["original"] = prev["text"]
            prev["inline"] = prev["text"]
            prev["why"] = "formula:merged_no"
            prev["symbols"] = sym_inventory(prev["text"])
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
