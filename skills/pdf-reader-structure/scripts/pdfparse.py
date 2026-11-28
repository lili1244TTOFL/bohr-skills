#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdfparse.py — PDF 版面解析：字符块 → 行 → 栏 → 阅读顺序 → 区块（含表格）。

只用 pypdf（纯 Python，无外部二进制）：visitor_text 拿每段文字的 (x, y, 字号, 字体)，
自己聚行、判栏、排序、去页眉页脚、识别标题层级，并把表格网格内的文字收进单元格。

每个 block 带页码与 bbox，供下游回溯原文。

用法：
  python pdfparse.py paper.pdf --out work/parse.json
  python pdfparse.py paper.pdf --page 1
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
import sys
from pathlib import Path

HEAD_WORDS = re.compile(r"(?i)^(abstract|introduction|background|related work|methods?|"
                        r"materials and methods|results?|discussion|conclusions?|"
                        r"references|acknowledge?ments?|appendix|摘要|引言|前言|方法|"
                        r"结果|讨论|结论|参考文献|致谢|附录)\b")
CAPTION = re.compile(r"^\s*(图|表|Figure|Fig\.?|Table)\s*[0-9A-Z]")
REF_ITEM = re.compile(r"^\s*(\[\d+\]|\d+\.)\s+\S")
FORMULA_NO = re.compile(r"[（(]\s*(\d+[a-z]?)\s*[）)]\s*$")
PAGE_NO = re.compile(r"^\s*[-–—]\s*\d{1,4}\s*[-–—]\s*$")
HEAD_NUM = re.compile(r"^\s*\d+(\.\d+)*[\s.、]\s*\S")
CJK = re.compile(r"[\u4e00-\u9fff]")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _tables_mod():
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("tables", here / "tables.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _figures_mod():
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("figures", here / "figures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def table_insert_index(blocks: list[dict], tb: dict) -> int:
    """表格插进阅读顺序的位置：优先紧跟其上方最近的那条表题，否则按 y 落位。"""
    best = None
    for i, b in enumerate(blocks):
        if b.get("role") != "caption":
            continue
        if not re.match(r"^\s*(表|Table)\s*[0-9]", b.get("text") or ""):
            continue
        if b["bbox"][1] >= tb["bbox"][3] - 1:
            d = b["bbox"][1] - tb["bbox"][3]
            if best is None or d < best[0]:
                best = (d, i + 1)
    if best is not None:
        return best[1]
    for i, b in enumerate(blocks):
        if b["bbox"][1] < tb["bbox"][3] - 1:
            return i
    return len(blocks)


def table_block(g: dict, pno: int, unassigned: list[dict]) -> dict:
    """表格块：网格 + 未归属文字（不硬塞，留给 gaps 与 result.json 如实列出）。"""
    return {"page": pno,
            "bbox": [round(g["x0"], 1), round(g["y0"], 1),
                     round(g["x1"], 1), round(g["y1"], 1)],
            "role": "table",
            "text": "[table %dx%d]" % (g["n_rows"], g["n_cols"]),
            "n_rows": g["n_rows"], "n_cols": g["n_cols"],
            "rows": [[c["text"] for c in row] for row in g["rows"]],
            "markdown": _tables_mod().to_markdown(g),
            "cells_total": g.get("cells_total", g["n_rows"] * g["n_cols"]),
            "cells_filled": g.get("cells_filled", 0),
            "n_assigned": g.get("n_assigned", 0),
            "lines": {"h": g.get("h", 0), "v": g.get("v", 0)},
            "unassigned": [{"text": c["text"].strip(), "x": round(c["x"], 1),
                            "y": round(c["y"], 1)} for c in unassigned],
            "why": "table:line_grid"}


def page_chunks(page) -> tuple[list[dict], float, float]:
    w = float(page.mediabox.width)
    h = float(page.mediabox.height)
    chunks: list[dict] = []

    def visitor(text, cm, tm, font, size):
        if not text or not text.strip():
            return
        x = float(tm[4])
        y = float(tm[5])
        s = float(size or 0) or abs(float(tm[3])) or 10.0
        base = str(font.get("/BaseFont") or "") if isinstance(font, dict) else ""
        chunks.append({"text": text.replace("\u00a0", " "), "x": round(x, 2),
                       "y": round(y, 2), "size": round(s, 2), "font": base})

    try:
        page.extract_text(visitor_text=visitor)
    except Exception:
        pass
    return chunks, w, h


def text_w(s: str, size: float) -> float:
    """估算文字宽度：中文 1.0×字号，其余按 0.52×字号。"""
    if not s:
        return 0.0
    cjk = len(CJK.findall(s))
    return cjk * size + (len(s) - cjk) * size * 0.52


def to_lines(chunks: list[dict]) -> list[dict]:
    chunks = sorted(chunks, key=lambda c: (-c["y"], c["x"]))
    lines: list[dict] = []
    for c in chunks:
        tol = max(1.5, c["size"] * 0.35)
        c_end = c["x"] + text_w(c["text"], c["size"])
        hit = None
        for ln in lines[-8:]:
            if abs(ln["y"] - c["y"]) <= tol and c["x"] - ln["x1"] <= max(4.0, c["size"] * 0.6):
                hit = ln
                break
        if hit is None:
            lines.append({"y": c["y"], "x0": c["x"], "x1": c_end, "size": c["size"],
                          "font": c["font"], "parts": [c]})
        else:
            hit["parts"].append(c)
            hit["x0"] = min(hit["x0"], c["x"])
            hit["x1"] = max(hit["x1"], c_end)
            hit["size"] = max(hit["size"], c["size"])
    out = []
    for ln in lines:
        text = ""
        prev = None
        for p in sorted(ln["parts"], key=lambda c: c["x"]):
            t = p["text"]
            if prev is not None:
                gap = p["x"] - prev["x"] - len(prev["text"]) * prev["size"] * 0.5
                cjk_adj = bool(CJK.search(prev["text"][-1:]) or CJK.search(t[:1]))
                if (gap > 0.3 * p["size"] and not cjk_adj
                        and not text.endswith(" ") and not t.startswith(" ")):
                    text += " "
            text += t
            prev = p
        clean = re.sub(r"\s{2,}", " ", text).strip()
        cjk = len(re.findall(r"[\u4e00-\u9fff]", clean))
        latin = len(clean) - cjk
        width = ln["x0"] + cjk * ln["size"] * 1.0 + latin * ln["size"] * 0.52
        out.append({"text": clean, "x0": ln["x0"],
                    "x1": round(max(ln["x1"], width), 1), "y": ln["y"],
                    "size": ln["size"], "font": ln["font"]})
    out.sort(key=lambda l: (-l["y"], l["x0"]))
    return out


def body_size(lines: list[dict]) -> float:
    sizes = [round(l["size"], 1) for l in lines if l["text"]]
    return statistics.mode(sizes) if sizes else 10.0


def find_columns(lines: list[dict], page_w: float) -> list[tuple[float, float]]:
    if len(lines) < 8:
        return [(0.0, page_w)]
    best = None
    for i in range(30, 71):
        split = page_w * i / 100
        left = [l for l in lines if l["x1"] <= split + 2]
        right = [l for l in lines if l["x0"] >= split - 2]
        cross = [l for l in lines if l["x0"] < split - 2 and l["x1"] > split + 2]
        if len(left) < 3 or len(right) < 3:
            continue
        score = len(left) + len(right) - 4 * len(cross)
        if best is None or score > best[0]:
            best = (score, split, len(cross))
    if not best or best[2] > max(2, len(lines) * 0.15):
        return [(0.0, page_w)]
    split = best[1]
    return [(0.0, split), (split, page_w)]


def order_lines(lines: list[dict], cols: list[tuple[float, float]]) -> list[dict]:
    if len(cols) == 1:
        return sorted(lines, key=lambda l: (-l["y"], l["x0"]))
    out: list[dict] = []
    assigned: dict[int, dict] = {}
    for c0, c1 in cols:
        for l in lines:
            if c0 - 2 <= l["x0"] <= c1:
                assigned.setdefault(id(l), l)
    rest = sorted([l for l in lines if id(l) not in assigned], key=lambda l: -l["y"])
    out.extend(rest)
    for c0, c1 in cols:
        col = [l for l in lines if assigned.get(id(l)) is not None
               and c0 - 2 <= l["x0"] <= c1]
        col.sort(key=lambda l: -l["y"])
        out.extend(col)
    return out


def classify(line: dict, body: float) -> str:
    t = line["text"]
    if FORMULA_NO.search(t):
        return "formula"
    if PAGE_NO.match(t):
        return "page_number"
    if CAPTION.match(t):
        return "caption"
    if REF_ITEM.match(t):
        return "reference"
    big = line["size"] >= body * 1.15
    bold = "bold" in line["font"].lower()
    if (big or bold) and len(t) <= 90 and not t.endswith(("。", ".", ";", "；")):
        return "heading"
    if HEAD_NUM.match(t) and len(t) <= 60 and HEAD_WORDS.match(t.split(None, 1)[-1] if " " in t else t):
        return "heading"
    return "body"


def heading_level(line: dict, body: float) -> int:
    ratio = line["size"] / body if body else 1.0
    if ratio >= 1.6:
        return 1
    if ratio >= 1.3:
        return 2
    return 3


def parse_page(page, pno: int) -> dict:
    tb = _tables_mod()
    chunks, w, h = page_chunks(page)
    table_blocks, taken = [], set()
    for g in tb.grids(page):
        in_chunks = [c for c in chunks if tb.inside(g, c["x"], c["y"])]
        for c in in_chunks:
            taken.add(id(c))
        unassigned = tb.assign(g, in_chunks)
        table_blocks.append(table_block(g, pno, unassigned))
    body_chunks = [c for c in chunks if id(c) not in taken]
    lines = to_lines(body_chunks)
    body = body_size(lines)
    cols = find_columns(lines, w)
    ordered = order_lines(lines, cols)
    blocks, seen = [], set()
    for ln in ordered:
        role = classify(ln, body)
        block = {"page": pno,
                 "bbox": [round(ln["x0"], 1), round(ln["y"], 1),
                          round(ln["x1"], 1), round(ln["y"] + ln["size"], 1)],
                 "text": ln["text"], "role": role, "size": ln["size"],
                 "font": ln["font"], "why": "coords:" + role}
        if role == "heading":
            block["level"] = heading_level(ln, body)
        m = FORMULA_NO.search(ln["text"])
        if m:
            block["formula_no"] = m.group(1)
        key = (block["text"], tuple(block["bbox"]))
        if key in seen:
            continue
        seen.add(key)
        blocks.append(block)
    merged: list[dict] = []
    for b in blocks:
        if b["role"] == "formula" and re.fullmatch(r"[（(]\s*\d+[a-z]?\s*[）)]", b["text"].strip()):
            prev = merged[-1] if merged else None
            same_line = prev is not None and abs(prev["bbox"][1] - b["bbox"][1]) <= 4
            if same_line and prev["role"] in ("heading", "body", "formula"):
                prev["formula_no"] = b["text"].strip("（）() ")
                prev["role"] = "formula"
                prev["text"] = (prev["text"] + " " + b["text"]).strip()
                prev["bbox"][2] = max(prev["bbox"][2], b["bbox"][2])
                continue
        merged.append(b)
    for b in merged:
        if b["role"] == "formula":
            b.setdefault("original", b["text"])
            b.setdefault("inline", b["text"])
            b.setdefault("scripts", [])
            b.setdefault("symbols", _layout_mod().sym_inventory(b["text"]))
    for tb_ in table_blocks:
        merged.insert(table_insert_index(merged, tb_), tb_)
    return {"page": pno, "width": w, "height": h, "n_lines": len(lines),
            "columns": len(cols), "body_size": body, "blocks": merged,
            "n_tables": len(table_blocks), "empty": not chunks and not table_blocks}



def _layout_mod():
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("layout", here / "layout.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_layout_page(page, pno: int) -> dict:
    """layout 模式正文 + 内容流表格网格。

    表格网格先算：单元格文字既要从正文段里剔除（否则单元格起始列会被
    聚成额外的栏），又要并进表格块，所以顺序不能反。
    """
    lay = _layout_mod()
    tb = _tables_mod()
    chunks, _, _ = page_chunks(page)
    tables_out, cell_texts = [], []
    for g in tb.grids(page):
        in_chunks = [c for c in chunks if tb.inside(g, c["x"], c["y"])]
        unassigned = tb.assign(g, in_chunks)
        for row in g["rows"]:
            for cell in row:
                if cell["text"].strip():
                    cell_texts.append(cell["text"].strip())
        tables_out.append(table_block(g, pno, unassigned))
    res = lay.parse_page_layout(page, pno, chunks, cell_texts)
    blocks = res["blocks"]
    for t in tables_out:
        blocks.insert(table_insert_index(blocks, t), t)
    res["blocks"] = blocks
    res["n_tables"] = len(tables_out)
    return res


def mark_running_heads(pages: list[dict]) -> dict:
    n = len(pages)
    if n < 2:
        return {"header": [], "footer": []}
    from collections import Counter
    cnt: Counter = Counter()
    for p in pages:
        seen = set()
        for b in p["blocks"]:
            if b["role"] != "body":
                continue
            if "line" in b:
                top = b["line"] <= 1
                bottom = b["line"] >= p.get("n_lines", 0) - 2
            else:
                top = b["bbox"][1] > p["height"] * 0.92
                bottom = b["bbox"][3] < p["height"] * 0.08
            if (top or bottom) and b["text"] not in seen:
                seen.add(b["text"])
                cnt[b["text"]] += 1
    repeated = {t for t, c in cnt.items() if c >= max(2, int(n * 0.6)) and len(t) < 100}
    marked = {"header": [], "footer": []}
    for p in pages:
        for b in p["blocks"]:
            if b["text"] in repeated:
                if "line" in b:
                    top = b["line"] <= 1
                else:
                    top = b["bbox"][1] > p["height"] * 0.92
                b["role"] = "header" if top else "footer"
                b["why"] = "header:repeat_top" if top else "footer:repeat_bottom"
                marked["header" if top else "footer"].append(b["text"])
    return marked


def parse(path: str | Path, pages: list[int] | None = None,
          engine: str = "layout") -> dict:
    from pypdf import PdfReader
    r = PdfReader(str(path))
    total = len(r.pages)
    encrypted = bool(getattr(r, "is_encrypted", False))
    if encrypted:
        try:
            r.decrypt("")
            encrypted = False
        except Exception:
            pass
    fmod = _figures_mod()
    out_pages = []
    for i, page in enumerate(r.pages, 1):
        if pages and i not in pages:
            continue
        rec = parse_layout_page(page, i) if engine == "layout" else parse_page(page, i)
        try:
            rec["figures"] = fmod.regions(page, i)
        except Exception:
            rec["figures"] = []
        out_pages.append(rec)
    heads = mark_running_heads(out_pages)
    empty = [p["page"] for p in out_pages if p["empty"]]
    figs = [f for p in out_pages for f in p.get("figures") or []]
    return {"ok": True, "source": str(path), "n_pages": total,
            "pages_parsed": len(out_pages), "encrypted": encrypted,
            "no_text_pages": empty, "running": heads, "figures": figs,
            "pages": out_pages}


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="PDF 版面解析")
    ap.add_argument("pdf")
    ap.add_argument("--out", default=None)
    ap.add_argument("--page", type=int, default=None)
    ap.add_argument("--engine", default="layout", choices=["layout", "coords"])
    args = ap.parse_args(argv)
    res = parse(args.pdf, [args.page] if args.page else None, args.engine)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        flat = [b for p in res["pages"] for b in p["blocks"]]
        from collections import Counter
        print(json.dumps({"ok": True, "pages": res["n_pages"], "blocks": len(flat),
                          "roles": dict(Counter(b["role"] for b in flat)),
                          "tables": sum(p.get("n_tables", 0) for p in res["pages"]),
                          "columns": [p["columns"] for p in res["pages"]],
                          "no_text_pages": res["no_text_pages"], "out": args.out},
                         ensure_ascii=False))
    else:
        print(json.dumps(res, ensure_ascii=False)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


