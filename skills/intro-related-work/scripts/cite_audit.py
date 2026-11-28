#!/usr/bin/env python3
"""cite_audit.py —— 论断与引用的一致性审计（纯脚本，不调用模型）。

来历：同一份病稿交给未加载 Skill 的基线模型，它查出四处问题；本脚本把其中两处
可机械判定的做成常驻检查，避免只靠模型偶尔发现。

两类：

    X01 一稿两用：同一引用键既出现在"未解决/不足"的论断里，又出现在"某方法"的论断里
    X02 悬空指向：正文引用 Fig./Table/Equation N，但材料里没有对应图注或章节出现该标记

输出：JSON + 一行摘要。判定只报位置与原句，不改原文。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NEG = re.compile(
    r"(未解决|尚未|不足|缺少|缺乏|无法|没有|未考虑|未覆盖|局限|fails? to|does not|"
    r"lack|insufficient|remain(s)? open|no\s+unified)", re.I)
METHOD = re.compile(r"(提出|采用|使用|基于|通过|propose|use|adopt|based on|present|introduce)", re.I)
KEY = re.compile(r"@([A-Za-z][\w:\-]*)|\{([A-Za-z][\w:\-]*)\}")
MARK = re.compile(r"\b(Fig(?:ure)?\.?\s*\d+|Table\s*\d+|Eq\.?\s*\(?\d+\)?|Section\s*\d+)", re.I)
CAPTION = re.compile(r"^\s*(图|表|Figure|Table)\s*\d+", re.I | re.M)


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？;.!?])\s+|\n+", text)
    return [s.strip() for s in parts if s and s.strip()]


def keys_in(s: str) -> list[str]:
    out = []
    for a, b in KEY.findall(s):
        k = a or b
        if k and not k.lower().startswith(("article", "inproceedings", "book", "misc")):
            out.append(k)
    return out


def audit(text: str) -> dict:
    sents = sentences(text)
    seen = {}
    x01 = []
    for i, s in enumerate(sents, 1):
        ks = keys_in(s)
        if not ks:
            continue
        role = "neg" if NEG.search(s) else ("method" if METHOD.search(s) else None)
        for k in ks:
            seen.setdefault(k, []).append({"line": i, "role": role, "text": s[:80]})
    for k, hits in seen.items():
        roles = {h["role"] for h in hits}
        if "neg" in roles and "method" in roles:
            x01.append({"code": "X01", "key": k,
                        "detail": "同一引用键既作「不足」证据又作「某方法」代表",
                        "hits": hits})

    marks = sorted({m.group(1) for m in MARK.finditer(text)})
    captioned = {m.group(1).strip().lower() for m in CAPTION.finditer(text)}
    x02 = []
    for m in marks:
        num = re.search(r"\d+", m)
        kind = "fig" if m.lower().startswith("fig") else ("tab" if m.lower().startswith("tab") else "other")
        has = any(num and num.group(0) in c and kind[:3] in c for c in captioned)
        if not has and kind in ("fig", "tab"):
            x02.append({"code": "X02", "mark": m,
                        "detail": f"正文引用 {m}，材料里没有对应图注/表题",
                        "line": next((i for i, s in enumerate(sents, 1) if m in s), None)})

    return {"checks": x01 + x02,
            "n_x01": len(x01), "n_x02": len(x02),
            "marks_seen": marks, "captions_seen": sorted(captioned)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="病稿 .md/.txt")
    ap.add_argument("--out", default=None, help="结果 json 路径")
    a = ap.parse_args(argv)
    p = Path(a.input)
    if not p.exists():
        print(json.dumps({"ok": False, "error": f"输入不存在：{p}"}, ensure_ascii=False))
        return 1
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    r = audit(text)
    r["ok"] = True
    body = json.dumps(r, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(body, encoding="utf-8")
    print(json.dumps({"ok": True, "X01": r["n_x01"], "X02": r["n_x02"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
