#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""project.py — 识别一个 LaTeX 工程：主文件、include 树、文献库、图片路径、引擎。

老版本 `run.py` 用"输入目录下首个 .tex"当主文件：多文件工程会挑到子文件或废弃草稿，
`\\input` 进去的章节根本不进检查，跨文件 `\\ref` 会被误报成"引用不存在"。
本模块按内容而不是文件名判定主文件，并把 `\\input/\\include/\\subfile/\\import` 展开成树。

用法：
    python project.py --input proj --out work/project.json
    python project.py --input proj --main chapters/main.tex --out work/project.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

TEX = ".tex"
INCLUDE_CMDS = ("input", "include", "subfile", "subfileinclude")
IMPORT_CMDS = ("import", "subimport", "inputfrom", "subinputfrom")
ENGINE_BINS = ("latexmk", "pdflatex", "xelatex", "lualatex", "bibtex", "biber", "kpsewhich")
NAME_HINTS = ("main", "paper", "manuscript", "thesis", "article", "root", "document",
              "book", "report", "dissertation", "ms", "preprint", "submission")
VERB_ENVS = ("verbatim", "verbatim*", "lstlisting", "minted", "comment", "alltt",
             "filecontents", "filecontents*")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()[:16]


def mask(text: str) -> str:
    """抹掉注释与 verbatim（保换行），只为了读结构，不改原文。"""
    chars = list(text)
    for env in VERB_ENVS:
        for m in re.finditer(r"\\begin\{" + re.escape(env) + r"\}.*?\\end\{"
                             + re.escape(env) + r"\}", text, re.S):
            for i in range(m.start(), m.end()):
                if chars[i] not in "\r\n":
                    chars[i] = " "
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
            if nb % 2 == 0:
                lines[i] = ln[:j] + " " * (len(ln) - j)
                break
            j += 1
    return "\n".join(lines)


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _resolve(root: Path, base: Path, name: str) -> Path | None:
    name = name.strip().strip('"')
    if not name:
        return None
    cands = []
    p = Path(name)
    if p.is_absolute():
        cands.append(p)
    else:
        cands += [base / name, root / name]
    for c in list(cands):
        if c.suffix.lower() != TEX:
            cands.append(c.with_suffix(TEX))
    for c in cands:
        if c.is_file():
            return c
    return None


def _includes(text: str) -> list[tuple[str, str]]:
    """返回 [(命令, 参数)]，参数已按 \\import 的两段式展开。"""
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"\\(input|include|subfile|subfileinclude)\s*\{([^}]*)\}", text):
        out.append((m.group(1), m.group(2)))
    for m in re.finditer(r"\\(import|subimport|inputfrom|subinputfrom)\s*\{([^}]*)\}"
                         r"\s*\{([^}]*)\}", text):
        d = m.group(2).strip().rstrip("/")
        out.append((m.group(1), f"{d}/{m.group(3)}" if d else m.group(3)))
    return out


def _graphicspath(text: str) -> list[str]:
    m = re.search(r"\\graphicspath\s*\{", text)
    if not m:
        return []
    start = m.end() - 1
    depth, j = 0, start
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    inner = text[start + 1:j]
    dirs: list[str] = []
    i = 0
    while i < len(inner):
        if inner[i] == "{":
            k = inner.find("}", i)
            if k < 0:
                break
            d = inner[i + 1:k].strip()
            if d:
                dirs.append(d)
            i = k + 1
        else:
            i += 1
    return dirs


