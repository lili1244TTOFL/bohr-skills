#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mklib.py — BibTeX / RIS / EndNote(.enw) / CNKI·RefWorks/NoteExpress 题录 → library.json。

字段：id type authors title container year volume issue pages doi publisher place url keywords raw
字段映射表见 rules/format_map.md；解析不了的条目不会被静默吞掉，按文件在 stdout 的
`unparsed` 里报出来（run.py 会写进 material_gaps）。

用法：
  python mklib.py lib.bib --out work/lib/library.json
  python mklib.py lib.ris cnki.txt lib.enw --out work/lib/library.json --tsv work/lib/library.tsv
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
# EndNote %0 与 CNKI/RefWorks/NoteExpress 的类型名 → 国标标识
TYPE_NAME = {
    "journal article": "J", "journal": "J", "article": "J", "magazine article": "J",
    "book": "M", "book section": "A", "edited book": "M", "conference paper": "C",
    "conference proceedings": "C", "thesis": "D", "dissertation": "D",
    "report": "R", "standard": "S", "patent": "P", "web page": "EB/OL",
    "electronic article": "EB/OL", "generic": "Z", "other": "Z",
    "期刊": "J", "期刊文章": "J", "图书": "M", "专著": "M", "会议论文": "C",
    "学位论文": "D", "博士论文": "D", "硕士论文": "D", "报告": "R", "标准": "S",
    "专利": "P", "电子资源": "EB/OL", "报纸": "N",
}
# 标签 → 统一字段（EndNote 用 %X；RefWorks 用 XX  ；NoteExpress/CNKI 用 {Tag}: 或 中文标签:）
ENW_TAG = {
    "0": "type_name", "A": "authors", "T": "title", "J": "container", "B": "container",
    "D": "year", "V": "volume", "N": "issue", "P": "pages", "R": "doi",
    "I": "publisher", "C": "place", "U": "url", "K": "keywords_raw",
}
REFWORKS_TAG = {
    "RT": "type_name", "A1": "authors", "AU": "authors", "T1": "title", "TI": "title",
    "JF": "container", "JO": "container", "T2": "container", "PY": "year", "Y1": "year",
    "VL": "volume", "IS": "issue", "SP": "pages_start", "EP": "pages_end",
    "DO": "doi", "PB": "publisher", "CY": "place", "UR": "url", "KW": "keywords_raw",
}
CN_TAG = {
    "题名": "title", "标题": "title", "作者": "authors", "刊名": "container",
    "期刊": "container", "来源": "container", "年": "year", "年份": "year",
    "卷": "volume", "期": "issue", "页码": "pages", "页": "pages",
    "DOI": "doi", "doi": "doi", "出版者": "publisher", "出版社": "publisher",
    "出版地": "place", "类型": "type_name", "文献类型": "type_name",
    "关键词": "keywords_raw", "URL": "url", "链接": "url",
}
ENW_LINE = re.compile(r"^%([0-9A-Za-z])\s*(.*)$")
TAG_LINE = re.compile(r"^\{?([^:{}]{1,14})\}?\s*[:：]\s*(.*)$")



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


# ------------------------------------------------------------- EndNote (.enw)
def parse_enw(text: str) -> list[dict]:
    """EndNote 导出：一条 = 一段 `%X 值` 行，空行分隔。"""
    entries, cur, raw = [], {}, []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = ENW_LINE.match(line.strip())
        if not m:
            if not line.strip() and cur:
                entries.append(_tagged_norm(cur, raw, "endnote"))
                cur, raw = {}, []
            continue
        tag, val = m.group(1), m.group(2).strip()
        raw.append(line.strip())
        if tag in ("0", "TY"):
            if cur:
                entries.append(_tagged_norm(cur, raw[:-1], "endnote"))
                cur, raw = {}, [line.strip()]
        key = ENW_TAG.get(tag)
        if not key:
            continue
        if key == "authors":
            cur.setdefault("authors", []).append(val)
        else:
            cur.setdefault(key, val)
    if cur:
        entries.append(_tagged_norm(cur, raw, "endnote"))
    return entries


