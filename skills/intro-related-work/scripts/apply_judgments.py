#!/usr/bin/env python3
"""apply_judgments.py —— 接收模型填好的分组判断，核验并落成报告。

分工：
    group_worklist.py  出工作单（脚本）
    调用方模型          填 judgment.group / rationale（模型）
    本脚本              核验并落盘（脚本）

核验四项：
    J01 judgment.group 未填            → 该条标 pending
    J02 rationale 不是原句的子串       → 依据不可回指，标回退
    J03 组内引用键在 bib 里不存在       → 引用造假
    J04 单句成组且 rationale 没写理由   → 分组可疑

用法：
    python apply_judgments.py --worklist worklist.json --filled filled.json \
        --bib examples/refs.bib --out judgments_report.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def bib_keys(p: Path | None) -> set[str]:
    if not p or not p.exists():
        return set()
    t = p.read_text(encoding="utf-8-sig", errors="replace")
    return set(re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", t))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worklist", required=True)
    ap.add_argument("--filled", required=True)
    ap.add_argument("--bib", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    wl = json.loads(Path(a.worklist).read_text(encoding="utf-8-sig"))
    fl = json.loads(Path(a.filled).read_text(encoding="utf-8-sig"))
    keys = bib_keys(Path(a.bib)) if a.bib else set()

    base = {it["id"]: it for it in wl.get("items", [])}
    filled = {it["id"]: it for it in fl.get("items", []) if it.get("id")}

    issues, groups = [], {}
    pending = 0
    for sid, it in base.items():
        f = filled.get(sid, {})
        j = (f.get("judgment") or {})
        g, rat = j.get("group"), (j.get("rationale") or "").strip()
        if not g:
            pending += 1
            issues.append({"code": "J01", "id": sid, "detail": "group 未填"})
            continue
        if rat and rat not in it["sentence"]:
            issues.append({"code": "J02", "id": sid,
                           "detail": "rationale 不是原句子串，无法回指"})
        for k in it.get("cite_keys") or []:
            if keys and k not in keys:
                issues.append({"code": "J03", "id": sid,
                               "detail": f"引用键 {k} 不在 bib 里"})
        groups.setdefault(g, []).append({"id": sid, "rationale": rat,
                                         "keys": it.get("cite_keys") or []})

    for g, members in groups.items():
        if len(members) == 1 and not members[0]["rationale"]:
            issues.append({"code": "J04", "group": g,
                           "detail": "单句成组且未写理由"})

    lines = ["# 分组判断核验", "",
             f"工作单条目 {len(base)}，已填 {len(base) - pending}，待填 {pending}", "",
             "| 组 | 句 | 依据 | 引用键 |", "|-|-|-|-|"]
    for g, ms in groups.items():
        for m in ms:
            lines.append(f"| {g} | {m['id']} | {m['rationale'][:40] or '—'} | "
                         f"{', '.join(m['keys']) or '—'} |")
    if issues:
        lines += ["", "## 待处理", "", "| 码 | 位置 | 说明 |", "|-|-|-|"]
        lines += [f"| {i['code']} | {i.get('id') or i.get('group')} | {i['detail']} |"
                  for i in issues]
    text = "\n".join(lines) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(json.dumps({"ok": True, "groups": len(groups), "filled": len(base) - pending,
                      "pending": pending, "issues": len(issues)}, ensure_ascii=False))
    return 0 if pending == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
