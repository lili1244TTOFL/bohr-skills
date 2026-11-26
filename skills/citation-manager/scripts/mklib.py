#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mklib.py — BibTeX / RIS 导出 → library.json（统一字段，保留原文）。

字段：id type authors title container year volume issue pages doi publisher place url keywords raw

用法：
  python mklib.py lib.bib --out work/lib/library.json
  python mklib.py lib.ris lib.bib --out work/lib/library.json --tsv work/lib/library.tsv
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BIB_TYPE = {
    "article": "J", "inproceedings": "C", "conference": "C", "proceedings": "C",
    "book": "M", "incollection": "A", "inbook": "A", "phdthesis": "D",
    "mastersthesis": "D", "thesis": "D", "techreport": "R", "report": "R",
    "standard": "S", "patent": "P", "online": "EB/OL", "electronic": "EB/OL",
    "misc": "Z", "unpublished": "Z", "preprint": "Z",
}
RIS_TYPE = {
    "JOUR": "J", "JFULL": "J", "MGZN": "J", "CONF": "C", "CPAPER": "C",
    "BOOK": "M", "CHAP": "A", "THES": "D", "RPRT": "R", "STAND": "S",
    "PAT": "P", "ELEC": "EB/OL", "WEB": "EB/OL", "GEN": "Z", "UNPB": "Z",
}


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


# ------------------------------------------------------------------ BibTeX
def _split_fields(body: str) -> list[str]:
    out, depth, buf, quote = [], 0, [], False
    for ch in body:
        if ch == '"' and depth == 0:
            quote = not quote
            buf.append(ch)
            continue
        if ch in "{" and not quote:
            depth += 1
        elif ch in "}" and not quote:
            depth -= 1
        if ch == "," and depth == 0 and not quote:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [x.strip() for x in out if x.strip()]


def _clean(v: str) -> str:
    v = v.strip()
    while (v.startswith("{") and v.endswith("}")) or (v.startswith('"') and v.endswith('"')):
        v = v[1:-1].strip()
    v = re.sub(r"\\[a-zA-Z]+\s*", "", v)          # 去 LaTeX 命令
    return re.sub(r"\s+", " ", v).strip()


def parse_bib(text: str) -> list[dict]:
    entries, pos = [], 0
    head = re.compile(r"@\s*(\w+)\s*\{", re.I)
    while True:
        m = head.search(text, pos)
        if not m:
            break
        kind = m.group(1).lower()
        i, depth = m.end(), 1
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body, pos = text[m.end():i - 1], i
        if kind in {"string", "comment", "preamble"}:
            continue
        fields = _split_fields(body)
        key, data = "", {}
        for f in fields:
            if "=" not in f:
                key = key or f.strip()
                continue
            k, v = f.split("=", 1)
            data[k.strip().lower()] = _clean(v)
        entries.append(_bib_norm(kind, key, data, body))
    return entries


def _bib_norm(kind: str, key: str, d: dict, raw: str) -> dict:
    au = [a for a in re.split(r"\s+and\s+", d.get("author", "")) if a.strip()]
    return {
        "id": key or "",
        "type": BIB_TYPE.get(kind, "Z"),
        "authors": au,
        "title": d.get("title", ""),
        "container": d.get("journal", "") or d.get("booktitle", ""),
        "year": d.get("year", ""),
        "volume": d.get("volume", ""),
        "issue": d.get("number", "") or d.get("issue", ""),
        "pages": d.get("pages", ""),
        "doi": d.get("doi", ""),
        "publisher": d.get("publisher", "") or d.get("school", "") or d.get("institution", ""),
        "place": d.get("address", ""),
        "url": d.get("url", ""),
        "keywords_raw": d.get("keywords", ""),
        "raw": "@" + kind + "{" + raw + "}",
        "source": "bibtex",
    }