# ------------------------------------------- CNKI / RefWorks / NoteExpress 标签式
def parse_tagged(text: str) -> list[dict]:
    """`{Tag}: 值` 或 `Tag: 值`（含中文标签）的题录；用空行或新的类型行断条。"""
    entries, cur, raw = [], {}, []
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        m = TAG_LINE.match(s)
        if not m:
            if not s and cur:
                entries.append(_tagged_norm(cur, raw, "cnki"))
                cur, raw = {}, []
            continue
        tag, val = m.group(1).strip(), m.group(2).strip()
        key = (CN_TAG.get(tag) or REFWORKS_TAG.get(tag.upper())
               or REFWORKS_TAG.get(tag) or CN_TAG.get(tag.strip()))
        if not key:
            raw.append(s)
            continue
        if key == "type_name" and cur:
            entries.append(_tagged_norm(cur, raw, "cnki"))
            cur, raw = {}, [s]
        else:
            raw.append(s)
        if key == "authors":
            parts = [x for x in re.split(r"[;；,，]\s*|\s+and\s+", val) if x.strip()]
            cur.setdefault("authors", []).extend(parts)
        else:
            cur.setdefault(key, val)
    if cur:
        entries.append(_tagged_norm(cur, raw, "cnki"))
    return entries


def _tagged_norm(c: dict, raw: list[str], source: str) -> dict:
    sp, ep = c.get("pages_start"), c.get("pages_end")
    pages = c.get("pages") or (f"{sp}-{ep}" if sp and ep else (sp or ""))
    tname = (c.get("type_name") or "").strip()
    typ = TYPE_NAME.get(tname.lower()) or TYPE_NAME.get(tname) or "Z"
    return {
        "id": c.get("id", ""), "type": typ,
        "authors": [a.strip() for a in c.get("authors", []) if a.strip()],
        "title": c.get("title", ""), "container": c.get("container", ""),
        "year": c.get("year", ""), "volume": c.get("volume", ""),
        "issue": c.get("issue", ""), "pages": pages,
        "doi": re.sub(r"(?i)^(https?://(dx\.)?doi\.org/|doi:\s*)", "",
                      (c.get("doi") or "").strip()).rstrip("."),
        "publisher": c.get("publisher", ""), "place": c.get("place", ""),
        "url": c.get("url", ""), "keywords_raw": c.get("keywords_raw", ""),
        "raw": " ".join(raw), "source": source,
    }


def sniff_format(text: str) -> str:
    if re.search(r"^TY  - ", text, re.M):
        return "ris"
    if re.search(r"^%0\s", text, re.M):
        return "endnote"
    if re.search(r"^@\s*\w+\s*\{", text, re.M):
        return "bibtex"
    if re.search(r"^\{?Reference Type\}?\s*[:：]", text, re.M):
        return "cnki"
    if re.search(r"^\s*(题名|作者|刊名|文献类型)\s*[:：]", text, re.M):
        return "cnki"
    if re.search(r"^(RT|T1|A1)\s*-\s", text, re.M):
        return "refworks"
    return ""


def parse_any(path: str, text: str) -> tuple[list[dict], str]:
    """先按扩展名，再按内容嗅探；都不认返回空列表（由调用方报 unparsed）。"""
    ext = Path(path).suffix.lower()
    order = {".ris": "ris", ".enw": "endnote", ".bib": "bibtex",
             ".nbib": "endnote", ".nw": "cnki", ".refworks": "refworks"}
    fmt = order.get(ext) or sniff_format(text)
    if fmt == "ris":
        return parse_ris(text), "ris"
    if fmt == "endnote":
        return parse_enw(text), "endnote"
    if fmt == "cnki":
        return parse_tagged(text), "cnki"
    if fmt == "refworks":
        return parse_tagged(text), "refworks"
    if fmt == "bibtex":
        return parse_bib(text), "bibtex"
    return [], ""


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
    parsed_files, unparsed = [], []
    for p in args.inputs:
        text = Path(p).read_text(encoding="utf-8-sig", errors="replace")
        got, fmt = parse_any(p, text)
        if not got:
            unparsed.append({"path": str(p), "bytes": len(text),
                             "why": "内容不像 bib/ris/EndNote/CNKI 题录，未解析出条目"})
            continue
        for e in got:
            e["source_file"] = str(p)
            e["source_format"] = fmt
        parsed_files.append({"path": str(p), "format": fmt, "n": len(got)})
        entries.extend(got)

    for n, e in enumerate(entries, 1):
        e["id"] = e.get("id") or f"lib{n:03d}"
        e["authors"] = [a for a in e.get("authors", []) if a.strip()]
        e["seq"] = n

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_by": "mklib.py", "n": len(entries),
               "fields": TSV_COLS, "files": parsed_files,
               "unparsed": unparsed, "entries": entries}
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
                      "files": parsed_files, "unparsed": unparsed,
                      "out": str(out), "tsv": args.tsv, "min_tsv": args.min_tsv},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
