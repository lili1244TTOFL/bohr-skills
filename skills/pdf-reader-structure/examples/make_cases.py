#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_cases.py — 造四份版式/语言不同的测试 PDF，写到 _cases/<name>/paper.pdf。

四份分别覆盖：
  single_column  单栏英文，期刊式「1. Introduction」编号、页眉页脚、作者单位、参考文献
  two_column     双栏英文，跨栏标题、左右栏各一节、公式编号、题注、页眉页脚
  table_grid     单栏英文，4 列 × 5 行带线表格 + 表题 + 表注 + 正文
  chinese        单栏中文，摘要/章节/参考文献，验证 CJK 无词间空格时的分段与排序

字体：F1 Helvetica、F2 Helvetica-Bold、F3 STSong-Light（Type0 + UniGB-UCS2-H，
pypdf 按预定义 CMap 解回 Unicode，不需要嵌入字体文件）。

用法：
  python make_cases.py <输出根目录>
"""

import sys
from pathlib import Path

W, H = 595, 842
LEFT, RIGHT, COL_W = 50, 310, 235


def esc(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def text(x, y, s, size=10, font="F1"):
    return f"BT /{font} {size} Tf {x} {y} Td ({esc(s)}) Tj ET"


def ctext(x, y, s, size=10):
    """中文用 UCS2-BE 十六进制串，Type0 字体按 2 字节编码。"""
    hexs = s.encode("utf-16-be").hex().upper()
    return f"BT /F3 {size} Tf {x} {y} Td <{hexs}> Tj ET"


def line(x1, y1, x2, y2, w=0.7):
    return f"{w} w {x1} {y1} m {x2} {y2} l S"


def rows(lines, x, y0, step=14, size=9, font="F1"):
    emit = ctext if font == "F3" else text
    return [emit(x, y0 - i * step, s, size) for i, s in enumerate(lines)]


# ---------------------------------------------------------------------------
# A 单栏英文：真实期刊首页的编号风格（1. Introduction / 1.1. Motivation）
# ---------------------------------------------------------------------------
A1 = [text(LEFT, 800, "Journal of Structural Parsing, Vol. 7, No. 2, 2023", 8)]
A1 += [
    text(LEFT, 755, "Reading Order and Structure Recovery for", 17, "F2"),
    text(LEFT, 735, "Single-Column Scientific Papers", 17, "F2"),
    text(LEFT, 710, "Alice Zhang, Bob Li, Chen Wu", 10),
    text(LEFT, 695, "Department of Computer Science, Example University, Beijing 100084, China",
         8),
    text(LEFT, 672, "Abstract", 10, "F2"),
]
A1 += rows([
    "We study how a single-column scientific page can be parsed into sections,",
    "captions, references and measured character boxes. A rule pipeline first",
    "groups characters into lines, then clusters line starts into reading-order",
    "bands, and finally labels each band with a structural role. On four layouts",
    "the pipeline recovers every numbered heading and both running heads.",
], LEFT, 655, 13, 9)
A1 += [
    text(LEFT, 578, "Keywords: layout analysis; reading order; section hierarchy", 8),
    text(LEFT, 548, "1. Introduction", 11, "F2"),
]
A1 += rows([
    "Scientific papers are read in a strict order: title, authors, abstract,",
    "numbered sections, then references. A parser that ignores the numbering",
    "turns every \"1. Introduction\" line into a reference entry and loses the",
    "whole section tree without raising an error.",
    "This article reports a measured baseline for that failure mode and the",
    "rules that repair it on single-column pages.",
], LEFT, 531, 14, 9)
A1 += [text(LEFT, 437, "1.1. Motivation", 10, "F2")]
A1 += rows([
    "Reading order errors are cheap to detect but expensive to correct later.",
    "We therefore log a measured box for every emitted block.",
    "The log is what a reviewer checks first, so it must be true.",
], LEFT, 420, 14, 9)
A1 += [text(LEFT, 355, "2. Methods", 11, "F2")]
A1 += rows([
    "Characters arrive with a text matrix, so their origin is measured rather",
    "than inferred. Lines are formed when baselines differ by less than a third",
    "of the font size and the horizontal gap stays below the same bound.",
    "Bands are then found by clustering the left edge of every line.",
], LEFT, 338, 14, 9)
A1 += [text(LEFT, 262, "2.1. Dataset", 10, "F2")]
A1 += rows([
    "Four pages from two journals, one Chinese and three English, were used.",
    "Each page carries a running head and a footer line that must not leak.",
], LEFT, 245, 14, 9)
A1 += [text(LEFT, 40, "Preprint submitted to Journal of Structural Parsing", 7),
       text(520, 26, "- 1 -", 8)]

A2 = [text(LEFT, 800, "Journal of Structural Parsing, Vol. 7, No. 2, 2023", 8)]
A2 += [text(LEFT, 760, "3. Results", 11, "F2")]
A2 += rows([
    "All four numbered headings were recovered, and both running heads were",
    "removed before the body text was written. The measured box of the title",
    "block starts at the left margin and is 17 points tall, which matches the",
    "generator that produced the page.",
], LEFT, 743, 14, 9)
A2 += [text(LEFT, 660, "References", 11, "F2")]
A2 += rows([
    "[1] A. Author. Layout parsing for single-column papers. J. Struct. Parsing, 2022.",
    "[2] B. Li. Measured boxes in PDF text extraction. Proc. DocEng, 2021.",
    "[3] C. Wu. Section trees from numbered headings. Preprint, 2023.",
], LEFT, 643, 13, 8)
A2 += [text(LEFT, 40, "Preprint submitted to Journal of Structural Parsing", 7),
       text(520, 26, "- 2 -", 8)]

# ---------------------------------------------------------------------------
# B 双栏英文：跨栏标题 + 左右栏、公式编号、题注、页眉页脚
# ---------------------------------------------------------------------------
B_HEAD = [text(LEFT, 800, "Journal of Layout Testing, Vol. 12, No. 3, 2024", 8)]
B_HEAD += [
    text(LEFT, 762, "Reading Order in Two-Column Papers", 16, "F2"),
    text(LEFT, 740, "D. Reader, E. Author", 10),
    text(LEFT, 726, "Institute of Document Engineering, Example University", 8),
]
B1 = B_HEAD + [text(LEFT, 700, "Abstract", 10, "F2")]
B1 += rows([
    "Two-column pages must be read left column first. A sorter that ignores",
    "column bounds splices sentences from different columns together.",
], LEFT, 684, 13, 9)
B1 += [text(LEFT, 640, "1 Introduction", 11, "F2")]
B1 += rows([
    "Two-column papers are common in science.",
    "A reader must follow the left column before the right one,",
    "otherwise sentences from different columns get spliced.",
    "This paragraph continues for several lines so that the",
    "column detector has enough material to work with.",
], LEFT, 623, 14, 9)
B1 += [text(LEFT, 540, "1.1 Motivation", 10, "F2")]
B1 += rows([
    "Layout errors are frequent when text is read with naive",
    "sorters that ignore column bounds entirely.",
    "The error is visible in the first joined sentence.",
], LEFT, 523, 14, 9)
B1 += [text(RIGHT, 640, "2 Method", 11, "F2")]
B1 += rows([
    "We cluster lines by baseline, then search for a vertical",
    "split crossed by few lines. Lines that span both columns",
    "flush the band and start a new one.",
], RIGHT, 623, 14, 9)
B1 += [text(RIGHT, 560, "2.1 Table extraction", 10, "F2")]
B1 += rows([
    "Rules detect the grid and assign text by cell centre.",
    "Table 1 Parse accuracy by layout type",
    "The caption sits above the grid in this layout.",
], RIGHT, 543, 14, 9)
B1 += [text(LEFT, 40, "Preprint submitted to Journal of Layout Testing", 7),
       text(520, 26, "- 1 -", 8)]

B2 = [text(LEFT, 800, "Journal of Layout Testing, Vol. 12, No. 3, 2024", 8)]
B2 += [text(LEFT, 760, "3 Results", 11, "F2")]
B2 += rows([
    "The score is computed from aligned characters:",
], LEFT, 743, 14, 9)
B2 += [text(LEFT + 60, 715, "S = A / N", 10, "F2"),
       text(LEFT + 170, 715, "(1)", 9)]
B2 += rows([
    "where A counts aligned characters and N counts all",
    "characters in the reference text.",
], LEFT, 698, 14, 9)
B2 += [text(RIGHT, 760, "4 Discussion", 11, "F2")]
B2 += rows([
    "Column order is recovered before roles are assigned,",
    "so a heading never absorbs the line below the band.",
    "The formula number is merged back into its equation.",
], RIGHT, 743, 14, 9)
B2 += [text(RIGHT, 660, "References", 11, "F2")]
B2 += rows([
    "[1] A. Author. Layout parsing. J. Testing, 2024.",
    "[2] B. Li. Column detection. Proc. DocEng, 2023.",
], RIGHT, 643, 13, 8)
B2 += [text(LEFT, 40, "Preprint submitted to Journal of Layout Testing", 7),
       text(520, 26, "- 2 -", 8)]

# ---------------------------------------------------------------------------
# C 单栏英文 + 4×5 带线表格：表题、表注、正文围绕表格
# ---------------------------------------------------------------------------
C1 = [text(LEFT, 800, "Journal of Table Testing, Vol. 4, No. 1, 2024", 8)]
C1 += [text(LEFT, 760, "3 Results", 11, "F2")]
C1 += rows([
    "Table 2 lists the measured accuracy for four layout classes.",
    "Each value is the mean of three repetitions on the same page set.",
], LEFT, 743, 14, 9)
C1 += [text(LEFT, 700, "Table 2 Measured accuracy by layout class", 9, "F2")]
tx0, tx1 = LEFT, LEFT + 495
ty0, rh, nr = 520, 22, 5
xs = [tx0, tx0 + 125, tx0 + 250, tx0 + 375, tx1]
for i in range(nr + 1):
    C1.append(line(tx0, ty0 + i * rh, tx1, ty0 + i * rh))
for x in xs:
    C1.append(line(x, ty0, x, ty0 + nr * rh))
cells = [("Layout", "Columns", "Accuracy", "Time (s)"),
         ("single", "1", "99.1%", "0.12"),
         ("double", "2", "97.4%", "0.15"),
         ("triple", "3", "95.8%", "0.19"),
         ("scanned", "1", "88.2%", "1.40")]
for r, row in enumerate(cells):
    yy = ty0 + (nr - r - 1) * rh + 7
    for c, val in enumerate(row):
        C1.append(text(xs[c] + 5, yy, val, 8))
C1 += rows([
    "Values are percentages of aligned characters; time is wall clock",
    "per page. The scanned class uses the same grid but no text layer.",
], LEFT, 470, 14, 8)
C1 += [text(LEFT, 420, "4 Discussion", 11, "F2")]
C1 += rows([
    "Cell text must not be re-emitted as body paragraphs, otherwise the",
    "same value appears twice in the reading order.",
    "Our pipeline removes a body block when its text is a cell value.",
], LEFT, 403, 14, 9)
C1 += [text(LEFT, 40, "Preprint submitted to Journal of Table Testing", 7),
       text(520, 26, "- 1 -", 8)]

C2 = [text(LEFT, 800, "Journal of Table Testing, Vol. 4, No. 1, 2024", 8)]
C2 += [text(LEFT, 760, "5 Conclusion", 11, "F2")]
C2 += rows([
    "A ruled table is recovered as a grid whose cells keep their own text.",
    "The table block is placed after its caption and before the next body",
    "paragraph, so the reading order stays explainable.",
], LEFT, 743, 14, 9)
C2 += [text(LEFT, 660, "References", 11, "F2")]
C2 += rows([
    "[1] A. Author. Ruled table extraction. J. Table Testing, 2024.",
    "[2] B. Li. Cell assignment by centre point. Proc. DocEng, 2023.",
], LEFT, 643, 13, 8)
C2 += [text(LEFT, 40, "Preprint submitted to Journal of Table Testing", 7),
       text(520, 26, "- 2 -", 8)]

# ---------------------------------------------------------------------------
# D 单栏中文：摘要/章节/参考文献，CJK 无词间空格
# ---------------------------------------------------------------------------
D1 = [ctext(LEFT, 800, "结构解析学报 2024 年第 3 卷第 1 期", 8)]
D1 += [
    ctext(LEFT, 755, "单栏中文论文的版面解析与结构还原", 16),
    ctext(LEFT, 725, "张伟，李娜，王强", 10),
    ctext(LEFT, 705, "示例大学计算机学院，北京 100084", 8),
    ctext(LEFT, 675, "摘要", 10),
]
D1 += rows([
    "本文报告单栏中文论文的版面解析方法。规则管线先按字符位置聚行，",
    "再按行首聚栏，最后为每段判角色。四份样例中，编号章节、页眉页脚",
    "与参考文献均被正确识别，正文顺序与原文一致。",
], LEFT, 655, 15, 9, "F3")
D1 += [ctext(LEFT, 595, "关键词：版面解析；阅读顺序；中文字距", 8)]
D1 += [ctext(LEFT, 560, "1 引言", 11)]
D1 += rows([
    "中文没有词间空格，行内分段只能靠字距判断。若把标题行按空格切段，",
    "整行会退化成一段，栏号与顺序都会失真。",
    "本文用起始列与测量框两路信息交叉校验标题行。",
], LEFT, 540, 15, 9, "F3")
D1 += [ctext(LEFT, 480, "1.1 研究背景", 10)]
D1 += rows([
    "已有工作多用坐标聚类，对字距变化敏感。",
    "我们把页面上的每个文字块都配上测量框，再按行号排序。",
], LEFT, 460, 15, 9, "F3")
D1 += [ctext(LEFT, 400, "2 方法", 11)]
D1 += rows([
    "先取文字层，再聚行；行首聚成栏；栏内自上而下。",
    "标题行的层级由编号点数决定，参考文献按方括号编号识别。",
], LEFT, 380, 15, 9, "F3")
D1 += [ctext(LEFT, 40, "本预印本由结构解析学报编辑部排版", 7),
       text(520, 26, "- 1 -", 8)]

D2 = [ctext(LEFT, 800, "结构解析学报 2024 年第 3 卷第 1 期", 8)]
D2 += [ctext(LEFT, 760, "3 结果", 11)]
D2 += rows([
    "四份样例的页覆盖为 2/2，标题层级为 1 至 2 级，参考文献 3 条。",
    "中文页的分段结果与原文行数一致，未出现串栏。",
], LEFT, 740, 15, 9, "F3")
D2 += [ctext(LEFT, 680, "参考文献", 11)]
D2 += rows([
    "[1] 张伟. 单栏论文的版面解析. 结构解析学报, 2023.",
    "[2] 李娜. 中文文本提取中的字距问题. 文档工程, 2022.",
], LEFT, 660, 15, 8, "F3")
D2 += [ctext(LEFT, 40, "本预印本由结构解析学报编辑部排版", 7),
       text(520, 26, "- 2 -", 8)]


def build(pages) -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{8 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")      # 3 F1
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")  # 4 F2
    objs.append(b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
                b"/Encoding /UniGB-UCS2-H /DescendantFonts [6 0 R] >>")          # 5 F3
    objs.append(b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
                b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> "
                b"/DW 1000 >>")                                                  # 6
    for i, ops in enumerate(pages):
        content = "\n".join(ops).encode("latin-1", "replace")
        cnum = 7 + 2 * i
        objs.append(f"<< /Length {len(content)} >>\nstream\n".encode()
                    + content + b"\nendstream")
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W} {H}] "
                     f"/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> "
                     f"/ProcSet [/PDF /Text] >> /Contents {cnum} 0 R >>").encode())
    body = bytearray(b"%PDF-1.4\n")
    offs = [0]
    for i, o in enumerate(objs, 1):
        offs.append(len(body))
        body += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(body)
    body += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for o in offs[1:]:
        body += f"{o:010d} 00000 n \n".encode()
    body += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
             f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(body)


CASES = {
    "single_column": [A1, A2],
    "two_column": [B1, B2],
    "table_grid": [C1, C2],
    "chinese": [D1, D2],
}

if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("cases")
    for name, pages in CASES.items():
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        p = d / "paper.pdf"
        p.write_bytes(build(pages))
        print(f"wrote {p} ({p.stat().st_size} bytes, {len(pages)} pages)")
