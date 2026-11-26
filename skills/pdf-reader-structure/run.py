#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py — 文献文档解析 Skill 的编排入口。

输入：/input（case.json、task.md、PDF）
输出：/app/output（parsed.md、blocks.json、sections.json、tables/、result.json）

步骤：
  0 读输入：case.json 的 case_id 与 output_contract，嗅探 PDF
  1 解析：pdfparse（栏、行、页眉页脚、标题层级、表格网格、公式编号）
  2 组装：parsed.md（阅读顺序）+ sections.json（层级）+ tables/*.md
  3 自检：页覆盖、表格列数、未解析页、乱码比例
  4 落盘：result.json（契约回显 + 证据 + 边界）

用法：
  python run.py --input /input --work /app/work --out /app/output
  python run.py --pdf paper.pdf --out out --case-id demo
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")
MOJI = re.compile(r"[\ufffd\u25a1]")          # 替换符、方块：字体缺字的典型信号


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def load(path: Path):
    spec = importlib.util.spec_from_file_location("pdfparse", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def sniff_pdf(d: Path) -> list[Path]:
    out = []
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        try:
            if p.read_bytes()[:5] == b"%PDF-":
                out.append(p)
        except Exception:
            continue
    return out


def build_sections(pages: list[dict]) -> list[dict]:
    secs: list[dict] = []
    stack: list[dict] = []
    for p in pages:
        for b in p["blocks"]:
            if b["role"] != "heading":
                continue
            lvl = int(b.get("level") or 3)
            node = {"title": b["text"], "level": lvl, "page": b["page"],
                    "bbox": b["bbox"], "children": []}
            while stack and stack[-1]["level"] >= lvl:
                stack.pop()
            (stack[-1]["children"] if stack else secs).append(node)
            stack.append(node)
    return secs


def to_markdown(pages: list[dict]) -> tuple[str, list[str]]:
    lines: list[str] = []
    gaps: list[str] = []
    for p in pages:
        lines.append(f"\n<!-- page {p['page']} -->\n")
        if p["empty"]:
            gaps.append(f"第 {p['page']} 页没有文字层")
            lines.append("_（本页无可提取文字层）_\n")
            continue
        for b in p["blocks"]:
            role = b["role"]
            if role in ("header", "footer", "page_number"):
                continue
            if role == "heading":
                lines.append("#" * min(int(b.get("level") or 3), 6) + " " + b["text"] + "\n")
            elif role == "table":
                lines.append(b.get("markdown") or "") 
                lines.append("")
            elif role == "caption":
                lines.append("**" + b["text"] + "**\n")
            elif role == "formula":
                lines.append("$$ " + b["text"] + " $$\n")
            else:
                lines.append(b["text"])
        lines.append("")
    return "\n".join(lines), gaps


def write_result(out: Path, case_id: str, contract: dict, res: dict,
                 sections: list[dict], tables: list[dict], md_path: Path,
                 gaps: list[str], stats: dict, checks: dict) -> Path:
    def pointer(kind: str) -> dict:
        return {"kind": kind, "path": str(out / kind), "bytes": (out / kind).stat().st_size
                if (out / kind).exists() else 0}

    deliverables = [pointer("parsed.md"), pointer("blocks.json"),
                    pointer("sections.json")]
    for t in tables:
        p = out / t["path"]
        if p.exists():
            deliverables.append({"kind": "table", "path": str(p), "bytes": p.stat().st_size,
                                 "table_no": t["no"], "n_rows": t["n_rows"],
                                 "n_cols": t["n_cols"]})
    by_kind = {d["kind"]: d for d in deliverables}
    echo, echo_status = {}, {}
    rules = [
        # 平台字段名精确匹配（先于模糊规则；改动见 review/ISSUES.md X-01）
        (r"^阅读[/／]解析结果", "parsed.md"),
        (r"^证据与边界", "blocks.json"),
        (r"^运行状态", "status"),
        (r"^目标产物", "parsed.md"),
        (r"^事实与来源映射", "blocks.json"),
        (r"^版本与边界", "sections.json"),
        (r"text|markdown|正文|全文|parsed", "parsed.md"),
        (r"(?<!re)table|表格", "table"),
        (r"section|章节|结构|heading", "sections.json"),
        (r"block|布局|layout|span", "blocks.json"),
    ]
    for key in contract:
        hit = None
        for pat, kind in rules:
            if re.search(pat, key, re.I):
                hit = kind
                break
        if hit == "table":
            if tables:
                echo[key] = [t["path"] for t in tables]
                echo_status[key] = {"status": "full", "source": "table"}
            else:
                echo[key] = {"note": "本文未检出表格网格", "pointer": ["blocks.json"]}
                echo_status[key] = {"status": "partial", "source": "pointer"}
        elif hit == "parsed.md":
            echo[key] = "parsed.md"
            echo_status[key] = {"status": "full", "source": "parsed.md"}
        elif hit == "sections.json":
            echo[key] = "sections.json"
            echo_status[key] = {"status": "full", "source": "sections.json"}
        elif hit == "blocks.json":
            echo[key] = "blocks.json"
            echo_status[key] = {"status": "full", "source": "blocks.json"}
        elif hit == "status":
            echo[key] = {"status": "success" if res["ok"] and not gaps else "partial",
                         "n_pages": res["n_pages"], "n_gaps": len(gaps)}
            echo_status[key] = {"status": "full", "source": "result.json"}
        else:
            echo[key] = {"note": "本字段无专门产出，见 parsed.md / blocks.json",
                         "pointer": ["parsed.md", "blocks.json"]}
            echo_status[key] = {"status": "partial", "source": "pointer"}

    result = {
        "output_contract_echo": echo_status,
        "case_id": case_id,
        "status": "success" if res["ok"] and not gaps else "partial",
        "source": {"path": res["source"], "sha256": sha(Path(res["source"])),
                   "n_pages": res["n_pages"], "parsed_pages": res["pages_parsed"],
                   "encrypted": res["encrypted"]},
        "stats": stats,
        "sections": sections,
        "tables": [{k: t[k] for k in ("no", "page", "n_rows", "n_cols", "path")}
                   for t in tables],
        "formulas": [{"page": b["page"], "no": b.get("formula_no"), "text": b["text"],
                      "bbox": b["bbox"]}
                     for p in res["pages"] for b in p["blocks"] if b["role"] == "formula"],
        "figures": [{"page": b["page"], "text": b["text"], "bbox": b["bbox"]}
                    for p in res["pages"] for b in p["blocks"] if b["role"] == "caption"],
        "deliverables": deliverables,
        "evidence": {"unit": "page+bbox", "blocks": "blocks.json",
                     "note": "每个 block 带页码与 bbox，可直接回原文定位"},
        "gaps": gaps,
        "checks": checks,
        "output_contract": echo,

    }
    p = out / "result.json"
    result["阅读/解析结果"] = "parsed.md + blocks.json（分节、表格、公式、图表位置）"
    result["证据与边界"] = "evidence 字段：page + bbox + 字符区间"
    result["运行状态"] = result.get("status")
    result.setdefault("uncertainty", list(result.get("gaps") or []))
    p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="文献文档解析")
    ap.add_argument("--input", default=None, help="平台输入目录（含 case.json / PDF）")
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="output")
    args = ap.parse_args(argv)

    t0 = time.time()
    work, out = Path(args.work), Path(args.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    case_id, contract, task_text = args.case_id or "case", {}, ""
    if args.input:
        d = Path(args.input)
        cj = d / "case.json"
        if cj.exists():
            try:
                obj = json.loads(cj.read_text(encoding="utf-8-sig"))
                case_id = str(obj.get("case_id") or obj.get("id") or case_id)
                oc = obj.get("output_contract")
                if isinstance(oc, dict):
                    contract = oc
                elif isinstance(oc, list):
                    contract = {str(k): "" for k in oc}
            except Exception:
                pass
        tm = d / "task.md"
        if tm.exists():
            task_text = tm.read_text(encoding="utf-8-sig", errors="replace")
        pdfs = sniff_pdf(d)
        if not pdfs and args.pdf:
            pdfs = [Path(args.pdf)]
        if not pdfs:
            # 失败也要留下契约载体：下游与评审靠 result.json 判断状态，不能只写 stdout。
            cid = case_id or (Path(args.input).name if args.input else "case")
            blocked = {
                "case_id": cid,
                "status": "blocked",
                "error": "输入目录里没有 PDF",
                "deliverables": [],
                "evidence": {"unit": "page+bbox", "note": "未解析，无证据"},
                "checks": {},
                "gaps": ["输入目录里没有 PDF"],
                "output_contract": {k: {"note": "未运行", "pointer": []} for k in contract},
                "output_contract_echo": {k: {"status": "partial", "source": "pointer"}
                                         for k in contract},
            }
            (out / "result.json").write_text(
                json.dumps(blocked, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps({"ok": False, "status": "blocked",
                              "error": "输入目录里没有 PDF",
                              "result": str(out / "result.json")}, ensure_ascii=False))
            return 1
        pdf = pdfs[0]
        if len(pdfs) > 1:
            case_id = case_id or pdfs[0].stem
    else:
        if not args.pdf:
            ap.error("需要 --pdf 或 --input")
        pdf = Path(args.pdf)

    parser = load(Path(__file__).parent / "scripts" / "pdfparse.py")
    res = parser.parse(pdf)
    (work / "parse_raw.json").write_text(json.dumps(res, ensure_ascii=False),
                                         encoding="utf-8")

    pages = res["pages"]
    blocks = [b for p in pages for b in p["blocks"]]
    sections = build_sections(pages)
    md, gaps = to_markdown(pages)
    (out / "parsed.md").write_text(md, encoding="utf-8")

    flat = [{k: b[k] for k in ("page", "role", "text", "bbox")} | {"size": b.get("size")}
            for b in blocks]
    (out / "blocks.json").write_text(json.dumps(flat, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    (out / "sections.json").write_text(json.dumps(sections, ensure_ascii=False, indent=1),
                                       encoding="utf-8")

    tables = []
    tdir = out / "tables"
    for p in pages:
        for b in p["blocks"]:
            if b["role"] != "table":
                continue
            tables.append({"no": len(tables) + 1, "page": b["page"], "n_rows": b["n_rows"],
                           "n_cols": b["n_cols"], "rows": b["rows"],
                           "markdown": b["markdown"], "bbox": b["bbox"],
                           "path": f"tables/table{len(tables) + 1}.md"})
            tdir.mkdir(parents=True, exist_ok=True)
            (out / tables[-1]["path"]).write_text(b["markdown"] + "\n", encoding="utf-8")

    if res["no_text_pages"]:
        gaps.append("无文字层页：" + ",".join(str(x) for x in res["no_text_pages"])
                    + "（需 OCR，本 Skill 未内置 OCR）")
    if res["encrypted"]:
        gaps.append("PDF 加密，已尝试空口令解密")
    single = [p["page"] for p in pages if p.get("columns", 1) == 1 and p.get("n_lines", 0) >= 20]
    if single:
        gaps.append("以下页判为单栏，若原文为多栏则栏序未验证：" + ",".join(map(str, single[:8])))
    cjk_chars = sum(len(CJK.findall(b.get("text") or "")) for b in blocks)
    if cjk_chars > 0:
        gaps.append(f"中文页共 {cjk_chars} 字：标题行可能受字距影响，顺序需人工核对")

    body = [b for b in blocks if b["role"] == "body"]
    moji = sum(len(MOJI.findall(b["text"])) for b in body)
    chars = sum(len(b["text"]) for b in body) or 1
    stats = {"pages": res["n_pages"], "parsed_pages": res["pages_parsed"],
             "blocks": len(blocks), "body_blocks": len(body),
             "headings": sum(1 for b in blocks if b["role"] == "heading"),
             "tables": len(tables), "formulas": sum(1 for b in blocks if b["role"] == "formula"),
             "captions": sum(1 for b in blocks if b["role"] == "caption"),
             "references": sum(1 for b in blocks if b["role"] == "reference"),
             "headers": sum(1 for b in blocks if b["role"] == "header"),
             "footers": sum(1 for b in blocks if b["role"] == "footer"),
             "columns": [p["columns"] for p in pages],
             "chars": chars, "mojibake_ratio": round(moji / chars, 4),
             "elapsed_s": round(time.time() - t0, 2)}
    if stats["mojibake_ratio"] > 0.02:
        gaps.append(f"疑似缺字/乱码比例 {stats['mojibake_ratio']:.2%}，字体可能未嵌入")

    checks = {
        "页覆盖": {"ok": res["pages_parsed"] == res["n_pages"],
                   "detail": f"{res['pages_parsed']}/{res['n_pages']}"},
        "表格列数一致": {"ok": all(len({len(r) for r in t["rows"]}) == 1 for t in tables),
                         "detail": [t["n_cols"] for t in tables]},
        "有正文": {"ok": len(body) > 0, "detail": len(body)},
        "页眉页脚已剔除": {"ok": (stats["headers"] + stats["footers"]) > 0,
                           "detail": f"header={stats['headers']} footer={stats['footers']}；"
                                     "为 0 表示未检出（无页眉页脚或未识别），不等同于已剔除"},
    }
    rp = write_result(out, case_id, contract, res, sections, tables, out / "parsed.md",
                      gaps, stats, checks)
    print(json.dumps({"ok": True, "status": "success" if not gaps else "partial",
                      "case_id": case_id, "result": str(rp),
                      "tables": len(tables), "gaps": gaps,
                      "elapsed_s": stats["elapsed_s"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
