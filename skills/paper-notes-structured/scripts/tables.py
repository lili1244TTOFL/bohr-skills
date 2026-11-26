#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tables.py — 从 PDF 内容流里的线段识别表格网格，并把文字块归到单元格。

规则优先、不猜内容：
  · 收集 m/l 线段与 re 矩形，按方向分成水平线、垂直线
  · 同一条线（坐标差 < tol）合并
  · 取 ≥2 水平 + ≥2 垂直的区域为表格；单元格由相邻线围成
  · 文字块按中心点落入单元格；落不进的进 cells_unassigned，不硬塞

纯 pypdf，无外部依赖。
"""

from __future__ import annotations

import re
from collections import defaultdict

NUM = r"[-+]?\d*\.?\d+"
SEG = re.compile(rf"({NUM})\s+({NUM})\s+m\s+({NUM})\s+({NUM})\s+l\s+S")
RECT = re.compile(rf"({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+re\s+[SfF]")


def content_ops(page) -> bytes:
    try:
        data = page.get_contents()
        if data is None:
            return b""
        return data.get_data() or b""
    except Exception:
        return b""


def segments(page, tol: float = 2.0) -> tuple[list[dict], list[dict]]:
    ops = content_ops(page).decode("latin-1", "replace")
    hs: dict[float, list[float]] = defaultdict(list)
    vs: dict[float, list[float]] = defaultdict(list)
    for m in SEG.finditer(ops):
        x1, y1, x2, y2 = (float(m.group(i)) for i in range(1, 5))
        if abs(y1 - y2) <= tol and abs(x1 - x2) > tol:
            hs[round((y1 + y2) / 2 / tol) * tol].append(min(x1, x2))
            hs[round((y1 + y2) / 2 / tol) * tol].append(max(x1, x2))
        elif abs(x1 - x2) <= tol and abs(y1 - y2) > tol:
            vs[round((x1 + x2) / 2 / tol) * tol].append(min(y1, y2))
            vs[round((x1 + x2) / 2 / tol) * tol].append(max(y1, y2))
    for m in RECT.finditer(ops):
        x, y, w, h = (float(m.group(i)) for i in range(1, 5))
        for yy in (y, y + h):
            hs[round(yy / tol) * tol].extend([x, x + w])
        for xx in (x, x + w):
            vs[round(xx / tol) * tol].extend([y, y + h])
    h_lines = [{"y": y, "x0": min(v), "x1": max(v)} for y, v in hs.items()]
    v_lines = [{"x": x, "y0": min(v), "y1": max(v)} for x, v in vs.items()]
    return h_lines, v_lines


def grids(page, tol: float = 2.0, min_lines: int = 2) -> list[dict]:
    h, v = segments(page, tol)
    if len(h) < min_lines or len(v) < min_lines:
        return []
    h.sort(key=lambda d: -d["y"])
    v.sort(key=lambda d: d["x"])
    ys = [d["y"] for d in h]
    xs = [d["x"] for d in v]
    region = {"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
              "n_rows": len(ys) - 1, "n_cols": len(xs) - 1,
              "h": len(h), "v": len(v)}
    region["rows"] = [[{"x0": xs[c], "x1": xs[c + 1], "y0": ys[r + 1], "y1": ys[r],
                        "text": ""} for c in range(len(xs) - 1)]
                      for r in range(len(ys) - 1)]
    return [region]


def inside(region: dict, cx: float, cy: float) -> bool:
    return region["x0"] - 2 <= cx <= region["x1"] + 2 and region["y0"] - 2 <= cy <= region["y1"] + 2


def assign(region: dict, chunks: list[dict]) -> list[dict]:
    unassigned = []
    for ch in chunks:
        cx = ch["x"] + max(1.0, ch["size"] * 0.3) * len(ch["text"].strip())
        cy = ch["y"] + ch["size"] * 0.35
        placed = False
        for row in region["rows"]:
            for cell in row:
                if cell["x0"] - 1 <= cx <= cell["x1"] + 1 and cell["y0"] - 1 <= cy <= cell["y1"] + 1:
                    cell["text"] = (cell["text"] + " " + ch["text"]).strip()
                    placed = True
                    break
            if placed:
                break
        if not placed:
            unassigned.append(ch)
    return unassigned


def to_markdown(region: dict) -> str:
    lines = []
    for row in region["rows"]:
        lines.append("| " + " | ".join((c["text"] or "").replace("|", "\\|") for c in row) + " |")
    if not lines:
        return ""
    cols = len(region["rows"][0])
    out = [lines[0], "| " + " | ".join(["---"] * cols) + " |"]
    out.extend(lines[1:])
    return "\n".join(out)
