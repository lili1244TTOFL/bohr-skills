#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""texcheck.py — LaTeX 源码静态检查（不依赖 TeX 发行版）。

检查项：
  T01 label 重复
  T02 \\ref 指向不存在的 label
  T03 label 未被引用（info）
  T04 \\cite 键在 .bib 中不存在
  T05 \\cite 未使用任何条目（info）
  T06 figure/table 缺 caption
  T07 \\begin/\\end 不配对
  T08 花括号不配对
  T09 \\includegraphics 指向的文件不存在
  T10 章节层级跳跃（subsection 出现在 section 之前）
  T11 表格行列数不一致（某行单元格数与表头不同）

用法：
  python texcheck.py --tex paper.tex --bib refs.bib --out work/checks.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LABEL = re.compile(r"\\label\{([^}]+)\}")
REF = re.compile(r"\\(?:ref|eqref|autoref|cref|pageref)\{([^}]+)\}")
CITE = re.compile(r"\\cite[a-z]*\{([^}]+)\}")
BEGIN = re.compile(r"\\begin\{([^}]+)\}")
END = re.compile(r"\\end\{([^}]+)\}")
GRAPHIC = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
SECTION = re.compile(r"\\(section|subsection|subsubsection)\*?\{")
CAPTION = re.compile(r"\\caption\{")
ENVS = ("figure", "table")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def bib_keys(p: Path | None) -> set[str]:
    if not p or not p.exists():
        return set()
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    return set(re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", text))


def check(tex: str, keys: set[str], base: Path) -> list[dict]:
    problems: list[dict] = []
    labels = LABEL.findall(tex)
    refs = REF.findall(tex)
    cites = [c.strip() for grp in CITE.findall(tex) for c in grp.split(",") if c.strip()]

    for lab in {x for x in labels if labels.count(x) > 1}:
        problems.append({"code": "T01", "detail": f"label 重复：{lab}"})
    for r in sorted(set(refs)):
        if r not in set(labels):
            problems.append({"code": "T02", "detail": f"引用不存在：{r}"})
    for lab in sorted(set(labels) - set(refs)):
        problems.append({"code": "T03", "severity": "info", "detail": f"label 未被引用：{lab}"})
    if keys:
        for c in sorted(set(cites) - keys):
            problems.append({"code": "T04", "detail": f"cite 键不在 bib：{c}"})
        for k in sorted(keys - set(cites)):
            problems.append({"code": "T05", "severity": "info", "detail": f"bib 条目未引用：{k}"})

    for env in ENVS:
        for m in re.finditer(r"\\begin\{" + env + r"\}(.*?)\\end\{" + env + r"\}", tex, re.S):
            if not CAPTION.search(m.group(1)):
                problems.append({"code": "T06", "detail": f"{env} 缺 caption"})

    stack: list[str] = []
    for m in re.finditer(r"\\(begin|end)\{([^}]+)\}", tex):
        if m.group(1) == "begin":
            stack.append(m.group(2))
        else:
            if not stack or stack[-1] != m.group(2):
                problems.append({"code": "T07", "detail": f"环境不配对：{m.group(2)}"})
                break
            stack.pop()
    if stack:
        problems.append({"code": "T07", "detail": f"未闭合环境：{stack[-1]}"})

    depth = 0
    for ch in re.sub(r"\\[{}]", "", tex):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth < 0:
            break
    if depth != 0:
        problems.append({"code": "T08", "detail": f"花括号不配对（差 {depth}）"})

    for g in GRAPHIC.findall(tex):
        cand = base / g
        if not (cand.exists() or cand.with_suffix(".pdf").exists()
                or cand.with_suffix(".png").exists()):
            problems.append({"code": "T09", "detail": f"图片不存在：{g}"})

    rank = {"section": 1, "subsection": 2, "subsubsection": 3}
    seen_section = False
    for m in SECTION.finditer(tex):
        lvl = rank[m.group(1)]
        if lvl == 1:
            seen_section = True
        elif not seen_section:
            problems.append({"code": "T10", "detail": "子章节出现在 section 之前"})
            break
    # T11 表格行列数不一致（纯字符串处理，不用正则）
    _b = chr(92) + 'begin{tabular}'
    _e = chr(92) + 'end{tabular}'
    _nl = chr(92) + chr(92)
    for _seg in tex.split(_b)[1:]:
        _head, _, _rest = _seg.partition('}')
        _body = _rest.split(_e)[0]
        _ncol = sum(_head.count(c) for c in 'lcrp')
        _rows = [r for r in _body.split(_nl)
                 if r.strip() and 'hline' not in r]
        _counts = [len(r.split('&')) for r in _rows]
        if len(set(_counts)) > 1:
            problems.append({'code': 'T11',
                             'detail': '表格行列数不一致：各行 ' + str(_counts)
                                       + '，声明 ' + str(_ncol)})
        elif _ncol and _counts and _counts[0] != _ncol:
            problems.append({'code': 'T11',
                             'detail': '表格列数与声明不符：声明 ' + str(_ncol)
                                       + '，实际 ' + str(_counts[0])})

    return problems


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="LaTeX 静态检查")
    ap.add_argument("--tex", required=True)
    ap.add_argument("--bib", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    p = Path(args.tex)
    problems = check(p.read_text(encoding="utf-8-sig", errors="replace"),
                     bib_keys(Path(args.bib) if args.bib else None), p.parent)
    counts: dict[str, int] = {}
    for x in problems:
        counts[x["code"]] = counts.get(x["code"], 0) + 1
    fatal = [x for x in problems if x.get("severity") != "info"]
    out = {"ok": not fatal, "n_problems": len(problems), "n_fatal": len(fatal),
           "counts": counts, "problems": problems}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("ok", "n_problems", "n_fatal", "counts")},
                     ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