def _engine(text: str) -> tuple[str, str]:
    """返回 (引擎, 依据)。先看 magic comment，再看宏包信号。"""
    m = re.search(r"%\s*!TEX\s+(?:TS-)?program\s*=\s*([A-Za-z]+)", text)
    if not m:
        m = re.search(r"%\s*!\s*TEX\s+program\s*=\s*([A-Za-z]+)", text)
    if m:
        eng = m.group(1).lower()
        eng = {"xelatex": "xelatex", "pdflatex": "pdflatex", "lualatex": "lualatex",
               "latex": "pdflatex", "pdftex": "pdflatex"}.get(eng, eng)
        return eng, f"magic comment % !TEX program = {m.group(1)}"
    low = mask(text)
    for pats, eng, why in (
            ((r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\bfontspec\b", r"\bxecjk\b", r"\bctex\b",
              r"\bpolyglossia\b", r"\\documentclass(?:\[[^\]]*\])?\{ctex", r"\\setCJKmainfont",
              r"\bunicode-math\b"), "xelatex", "字体/中文宏包需要 XeLaTeX"),
            ((r"\bluatexja\b", r"\\directlua", r"\\luacode", r"\bluatex\b"), "lualatex",
             "Lua 相关宏包需要 LuaLaTeX"),
            ((r"\\pdfoutput", r"\binputenc\b", r"\bpstricks\b"), "pdflatex", "pdfTeX 专用宏包")):
        for p in pats:
            if re.search(p, low, re.I):
                return eng, why
    return "pdflatex", "缺省（无 magic comment 与字体/XeCJK 信号）"


def _packages(text: str) -> list[dict]:
    out: list[dict] = []
    for m in re.finditer(r"\\(?:usepackage|RequirePackage)\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}",
                         text):
        for name in m.group(2).split(","):
            name = name.strip()
            if name:
                out.append({"name": name, "options": (m.group(1) or "").strip(),
                            "line": line_of(text, m.start())})
    return out


