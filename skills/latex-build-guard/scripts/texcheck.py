#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""texcheck.py — LaTeX 工程静态检查（T01–T18），不依赖 TeX 发行版。

两种入口：
  单文件（向后兼容）：
      python texcheck.py --tex paper.tex --bib refs.bib --out work/checks.json
  整个工程（多文件、跨文件解析 label/ref/cite）：
      python texcheck.py --input proj --main main.tex --out work/checks.json

每条问题带 `file` 与 `line`，可直接当证据回指源码；跨文件的 \\label/\\ref/\\cite
在工程范围内统一解析，避免把"引用在 A 文件、定义在 B 文件"误报成 T02。

检查项（判据见 rules/codes.md）：
  T01 label 重复                     T10 子章节早于 section
  T02 ref 指向不存在的 label         T11 表格行列数不一致
  T03 label 未被引用（info）         T12 数学定界符不配对
  T04 cite 键不在 bib                T13 label 位置错误
  T05 bib 条目未被引用（info）       T14 bib 条目缺必填字段
  T06 figure/table 缺 caption        T15 bib 键重复
  T07 环境不配对                     T16 模板必填项缺失
  T08 花括号不配对                   T17 参考文献资源缺失
  T09 图片文件不存在                 T18 主文件结构缺项
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# code -> (一句话说明, 默认 severity)。判据与阈值以本表为准，rules/codes.md 是同源说明。
CODES: dict[str, tuple[str, str]] = {
    "T01": ("label 重复", "error"),
    "T02": ("ref 指向不存在的 label", "error"),
    "T03": ("label 未被引用", "info"),
    "T04": ("cite 键不在 bib", "error"),
    "T05": ("bib 条目未被引用", "info"),
    "T06": ("figure/table 缺 caption", "error"),
    "T07": ("环境不配对", "error"),
    "T08": ("花括号不配对", "error"),
    "T09": ("图片文件不存在", "error"),
    "T10": ("子章节早于 section", "error"),
    "T11": ("表格行列数不一致", "error"),
    "T12": ("数学定界符不配对", "error"),
    "T13": ("label 位置错误", "warn"),
    "T14": ("bib 条目缺必填字段", "warn"),
    "T15": ("bib 键重复", "warn"),
    "T16": ("模板必填项缺失", "warn"),
    "T17": ("参考文献资源缺失", "error"),
    "T18": ("主文件结构缺项", "error"),
}

LABEL = re.compile(r"\\label\{([^}]*)\}")
REF = re.compile(r"\\(?:ref|eqref|autoref|cref|Cref|pageref|nameref)\{([^}]*)\}")
CITE = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}")
GRAPHIC = re.compile(r"\\includegraphics\*?(?:\[[^\]]*\])?\{([^}]*)\}")
SECTION = re.compile(r"\\(section|subsection|subsubsection|chapter|part)\*?\{")
CAPTION = re.compile(r"\\caption\*?(?:\[[^\]]*\])?\{")
FLOAT_ENVS = ("figure", "table", "figure*", "table*")
STAR_MATH = ("equation*", "align*", "gather*", "multline*", "eqnarray*", "flalign*", "alignat*")
TABLE_ENVS = ("tabular", "tabular*", "tabularx", "longtable", "longtable*")
VERB_ENVS = ("verbatim", "verbatim*", "Verbatim", "Verbatim*", "lstlisting", "minted",
             "comment", "alltt", "filecontents", "filecontents*")
IMG_EXT = ("", ".pdf", ".png", ".jpg", ".jpeg", ".eps", ".ps", ".svg")
BIB_REQUIRED = {
    "article": ("author", "title", "journal", "year"),
    "book": ("author", "title", "publisher", "year"),
    "inproceedings": ("author", "title", "booktitle", "year"),
    "conference": ("author", "title", "booktitle", "year"),
    "incollection": ("author", "title", "booktitle", "year"),
    "phdthesis": ("author", "title", "school", "year"),
    "mastersthesis": ("author", "title", "school", "year"),
    "techreport": ("author", "title", "institution", "year"),
    "misc": ("title",),
    "online": ("title",),
    "unpublished": ("author", "title", "note"),
}


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


