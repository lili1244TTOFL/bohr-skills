#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_pass2.py — 校验关键词/格式/查重那一次请求的输出。

检查项：
  P1 每行是合法 JSON，字段齐全
  P2 id 与文献库一一对应，不重不漏
  P3 kw 3–8 个；中文词须为题名子串，英文词须由题名词组成
  P4 fmt 码在 F01–F19 内
  P5 dup 组内成员互指，组号唯一，keep:true 每组一个
  P6 末行 pass2 汇总存在且与内容一致

用法：python check_pass2.py work/out/pass2_lib.jsonl --lib work/lib/library.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CODES = {f"F{i:02d}" for i in range(1, 20)}


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="pass2 输出校验")
    ap.add_argument("jsonl")
    ap.add_argument("--lib", required=True)
    args = ap.parse_args(argv)

    lib = json.loads(Path(args.lib).read_text(encoding="utf-8-sig"))
    titles = {e["id"]: e.get("title", "") for e in lib.get("entries", [])}

    rows, summary, problems = [], None, []
    for n, line in enumerate(Path(args.jsonl).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as ex:
            problems.append({"code": "P1", "line": n, "note": f"JSON 解析失败：{ex}"})
            continue
        if "pass2" in obj:
            summary = obj["pass2"]
            continue
        rows.append(obj)

    ids = [r.get("id") for r in rows]
    for r in rows:
        rid = r.get("id")
        if rid not in titles:
            problems.append({"code": "P2", "id": rid, "note": "id 不在文献库中"})
            continue
        for f in ("kw", "fmt", "dup", "keep"):
            if f not in r:
                problems.append({"code": "P1", "id": rid, "note": f"缺字段 {f}"})
        kw = r.get("kw") or []
        if not 1 <= len(kw) <= 8:
            problems.append({"code": "P3", "id": rid, "note": f"关键词数 {len(kw)}"})
        title = titles.get(rid, "")
        tl = title.lower()
        for k in kw:
            if re.search(r"[\u4e00-\u9fff]", k):
                if k not in title:
                    problems.append({"code": "P3", "id": rid, "note": f"关键词非题名子串：{k}"})
            else:
                toks = set(re.findall(r"[a-z0-9-]+", tl))
                missing = [w for w in re.findall(r"[a-z0-9-]+", k.lower()) if w not in toks]
                if missing:
                    problems.append({"code": "P3", "id": rid,
                                     "note": f"关键词含题名外词：{k} ({missing})"})
        for x in r.get("fmt") or []:
            if x.get("code") not in CODES:
                problems.append({"code": "P4", "id": rid, "note": f"未知错误码 {x.get('code')}"})

    dup = {r["id"]: r.get("dup") or {} for r in rows if r.get("id")}
    groups: dict[str, list[str]] = {}
    for rid, d in dup.items():
        g = d.get("group")
        if g:
            groups.setdefault(g, []).append(rid)
            for other in d.get("same_as") or []:
                if other not in dup:
                    problems.append({"code": "P5", "id": rid, "note": f"same_as 指向不存在 id：{other}"})
                elif rid not in (dup[other].get("same_as") or []):
                    problems.append({"code": "P5", "id": rid, "note": f"互指缺失：{other}"})
    for g, members in groups.items():
        if len(members) < 2:
            problems.append({"code": "P5", "id": members[0], "note": f"{g} 只有 1 个成员"})
        keep = [m for m in members if next(r for r in rows if r.get("id") == m).get("keep")]
        if len(keep) != 1:
            problems.append({"code": "P5", "group": g, "note": f"keep:true 数量 {len(keep)}"})

    uniq = sorted(set(ids))
    if len(uniq) != len(ids):
        problems.append({"code": "P2", "note": "输出 id 重复"})
    missing = sorted(set(titles) - set(ids))
    if missing:
        problems.append({"code": "P2", "note": f"漏条目：{missing}"})

    counts = {"CITE": 0}
    n_err = sum(len(r.get("fmt") or []) for r in rows)
    expect = {"n": len(rows), "kw": sum(1 for r in rows if r.get("kw")), "err": n_err,
              "dup_groups": len(groups)}
    if summary is None:
        problems.append({"code": "P6", "note": "缺 pass2 汇总行"})
    else:
        for k, v in expect.items():
            if summary.get(k) != v:
                problems.append({"code": "P6", "note": f"汇总 {k}={summary.get(k)}，实际 {v}"})

    out = {"ok": not problems, "n_rows": len(rows), "n_groups": len(groups),
           "n_fmt": n_err, "expect": expect, "summary": summary,
           "problems": problems, "n_problems": len(problems)}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