def discover(root: Path | str, main_hint: str | None = None) -> dict:
    root = Path(root).resolve()
    tex_files = sorted(p for p in root.rglob("*" + TEX) if p.is_file())
    if not tex_files:
        return {"root": str(root), "main": None, "files": [], "bibs": [], "engine": "pdflatex",
                "engine_reason": "无 .tex", "missing": "没有 .tex 文件", "stats": {"n_tex": 0}}
    texts = {p: read(p) for p in tex_files}
    masked = {p: mask(t) for p, t in texts.items()}

    # 1 include 图：谁被谁引入
    inc_from: dict[Path, list[tuple[Path, str]]] = {p: [] for p in tex_files}
    for p, t in masked.items():
        for cmd, arg in _includes(t):
            q = _resolve(root, p.parent, arg)
            if q and q != p:
                inc_from.setdefault(q, []).append((p, cmd))

    # 2 主文件候选：有 \documentclass 的优先；其次没被别的文件引入的
    mentioned: dict[Path, int] = {}
    for m in re.finditer(r"%\s*!TEX\s+root\s*=\s*([^\s]+)", texts.get(tex_files[0], "")):
        r = _resolve(root, root, m.group(1))
        if r:
            mentioned[r] = mentioned.get(r, 0) + 1
    has_cls = [p for p in tex_files if re.search(r"\\documentclass", masked[p])]
    orphan = [p for p in tex_files if not inc_from.get(p)]
    pool = has_cls or orphan or tex_files
    if main_hint:
        q = _resolve(root, root, main_hint)
        pool = [q] if q else pool

    def score(p: Path) -> tuple:
        t = masked[p]
        s = 0
        if re.search(r"\\documentclass", t):
            s += 4
        if re.search(r"\\begin\{document\}", t):
            s += 3
        if p.stem.lower() in NAME_HINTS:
            s += 2
        if p.parent == root:
            s += 1
        if mentioned.get(p):
            s += 3
        s += min(len(t) / 20000.0, 1.0)  # 大文件更像主文件，最多 +1
        return (s, -len(p.relative_to(root).as_posix()))

    main = max(pool, key=score)

    # 3 从主文件展开 include 树（深度优先，按出现顺序，防环）
    tree: list[dict] = []
    seen: set[Path] = set()

    def walk(p: Path, role: str, src: str | None, depth: int) -> None:
        if p in seen or depth > 12:
            return
        seen.add(p)
        tree.append({"rel": p.relative_to(root).as_posix(), "role": role,
                     "from": src, "depth": depth, "lines": len(texts[p].splitlines()),
                     "bytes": len(texts[p].encode("utf-8")), "sha256_16": sha(p)})
        for cmd, arg in _includes(masked[p]):
            q = _resolve(root, p.parent, arg)
            if q:
                walk(q, cmd, p.relative_to(root).as_posix(), depth + 1)

    walk(main, "main", None, 0)
    if len(tree) == 1:  # 没展开出子文件：把同目录内联的其他 .tex 也列成兄弟稿
        for p in tex_files:
            if p not in seen:
                walk(p, "standalone", None, 0)

    order = {e["rel"]: i for i, e in enumerate(tree)}
    files = sorted(({"rel": e["rel"], "role": e["role"], "from": e["from"],
                     "depth": e["depth"],
                     "lines": len(texts[root / e["rel"]].splitlines()),
                     "bytes": len(texts[root / e["rel"]].encode("utf-8")),
                     "sha256_16": sha(root / e["rel"])} for e in tree),
                   key=lambda e: order[e["rel"]])
    skipped = [p.relative_to(root).as_posix() for p in tex_files
               if p.relative_to(root).as_posix() not in order]

    main_rel = main.relative_to(root).as_posix()
    mt = texts[main]

    # 4 文献库：先按 \bibliography / \addbibresource 解析，没有再退到工程内 .bib
    bibs: list[dict] = []
    bib_seen: set[Path] = set()
    for p in tex_files:
        for m in re.finditer(r"\\(?:bibliography|addbibresource)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}",
                             masked[p]):
            for name in m.group(1).split(","):
                nm = name.strip()
                if not nm:
                    continue
                cands = [Path(nm)] if Path(nm).is_absolute() else [root / nm, p.parent / nm]
                cands = [c if c.suffix else c.with_suffix(".bib") for c in cands]
                for c in cands:
                    if c.is_file() and c not in bib_seen:
                        bib_seen.add(c)
                        bibs.append({"rel": c.relative_to(root).as_posix(),
                                     "declared_at": f"{p.relative_to(root).as_posix()}:"
                                                    f"{line_of(masked[p], m.start())}",
                                     "sha256_16": sha(c),
                                     "bytes": len(c.read_bytes())})
                        break
    if not bibs:
        for c in sorted(root.rglob("*.bib")):
            if c.is_file() and c not in bib_seen:
                bib_seen.add(c)
                bibs.append({"rel": c.relative_to(root).as_posix(), "declared_at": None,
                             "sha256_16": sha(c), "bytes": len(c.read_bytes())})

    engine, reason = _engine(mt)
    cls = re.search(r"\\documentclass\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}", mt)
    tool = {b: shutil.which(b) for b in ENGINE_BINS}
    return {
        "root": str(root),
        "main": main_rel,
        "documentclass": {"name": cls.group(2).strip() if cls else None,
                          "options": (cls.group(1) or "").strip() if cls else "",
                          "line": line_of(mt, cls.start()) if cls else None},
        "engine": engine,
        "engine_reason": reason,
        "files": files,
        "skipped_tex": skipped,
        "bibs": bibs,
        "graphicspath": _graphicspath(mt),
        "packages": _packages(mt),
        "toolchain_available": {k: (v is not None) for k, v in tool.items()},
        "toolchain_path": {k: v for k, v in tool.items() if v},
        "stats": {"n_tex": len(tex_files), "n_in_tree": len(files),
                  "n_skipped": len(skipped), "n_bibs": len(bibs),
                  "n_lines": sum(f["lines"] for f in files),
                  "bytes": sum(f["bytes"] for f in files),
                  "n_packages": len(_packages(mt))},
        "notes": [f"跳过 {len(skipped)} 个未被主文件引入的 .tex（不在检查范围）"] if skipped else [],
    }


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="识别 LaTeX 工程结构")
    ap.add_argument("--input", required=True)
    ap.add_argument("--main", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    info = discover(a.input, a.main)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"main": info.get("main"), "engine": info.get("engine"),
                      "n_in_tree": info.get("stats", {}).get("n_in_tree"),
                      "n_bibs": info.get("stats", {}).get("n_bibs")}, ensure_ascii=False))
    return 0 if info.get("main") else 1


if __name__ == "__main__":
    raise SystemExit(main())
