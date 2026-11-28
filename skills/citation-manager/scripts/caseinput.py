#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""caseinput.py — 读平台输入目录，解析 case.json / task.md，定位稿件与文献库。

平台在执行时给的是 /input：case.json（含 output_contract）、task.md、稿件、文献库。
本模块只做定位与解析，不判断内容；拿不到的文件写进 gaps，不猜。

用法（在 run.py 内调用）：
    plan = caseinput.load("/input")
    plan["docx"] / plan["refs"] / plan["libs"] / plan["case_id"] / plan["output_contract"]
独立自检：
    python caseinput.py --input /input
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DOCX_MAGIC = b"PK\x03\x04"
PDF_MAGIC = b"%PDF"
BIB_RE = re.compile(r"^\s*@\w+\s*\{", re.M)
RIS_RE = re.compile(r"^TY  - ", re.M)
ENW_RE = re.compile(r"^%0\s", re.M)
CNKI_RE = re.compile(r"^\{?(文献类型|Reference Type)\}?\s*[:：]", re.M)
CN_TAGS_RE = re.compile(r"^\s*(题名|作者|刊名)\s*[:：]", re.M)
REFWORKS_RE = re.compile(r"^(RT|T1|A1)\s*-\s", re.M)
REF_RE = re.compile(r"^\s*\[\d+\]\s+\S", re.M)


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def sniff(path: Path) -> str:
    """按内容判类型，不看扩展名。"""
    head = path.read_bytes()[:4096]
    if head.startswith(DOCX_MAGIC) and b"word/" in path.read_bytes()[:8192]:
        return "docx"
    if head.startswith(PDF_MAGIC):
        return "pdf"
    text = head.decode("utf-8", "replace")
    if BIB_RE.search(text):
        return "bib"
    if RIS_RE.search(text):
        return "ris"
    if ENW_RE.search(text):
        return "enw"
    if CNKI_RE.search(text) or CN_TAGS_RE.search(text):
        return "cnki"
    if REFWORKS_RE.search(text):
        return "refworks"
    if re.search(r"^\s*(#{1,6}\s|\d+(\.\d+)*\s+\S)", text, re.M):
        return "md"
    return "txt"


def md_to_docx(md_path: Path, out_path: Path) -> Path:
    """把 markdown 稿转成 docx（保留标题层级与段落）。"""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.size = Pt(12)
    for raw in read_text(md_path).split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            doc.add_heading(m.group(2), level=min(len(m.group(1)), 4))
        else:
            doc.add_paragraph(line)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def load(input_dir: str | Path) -> dict:
    d = Path(input_dir)
    plan = {"input_dir": str(d), "case_id": d.name, "output_contract": {},
            "docx": None, "refs": None, "libs": [], "task_text": "",
            "style": "GB/T 7714-2015", "gaps": [], "files": []}
    if not d.exists():
        plan["gaps"].append("输入目录不存在")
        return plan

    case = d / "case.json"
    if case.exists():
        try:
            obj = json.loads(read_text(case))
            plan["case_id"] = str(obj.get("case_id") or obj.get("id") or plan["case_id"])
            oc = obj.get("output_contract")
            if isinstance(oc, dict):
                plan["output_contract"] = oc
            elif isinstance(oc, list):
                plan["output_contract"] = {str(k): "" for k in oc}
        except Exception as ex:
            plan["gaps"].append(f"case.json 解析失败：{ex}")
    else:
        plan["gaps"].append("缺 case.json，按目录名作 case_id")

    task = d / "task.md"
    if task.exists():
        plan["task_text"] = read_text(task)[:20000]
        t = plan["task_text"]
        if re.search(r"APA", t, re.I):
            plan["style"] = "APA 7"
        elif re.search(r"IEEE", t, re.I):
            plan["style"] = "IEEE"
        elif re.search(r"GB/T\s*7714", t, re.I):
            plan["style"] = "GB/T 7714-2015"
    else:
        plan["gaps"].append("缺 task.md，目标规范按默认 GB/T 7714-2015")

    refs_cand, doc_cand = [], []
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.name in {"case.json", "task.md"}:
            continue
        kind = sniff(p)
        plan["files"].append({"path": str(p), "kind": kind, "bytes": p.stat().st_size})
        if kind in ("bib", "ris", "enw", "cnki", "refworks"):
            # EndNote(.enw) 与 CNKI/NoteExpress/RefWorks 题录同样当文献库输入
            plan["libs"].append(str(p))
        elif kind == "docx":
            doc_cand.append((p.stat().st_size, p))
        elif kind in ("md", "txt"):
            text = read_text(p)
            lines = [x for x in text.splitlines() if x.strip()]
            if lines and sum(1 for x in lines if REF_RE.match(x)) >= max(3, len(lines) // 2):
                refs_cand.append(p)
            elif kind == "md":
                doc_cand.append((p.stat().st_size, p))
        elif kind == "pdf":
            doc_cand.append((p.stat().st_size + 1, p))

    if refs_cand:
        plan["refs"] = str(refs_cand[0])
    doc_cand.sort(key=lambda x: -x[0])
    if doc_cand:
        src = doc_cand[0][1]
        if src.suffix.lower() == ".docx":
            plan["docx"] = str(src)
        else:
            if src.suffix.lower() == ".pdf":
                import importlib.util as _u
                here = Path(__file__).resolve().parent
                spec = _u.spec_from_file_location("pdftext", here / "pdftext.py")
                pt = _u.module_from_spec(spec)
                spec.loader.exec_module(pt)
                res = pt.extract(src)
                plan["pdf"] = {k: v for k, v in res.items() if k != "text"}
                if not res["ok"]:
                    plan["gaps"].append("PDF 抽取失败：" + src.name + "（" + str(res.get("gap")) + "）")
                    return plan
                md = d / "_converted" / (src.stem + ".md")
                md.parent.mkdir(parents=True, exist_ok=True)
                md.write_text(res["text"], encoding="utf-8")
                plan["converted_from"] = str(src)
                plan["gaps"].append(
                    "稿件来自 PDF（" + src.name + "，" + str(res["pages"]) + " 页，方法 "
                    + str(res["method"]) + "）：成品为文本重排，原版式不保留")
                src = md
            out = d / "_converted" / (src.stem + ".docx")
            plan["docx"] = str(md_to_docx(src, out))
            plan["converted_from"] = str(src)
    else:
        plan["gaps"].append("未找到稿件（docx/md/txt）")
    if not plan["libs"]:
        plan["gaps"].append("未找到文献库（bib/ris）")
    return plan


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="读 /input 目录")
    ap.add_argument("--input", default="/input")
    args = ap.parse_args(argv)
    print(json.dumps(load(args.input), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
