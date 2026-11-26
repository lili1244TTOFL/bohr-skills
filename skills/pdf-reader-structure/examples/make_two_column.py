#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_two_column.py — 造一个双栏测试 PDF：页眉页脚、栏内正文、标题、表格线、公式编号。"""

import sys
from pathlib import Path

W, H = 595, 842
LEFT, RIGHT = 50, 310          # 两栏左边界
COL_W = 235


def esc(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def text(x, y, s, size=10, font="F1"):
    return f"BT /{font} {size} Tf {x} {y} Td ({esc(s)}) Tj ET"


def line(x1, y1, x2, y2, w=0.7):
    return f"{w} w {x1} {y1} m {x2} {y2} l S"


PAGE1 = []
PAGE1.append(text(LEFT, H - 45, "Journal of Layout Testing, Vol. 12, No. 3, 2024", 8))
PAGE1.append(text(LEFT, H - 75, "Multi-column Reading Order and Structure", 16, "F2"))
PAGE1.append(text(LEFT, H - 95, "A. Author, B. Author", 10))
PAGE1.append(text(LEFT, H - 125, "Abstract", 11, "F2"))
PAGE1.append(text(LEFT, H - 140, "We test column order, headings, tables and formulas in", 9))
PAGE1.append(text(LEFT, H - 152, "a two-column layout with running heads.", 9))
lv_y = H - 185
left_lines = [
    ("1 Introduction", 11, "F2"),
    ("Two-column papers are common in science. A reader must", 9, "F1"),
    ("follow the left column before the right column, otherwise", 9, "F1"),
    ("sentences from different columns get spliced together.", 9, "F1"),
    ("This paragraph continues for several lines so the", 9, "F1"),
    ("column detector has enough material to work.", 9, "F1"),
    ("1.1 Motivation", 10, "F2"),
    ("Layout errors are frequent when text is read with", 9, "F1"),
    ("naive sorters ignoring column bounds entirely.", 9, "F1"),
]
y = lv_y
for s, sz, f in left_lines:
    PAGE1.append(text(LEFT, y, s, sz, f))
    y -= 15
right_lines = [
    ("2 Method", 11, "F2"),
    ("We cluster lines by baseline, then find a vertical", 9, "F1"),
    ("split with few crossing lines. Lines that span", 9, "F1"),
    ("both columns flush the band and start a new one.", 9, "F1"),
    ("2.1 Table extraction", 10, "F2"),
    ("Rules detect the grid; text is assigned by bbox.", 9, "F1"),
    ("Table 1 reports parse accuracy per layout.", 9, "F1"),
]
y = lv_y
for s, sz, f in right_lines:
    PAGE1.append(text(RIGHT, y, s, sz, f))
    y -= 15
PAGE1.append(text(LEFT, 40, "- 1 -", 8))

PAGE2 = []
PAGE2.append(text(LEFT, H - 45, "Journal of Layout Testing, Vol. 12, No. 3, 2024", 8))
PAGE2.append(text(LEFT, H - 75, "3 Results", 11, "F2"))
PAGE2.append(text(LEFT, H - 95, "Table 1 Parse accuracy by layout type", 9, "F2"))
# 表格线
tx0, tx1 = LEFT, LEFT + 300
ty0 = H - 190
rows = 3
for i in range(rows + 1):
    PAGE2.append(line(tx0, ty0 + i * 18, tx1, ty0 + i * 18))
PAGE2.append(line(tx0, ty0, tx0, ty0 + rows * 18))
PAGE2.append(line(tx0 + 120, ty0, tx0 + 120, ty0 + rows * 18))
PAGE2.append(line(tx0 + 210, ty0, tx0 + 210, ty0 + rows * 18))
PAGE2.append(line(tx1, ty0, tx1, ty0 + rows * 18))
cells = [("Layout", "Columns", "Accuracy"), ("single", "1", "99.1%"),
         ("double", "2", "97.4%")]
for r, (a, b, c) in enumerate(cells):
    yy = ty0 + (rows - r - 1) * 18 + 4
    PAGE2.append(text(tx0 + 4, yy, a, 8))
    PAGE2.append(text(tx0 + 124, yy, b, 8))
    PAGE2.append(text(tx0 + 214, yy, c, 8))
PAGE2.append(text(LEFT, H - 240, "The score is computed from aligned characters:", 9))
PAGE2.append(text(LEFT + 60, H - 262, "S = A / N", 10, "F2"))
PAGE2.append(text(LEFT + 300, H - 262, "(1)", 9))
PAGE2.append(text(LEFT, H - 300, "References", 11, "F2"))
PAGE2.append(text(LEFT, H - 315, "[1] A. Author. Layout parsing. J. Testing, 2024.", 8))
PAGE2.append(text(LEFT, 40, "- 2 -", 8))


def build(pages) -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{6 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    for i, ops in enumerate(pages):
        content = "\n".join(ops).encode("latin-1", "replace")
        cnum = 5 + 2 * i
        objs.append(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                    b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>" % cnum)
    body = bytearray(b"%PDF-1.4\n")
    offs = [0]
    for i, o in enumerate(objs, 1):
        offs.append(len(body))
        body += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(body)
    body += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for o in offs[1:]:
        body += f"{o:010d} 00000 n \n".encode()
    body += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
    return bytes(body)


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build([PAGE1, PAGE2]))
    print(f"wrote {out} ({out.stat().st_size} bytes, 2 pages)")


