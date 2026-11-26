#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export.py — 把合并后的文献库导出为 BibTeX / RIS。

只写库内已有字段，不补、不猜；缺项留空，便于对方工具再校验。

用法：
  python export.py --lib work/lib/library_merged.json --out-dir work/export --formats bib,ris
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


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _pages(p: str) -> str:
    return (p or "").replace("--", "-").strip()


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
                v = _pages(str(v)).replace("-", "--")
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


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="导出 BibTeX / RIS")
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
    print(json.dumps({"ok": True, "written": written}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
