#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tables.py — 从 PDF 内容流里的线段识别表格网格，并把文字块归到单元格。

规则优先、不猜内容：
  · 收集 m/l 线段与 re 矩形，按方向分成水平线、垂直线
  · 同一条线（坐标差 < tol）合并
  · 线按"是否相交"聚成连通区域：同一张表的线互相交叉，两张表竖着堆叠时
    横线互不相交，于是分成两个区域，不会并成一张巨表
  · 区域里取 ≥2 水平 + ≥2 垂直为表格；单元格由相邻线围成
  · 文字块按中心点落入单元格；落不进的进 unassigned，不硬塞、不并入相邻格

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
    """横线/竖线清单。同一条线上的多段只在"共线且相邻或重叠"时合并，
    避免把上下两张表各自的左边框（x 相同、y 不相接）并成一条贯通线。"""
    ops = content_ops(page).decode("latin-1", "replace")
    hraw: list[dict] = []
    vraw: list[dict] = []
    for m in SEG.finditer(ops):
        x1, y1, x2, y2 = (float(m.group(i)) for i in range(1, 5))
        if abs(y1 - y2) <= tol and abs(x1 - x2) > tol:
            hraw.append({"y": (y1 + y2) / 2, "x0": min(x1, x2), "x1": max(x1, x2)})
        elif abs(x1 - x2) <= tol and abs(y1 - y2) > tol:
            vraw.append({"x": (x1 + x2) / 2, "y0": min(y1, y2), "y1": max(y1, y2)})
    for m in RECT.finditer(ops):
        x, y, w, h = (float(m.group(i)) for i in range(1, 5))
        for yy in (y, y + h):
            hraw.append({"y": yy, "x0": x, "x1": x + w})
        for xx in (x, x + w):
            vraw.append({"x": xx, "y0": y, "y1": y + h})
    return _merge(hraw, vraw, tol)


def _merge(hraw: list[dict], vraw: list[dict], tol: float) -> tuple[list[dict], list[dict]]:
    hraw.sort(key=lambda d: (d["y"], d["x0"]))
    vraw.sort(key=lambda d: (d["x"], d["y0"]))
    h: list[dict] = []
    for seg in hraw:
        for line in h:
            if (abs(line["y"] - seg["y"]) <= tol
                    and seg["x0"] <= line["x1"] + 3 * tol
                    and seg["x1"] >= line["x0"] - 3 * tol):
                line["x0"] = min(line["x0"], seg["x0"])
                line["x1"] = max(line["x1"], seg["x1"])
                break
        else:
            h.append({"y": round(seg["y"] / tol) * tol,
                      "x0": seg["x0"], "x1": seg["x1"]})
    v: list[dict] = []
    for seg in vraw:
        for line in v:
            if (abs(line["x"] - seg["x"]) <= tol
                    and seg["y0"] <= line["y1"] + 3 * tol
                    and seg["y1"] >= line["y0"] - 3 * tol):
                line["y0"] = min(line["y0"], seg["y0"])
                line["y1"] = max(line["y1"], seg["y1"])
                break
        else:
            v.append({"x": round(seg["x"] / tol) * tol,
                      "y0": seg["y0"], "y1": seg["y1"]})
    return h, v


def _crosses(hl: dict, vl: dict, tol: float) -> bool:
    """横线与竖线是否相交（容差 tol）。"""
    return (vl["x"] >= hl["x0"] - tol and vl["x"] <= hl["x1"] + tol
            and hl["y"] >= vl["y0"] - tol and hl["y"] <= vl["y1"] + tol)


def _regions(h: list[dict], v: list[dict], tol: float, min_lines: int) -> list[dict]:
    """按相交关系把线聚成独立区域；一个区域一张表。"""
    if len(h) < min_lines or len(v) < min_lines:
        return []
    n = len(h) + len(v)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, hl in enumerate(h):
        for j, vl in enumerate(v):
            if _crosses(hl, vl, tol):
                union(i, len(h) + j)
    bags: dict[int, dict] = defaultdict(lambda: {"h": [], "v": []})
    for i, hl in enumerate(h):
        bags[find(i)]["h"].append(hl)
    for j, vl in enumerate(v):
        bags[find(len(h) + j)]["v"].append(vl)
    out = []
    for bag in bags.values():
        if len(bag["h"]) < min_lines or len(bag["v"]) < min_lines:
            continue
        ys = sorted((d["y"] for d in bag["h"]), reverse=True)
        xs = sorted(d["x"] for d in bag["v"])
        region = {"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
                  "n_rows": len(ys) - 1, "n_cols": len(xs) - 1,
                  "h": len(bag["h"]), "v": len(bag["v"]),
                  "cells_total": (len(ys) - 1) * (len(xs) - 1)}
        region["rows"] = [[{"x0": xs[c], "x1": xs[c + 1], "y0": ys[r + 1], "y1": ys[r],
                            "text": ""} for c in range(len(xs) - 1)]
                          for r in range(len(ys) - 1)]
        out.append(region)
    out.sort(key=lambda g: (-g["y1"], g["x0"]))
    return out


def grids(page, tol: float = 2.0, min_lines: int = 2) -> list[dict]:
    h, v = segments(page, tol)
    return _regions(h, v, tol, min_lines)


def inside(region: dict, cx: float, cy: float) -> bool:
    return region["x0"] - 2 <= cx <= region["x1"] + 2 and region["y0"] - 2 <= cy <= region["y1"] + 2


def assign(region: dict, chunks: list[dict]) -> list[dict]:
    """文字块按中心点入格；落不进任何格子的返回 unassigned（不并入相邻格）。"""
    unassigned = []
    for ch in chunks:
        cx = ch["x"] + max(1.0, ch["size"] * 0.3) * len(ch["text"].strip())
        cy = ch["y"] + ch["size"] * 0.35
        placed = False
        for row in region["rows"]:
            for cell in row:
                if cell["x0"] - 1 <= cx <= cell["x1"] + 1 and cell["y0"] - 1 <= cy <= cell["y1"] + 1:
                    cell["text"] = (cell["text"] + " " + ch["text"]).strip()
                    ch["cell"] = [round(cell["x0"], 1), round(cell["y0"], 1),
                                  round(cell["x1"], 1), round(cell["y1"], 1)]
                    placed = True
                    break
            if placed:
                break
        if not placed:
            unassigned.append(ch)
    region["cells_filled"] = sum(1 for row in region["rows"] for c in row if c["text"].strip())
    region["n_assigned"] = len(chunks) - len(unassigned)
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