# ------------------------------------------------------------------ RIS
def parse_ris(text: str) -> list[dict]:
    entries, cur = [], {}
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^([A-Z][A-Z0-9])(?:  -[ \t]?|\s*-\s)(.*)$", line.rstrip("\r\n"))
        if not m:
            continue
        tag, val = m.group(1), (m.group(2) or "").strip()
        if tag == "TY":
            cur = {"TY": val, "AU": [], "raw": [line]}
            continue
        if not cur:
            continue
        cur["raw"].append(line)
        if tag in ("AU", "A1"):
            cur["AU"].append(val)
        elif tag == "ER":
            entries.append(_ris_norm(cur))
            cur = {}
        else:
            cur.setdefault(tag, val)
    return entries


def _ris_norm(c: dict) -> dict:
    t = c.get("TY", "").upper()
    sp, ep = c.get("SP", ""), c.get("EP", "")
    pages = f"{sp}-{ep}" if sp and ep else sp
    return {
        "id": c.get("ID", ""),
        "type": RIS_TYPE.get(t, "Z"),
        "authors": c.get("AU", []),
        "title": c.get("TI", "") or c.get("T1", ""),
        "container": c.get("JO", "") or c.get("T2", "") or c.get("JF", ""),
        "year": c.get("PY", "") or c.get("Y1", ""),
        "volume": c.get("VL", ""),
        "issue": c.get("IS", ""),
        "pages": pages,
        "doi": c.get("DO", ""),
        "publisher": c.get("PB", ""),
        "place": c.get("CY", ""),
        "url": c.get("UR", ""),
        "keywords_raw": c.get("KW", ""),
        "raw": "\n".join(c.get("raw", [])),
        "source": "ris",
    }


# ------------------------------------------------------------------ 主流程
TSV_COLS = ["id", "type", "authors", "title", "container", "year",
            "volume", "issue", "pages", "doi", "publisher", "raw"]


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="文献库导入")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", default="work/lib/library.json")
    ap.add_argument("--tsv", default=None, help="同时导出模型输入用 TSV")
    ap.add_argument("--min-tsv", default=None, help="精简清单：id/type/authors/title/container/year/doi")
    args = ap.parse_args(argv)

    entries: list[dict] = []
    for p in args.inputs:
        text = Path(p).read_text(encoding="utf-8-sig", errors="replace")
        got = parse_ris(text) if p.lower().endswith(".ris") else parse_bib(text)
        for e in got:
            e["source_file"] = str(p)
        entries.extend(got)

    for n, e in enumerate(entries, 1):
        e["id"] = e.get("id") or f"lib{n:03d}"
        e["authors"] = [a for a in e.get("authors", []) if a.strip()]
        e["seq"] = n

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_by": "mklib.py", "n": len(entries),
               "fields": TSV_COLS, "entries": entries}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.tsv:
        tsv = Path(args.tsv)
        tsv.parent.mkdir(parents=True, exist_ok=True)
        lines = ["\t".join(TSV_COLS)]
        for e in entries:
            row = [str(e.get(k, "")) if k != "authors" else "; ".join(e.get("authors", []))
                   for k in TSV_COLS]
            row[TSV_COLS.index("raw")] = e.get("raw", "").replace("\n", " ") \
                .replace("\r", " ").replace("\t", " ")
            lines.append("\t".join(row))
        tsv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if args.min_tsv:
        mp = Path(args.min_tsv)
        mp.parent.mkdir(parents=True, exist_ok=True)
        cols = ["id", "type", "authors", "title", "container", "year", "doi"]
        lines = ["\t".join(cols)]
        for e in entries:
            lines.append("\t".join([
                e.get("id", ""), e.get("type", ""), "; ".join(e.get("authors", [])),
                (e.get("title") or "").replace("\t", " "),
                (e.get("container") or "").replace("\t", " "),
                str(e.get("year", "")), e.get("doi", "")]))
        mp.write_text("\n".join(lines) + "\n", encoding="utf-8")

    by_type: dict[str, int] = {}
    for e in entries:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    print(json.dumps({"ok": True, "n": len(entries), "by_type": by_type,
                      "out": str(out), "tsv": args.tsv, "min_tsv": args.min_tsv},
                     ensure_ascii=False))
    return 0
    print(json.dumps({"ok": True, "n": len(entries), "by_type": by_type,
                      "out": str(out), "tsv": args.tsv}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
