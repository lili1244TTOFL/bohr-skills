#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_hard.py — 造一份"难版式"样例，专门覆盖原四份样例没覆盖的结构：

    hard_layout/paper.pdf（3 页）
      · 第 1 页 单栏：运行页眉/页脚、标题、作者（带 * 脚注标记）、单位、
        摘要、**图片对象 + 图题**、**带上下标的公式 (1)**（含希腊字母）、
        **脚注**（`*`，正文里有引用）、页末一句未收尾的正文（供跨页续接验证）
      · 第 2 页 双栏：左栏接续上页那股正文、右栏给**同页上下两张带线表格**、
        **公式 (2)**（含 ≤ 与 ±）、参考文献
      · 第 3 页 三栏：验证"栏数由行首聚集决定、不封顶"这句

字体：F1 Helvetica、F2 Helvetica-Bold、F3 STSong-Light（中文）、
F4 Helvetica + /Differences（希腊字母与 ± ° ≤ ≥ ·，无需嵌入字体文件）。

同时把生成时的"标准答案"写在 GT 里，供 scripts/benchmark.py 做公平对照；
GT 是生成器自己写下的内容，不是解析结果反推。

用法：
  python make_hard.py <输出根目录>
"""

import json
import sys
from pathlib import Path

W, H = 595, 842
LEFT = 50
RIGHT = 310
COL3 = [50, 215, 380]
SYMS = "\u03b1\u03b2\u03bb\u03a3\u03bc\u00b1\u00b0\u2264\u2265\u00b7"


def esc(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def text(x, y, s, size=10, font="F1"):
    return f"BT /{font} {size} Tf {x} {y} Td ({esc(s)}) Tj ET"


def symtext(x, y, s, size=10):
    """符号字符走 /Differences 码位；空格、数字、等号等 ASCII 原样保留。"""
    out = []
    for ch in s:
        if ch in SYMS:
            out.append(chr(65 + SYMS.index(ch)))
        else:
            out.append(ch)
    return text(x, y, "".join(out), size, "F4")


def ctext(x, y, s, size=10):
    hexs = s.encode("utf-16-be").hex().upper()
    return f"BT /F3 {size} Tf {x} {y} Td <{hexs}> Tj ET"


def line(x1, y1, x2, y2, w=0.7):
    return f"{w} w {x1} {y1} m {x2} {y2} l S"


def body_rows(lines, x, y0, step=14, size=9, font="F1"):
    return [text(x, y0 - i * step, s, size, font) for i, s in enumerate(lines)]


def grid(y0, n_rows, n_cols, x0, rh=18, cw=110):
    out = []
    x1 = x0 + n_cols * cw
    for i in range(n_rows + 1):
        out.append(line(x0, y0 + i * rh, x1, y0 + i * rh))
    for c in range(n_cols + 1):
        out.append(line(x0 + c * cw, y0, x0 + c * cw, y0 + n_rows * rh))
    return out


def cells(values, y0, x0, rh=18, cw=110, size=8):
    out = []
    n = len(values)
    for r, row in enumerate(values):
        for c, v in enumerate(row):
            out.append(text(x0 + c * cw + 5, y0 + (n - r - 1) * rh + 6, v, size))
    return out


RUNNING_HEAD = "Journal of Hard Layouts, Vol. 3, No. 1, 2025"

# ---------------------------------------------------------------------------
# 第 1 页：单栏 + 图片 + 公式(1) + 脚注 + 未收尾段落
# ---------------------------------------------------------------------------
P1 = [text(LEFT, 800, RUNNING_HEAD, 8)]
P1 += [
    text(LEFT, 752, "Recovering Figures, Formulas and Footnotes", 16, "F2"),
    text(LEFT, 730, "Q. Zhang*, L. Wang, M. Ito", 10),
    text(LEFT, 714, "Institute of Document Engineering, Example University, Tokyo 100-0001", 8),
    text(LEFT, 690, "Abstract", 10, "F2"),
]
P1 += body_rows([
    "We test a parser on a page that carries an embedded image, a numbered",
    "equation with super- and subscripts, a footnote marked with an asterisk",
    "and a body paragraph that ends without terminal punctuation. The parser",
    "must place the figure box, rebuild the equation scripts and list the",
    "footnote without guessing any missing text.",
], LEFT, 672, 13, 9)
P1 += [text(LEFT, 590, "Keywords: figures; formulas; footnotes; reading order", 8)]
# 图片对象：220 x 150 pt，放在 (185, 420)
P1.append("q 220 0 0 150 185 420 cm /Im1 Do Q")
P1.append(text(185, 404, "Figure 1 Parsing pipeline with figure placement", 9, "F2"))
# 公式 (1)：λ = Σ α x^2 / N_k
P1.append(symtext(150, 330, "\u03bb = \u03a3 \u03b1", 10))
P1.append(text(216, 330, "x", 10))
P1.append(text(224, 337, "2", 6.5, "F2"))          # 上标 2
P1.append(text(236, 330, "/ N", 10))
P1.append(text(258, 326, "k", 6.5, "F2"))          # 下标 k
P1.append(text(500, 330, "(1)", 9))
P1 += body_rows([
    "Equation (1) combines a Greek alphabet with a true superscript and a",
    "true subscript, so the extraction can be checked character by character.",
    "The paragraph below continues on the next page and is left unterminated",
], LEFT, 300, 14, 9)
# 脚注：分隔线 + 小字号
P1.append(line(LEFT, 140, 545, 140, 0.4))
P1.append(text(LEFT, 126, "* Corresponding author. qz@example.edu. Funded by grant 6217.", 7))
P1.append(text(LEFT, 40, "Preprint submitted to Journal of Hard Layouts", 7))
P1.append(text(520, 26, "- 1 -", 8))

# ---------------------------------------------------------------------------
# 第 2 页：双栏 + 两张表 + 公式(2) + 参考文献
# ---------------------------------------------------------------------------
P2 = [text(LEFT, 800, RUNNING_HEAD, 8)]
P2 += [text(LEFT, 762, "2 Results", 11, "F2")]
P2 += body_rows([
    "clusters the line starts into reading-order",
    "bands and assigns a role to every band before",
    "it is finally written out.",
], LEFT, 744, 14, 9)
P2 += [text(LEFT, 700, "Table 1 Accuracy by layout class", 9, "F2")]
P2 += grid(604, 3, 3, LEFT, 18, 85)
P2 += cells([("Layout", "Class", "Accuracy"),
             ("single", "1", "99.1%"),
             ("double", "2", "97.4%")], 604, LEFT, 18, 85)
P2 += [text(LEFT, 586, "Table 2 Entries recovered per feature", 9, "F2")]
P2 += grid(500, 2, 3, LEFT, 18, 85)
P2 += cells([("Feature", "Found", "Missed"),
             ("formula", "2", "0")], 500, LEFT, 18, 85)
P2 += body_rows([
    "Both grids share the same left and right",
    "borders but never touch, so they must come",
    "out as two tables rather than one tall grid.",
], LEFT, 478, 14, 9)
P2 += [text(RIGHT, 762, "3 Discussion", 11, "F2")]
P2 += body_rows([
    "Two equations carry scripts: the first",
    "uses Greek letters, the second only",
    "symbols.",
], RIGHT, 744, 14, 9)
# 公式 (2)：P ≤ 0.05 ± 0.01
P2.append(text(RIGHT, 690, "P", 10))
P2.append(symtext(318, 690, " \u2264 0.05 \u00b1 0.01", 10))
P2.append(text(520, 690, "(2)", 9))
P2 += body_rows([
    "The symbol inventory must show the",
    "less-or-equal and plus-minus signs with",
    "their code points, and drop no character.",
], RIGHT, 670, 14, 9)
P2 += [text(RIGHT, 600, "References", 11, "F2")]
P2 += body_rows([
    "[1] A. Author. Figure boxes from content streams.",
    "[2] B. Li. Equation scripts by font size. DocEng.",
], RIGHT, 583, 13, 8)
P2 += [text(LEFT, 40, "Preprint submitted to Journal of Hard Layouts", 7),
       text(520, 26, "- 2 -", 8)]

# ---------------------------------------------------------------------------
# 第 3 页：三栏（每栏宽 160 pt，行宽 ≤ 34 字符）
# ---------------------------------------------------------------------------
P3 = [text(LEFT, 800, RUNNING_HEAD, 8)]
P3 += [text(LEFT, 762, "4 Three-column page", 11, "F2")]
P3 += body_rows([
    "Column one holds the opening",
    "lines of a paragraph that keeps",
    "going into the second column.",
], COL3[0], 740, 13, 9)
P3 += body_rows([
    "Column two continues the same",
    "argument and adds the numbered",
    "sentence about ordering.",
], COL3[1], 740, 13, 9)
P3 += body_rows([
    "Column three closes the",
    "paragraph and gives the pair",
    "that must come last.",
], COL3[2], 740, 13, 9)
P3 += [text(LEFT, 40, "Preprint submitted to Journal of Hard Layouts", 7),
       text(520, 26, "- 3 -", 8)]

IMG = bytes([200, 210, 220] * 8 * 8)

PAGES = [P1, P2, P3]


def build(pages) -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{10 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")       # 3 F1
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")  # 4 F2
    objs.append(b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
                b"/Encoding /UniGB-UCS2-H /DescendantFonts [6 0 R] >>")           # 5 F3
    objs.append(b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
                b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> "
                b"/DW 1000 >>")                                                   # 6
    diffs = " ".join("65 /alpha 66 /beta 67 /lambda 68 /Sigma 69 /mu 70 /plusminus "
                     "71 /degree 72 /lessequal 73 /greaterequal 74 /periodcentered".split())
    objs.append(("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding "
                 "<< /Type /Encoding /BaseEncoding /WinAnsiEncoding "
                 "/Differences [%s] >> >>" % diffs).encode())                     # 7 F4
    objs.append(b"<< /Type /XObject /Subtype /Image /Width 8 /Height 8 "
                b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length %d >>\nstream\n"
                % len(IMG) + IMG + b"\nendstream")                                # 8 Im1
    for i, ops in enumerate(pages):
        content = "\n".join(ops).encode("latin-1", "replace")
        cnum = 9 + 2 * i
        objs.append(f"<< /Length {len(content)} >>\nstream\n".encode()
                    + content + b"\nendstream")
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W} {H}] "
                     f"/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R /F4 7 0 R >> "
                     f"/XObject << /Im1 8 0 R >> /ProcSet [/PDF /Text /ImageB /ImageC] >> "
                     f"/Contents {cnum} 0 R >>").encode())
    body = bytearray(b"%PDF-1.4\n")
    offs = [0]
    for i, o in enumerate(objs, 1):
        offs.append(len(body))
        body += f"{i} 0 obj\n".encode() + (o if isinstance(o, bytes) else o.encode()) + b"\nendobj\n"
    xref = len(body)
    body += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for o in offs[1:]:
        body += f"{o:010d} 00000 n \n".encode()
    body += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
             f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(body)


# 生成器自己写下的内容：benchmark 拿这份当标准答案，不靠解析结果反推
GT = {
    "case_id": "hard-layout",
    "pages": 3,
    "title": "Recovering Figures, Formulas and Footnotes",
    "authors_prefix": "Q. Zhang",
    "affiliation_contains": "Institute of Document Engineering",
    "abstract_first": "We test a parser on a page that carries an embedded image",
    "headings": ["Abstract", "2 Results", "3 Discussion",
                 "4 Three-column page", "References"],
    "tables": [
        {"page": 2, "n_rows": 3, "n_cols": 3,
         "cells": [["Layout", "Class", "Accuracy"], ["single", "1", "99.1%"],
                   ["double", "2", "97.4%"]]},
        {"page": 2, "n_rows": 2, "n_cols": 3,
         "cells": [["Feature", "Found", "Missed"], ["formula", "2", "0"]]},
    ],
    "formula_nos": ["1", "2"],
    "formula_symbols": ["\u03bb", "\u03a3", "\u03b1", "\u2264", "\u00b1"],
    "formula_scripts": 3,
    "figures": 1,
    "footnotes": 1,
    "footnote_marker": "*",
    "receipts": 0,
    "references": 2,
    "columns_by_page": [1, 2, 3],
    "body_sentences": [
        "We test a parser on a page that carries an embedded image,",
        "clusters the line starts into reading-order bands and assigns a role",
        "Column one holds the opening lines of the",
        "Column two continues the same argument and",
        "Column three closes the paragraph and gives",
    ],
    "running_head": RUNNING_HEAD,
    "cross_page_paragraph": True,
}


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("cases")
    d = root / "hard_layout"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "paper.pdf"
    p.write_bytes(build(PAGES))
    (d / "case.json").write_text(json.dumps({
        "case_id": GT["case_id"],
        "output_contract": ["阅读/解析结果", "证据与边界", "运行状态"],
    }, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {p} ({p.stat().st_size} bytes, {len(PAGES)} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
