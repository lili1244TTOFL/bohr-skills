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
        # 找不到原文也不空手返回：写 result.json + 可读缺料说明
        res = {"case_id": case_id, "status": "blocked",
               "gaps": ["找不到原文文件"], "uncertainty": ["无输入"],
               "deliverables": ["missing_input.md"]}
        (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        (out / "missing_input.md").write_text("\n".join([
            "# 缺料说明：没有找到原文", "",
            f"- 输入目录：`{case_dir}`",
            "- 找过：该目录下所有 `.pdf` / `.txt` / `.md` / `.tex` / `.docx`",
            "- 需要补：待解析的论文全文（PDF 需带文字层，或直接给 `.md`/`.txt`）",
            "- 缺什么才能出产物：没有原文就没有块、没有章节，`notes.md` 无从生成",
            "- 补齐后重跑：`python run.py --input <目录> --work <w> --out <o>`",
        ]) + "\n", encoding="utf-8")
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
        gaps.append("笔记由内置规则生成：标题块切节 → 五类字段信号挑候选句 → 卡片与知识点；"
                    "块 id 锚点不变，未做人工改写")

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
    # 契约字段回填本次运行算出来的真实内容。v1 回填的是文件名（"notes.md" 之类），
    # 平台侧等同 pointer，评审无法据此判断笔记到底有没有内容。
    sec_list = use.get("sections") or []
    kn_list = use.get("knowledge") or []
    # 规则抽取版本（notes_status=auto）本身就是真产出：章节切分、字段候选、卡片知识点
    # 都由内置规则从原文算出，块 id 可回溯。它不该再被顶层状态压成"没做完"。
    has_notes = bool(sec_list) and (bool(kn_list) or bool(ver["card_filled"]))
    result_status = "success" if ver["ok"] and has_notes else "partial"
    anchors = []
    for fld, obj in (use.get("card") or {}).items():
        for it in (obj or {}).get("items") or []:
            anchors.append({"来源": f"card.{fld}", "anchor": it.get("anchor"),
                            "page": it.get("page"), "section": it.get("section")})
    for it in kn_list:
        anchors.append({"来源": f"knowledge.{it.get('kind')}", "anchor": it.get("anchor"),
                        "page": it.get("page"), "section": it.get("section")})
    contract_values = {
        "阅读/解析结果": {
            "节数": len(sec_list),
            "章节": [{"sec_id": s.get("sec_id"), "title": s.get("title"),
                      "page": s.get("page")} for s in sec_list[:20]],
            "知识点": len(kn_list),
            "带锚点条目": ver["n_items"],
            "卡片已填字段": ver["card_filled"],
        },
        "证据与边界": {
            "证据单位": "block id + page（块 id 形如 P4-12）",
            "锚点": anchors[:20],
            "锚点总数": len(anchors),
            "缺口": gaps,
        },
        "运行状态": {
            "status": result_status,
            "notes_status": status,
            "检查": {k: v["ok"] for k, v in checks.items()},
            "未过检查": [k for k, v in checks.items() if not v["ok"]],
        },
    }
    contract_src = {"阅读/解析结果": "notes.md + notes.json",
                    "证据与边界": "notes.json 的 anchor 字段",
                    "运行状态": "verify.json 的 ok/counts"}
    echo, echo_status = {}, {}
    for key in contract:
        name = str(key).replace("／", "/").strip()
        if name in contract_values:
            echo[key] = contract_values[name]
            echo_status[key] = {"status": "full", "source": contract_src[name]}
        else:
            echo[key] = {"note": "本字段无专门产出，见 notes.md / notes.json",
                         "pointer": ["notes.md", "notes.json"]}
            echo_status[key] = {"status": "partial", "source": "pointer"}

    rules = {
        "方式": "标题块切节 → 五类字段信号挑候选句 → 卡片/知识点归并",
        "切节": {"节数": len(sec_list),
                 "标题块": [s.get("title") for s in sec_list[:20]]},
        "字段命中": {f: sum(len(s.get("candidates", {}).get(k) or [])
                            for s in sec_list)
                     for f, k in (("研究问题", "question"), ("数据", "data"),
                                  ("方法条件", "method"), ("主要发现", "finding"),
                                  ("局限", "limitation"))},
        "知识点": len(kn_list),
        "卡片已填字段": ver["card_filled"],
    }
    result = {"case_id": case_id,
              "status": result_status,
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
              "规则命中": rules,
              "output_contract": echo, "output_contract_echo": echo_status}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": result["status"], "sections": result["stats"]["sections"],
                      "knowledge": result["stats"]["knowledge"], "gaps": gaps},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
