#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export.py — 把合并后的文献库导出为 BibTeX / RIS / EndNote(.enw) / CNKI 题录。

只写库内已有字段，不补、不猜；缺项留空，便于对方工具再校验。
字段映射方向见 rules/format_map.md。

用法：
  python export.py --lib work/lib/library_merged.json --out-dir work/export --formats bib,ris
  python export.py --lib ... --out-dir output --formats bib,ris,enw,cnki
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BIB_TYPE = {"J": "article", "M": "book", "C": "inproceedings", "A": "incollection",
            "D": "phdthesis", "R": "techreport", "S": "standard", "P": "patent",
            "EB/OL": "misc", "Z": "misc"}
RIS_TYPE = {"J": "JOUR", "M": "BOOK", "C": "CONF", "A": "CHAP", "D": "THES",
            "R": "RPRT", "S": "STAND", "P": "PAT", "EB/OL": "ELEC", "Z": "GEN"}
ENW_TYPE = {"J": "Journal Article", "M": "Book", "C": "Conference Paper",
            "A": "Book Section", "D": "Thesis", "R": "Report", "S": "Standard",
            "P": "Patent", "EB/OL": "Web Page", "Z": "Generic"}
CN_TYPE = {"J": "期刊", "M": "专著", "C": "会议论文", "A": "析出文献", "D": "学位论文",
           "R": "报告", "S": "标准", "P": "专利", "EB/OL": "电子资源", "Z": "其他"}



def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _pages(p: str) -> str:
    return (p or "").replace("--", "-").strip()


def _pages_bib(p: str) -> str:
    return _pages(p).replace("-", "--")


def to_bib(e: dict) -> str:
    kind = BIB_TYPE.get((e.get("type") or "Z").upper(), "misc")
    key = e.get("id") or "ref"
    rows = []
    au = e.get("authors") or []
    if au:
        rows.append(f"  author    = {{{' and '.join(au)}}}")
    for f, k in (("title", "title"), ("container", "journal"), ("year", "year"),
                 ("volume", "volume"), ("issue", "number"), ("pages", "pages"),
                 ("doi", "doi"), ("publisher", "publisher"), ("place", "address"),
                 ("url", "url")):
        v = e.get(f)
        if v:
            if f == "pages":
                v = _pages_bib(str(v))
            rows.append(f"  {k:<9} = {{{v}}}")
    return f"@{kind}{{{key},\n" + ",\n".join(rows) + "\n}\n"


def to_ris(e: dict) -> str:
    lines = [f"TY  - {RIS_TYPE.get((e.get('type') or 'Z').upper(), 'GEN')}"]
    for a in e.get("authors") or []:
        lines.append(f"AU  - {a}")
    if e.get("title"):
        lines.append(f"TI  - {e['title']}")
    if e.get("container"):
        lines.append(f"JO  - {e['container']}")
    for tag, f in (("PY", "year"), ("VL", "volume"), ("IS", "issue")):
        if e.get(f):
            lines.append(f"{tag}  - {e[f]}")
    pg = _pages(e.get("pages") or "")
    if pg:
        if "-" in pg:
            a, b = pg.split("-", 1)
            lines += [f"SP  - {a}", f"EP  - {b}"]
        else:
            lines.append(f"SP  - {pg}")
    if e.get("doi"):
        lines.append(f"DO  - {e['doi']}")
    if e.get("publisher"):
        lines.append(f"PB  - {e['publisher']}")
    if e.get("url"):
        lines.append(f"UR  - {e['url']}")
    lines.append("ER  - ")
    return "\n".join(lines) + "\n"


def to_enw(e: dict) -> str:
    """EndNote 导入格式：%0 类型 + 各字段一行；连写多条即可被 EndNote 识别。"""
    lines = [f"%0 {ENW_TYPE.get((e.get('type') or 'Z').upper(), 'Generic')}"]
    for a in e.get("authors") or []:
        lines.append(f"%A {a}")
    if e.get("title"):
        lines.append(f"%T {e['title']}")
    if e.get("container"):
        lines.append(f"%J {e['container']}")
    if e.get("year"):
        lines.append(f"%D {e['year']}")
    for tag, f in (("V", "volume"), ("N", "issue"), ("P", "pages")):
        if e.get(f):
            lines.append(f"%{tag} {_pages(str(e[f])) if f == 'pages' else e[f]}")
    if e.get("doi"):
        lines.append(f"%R {e['doi']}")
    if e.get("publisher"):
        lines.append(f"%I {e['publisher']}")
    if e.get("place"):
        lines.append(f"%C {e['place']}")
    if e.get("url"):
        lines.append(f"%U {e['url']}")
    return "\n".join(lines) + "\n\n"


def to_cnki(e: dict) -> str:
    """CNKI/NoteExpress 风格中文题录：`{标签}: 值`，空行分条。"""
    rows = [f"{{文献类型}}: {CN_TYPE.get((e.get('type') or 'Z').upper(), '其他')}"]
    if e.get("authors"):
        rows.append("{作者}: " + "; ".join(e["authors"]))
    for tag, f in (("题名", "title"), ("刊名", "container"), ("年", "year"),
                   ("卷", "volume"), ("期", "issue"), ("页码", "pages"),
                   ("DOI", "doi"), ("出版者", "publisher"), ("出版地", "place"),
                   ("链接", "url")):
        if e.get(f):
            rows.append(f"{{{tag}}}: {_pages(str(e[f])) if f == 'pages' else e[f]}")
    return "\n".join(rows) + "\n\n"


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="导出 BibTeX / RIS / EndNote / CNKI")
    ap.add_argument("--lib", required=True)
    ap.add_argument("--out-dir", default="work/export")
    ap.add_argument("--formats", default="bib,ris")
    ap.add_argument("--keep", default=None, help="只导出 keep=true 的条目（需 ingest 结果）")
    args = ap.parse_args(argv)

    lib = json.loads(Path(args.lib).read_text(encoding="utf-8-sig"))
    entries = lib.get("entries", [])
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    want = {x.strip().lower() for x in args.formats.split(",") if x.strip()}
    if "bib" in want:
        p = out / "references.bib"
        p.write_text("\n".join(to_bib(e) for e in entries), encoding="utf-8")
        written.append({"format": "bib", "path": str(p), "entries": len(entries)})
    if "ris" in want:
        p = out / "references.ris"
        p.write_text("\n".join(to_ris(e) for e in entries), encoding="utf-8")
        written.append({"format": "ris", "path": str(p), "entries": len(entries)})
    if "enw" in want or "endnote" in want:
        p = out / "references.enw"
        p.write_text("".join(to_enw(e) for e in entries), encoding="utf-8")
        written.append({"format": "enw", "path": str(p), "entries": len(entries)})
    if "cnki" in want:
        p = out / "references.cnki.txt"
        p.write_text("".join(to_cnki(e) for e in entries), encoding="utf-8")
        written.append({"format": "cnki", "path": str(p), "entries": len(entries)})
    print(json.dumps({"ok": True, "written": written}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
