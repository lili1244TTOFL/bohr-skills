#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_pass3.py — 校验句—库匹配结果。

M1 每行四段
M2 CITE 句一一对应，不重不漏
M3 编号均在库内，单句 ≤ 3 个
M4 NONE 与置信度组合合法
M5 依据词非空
M6 末行汇总与内容一致

用法：python check_pass3.py work/zh/pass3_match.txt --pass1 work/zh/pass1_out.txt --lib work/lib/library.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="pass3 校验")
    ap.add_argument("match")
    ap.add_argument("--pass1", required=True)
    ap.add_argument("--lib", required=True)
    args = ap.parse_args(argv)

    lib = json.loads(Path(args.lib).read_text(encoding="utf-8-sig"))
    ids = {e["id"] for e in lib.get("entries", [])}
    cite = [ln.split("|")[0].strip()
            for ln in Path(args.pass1).read_text(encoding="utf-8-sig").splitlines()
            if len(ln.split("|")) >= 4 and ln.split("|")[1].strip() == "CITE"]

    rows, declared, problems = [], {}, []
    for n, line in enumerate(Path(args.match).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#MATCH"):
            for k, v in re.findall(r"(n_assign|n_none|n_new|n)[=:](\d+)", line):
                declared[k] = int(v)
            continue
        f = [x.strip() for x in line.split("|")]
        if len(f) != 4:
            problems.append({"code": "M1", "line": n, "note": f"字段数 {len(f)}"})
            continue
        uid, ids_s, conf, basis = f
        rows.append({"uid": uid, "ids": ids_s, "conf": conf, "basis": basis})

    seen = [r["uid"] for r in rows]
    for r in rows:
        lst = [] if r["ids"] == "NONE" else [x for x in r["ids"].split(",") if x.strip()]
        if r["ids"] == "NONE":
            if r["conf"] not in {"H", "L"}:
                problems.append({"code": "M4", "uid": r["uid"], "note": f"NONE 置信度 {r['conf']}"})
        else:
            if r["conf"] not in {"H", "M", "L"}:
                problems.append({"code": "M4", "uid": r["uid"], "note": f"置信度 {r['conf']}"})
            if len(lst) > 3:
                problems.append({"code": "M3", "uid": r["uid"], "note": f"编号 {len(lst)} 个"})
            for i in lst:
                if i not in ids:
                    problems.append({"code": "M3", "uid": r["uid"], "note": f"库内无 {i}"})
        if not r["basis"]:
            problems.append({"code": "M5", "uid": r["uid"], "note": "缺依据词"})

    missing = [u for u in cite if u not in seen]
    extra = [u for u in seen if u not in cite]
    dup = [u for u in set(seen) if seen.count(u) > 1]
    if missing:
        problems.append({"code": "M2", "note": f"漏句 {missing}"})
    if extra:
        problems.append({"code": "M2", "note": f"非 CITE 句 {extra}"})
    if dup:
        problems.append({"code": "M2", "note": f"重复 {dup}"})

    n_assign = sum(1 for r in rows if r["ids"] != "NONE")
    n_none = sum(1 for r in rows if r["ids"] == "NONE")
    n_new = sum(1 for r in rows if r["ids"] == "NONE" and r["conf"] == "L")
    expect = {"n": len(rows), "n_assign": n_assign, "n_none": n_none, "n_new": n_new}
    for k, v in expect.items():
        if declared.get(k) != v:
            problems.append({"code": "M6", "note": f"汇总 {k}={declared.get(k)}，实际 {v}"})

    out = {"ok": not problems, "n_rows": len(rows), "expect": expect,
           "n_problems": len(problems), "problems": problems}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