# ---------------------------------------------------------------- 文本预处理
def _blank_span(chars: list[str], a: int, b: int) -> None:
    for i in range(a, min(b, len(chars))):
        if chars[i] not in "\r\n":
            chars[i] = " "


def _verb_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for env in VERB_ENVS:
        pat = re.compile(r"\\begin\{" + re.escape(env) + r"\}.*?\\end\{" + re.escape(env) + r"\}", re.S)
        spans += [(m.start(), m.end()) for m in pat.finditer(text)]
    # \verb|...|（单行）；\verb* 同
    for m in re.finditer(r"\\verb\*?([^A-Za-z\s*])([^\n]*?)\1", text):
        spans.append((m.start(), m.end()))
    return spans


def mask(text: str) -> str:
    """把注释、verbatim 类环境与 \\verb 内容抹成空格（保留换行），行号不变。

    注释与 verbatim 里的花括号/环境/数学符不该参与结构检查：老版本 T08 直接数字符，
    遇到注释就误报（SKILL.md 缺陷 e）。mask 之后所有结构检查都只看到有效源码。
    """
    chars = list(text)
    for a, b in _verb_spans(text):
        _blank_span(chars, a, b)
    lines = "".join(chars).split("\n")
    for i, ln in enumerate(lines):
        j = 0
        while True:
            j = ln.find("%", j)
            if j < 0:
                break
            k, nb = j - 1, 0
            while k >= 0 and ln[k] == "\\":
                nb += 1
                k -= 1
            if nb % 2 == 0:  # 未被转义的 % 才是注释
                lines[i] = ln[:j] + " " * (len(ln) - j)
                break
            j += 1
    return "\n".join(lines)


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _groups(text: str, i: int = 0) -> list[tuple[int, int, int, int]]:
    """从 i 起逐个取平衡花括号组：(起, 止, 内容起, 内容止)。跳过 \\x 转义。"""
    out: list[tuple[int, int, int, int]] = []
    n, i = len(text), max(i, 0)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth, j = 1, i + 1
            while j < n and depth:
                ch = text[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                j += 1
            out.append((i, j, i + 1, j - 1))
            i = j
            continue
        i += 1
    return out


def _depth_scan(text: str) -> tuple[int, int]:
    """花括号净值与首次走负的行号（0 表示没走负）。"""
    depth, i, neg = 0, 0, 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0 and not neg:
                neg = line_of(text, i)
        i += 1
    return depth, neg


def _math_counts(text: str) -> dict[str, int]:
    """数数学定界符：$ / \\[ \\] / \\( \\)。按 token 走，\\$ 与 \\\\[2pt] 不误计。"""
    cnt = {"dollar": 0, "lb": 0, "rb": 0, "lp": 0, "rp": 0}
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt.isalpha():
                i += 2
                while i < n and text[i].isalpha():
                    i += 1
                continue
            if nxt == "[":
                cnt["lb"] += 1
            elif nxt == "]":
                cnt["rb"] += 1
            elif nxt == "(":
                cnt["lp"] += 1
            elif nxt == ")":
                cnt["rp"] += 1
            i += 2
            continue
        if c == "$":
            cnt["dollar"] += 1
        i += 1
    return cnt


def _col_count(spec: str) -> int:
    """列声明里真实列数：l c r X S D 各 1；p/m/b/w/W 后跟宽度组算 1；*{n}{...} 展开。"""
    n, i = 0, 0
    while i < len(spec):
        c = spec[i]
        if c in "lcrXSD":
            n += 1
            i += 1
        elif c in "pmbwW":
            n += 1
            j = spec.find("{", i)
            grp = _groups(spec, j) if j >= 0 else []
            i = grp[0][1] if grp else i + 1
        elif c in ">@!<":
            j = spec.find("{", i)
            grp = _groups(spec, j) if j >= 0 else []
            i = grp[0][1] if grp else i + 1
        elif c == "*":
            grp = _groups(spec, spec.find("{", i))
            if len(grp) >= 2:
                try:
                    k = int(spec[grp[0][2]:grp[0][3]].strip())
                except ValueError:
                    k = 1
                n += k * _col_count(spec[grp[1][2]:grp[1][3]])
                i = grp[1][1]
            else:
                i += 1
        else:
            i += 1
    return n


def _rows(body: str) -> list[tuple[int, str]]:
    """按深度 0 的 \\\\ 切表行，返回 (行内偏移, 行文本)。"""
    out: list[tuple[int, str]] = []
    depth, start, i, n = 0, 0, 0, len(body)
    while i < n:
        c = body[i]
        if c == "\\" and i + 1 < n and body[i + 1] == "\\" and depth == 0:
            out.append((start, body[start:i]))
            i += 2
            start = i
            continue
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    out.append((start, body[start:]))
    return out


def _cells(row: str) -> int:
    depth, cells, i, n = 0, 1, 0, len(row)
    while i < n:
        c = row[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "&" and depth == 0:
            cells += 1
        i += 1
    return cells


def _is_rule_row(row: str) -> bool:
    s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", " ", row)
    s = re.sub(r"[\[\]()\s\d.,:;+\-*/=]", "", s)
    return s == ""


# ---------------------------------------------------------------- .bib 解析
def _bib_entries(files: list[tuple[str, str]]) -> list[dict]:
    entries: list[dict] = []
    for rel, text in files:
        clean = "\n".join(ln.split("%")[0] if not ln.lstrip().startswith("%") else ""
                          for ln in text.split("\n"))
        for m in re.finditer(r"@(\w+)\s*\{", clean):
            typ = m.group(1).lower()
            if typ in ("comment", "string", "preamble"):
                continue
            grp = _groups(clean, m.end() - 1)
            if not grp:
                continue
            a, b, cs, ce = grp[0]
            body = clean[cs:ce]
            key = body.split(",")[0].strip()
            fields = set()
            depth, i, n, cur = 0, 0, len(body), ""
            word = ""
            for i, ch in enumerate(body):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                elif ch == "=" and depth == 0 and not cur:
                    cur = word.strip().lower()
                    word = ""
                    fields.add(cur)
                    continue
                elif ch == "," and depth == 0:
                    cur, word = "", ""
                    continue
                if ch.isalnum() or ch in "_-":
                    word += ch
                elif depth == 0 and ch in " \t\n":
                    pass
            entries.append({"type": typ, "key": key, "fields": fields,
                            "file": rel, "line": line_of(clean, m.start())})
    return entries


def bib_keys(p: Path | None) -> set[str]:
    """向后兼容：只取键集合。"""
    if not p or not Path(p).exists():
        return set()
    return {e["key"] for e in _bib_entries([(Path(p).name,
                                            Path(p).read_text(encoding="utf-8-sig",
                                                              errors="replace"))])}


# ---------------------------------------------------------------- 模板必填项
def _profiles(package_root: Path | None) -> dict:
    if package_root is None:
        return {}
    f = package_root / "rules" / "templates.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _pick_profile(profiles: dict, cls: str) -> tuple[str, dict]:
    prof = profiles.get("profiles", {})
    if cls in prof:
        return cls, prof[cls]
    low = cls.lower()
    for name, p in prof.items():
        if name == "generic":
            continue
        if low.startswith(name.lower()) or name.lower() in low:
            return name, p
    return "generic", prof.get("generic", {})


def _check_template(main_rel: str, text: str, profiles: dict) -> list[dict]:
    """T16：文档类必填项逐项比对。只在能读到 \\documentclass 时做（片段稿不比模板）。

    generic 骨架项与具体文档类的额外必填项都要比：article 这类"没有额外项"的类，
    也仍然要求有标题/作者/摘要，否则模板必填项就是空的。
    """
    m = re.search(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", text)
    if not m or not profiles:
        return []
    cls = m.group(1).strip()
    name, prof = _pick_profile(profiles, cls)
    reqs: list[dict] = list(profiles.get("profiles", {}).get("generic", {}).get("requires", []))
    seen = {r.get("pattern") for r in reqs}
    for r in prof.get("requires", []):
        if r.get("pattern") not in seen:
            reqs.append(r)
            seen.add(r.get("pattern"))
    out: list[dict] = []
    for req in reqs:
        pat = req.get("pattern", "")
        if not pat:
            continue
        if re.search(pat, text, re.M):
            continue
        out.append({"code": "T16", "severity": req.get("severity", "warn"),
                    "file": main_rel, "line": line_of(text, m.start()),
                    "detail": f"模板必填项缺失[{name}] {req.get('name', req.get('id', '?'))}："
                              f"未见 {req.get('show', pat)}",
                    "hint": req.get("hint", ""),
                    "basis": req.get("basis", "")})
    return out


# ---------------------------------------------------------------- 主检查
def _run(files: list[tuple[str, str]] | dict[str, str], bibs: list[tuple[str, str]],
         root: Path, main_rel: str | None, package_root: Path | None = None,
         graphicspath: list[str] | None = None) -> dict:
    if isinstance(files, dict):  # 也接受 {相对路径: 文本}
        files = list(files.items())
    problems: list[dict] = []
    masked = {rel: mask(txt) for rel, txt in files}
    main_dir = Path(main_rel).parent if main_rel else Path(".")
    gp = graphicspath or []

    labels: list[tuple[str, str, int]] = []
    refs: list[tuple[str, str, int]] = []
    cites: list[tuple[str, str, int]] = []
    graphics: list[tuple[str, str, int]] = []
    for rel, _txt in files:
        t = masked[rel]
        labels += [(m.group(1).strip(), rel, line_of(t, m.start())) for m in LABEL.finditer(t)]
        refs += [(m.group(1).strip(), rel, line_of(t, m.start())) for m in REF.finditer(t)]
        for m in CITE.finditer(t):
            for k in m.group(1).split(","):
                if k.strip():
                    cites.append((k.strip(), rel, line_of(t, m.start())))
        graphics += [(m.group(1).strip(), rel, line_of(t, m.start())) for m in GRAPHIC.finditer(t)]

    bib_entries: list[dict] = _bib_entries(bibs) if bibs else []
    bib_set = {e["key"] for e in bib_entries}

    # T01 label 重复
    seen: dict[str, tuple[str, int]] = {}
    for key, rel, ln in labels:
        if key in seen:
            f0, l0 = seen[key]
            problems.append({"code": "T01", "file": rel, "line": ln,
                             "detail": f"label 重复：{key}（首次在 {f0}:{l0}）"})
        else:
            seen[key] = (rel, ln)
    # T02 / T03 跨文件解析
    label_set = {k for k, _r, _l in labels}
    first_ref: dict[str, tuple[str, int]] = {}
    for key, rel, ln in refs:
        first_ref.setdefault(key, (rel, ln))
    for key, (rel, ln) in first_ref.items():
        if key not in label_set:
            n = sum(1 for k, _r, _l in refs if k == key)
            problems.append({"code": "T02", "file": rel, "line": ln,
                             "detail": f"引用不存在：{key}（共 {n} 处；工程内 {len(label_set)} 个 label）"})
    first_lab: dict[str, tuple[str, int]] = {}
    for key, rel, ln in labels:
        first_lab.setdefault(key, (rel, ln))
    ref_set = {k for k, _r, _l in refs}
    for key, (rel, ln) in first_lab.items():
        if key not in ref_set:
            problems.append({"code": "T03", "severity": "info", "file": rel, "line": ln,
                             "detail": f"label 未被引用：{key}"})
    # T04 / T05
    if bib_set:
        first_cite: dict[str, tuple[str, int]] = {}
        for key, rel, ln in cites:
            first_cite.setdefault(key, (rel, ln))
        for key, (rel, ln) in first_cite.items():
            if key not in bib_set:
                problems.append({"code": "T04", "file": rel, "line": ln,
                                 "detail": f"cite 键不在 bib：{key}（bib 共 {len(bib_set)} 条）"})
        cite_set = {k for k, _r, _l in cites}
        for e in bib_entries:
            if e["key"] not in cite_set:
                problems.append({"code": "T05", "severity": "info", "file": e["file"],
                                 "line": e["line"], "detail": f"bib 条目未被引用：{e['key']}"})
    # T15 bib 键重复（先报，T14 逐条再查字段）
    by_key: dict[str, list[dict]] = {}
    for e in bib_entries:
        by_key.setdefault(e["key"], []).append(e)
    for key, es in by_key.items():
        if len(es) > 1:
            locs = "、".join(f"{e['file']}:{e['line']}" for e in es)
            problems.append({"code": "T15", "file": es[-1]["file"], "line": es[-1]["line"],
                             "detail": f"bib 键重复：{key}（{locs}）"})
    # T14 bib 必填字段
    for e in bib_entries:
        need = BIB_REQUIRED.get(e["type"], ())
        miss = [f for f in need if f not in e["fields"]]
        if miss:
            problems.append({"code": "T14", "file": e["file"], "line": e["line"],
                             "detail": f"bib 条目缺必填字段：@{e['type']}{{{e['key']}}} 缺 "
                                       f"{'/'.join(miss)}"})

    # 逐文件：T06 T07 T08 T09 T11 T12 T13 T18
    for rel, _txt in files:
        t = masked[rel]
        # T06 浮动体缺 caption
        for env in FLOAT_ENVS:
            for m in re.finditer(r"\\begin\{" + re.escape(env) + r"\}(.*?)\\end\{"
                                 + re.escape(env) + r"\}", t, re.S):
                if not CAPTION.search(m.group(1)):
                    problems.append({"code": "T06", "file": rel, "line": line_of(t, m.start()),
                                     "detail": f"{env} 缺 caption"})
        # T07 环境配对
        stack: list[tuple[str, int]] = []
        bad = False
        for m in re.finditer(r"\\(begin|end)\{([^}]+)\}", t):
            name = m.group(2)
            if m.group(1) == "begin":
                stack.append((name, line_of(t, m.start())))
            else:
                if not stack or stack[-1][0] != name:
                    top = stack[-1][0] if stack else "（空）"
                    problems.append({"code": "T07", "file": rel, "line": line_of(t, m.start()),
                                     "detail": f"环境不配对：\\end{{{name}}} 与栈顶 begin{{{top}}}"})
                    bad = True
                    break
                stack.pop()
        if stack and not bad:
            name, ln = stack[-1]
            problems.append({"code": "T07", "file": rel, "line": ln,
                             "detail": f"未闭合环境：{name}"})
        # T08 花括号
        depth, neg = _depth_scan(t)
        if depth != 0:
            problems.append({"code": "T08", "file": rel,
                             "line": neg or (t.count("\n") + 1),
                             "detail": f"花括号不配对（净值 {depth:+d}）"})
        # T09 图片（含 \\graphicspath）
        for name, grel, gln in [g for g in graphics if g[1] == rel]:
            bases = [Path(name)] if Path(name).is_absolute() else \
                [main_dir / name, root / name, Path(rel).parent / name] + \
                [root / str(d) / name for d in gp]
            hit = False
            for b in bases:
                for ext in IMG_EXT:
                    cand = b if (ext == "" or b.suffix.lower() == ext.lower()) else \
                        b.with_suffix(ext)
                    if cand.is_file():
                        hit = True
                        break
                if hit:
                    break
            if not hit:
                problems.append({"code": "T09", "file": rel, "line": gln,
                                 "detail": f"图片不存在：{name}"
                                           + (f"（查过 graphicspath {gp}）" if gp else "")})
        # T11 表格行列数
        for env in TABLE_ENVS:
            for m in re.finditer(r"\\begin\{" + re.escape(env) + r"\}", t):
                start = m.end()
                grps = [g for g in _groups(t, start) if g[0] < start + 400]
                spec_i, width = -1, False
                if env in ("tabular*", "tabularx") and len(grps) >= 2:
                    spec_i, width = 1, True
                elif grps:
                    spec_i = 0
                if spec_i < 0:
                    continue
                spec = t[grps[spec_i][2]:grps[spec_i][3]]
                ncol = _col_count(spec)
                endm = re.search(r"\\end\{" + re.escape(env) + r"\}", t[grps[spec_i][1]:])
                if not endm or not ncol:
                    continue
                body_start = grps[spec_i][1]
                body = t[body_start:body_start + endm.start()]
                bad_rows: list[str] = []
                for off, row in _rows(body):
                    if "\\multicolumn" in row or _is_rule_row(row):
                        continue
                    cells = _cells(row)
                    if cells != ncol:
                        bad_rows.append(f"第 {line_of(t, body_start + off)} 行 {cells} 列")
                if bad_rows:
                    problems.append({"code": "T11", "file": rel, "line": line_of(t, m.start()),
                                     "detail": f"{env} 列数不符：声明 {ncol} 列，"
                                               + "；".join(bad_rows[:5])})
        # T12 数学定界符
        mc = _math_counts(t)
        if (mc["dollar"] % 2) or mc["lb"] != mc["rb"] or mc["lp"] != mc["rp"]:
            problems.append({"code": "T12", "file": rel, "line": t.count("\n") + 1,
                             "detail": "数学定界符不配对：$ 出现 "
                                       f"{mc['dollar']} 次{'（奇数）' if mc['dollar'] % 2 else ''}、"
                                       f"\\[ {mc['lb']} / \\] {mc['rb']}、"
                                       f"\\( {mc['lp']} / \\) {mc['rp']}"})
        # T13 label 位置：浮动体内 label 在 caption 之前；星号数学环境里的 label
        for env in FLOAT_ENVS:
            for m in re.finditer(r"\\begin\{" + re.escape(env) + r"\}(.*?)\\end\{"
                                 + re.escape(env) + r"\}", t, re.S):
                body = m.group(1)
                lab = LABEL.search(body)
                cap = CAPTION.search(body)
                if lab and cap and lab.start() < cap.start():
                    problems.append({"code": "T13", "file": rel,
                                     "line": line_of(t, m.start(1) + lab.start()),
                                     "detail": f"浮动体内 \\label 在 \\caption 之前（{env}）："
                                               "交叉引用会取到上一个编号"})
        for env in STAR_MATH:
            for m in re.finditer(r"\\begin\{" + re.escape(env) + r"\}(.*?)\\end\{"
                                 + re.escape(env) + r"\}", t, re.S):
                if LABEL.search(m.group(1)):
                    problems.append({"code": "T13", "file": rel, "line": line_of(t, m.start()),
                                     "detail": f"星号（无编号）环境 {env} 内有 \\label：引用拿不到编号"})
        # T17 参考文献资源
        for m in re.finditer(r"\\(?:bibliography|addbibresource)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", t):
            for name in m.group(1).split(","):
                nm = name.strip()
                if not nm:
                    continue
                cand = [Path(nm)] if Path(nm).is_absolute() else [root / nm, main_dir / nm]
                cand = [c if c.suffix else c.with_suffix(".bib") for c in cand]
                if not any(c.is_file() for c in cand):
                    problems.append({"code": "T17", "file": rel, "line": line_of(t, m.start()),
                                     "detail": f"参考文献资源缺失：{nm}（查过 "
                                               + "、".join(str(c) for c in cand) + "）"})
        if re.search(r"\\printbibliography", t) and not re.search(
                r"\\addbibresource|\\bibliography\s*\{", t):
            problems.append({"code": "T17", "file": rel,
                             "line": line_of(t, re.search(r"\\printbibliography", t).start()),
                             "detail": "声明了 \\printbibliography 但未指定 .bib 资源"})

    # T10 子章节早于 section：只对主文件（或独立成文的文件）判
    for rel, _txt in files:
        if main_rel and rel != main_rel and "\\documentclass" not in masked[rel]:
            continue
        t = masked[rel]
        seen_section = False
        for m in SECTION.finditer(t):
            lvl = m.group(1)
            if lvl in ("section", "chapter", "part"):
                seen_section = True
            elif not seen_section:
                problems.append({"code": "T10", "file": rel, "line": line_of(t, m.start()),
                                 "detail": f"子章节 \\{lvl} 出现在 \\section 之前"})
                break
    # T16 模板必填项 + T18 主文件结构
    if main_rel and main_rel in masked:
        t = masked[main_rel]
        problems += _check_template(main_rel, t, _profiles(package_root))
        has_cls = bool(re.search(r"\\documentclass", t))
        has_begin = re.search(r"\\begin\{document\}", t)
        has_end = re.search(r"\\end\{document\}", t)
        if has_begin and not has_cls:
            problems.append({"code": "T18", "file": main_rel,
                             "line": line_of(t, has_begin.start()),
                             "detail": "有 \\begin{document} 但缺 \\documentclass：编译必失败"})
        if not has_begin:
            problems.append({"code": "T18", "severity": "warn", "file": main_rel,
                             "line": 1,
                             "detail": "主文件缺 \\begin{document}（片段稿？并入稿件前需确认）"})
        elif not has_end:
            problems.append({"code": "T18", "file": main_rel,
                             "line": t.count("\n") + 1,
                             "detail": "缺 \\end{document}：编译会报 Unexpected end of file"})

    for p in problems:
        p.setdefault("severity", CODES.get(p["code"], ("", "error"))[1])
    problems.sort(key=lambda p: (list(masked).index(p["file"]) if p["file"] in masked else 99,
                                 p.get("line", 0), p["code"]))
    counts: dict[str, int] = {}
    for p in problems:
        counts[p["code"]] = counts.get(p["code"], 0) + 1
    n_err = sum(1 for p in problems if p.get("severity") == "error")
    n_warn = sum(1 for p in problems if p.get("severity") == "warn")
    n_info = sum(1 for p in problems if p.get("severity") == "info")
    # 来源映射：每个 label/cite/图片 在哪个文件哪一行出现，引用它的地方在哪里。
    # 这是 result.json「事实与来源映射」的原料：引用与图表都能逐条回溯。
    ref_sites: dict[str, list[str]] = {}
    for key, rel, ln in refs:
        ref_sites.setdefault(key, []).append(f"{rel}:{ln}")
    cite_sites: dict[str, list[str]] = {}
    for key, rel, ln in cites:
        cite_sites.setdefault(key, []).append(f"{rel}:{ln}")
    source_map = {
        "labels": [{"key": k, "def": f"{r}:{l}", "refs": ref_sites.get(k, [])[:8],
                    "n_refs": len(ref_sites.get(k, []))}
                   for k, (r, l) in sorted(first_lab.items())][:300],
        "cites": [{"key": k, "cited_at": v[:8], "n_cite_sites": len(v),
                   "in_bib": (k in bib_set) if bib_set else None,
                   "bib_def": next((f"{e['file']}:{e['line']}" for e in bib_entries
                                    if e["key"] == k), None)}
                  for k, v in sorted(cite_sites.items())][:300],
        "graphics": [{"ref": n, "at": f"{r}:{l}",
                      "exists": _img_exists(n, root, main_dir, gp)}
                     for n, r, l in graphics][:200],
        "bib": [{"key": e["key"], "type": e["type"], "def": f"{e['file']}:{e['line']}",
                 "fields": sorted(e["fields"])[:12]} for e in bib_entries][:300],
    }
    return {"ok": n_err == 0, "n_problems": len(problems), "n_error": n_err,
            "n_warn": n_warn, "n_info": n_info, "n_fatal": n_err,
            "counts": counts, "problems": problems, "n_files": len(files),
            "n_bib_entries": len(bib_entries), "map": source_map}


def _img_exists(name: str, root: Path, main_dir: Path, gp: list[str]) -> bool:
    if Path(name).is_absolute():
        bases = [Path(name)]
    else:
        bases = [main_dir / name, root / name] + [root / str(d) / name for d in gp]
    for b in bases:
        for ext in IMG_EXT:
            cand = b if (ext == "" or b.suffix.lower() == ext.lower()) else b.with_suffix(ext)
            if cand.is_file():
                return True
    return False


def check_project(files: list[tuple[str, str]], bib_files: list[tuple[str, str]] | None = None,
                  root: Path | str = ".", main: str | None = None,
                  package_root: Path | str | None = None,
                  graphicspath: list[str] | None = None) -> dict:
    """整个工程一遍查完；files = [(相对路径, 文本)]。"""
    return _run(files, bib_files or [], Path(root), main,
                Path(package_root) if package_root else None, graphicspath)


def check(tex: str, keys: set[str], base: Path) -> list[dict]:
    """向后兼容单文件入口：返回问题列表（带 file/line）。"""
    bibs = [("<bib>", "\n".join(f"@{'misc'}{{{k}, title={{{k}}}}}" for k in sorted(keys)))] \
        if keys else []
    res = _run([("<input>", tex)], bibs, Path(base), "<input>")
    return res["problems"]


# ---------------------------------------------------------------- CLI
def _load_project(inp: Path, main: str | None) -> tuple[list, list, str | None, list, Path]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import project as pj  # 同目录模块：只在这里用，避免运行期硬依赖
    info = pj.discover(inp, main)
    root = Path(info["root"])
    files = [(f["rel"], (root / f["rel"]).read_text(encoding="utf-8-sig", errors="replace"))
             for f in info["files"]]
    bibs = [(b["rel"], (root / b["rel"]).read_text(encoding="utf-8-sig", errors="replace"))
            for b in info["bibs"]]
    return files, bibs, info["main"], info.get("graphicspath", []), root


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="LaTeX 工程静态检查")
    ap.add_argument("--tex", default=None, help="单文件模式")
    ap.add_argument("--input", default=None, help="工程目录模式")
    ap.add_argument("--main", default=None, help="主文件相对路径（缺省自动识别）")
    ap.add_argument("--bib", default=None, help="逗号分隔的 .bib 路径")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    pkg_root = Path(__file__).resolve().parent.parent
    if a.input:
        files, bibs, main_rel, gp, root = _load_project(Path(a.input), a.main)
    elif a.tex:
        p = Path(a.tex)
        files = [(p.name, p.read_text(encoding="utf-8-sig", errors="replace"))]
        bibs = []
        if a.bib:
            for b in a.bib.split(","):
                bp = Path(b.strip())
                if bp.is_file():
                    bibs.append((bp.name, bp.read_text(encoding="utf-8-sig", errors="replace")))
        main_rel, gp, root = p.name, [], p.parent
    else:
        print("需要 --tex 或 --input", file=sys.stderr)
        return 2
    res = check_project(files, bibs, root, main_rel, pkg_root, gp)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("ok", "n_problems", "n_error", "n_warn", "counts")},
                     ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
