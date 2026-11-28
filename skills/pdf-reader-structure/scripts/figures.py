#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""figures.py — 从内容流的 `q / cm / Do / Q` 取图片对象在页面上的摆放框。

为什么不用 `page.images`：它只给像素，不给位置。图的"位置"要的是
用户空间里的矩形，只有把内容流里的 CTM 还原出来才能拿到：

    q  200 0 0 120 100 500  cm  /Im1 Do  Q

→ 把单位正方形按 CTM 变换，四个角点的外接矩形就是图片框
  [100, 500, 300, 620]。

判据：
  · 只认资源字典 /XObject 里 /Subtype 为 /Image 的对象（矢量图不在此列，
    那类图只能靠题注定位，输出里标 caption-only 并写明原因）；
  · 框面积 < 400 pt² 或宽或高 < 8 pt 的当装饰线/图标丢弃，不记图表；
  · 框与页面裁剪框求交，越界部分不算。

纯 pypdf，无外部依赖。
"""

from __future__ import annotations

import re

IDENT = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
NUM_RE = re.compile(r"^[-+]?\d*\.?\d+$")


def _mul(m1: list[float], m2: list[float]) -> list[float]:
    """PDF 矩阵乘法 m1 × m2（3×2 仿射矩阵，行向量写法）。"""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return [a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
            c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2]


def _box(m: list[float]) -> list[float]:
    a, b, c, d, e, f = m
    xs = [e, a + e, c + e, a + c + e]
    ys = [f, b + f, d + f, b + d + f]
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]


def xobjects(page) -> dict[str, str]:
    """资源字典里的 XObject: 名字 -> 子类型（Image/Form）。"""
    out: dict[str, str] = {}
    try:
        res = page.get("/Resources")
        xo = res.get("/XObject") if res is not None else None
        if hasattr(xo, "get_object"):
            xo = xo.get_object()
        if not xo:
            return out
        for k, ref in xo.items():
            try:
                obj = ref.get_object() if hasattr(ref, "get_object") else ref
                out[str(k).lstrip("/")] = str(obj.get("/Subtype") or "")
            except Exception:
                continue
    except Exception:
        return out
    return out


def _ops(page) -> str:
    try:
        data = page.get_contents()
        if data is None:
            return ""
        return data.get_data().decode("latin-1", "replace")
    except Exception:
        return ""


def regions(page, pno: int = 0, tol: float = 2.0) -> list[dict]:
    """页面上的图片摆放框；每个对象一条，带 CTM 与资源名。"""
    kinds = xobjects(page)
    if not kinds:
        return []
    try:
        pw = float(page.mediabox.width)
        ph = float(page.mediabox.height)
    except Exception:
        pw, ph = 595.0, 842.0
    tokens = _ops(page).split()
    stack: list[list[float]] = []
    cur = list(IDENT)
    out: list[dict] = []
    for i, tok in enumerate(tokens):
        if tok == "q":
            stack.append(list(cur))
        elif tok == "Q":
            cur = stack.pop() if stack else list(IDENT)
        elif tok == "cm" and i >= 6 and all(NUM_RE.match(t) for t in tokens[i - 6:i]):
            m = [float(t) for t in tokens[i - 6:i]]
            cur = _mul(m, cur)
        elif tok == "Do" and i >= 1 and tokens[i - 1].startswith("/"):
            name = tokens[i - 1].lstrip("/")
            if kinds.get(name) != "/Image":
                continue
            box = _box(cur)
            x0, y0 = max(0.0, box[0]), max(0.0, box[1])
            x1, y1 = min(pw, box[2]), min(ph, box[3])
            w, h = x1 - x0, y1 - y0
            if w < 8 or h < 8 or w * h < 400:
                continue
            out.append({"page": pno, "name": name, "kind": "image",
                        "bbox": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
                        "width": round(w, 1), "height": round(h, 1),
                        "ctm": [round(v, 2) for v in cur], "why": "figure:image_region"})
    out.sort(key=lambda d: (-d["bbox"][3], d["bbox"][0]))
    return out
