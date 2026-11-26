#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""captions.py — 图/表/式编号连锁：扫描题注与正文提及，重排后同步改写。

覆盖：
  · 主编号：图 3 / 表 4 / Figure 5 / Table 6
  · 子图：图 3a、图 3b（重排时保留字母）
  · 附录：附表 A1、图 A1、式(A.1)（按前缀字母单独成组，各自从 1 连续）
  · 区间提及：图 3–5（两端都改）

默认 dry-run；--apply 才写 docx。只改数字 span，不动段落其余文字与格式。

用法：
  python captions.py --docx cited.docx --report work/captions.json
  python captions.py --docx cited.docx --out cited_renum.docx --apply
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

LABELS = r"(附表|附图|图|表|Figure|Fig\.?|Table|式)"
CAP = re.compile(rf"^\s*{LABELS}\s*[（(]?\s*([A-Z])?\s*[.．]?\s*([0-9]+)\s*([a-z])?\s*[）)]?")
MENTION = re.compile(
    rf"{LABELS}\s*[（(]?\s*([A-Z])?\s*[.．]?\s*([0-9]+)\s*([a-z])?"
    r"(?:\s*[-–—~]\s*[（(]?\s*([A-Z])?\s*[.．]?\s*([0-9]+))?")
KIND = {"图": "figure", "附图": "figure", "figure": "figure", "fig": "figure",
        "表": "table", "附表": "table", "table": "table", "tab": "table",
        "式": "equation"}


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def kind_of(label: str) -> str:
    return KIND.get(label.strip().lower().rstrip("."), KIND.get(label.strip(), "other"))


def _docx_io():
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("docx_io", here / "docx_io.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _key(kind: str, prefix: str | None) -> str:
    return f"{kind}:{(prefix or '').upper()}"


def scan(doc) -> tuple[list[dict], list[dict]]:
    caps, mentions = [], []
    for i, p in enumerate(doc.paragraphs):
        text = p.text or ""
        m = CAP.match(text)
        if m:
            label, prefix, num, sub = m.group(1), m.group(2), m.group(3), m.group(4)
            caps.append({"para": i, "kind": kind_of(label), "prefix": prefix or "",
                         "num": int(num), "sub": sub or "", "span": [m.start(3), m.end(3)],
                         "text": text[:60]})
            continue
        for mm in MENTION.finditer(text):
            label, prefix, num, sub = mm.group(1), mm.group(2), mm.group(3), mm.group(4)
            mentions.append({"para": i, "kind": kind_of(label), "prefix": prefix or "",
                             "num": int(num), "sub": sub or "",
                             "span": [mm.start(3), mm.end(3)],
                             "range_end": int(mm.group(6)) if mm.group(6) else None})
    return caps, mentions


def plan(caps: list[dict], mentions: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for c in caps:
        groups.setdefault(_key(c["kind"], c["prefix"]), []).append(c)

    mapping, duplicates, gaps = {}, {}, {}
    for key, items in groups.items():
        old = [c["num"] for c in items if not c.get("sub")]
        mp = {}
        for new, c in enumerate(items, 1):
            mp.setdefault(c["num"], new)
        mapping[key] = mp
        dup = sorted({n for n in old if old.count(n) > 1})
        if dup:
            duplicates[key] = dup
        miss = [n for n in range(1, max(old) + 1) if n not in old]
        if miss:
            gaps[key] = miss

    orphan = []
    for m in mentions:
        mp = mapping.get(_key(m["kind"], m["prefix"]), {})
        if m["num"] not in mp:
            orphan.append({"para": m["para"], "kind": m["kind"], "prefix": m["prefix"],
                           "num": m["num"]})
    changed = any(old != new for mp in mapping.values() for old, new in mp.items())
    return {"mapping": mapping, "duplicates": duplicates, "gaps": gaps,
            "orphan_mentions": orphan, "n_captions": len(caps),
            "n_mentions": len(mentions), "n_groups": len(groups), "changed": changed}


def _replace_span(paragraph, start: int, end: int, text: str) -> None:
    io = _docx_io()
    io.delete_range(paragraph, start, end)
    io.insert_superscript(paragraph, start, text, superscript=False)


def apply(doc, caps: list[dict], mentions: list[dict], mapping: dict, out_path: str) -> dict:
    log = []
    by_para: dict[int, list[dict]] = {}
    for m in mentions:
        by_para.setdefault(m["para"], []).append(m)
    for para_idx, items in by_para.items():
        para = doc.paragraphs[para_idx]
        for m in sorted(items, key=lambda x: -x["span"][0]):
            mp = mapping.get(_key(m["kind"], m["prefix"]), {})
            new = mp.get(m["num"])
            if new is None or new == m["num"]:
                continue
            if m.get("range_end") is not None and m["range_end"] not in mp:
                continue
            before = (para.text or "")[:60]
            _replace_span(para, m["span"][0], m["span"][1], str(new))
            log.append({"para": para_idx, "kind": m["kind"], "prefix": m["prefix"],
                        "old": m["num"], "new": new, "before": before})
    for c in sorted(caps, key=lambda x: -x["para"]):
        mp = mapping.get(_key(c["kind"], c["prefix"]), {})
        new = mp.get(c["num"])
        if new is None or new == c["num"]:
            continue
        para = doc.paragraphs[c["para"]]
        _replace_span(para, c["span"][0], c["span"][1], str(new))
        log.append({"para": c["para"], "kind": c["kind"], "prefix": c["prefix"],
                    "old": c["num"], "new": new, "caption": True, "before": c["text"]})
    doc.save(out_path)
    return {"changes": log, "n_changes": len(log)}


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="图表编号连锁")
    ap.add_argument("--docx", required=True)
    ap.add_argument("--out", default=None, help="--apply 时写出的 docx")
    ap.add_argument("--report", default=None, help="扫描/改动报告 JSON")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    from docx import Document
    doc = Document(args.docx)
    caps, mentions = scan(doc)
    info = plan(caps, mentions)
    result = {"ok": True, **info}
    if args.apply and info["changed"]:
        if not args.out:
            ap.error("--apply 需要 --out")
        result.update(apply(doc, caps, mentions, info["mapping"], args.out))
        result["applied"] = True
        result["out"] = args.out
    else:
        result["applied"] = False
    rep = Path(args.report) if args.report else Path(args.docx).with_suffix(".captions.json")
    rep.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "changes"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

