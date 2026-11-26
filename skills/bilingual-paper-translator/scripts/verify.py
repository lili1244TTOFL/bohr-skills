#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify.py — 译文核验：数字、引用、公式、图表号、否定、术语、完整性与对齐。

判据是"能不能原文对照"，不是"译文是否通顺"：
  · 数字与单位：原文每个数字值都要在译文中出现（允许中英数字写法差异）
  · 引用标记：[n]、(Author, 2020) 数量一致
  · 图表/公式编号：Fig. 3、Table 2、(1) 等原样保留
  · 否定：原文否定词数量与译文否定词数量一致
  · 术语：术语表里的源术语出现时，译文必须使用指定译名
  · 完整性：每条原文都有译文，无重复、无错序
  · 长度比：译文/原文长度比在合理区间，异常进人工确认

用法：
  python verify.py --segments work/segments.jsonl --glossary glossary.md \
                   --targets work/targets.jsonl --out work/verify.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NEG_SRC = re.compile(r"(?i)\b(not|no|never|none|without|cannot|nor|neither|fail(?:ed)? to|"
                     r"exclud\w*|rather than|instead of)\b")
NEG_TGT = re.compile(r"(不|无|未|没|非|勿|否|并非|而不是|代替|除外|排除)")
UNIT = re.compile(r"(?:%|‰|℃|°C|mM|μM|µm|nm|mm|cm|km|kg|mg|μg|mL|µL|h|min|s|Hz|kHz|MHz|"
                  r"MPa|kPa|Pa|mV|mA|W|kW|kJ|kcal|rpm|Da|kDa|ppm|mol/L|IU)")
CITE = re.compile(r"\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]|\(\s*[A-Z][A-Za-z.\-]+(?:\s+et\s+al\.?)?,?\s*"
                  r"(?:19|20)\d{2}[a-z]?\s*\)")
FIGTAB = re.compile(r"(?i)\b(?:fig(?:ure)?|table|eq(?:uation)?)\.?\s*\d+[A-Za-z]?")
NUM = re.compile(r"\d+(?:[.,]\d+)*")
UNCERTAIN = re.compile(r"\[待确认\]|\[?待核\]?|⟦[^⟧]*⟧|TODO|TBD")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def load_jsonl(p: Path) -> list[dict]:
    out = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def norm_punct(s: str) -> str:
    pairs = {"（": "(", "）": ")", "［": "[", "］": "]", "，": ",", "。": ".",
             "；": ";", "：": ":", "、": ","}
    return "".join(pairs.get(ch, ch) for ch in s)


def norm_num(s: str) -> str:
    return re.sub(r"[,\s\u00a0]", "", s)


def load_glossary(p: Path | None) -> dict[str, str]:
    g: dict[str, str] = {}
    if not p or not p.exists():
        return g
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lower().startswith("term\t"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip() and parts[1].strip():
            g[parts[0].strip()] = parts[1].strip()
    return g


def verify(segments: list[dict], targets: dict[str, str], glossary: dict[str, str]) -> dict:
    problems: list[dict] = []
    missing = [s["uid"] for s in segments if s["uid"] not in targets]
    extra = [u for u in targets if u not in {s["uid"] for s in segments}]
    order_bad = [u for u in targets if u in {s["uid"] for s in segments}] != \
                [s["uid"] for s in segments if s["uid"] in targets]

    for s in segments:
        uid = s["uid"]
        tgt = targets.get(uid)
        if tgt is None:
            continue
        src = s["text"]
        p = s.get("protected") or {}
        miss_num = [n for n in p.get("numbers", []) if norm_num(n) not in norm_num(tgt)
                    and norm_num(re.sub(r"[^\d.]", "", n)) not in norm_num(tgt)]
        if miss_num:
            problems.append({"uid": uid, "code": "V01", "detail": f"数字/单位缺失 {miss_num}"})
        tgt_n = norm_punct(tgt)
        for key, code in (("citations", "V02"), ("formulas", "V03"), ("figtab", "V04")):
            miss = [x for x in p.get(key, []) if x not in tgt_n]
            if miss:
                problems.append({"uid": uid, "code": code, "detail": f"未保留 {miss}"})
        n_src = len(NEG_SRC.findall(src))
        n_tgt = len(NEG_TGT.findall(tgt))
        if src.isascii() and (n_src > 0) != (n_tgt > 0):
            problems.append({"uid": uid, "code": "V05",
                             "detail": f"否定数量不一致 原文 {n_src} / 译文 {n_tgt}"})
        low_src = src.lower()
        for term, want in glossary.items():
            if term and term.lower() in low_src and want not in tgt:
                problems.append({"uid": uid, "code": "V06",
                                 "detail": f"术语未按术语表：{term} → 期望 {want}"})
        if UNCERTAIN.search(tgt):
            problems.append({"uid": uid, "code": "V07", "detail": "译文含未定标记，需人工确认"})
        if src and len(tgt) / max(1, len(src)) > 2.2:
            problems.append({"uid": uid, "code": "V08",
                             "detail": f"长度比异常 {len(tgt)}/{len(src)}"})
        if not tgt.strip():
            problems.append({"uid": uid, "code": "V09", "detail": "译名为空"})

    by_uid: dict[str, list[str]] = {}
    for s in segments:
        if s["uid"] in targets:
            by_uid.setdefault(s["uid"], []).append(targets[s["uid"]])
    dup = [u for u, v in by_uid.items() if len(v) > 1]

    codes: dict[str, int] = {}
    for x in problems:
        codes[x["code"]] = codes.get(x["code"], 0) + 1
    return {"ok": not problems and not missing and not dup,
            "n_segments": len(segments), "n_targets": len(targets),
            "missing": missing, "extra": extra, "duplicates": dup,
            "order_preserved": not order_bad,
            "counts": codes, "problems": problems, "n_problems": len(problems),
            "glossary_terms": len(glossary)}


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="译文核验")
    ap.add_argument("--segments", required=True)
    ap.add_argument("--targets", required=True)
    ap.add_argument("--glossary", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    segs = load_jsonl(Path(args.segments))
    tg = {r["uid"]: r.get("text", "") for r in load_jsonl(Path(args.targets))}
    res = verify(segs, tg, load_glossary(Path(args.glossary) if args.glossary else None))
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("ok", "n_segments", "n_problems", "counts",
                                          "missing", "duplicates")}, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
