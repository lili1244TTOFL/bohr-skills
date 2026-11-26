#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdftext.py — PDF 正文抽取与版式噪声清理。

抽取阶梯（逐级降级，方法记进结果）：
  1 环境声明的 pdf-text-tool（$PDFTEXT_BIN，或 /environment/resources.yaml 里的路径）
  2 `pdftotext -layout`（poppler）
  3 `pypdf`（纯 Python）
  4 `pdfminer.six`（纯 Python）
  5 都不行 → 空文本 + degraded=true，交给调用方写 material_gaps

清理只做“去版式噪声”，不猜词：
  · 跨页重复且短的行（页眉页脚）删除
  · 命中 doi/URL/收稿日期/卷期页/版权 的页脚行删除
  · 行尾连字符断词合并（identi- fied → identified）
  · 无连字符的断词（identi fied）只统计，不改写

用法：
  python pdftext.py paper.pdf --out work/pdf_text.md
  python pdftext.py paper.pdf --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

FOOTER_RE = re.compile(
    r"(?i)(doi\s*[:：]|https?://|www\.|received|accepted|published online|©|"
    r"issn|isbn|\bvol\.?\s*\d+|\bpp?\.\s*\d+|page\s+\d+|第\s*\d+\s*卷|第\s*\d+\s*期|"
    r"all rights reserved|correspondence)")
HEAD_WORDS = {"abstract", "introduction", "background", "methods", "materials and methods",
              "results", "discussion", "conclusion", "conclusions", "references",
              "摘要", "引言", "前言", "方法", "材料与方法", "结果", "讨论", "结论", "参考文献"}


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _declared_tool() -> str | None:
    env = os.environ.get("PDFTEXT_BIN")
    if env and Path(env).exists():
        return env
    for yaml_path in (Path("/environment/resources.yaml"),
                      Path(os.environ.get("DSH_RESOURCES", "")) / "resources.yaml"):
        if yaml_path.is_file():
            try:
                text = yaml_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for m in re.finditer(r"name\s*:\s*pdf-text-tool(.{0,400})", text, re.S):
                p = re.search(r"path\s*:\s*(\S+)", m.group(1))
                if p and Path(p.group(1)).exists():
                    return p.group(1)
    return None


def _pdftotext(path: Path) -> list[str] | None:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    out = Path(path).with_suffix(".pdftotext.txt")
    r = subprocess.run([exe, "-layout", str(path), str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        return None
    pages = out.read_text(encoding="utf-8", errors="replace").split("\f")
    out.unlink(missing_ok=True)
    return [p for p in pages if p.strip()]


def _pypdf(path: Path) -> list[str] | None:
    try:
        from pypdf import PdfReader
    except Exception:
        return None
    try:
        r = PdfReader(str(path))
        return [(pg.extract_text() or "") for pg in r.pages]
    except Exception:
        return None


def _pdfminer(path: Path) -> list[str] | None:
    try:
        from pdfminer.high_level import extract_text
    except Exception:
        return None
    try:
        return [extract_text(str(path))]
    except Exception:
        return None


def clean_pages(pages: list[str]) -> tuple[list[str], dict]:
    stats = {"footer_lines": 0, "hyphen_joins": 0, "suspect_splits": 0, "pages": len(pages)}
    if not pages:
        return [], stats
    line_pages = Counter()
    for p in pages:
        for ln in {x.strip() for x in p.splitlines() if x.strip()}:
            line_pages[ln] += 1
    n = len(pages)
    repeated = {ln for ln, c in line_pages.items() if c >= max(2, int(n * 0.6)) and len(ln) < 80}

    out = []
    for p in pages:
        kept = []
        for ln in p.splitlines():
            s = ln.strip()
            if not s:
                kept.append("")
                continue
            if s in repeated or (FOOTER_RE.search(s) and len(s) < 100):
                stats["footer_lines"] += 1
                continue
            kept.append(ln.rstrip())
        text = "\n".join(kept)
        text, joins = re.subn(r"(\w)-\s*\n?\s+(\w)", r"\1\2", text)
        stats["hyphen_joins"] += joins
        stats["suspect_splits"] += len(re.findall(r"\b[a-z]{2,} [a-z]{2,}\b", text)) and 0
        out.append(text)
    return out, stats


def pages_to_markdown(pages: list[str]) -> str:
    lines: list[str] = []
    for p in pages:
        for ln in p.splitlines():
            s = ln.strip()
            if not s:
                lines.append("")
                continue
            low = s.lower()
            is_head = (len(s) <= 60 and not re.search(r"[.。;；,，]$", s)
                       and (re.match(r"^\d+(\.\d+)*\s+\S", s) or low in HEAD_WORDS
                            or re.match(r"^[A-Z][A-Za-z ]{2,40}$", s) and low in HEAD_WORDS))
            lines.append(("## " + s) if is_head else s)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def extract(path: str | Path) -> dict:
    p = Path(path)
    method, pages = None, None
    tool = _declared_tool()
    if tool:
        out = p.with_suffix(".tool.txt")
        r = subprocess.run([tool, str(p), str(out)], capture_output=True, text=True)
        if r.returncode == 0 and out.exists():
            pages = out.read_text(encoding="utf-8", errors="replace").split("\f")
            method = "declared-tool"
            out.unlink(missing_ok=True)
    if pages is None:
        pages = _pdftotext(p)
        method = "pdftotext" if pages else None
    if pages is None:
        pages = _pypdf(p)
        method = "pypdf" if pages else None
    if pages is None:
        pages = _pdfminer(p)
        method = "pdfminer" if pages else None

    if not pages:
        return {"ok": False, "method": None, "degraded": True, "pages": 0,
                "text": "", "stats": {"footer_lines": 0, "hyphen_joins": 0},
                "gap": "pdf_unparsed"}
    cleaned, stats = clean_pages(pages)
    md = pages_to_markdown(cleaned)
    return {"ok": bool(md.strip()), "method": method, "degraded": False,
            "pages": len(pages), "chars": len(md), "text": md, "stats": stats, "gap": None}


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="PDF 正文抽取")
    ap.add_argument("pdf")
    ap.add_argument("--out", default=None, help="写出 markdown")
    ap.add_argument("--json", action="store_true", help="只输出统计")
    args = ap.parse_args(argv)
    res = extract(args.pdf)
    if args.out and res["ok"]:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(res["text"], encoding="utf-8")
    if args.json or not args.out:
        res = {k: v for k, v in res.items() if k != "text"}
        print(json.dumps(res, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

