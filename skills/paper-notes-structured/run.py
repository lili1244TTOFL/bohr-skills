#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py — 文献结构化笔记 Skill 的编排入口。

流程：
  0 读输入：case.json（case_id、output_contract）、嗅探 PDF/文本
  1 解析：复用 pdfparse（layout 引擎）+ tables
  2 章节：按标题切节，节内保留段落、表格、页码与块 id
  3 笔记：按字段信号挑候选句，生成论文卡片与知识点；调用方可改写，锚点不变
  4 核验：锚点存在、数字可回溯、章节对齐、字段完整、条目不重复
  5 落盘：notes.md、notes.json、card.json、result.json

没有调用方改写时，notes.json 为规则抽取版本，status=auto，不冒充人工总结。

用法：
  python run.py --input /input --work /app/work --out /app/output
  python run.py --input /input --notes work/notes.json --out /app/output
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
import time
from pathlib import Path

SRC_EXT = (".pdf", ".txt", ".md", ".tex", ".docx")
KEEP = ("body", "caption", "table", "reference", "formula")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_src(d: Path) -> Path | None:
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.suffix.lower() in SRC_EXT:
            return p
    return None


def text_to_parse(path: Path) -> dict:
    """纯文本输入：按行构造最小 parse 结构。"""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    blocks, page = [], 1
    for line in text.split("\n"):
        t = line.strip()
        if not t:
            continue
        role = "heading" if (len(t) <= 60 and not re.search(r"[.。;；,，]$", t)) else "body"
        blocks.append({"page": page, "role": role, "text": t,
                       "bbox": [0, 0, len(t), 1]})
    return {"ok": True, "source": str(path), "n_pages": 1, "pages_parsed": 1,
            "encrypted": False, "no_text_pages": [], "running": {},
            "pages": [{"page": 1, "n_lines": len(blocks), "columns": 1, "body_size": 10,
                       "blocks": blocks, "n_tables": 0, "empty": not blocks}]}


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="文献结构化笔记")
    ap.add_argument("--input", default=None)
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--notes", default=None, help="调用方改写后的 notes.json")
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="output")
    args = ap.parse_args(argv)

    t0 = time.time()
    work, out = Path(args.work), Path(args.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case_id, contract, case_dir = args.case_id or "case", {}, Path(".")
    if args.input:
        case_dir = Path(args.input)
        cj = case_dir / "case.json"
        if cj.exists():
            try:
                obj = json.loads(cj.read_text(encoding="utf-8-sig"))
                case_id = str(obj.get("case_id") or obj.get("id") or case_id)
                oc = obj.get("output_contract")
                contract = oc if isinstance(oc, dict) else (
                    {str(k): "" for k in oc} if isinstance(oc, list) else {})
            except Exception:
                pass
    src = Path(args.pdf) if args.pdf else find_src(case_dir)
    if not src or not src.exists():
        print(json.dumps({"ok": False, "status": "blocked", "error": "找不到原文文件"},
                         ensure_ascii=False))
        return 1

    gaps: list[str] = []
    if src.suffix.lower() == ".pdf":
        parser = load(Path(__file__).parent / "scripts" / "pdfparse.py")
        parse = parser.parse(src)
    else:
        parse = text_to_parse(src)
    (work / "parse.json").write_text(json.dumps(parse, ensure_ascii=False), encoding="utf-8")
    if parse.get("no_text_pages"):
        gaps.append("无文字层页：" + ",".join(map(str, parse["no_text_pages"])))

    notes_mod = load(Path(__file__).parent / "scripts" / "notes.py")
    res = notes_mod.build(parse)
    (work / "notes_auto.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                          encoding="utf-8")

    notes_path = Path(args.notes) if args.notes else Path("")
    use = res
    status = "auto"
    if args.notes and notes_path.exists():
        use = json.loads(notes_path.read_text(encoding="utf-8-sig"))
        status = "agent"
        for field in ("sections", "card", "knowledge"):
            if field not in use:
                gaps.append(f"notes.json 缺 {field}，回退到自动抽取")
                use = res
                status = "auto"
                break

    (out / "notes.json").write_text(json.dumps(use, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    (out / "card.json").write_text(json.dumps(use.get("card") or {}, ensure_ascii=False,
                                              indent=1), encoding="utf-8")
    (out / "notes.md").write_text(notes_mod.to_markdown(use), encoding="utf-8")

    ver_mod = load(Path(__file__).parent / "scripts" / "verify_notes.py")
    ver = ver_mod.verify(parse, use)
    (out / "verify.json").write_text(json.dumps(ver, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    if not ver["ok"]:
        gaps.append(f"核验未过：{ver['counts']}")
    if status == "auto":
        gaps.append("笔记为规则抽取版本（status=auto），未做人工改写")

    deliverables = []
    for name in ("notes.md", "notes.json", "card.json", "verify.json"):
        p = out / name
        deliverables.append({"kind": name.rsplit(".", 1)[0], "path": str(p),
                             "bytes": p.stat().st_size})
    checks = {
        "有章节": {"ok": not ver["counts"].get("N00"), "detail": ver["counts"].get("N00", 0)},
        "锚点": {"ok": not ver["counts"].get("N01"), "detail": ver["counts"].get("N01", 0)},
        "数字可回溯": {"ok": not ver["counts"].get("N02"), "detail": ver["counts"].get("N02", 0)},
        "章节对齐": {"ok": ver["sections_aligned"], "detail": ver["n_sections"]},
        "字段完整": {"ok": not ver["counts"].get("N04"), "detail": ver["card_filled"]},
        "知识点不重复": {"ok": not ver["counts"].get("N05"), "detail": ver["counts"].get("N05", 0)},
    }
    rules = [
        (r"^运行状态", "verify.json"),
        # 平台字段名精确匹配（先于模糊规则；改动见 review/ISSUES.md X-01）
        (r"^阅读[/／]解析结果", "notes.md"),
        (r"^证据与边界", "notes.json"),
        (r"^目标产物", "notes.md"),
        (r"^事实与来源映射", "notes.json"),
        (r"^版本与边界", "verify.json"),
        (r"note|笔记|markdown|summary", "notes.md"),
        (r"card|卡片|字段", "card.json"),
        (r"verif|核验|check", "verify.json"),
        (r"section|章节|structure", "notes.json"),
    ]
    echo, echo_status = {}, {}
    for key in contract:
        hit = None
        for pat, f in rules:
            if re.search(pat, key, re.I):
                hit = f
                break
        if hit:
            echo[key] = hit
            echo_status[key] = {"status": "full", "source": hit}
        else:
            echo[key] = {"note": "本字段无专门产出，见 notes.md / notes.json",
                         "pointer": ["notes.md", "notes.json"]}
            echo_status[key] = {"status": "partial", "source": "pointer"}

    result = {"case_id": case_id,
              "status": "success" if ver["ok"] and status == "agent" else "partial",
              "source": {"path": str(src), "n_pages": parse.get("n_pages"),
                         "n_sections": len(use.get("sections") or [])},
              "stats": {"sections": len(use.get("sections") or []),
                        "knowledge": len(use.get("knowledge") or []),
                        "items": ver["n_items"],
                        "card_filled": ver["card_filled"],
                        "elapsed_s": round(time.time() - t0, 2)},
              "deliverables": deliverables,
              "evidence": {"unit": "block id + page",
                           "note": "块 id 形如 P4-12，指向第 4 页第 12 个正文块"},
              "checks": checks, "gaps": gaps,
              "notes_status": status,
              "output_contract": echo, "output_contract_echo": echo_status}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": result["status"], "sections": result["stats"]["sections"],
                      "knowledge": result["stats"]["knowledge"], "gaps": gaps},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
