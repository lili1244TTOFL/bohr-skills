#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""segments.py — 把原文切成可对齐的段，抽术语候选与受保护片段。

切段：按空行分段；段内按句末标点切句并保留句号。每段固定 id（P001），
与译文一一对应，跨页/跨栏只在页码上体现，不合并段落。

受保护片段：数字、单位、公式、引用标记、图表编号——翻译时必须原样保留。

用法：
  python segments.py --src paper.txt --out work/segments.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SENT_END = re.compile(r"(?<=[.。!?！？;；])\s+")
NUM = re.compile(r"\d[\d,.\u00a0]*\s*(?:%|‰|℃|°C|mM|μM|µm|nm|mm|cm|km|kg|mg|μg|mL|µL|L|"
                 r"h|min|s|ms|Hz|kHz|MHz|GHz|Gy|Sv|MPa|kPa|Pa|V|mV|mA|A|W|kW|kJ|kcal|"
                 r"mm Hg|rpm|r/min|bp|kb|Da|kDa|ppm|ppb|mg/kg|mg/L|g/L|mol/L|IU|U)\b")
CITE = re.compile(r"\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]|\(\s*[A-Z][A-Za-z.\-]+(?:\s+et\s+al\.?)?,?\s*(?:19|20)\d{2}[a-z]?\s*\)")
FIGTAB = re.compile(r"(?i)\b(?:fig(?:ure)?|table|eq(?:uation)?)\.?\s*\d+[A-Za-z]?")
FORMULA = re.compile(r"\$[^$]{1,80}\$|\\\([^)]{1,80}\\\)")
CJK = re.compile(r"[\u4e00-\u9fff]")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def read_source(path: Path) -> tuple[str, list[dict]]:
    """返回全文与页定位（仅 PDF 有页码）。"""
    suf = path.suffix.lower()
    pages: list[dict] = []
    if suf == ".pdf":
        from pypdf import PdfReader
        r = PdfReader(str(path))
        for i, pg in enumerate(r.pages, 1):
            pages.append({"page": i, "text": pg.extract_text() or ""})
    elif suf == ".docx":
        from docx import Document
        d = Document(str(path))
        pages.append({"page": 1, "text": "\n\n".join(p.text for p in d.paragraphs)})
    else:
        pages.append({"page": 1, "text": path.read_text(encoding="utf-8-sig", errors="replace")})
    return "\n".join(p["text"] for p in pages), pages


def split_paragraphs(text: str) -> list[str]:
    text = text.replace("\r\n", "\n")
    blocks = re.split(r"\n\s*\n", text)
    out = []
    for b in blocks:
        b = re.sub(r"[ \t]+", " ", b).strip()
        if b:
            out.append(b)
    if len(out) > 1:
        return out
    # PDF 抽出的文本常没有空行：按行重组，遇到短标题行或满 8 行另起一段
    lines = [x.strip() for x in text.split("\n") if x.strip()]
    if not lines:
        return []
    paras, cur = [], []
    for ln in lines:
        looks_head = len(ln) <= 60 and not re.search(r"[.。;；:：,，]$", ln)
        if cur and (looks_head or len(cur) >= 8):
            paras.append(" ".join(cur))
            cur = []
        cur.append(ln)
    if cur:
        paras.append(" ".join(cur))
    return [p for p in paras if p]


def split_sentences(par: str) -> list[str]:
    sents = [s.strip() for s in SENT_END.split(par) if s.strip()]
    return sents or [par]


def protected(sent: str) -> dict:
    nums = [m.group(0).strip() for m in NUM.finditer(sent)]
    plain = [m.group(0) for m in re.finditer(r"\d+(?:[.,]\d+)*", sent)]
    for x in plain:
        if x not in nums and all(x not in n for n in nums):
            nums.append(x)
    return {"numbers": nums,
            "citations": [m.group(0).strip() for m in CITE.finditer(sent)],
            "figtab": [m.group(0).strip() for m in FIGTAB.finditer(sent)],
            "formulas": [m.group(0).strip() for m in FORMULA.finditer(sent)],
            "negations": len(re.findall(r"(?i)\b(?:not|no|never|none|without|cannot|nor|neither|"
                                        r"fail(?:ed)? to|exclud\w*)\b", sent))}


def page_of(pages: list[dict], needle: str) -> int:
    for p in pages:
        if needle[:40] and needle[:40] in p["text"]:
            return p["page"]
    return 0


def build(src: Path) -> dict:
    text, pages = read_source(src)
    pars = split_paragraphs(text)
    segs = []
    for i, par in enumerate(pars, 1):
        for j, sent in enumerate(split_sentences(par), 1):
            segs.append({"uid": f"P{i:03d}S{j:03d}", "pid": f"P{i:03d}", "sid": j,
                         "page": page_of(pages, par), "text": sent,
                         "len": len(sent), "cjk": bool(CJK.search(sent)),
                         "protected": protected(sent)})
    return {"source": str(src), "n_pages": len(pages), "n_paras": len(pars),
            "n_segs": len(segs), "segments": segs}


def term_candidates(segs: list[dict], min_len: int = 3) -> list[dict]:
    """术语候选：出现 ≥2 次的英文多词短语与带连字符/首字母大写的词。"""
    from collections import Counter
    cnt: Counter = Counter()
    for s in segs:
        t = s["text"]
        for m in re.finditer(r"\b[A-Z][A-Za-z\-]+(?:\s+[A-Za-z][A-Za-z\-]+){0,3}\b", t):
            p = m.group(0)
            if len(p) >= min_len:
                cnt[p] += 1
        for m in re.finditer(r"\b[a-z]+-[a-z]+\b", t):
            cnt[m.group(0)] += 1
    return [{"term": k, "count": v} for k, v in cnt.most_common(60) if v >= 2]


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="切段")
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    res = build(Path(args.src))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in res["segments"]) + "\n",
                   encoding="utf-8")
    terms = term_candidates(res["segments"])
    (out.parent / "term_candidates.tsv").write_text(
        "term\tcount\n" + "\n".join(f"{t['term']}\t{t['count']}" for t in terms) + "\n",
        encoding="utf-8")
    print(json.dumps({"ok": True, "pages": res["n_pages"], "paras": res["n_paras"],
                      "segments": res["n_segs"], "terms": len(terms), "out": str(out)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
