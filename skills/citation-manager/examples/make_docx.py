#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_docx.py — 把 markdown 稿转成 docx，用于测试插入链路（测试夹具，不进 Skill）。"""

import re
import sys
from pathlib import Path

from docx import Document
from docx.shared import Pt


def main(src: str, out: str) -> int:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(12)
    for raw in Path(src).read_text(encoding="utf-8-sig").split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            doc.add_heading(m.group(2), level=min(lvl, 3))
        else:
            doc.add_paragraph(line)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
