#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py — 文献结构化笔记 Skill 的编排入口（v2）。

流程：
  0 读输入：case.json（case_id、output_contract）、嗅探 PDF/文本
  1 解析：pdfparse（layout 引擎）+ tables；文本输入走最小 parse + 同一套标题判据
  2 切节：按标题切节，节带 role / first-last 锚点 / 承接边
  3 抽取：六字段候选（每条带 quote + span + 规则证据）、知识点、冲突点
  4 核验：N00–N09
  5 落盘：notes.md / notes.json / card.json / verify.json / index.json / trace.json / result.json

没有调用方改写时，notes.json 为规则抽取版本，notes_status=auto，不冒充人工总结。

用法：
  python run.py --input /input --work /app/work --out /app/output
  python run.py --input /input --notes /app/work/notes_agent.json --work /app/work --out /app/output
  python run.py --pdf examples/paper.md --work work --out output --case-id demo
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

SRC_EXT = (".pdf", ".txt", ".md", ".tex", ".docx")
KEEP = ("body", "caption", "table", "reference", "formula")
CARD_ORDER = ("研究问题", "数据", "方法条件", "主要发现", "结论", "局限")
KNOW_KINDS = ("term", "variable", "method", "finding", "conclusion", "limitation")
JUDGMENT_UNIT = "要点分类"
FLOAT_RE = re.compile(r"^\s*(图|表|Figure|Fig\.?|Table)\s*([0-9]+[A-Za-z]?)")
VERSION = "2.0.0"


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
    """纯文本输入：按行构造最小 parse 结构，行角色交给 textclass 判定。"""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    blocks, page = [], 1
    for line in text.split("\n"):
        t = line.replace("\u00a0", " ").strip()
        if not t:
            continue
        role = "heading" if (len(t) <= 60 and not re.search(r"[.。;；,，]$", t)) else "body"
        blocks.append({"page": page, "role": role, "text": t,
                       "bbox": [0, 0, len(t), 1]})
    return {"ok": True, "source": str(path), "n_pages": 1, "pages_parsed": 1,
            "encrypted": False, "no_text_pages": [], "running": {},
            "pages": [{"page": 1, "n_lines": len(blocks), "columns": 1, "body_size": 10,
                       "blocks": blocks, "n_tables": 0, "empty": not blocks}]}


def diagnostics(parse: dict, audit: dict, notes: dict, ver: dict) -> dict:
    """把题目点名的六类异常逐条识别并写处置，供评审直接看。"""
    pages = parse.get("pages") or []
    two_col = [p["page"] for p in pages if (p.get("columns") or 1) > 1]
    no_text = parse.get("no_text_pages") or []
    n_tables = sum(p.get("n_tables") or 0 for p in pages)
    n_caption = sum(1 for p in pages for b in p["blocks"] if b["role"] == "caption")
    n_table_blocks = sum(1 for p in pages for b in p["blocks"] if b["role"] == "table")
    n_formula = sum(1 for p in pages for b in p["blocks"] if b["role"] == "formula")
    chars = "".join(b["text"] for p in pages for b in p["blocks"])
    n_cjk = len(re.findall(r"[\u4e00-\u9fff]", chars))
    ratio = round(n_cjk / max(1, len(chars)), 3)
    lang = "中文为主" if ratio >= 0.3 else ("混合" if ratio >= 0.05 else "英文/拉丁文为主")
    n_pages = parse.get("n_pages") or len(pages)
    return {
        "篇幅": {"页数": n_pages, "解析页数": parse.get("pages_parsed"),
                 "正文块": sum(1 for p in pages for b in p["blocks"] if b["role"] in KEEP),
                 "超长文": n_pages > 40,
                 "处置": ("超过 40 页：按页顺序一次性抽取，未做分块预算，"
                          "候选只保留节内前 3 条，宁缺不全篇扫" if n_pages > 40
                          else "常规篇幅：全篇解析，节内候选保留前 3 条")},
        "双栏": {"双栏页": two_col[:20], "页数": len(two_col),
                 "处置": ("按栏切段后左栏→右栏排序；跨栏图注可能错序，笔记锚点可回页面抽查"
                          if two_col else "未检出双栏")},
        "无文字层": {"页": no_text, "页数": len(no_text),
                     "处置": ("该页无块，笔记跳过，不补 OCR；缺口写入 gaps 与 uncertainty"
                              if no_text else "全部页均有文字层")},
        "加密": {"标志": bool(parse.get("encrypted")),
                 "处置": ("空口令解密失败：仅解析可读页，缺口写入 uncertainty"
                          if parse.get("encrypted") else "无加密标志")},
        "图表": {"图注表注块": n_caption, "表格网格块": n_table_blocks,
                 "页面表格计数": n_tables,
                 "处置": ("表格以网格块 + 图注/表注锚点（T<页>-<序>）记录行列原文；"
                          "未识别网格时只保留表注锚点，不猜单元格" if n_tables or n_caption
                          else "未见图注/表注：可能确实无图表，也可能图注未被识别")},
        "公式": {"公式块": n_formula,
                 "处置": ("公式块可按 F<页>-<序> 回指，公式编号写入块属性，不做推导"
                          if n_formula else "未检出公式块")},
        "语种": {"中文字符占比": ratio, "判定": lang,
                 "处置": ("中英混合：字段信号表同时含中英模式，quote 保持原文逐字"
                          if lang != "英文/拉丁文为主" else "英文为主：按英文信号词抽取")},
        "标题审计": {"解析器原判标题": audit.get("n_before", 0),
                     "保留标题": audit.get("n_after", 0),
                     "降级原因": audit.get("by_reason") or {},
                     "处置": ("降级项疑为表格首列/断行残片/参考文献条目；"
                              "如原文确有该节，按 uncertainty 人工补节" if audit.get("demoted")
                              else "无降级")},
        "核验": {"未过判据": ver.get("counts") or {},
                 "处置": "未过项逐条写入 verify.json.problems 与 gaps"},
    }


def build_index(notes: dict, case_id: str) -> dict:
    items, tokens = {}, {}
    for k in notes.get("knowledge") or []:
        kid = k.get("id") or ""
        items[kid] = {"kind": k.get("kind"), "page": k.get("page"),
                      "anchor": k.get("anchor"), "section": k.get("section"),
                      "text": (k.get("text") or "")[:160]}
        for t in k.get("tokens") or []:
            tokens.setdefault(t, []).append(kid)
    rev = {t: ids[:20] for t, ids in tokens.items()}
    return {"case_id": case_id, "n_items": len(items), "n_tokens": len(rev),
            "如何查": "python scripts/query.py --index index.json --q <词>",
            "items": items, "tokens": rev}


def notes_judgment(use: dict, status: str, notes_path: Path, parse: dict) -> dict:
    """判断层：要点分类。

    分类由调用方模型做，落点是 `--notes` 给的 notes.json（卡片六字段 + 知识点的 kind）。
    没给 --notes 时 notes.json 只是规则草案：全部条目标 pending，不把规则判类当模型结论。
    """
    agent = status == "agent"
    text_all = "\n".join((b.get("text") or "") for pg in parse.get("pages") or []
                         for b in pg.get("blocks") or [])
    items: list[dict] = []
    seen: dict[str, int] = {}

    def row(base: str, source: str, it: dict, candidates: list[str], kind: str) -> None:
        seen[base] = seen.get(base, 0) + 1
        quote = (it.get("quote") or it.get("text") or "").strip()
        items.append({
            "id": f"{base}#{seen[base]}", "item": JUDGMENT_UNIT, "来源": source,
            "section": it.get("section"), "page": it.get("page"),
            "候选": candidates, "规则判类": kind,
            "value": kind if agent else "pending",
            "basis": quote[:100],
            "filled_by": "调用方模型（--notes notes.json）" if agent else "",
            "status": "filled" if agent else "pending",
            "in_candidates": (kind in candidates) if agent else None,
            "basis_verbatim": ((quote in text_all) if quote else None) if agent else None,
        })

    for field in CARD_ORDER:
        for it in (((use.get("card") or {}).get(field) or {}).get("items") or []):
            row(f"{it.get('anchor') or '?'}@card.{field}", f"card.{field}", it,
                list(CARD_ORDER), field)
    for it in use.get("knowledge") or []:
        kind = str(it.get("kind") or "")
        row(f"{it.get('anchor') or '?'}@knowledge.{kind}", f"knowledge.{kind}", it,
            list(KNOW_KINDS), kind)

    n = len(items)
    filled = n if agent else 0
    return {
        "unit": JUDGMENT_UNIT,
        "who_fills": "调用方模型：把要点分到六字段卡片 / 知识点 kind，写 notes.json 后走 --notes",
        "who_checks": "run.py 核 id 是否可回锚点、value 是否在候选内、basis（quote）是否逐字出现在原文",
        "source": (str(notes_path) if agent else
                   "未给 --notes：notes.md 是规则草案，不是模型结论"),
        "notes_status": "agent" if agent else "pending",
        "n": n, "filled": filled, "pending": n - filled,
        "items": items[:300],
        "by_field": {f: sum(1 for i in items if i["来源"] == f"card.{f}") for f in CARD_ORDER},
        "by_kind": {k: sum(1 for i in items if i["来源"] == f"knowledge.{k}")
                    for k in KNOW_KINDS},
    }


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
        res = {"case_id": case_id, "status": "blocked", "notes_status": "none",
               "sources": {"input_dir": str(case_dir), "looked_for": list(SRC_EXT)},
               "gaps": ["找不到原文文件"], "uncertainty": ["无输入：未解析任何页"],
               "deliverables": [{"kind": "missing_input", "path": "missing_input.md",
                                 "bytes": 0}],
               "change_records": [{"步骤": "0 读输入", "动作": "rglob 源文件", "结果": "未找到",
                                   "脚本": "run.py"}],
               "判断层": {"unit": JUDGMENT_UNIT, "n": 0, "filled": 0, "pending": 0,
                        "source": "没有原文，没有可判要点", "notes_status": "none",
                        "who_fills": "调用方模型", "who_checks": "run.py", "items": []},
               "output_contract": {str(k): {"note": "无产物：找不到原文",
                                            "pointer": ["missing_input.md"]}
                                   for k in contract},
               "output_contract_echo": {str(k): {"status": "partial", "source": "pointer"}
                                        for k in contract}}
        (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        (out / "missing_input.md").write_text("\n".join([
            "# 缺料说明：没有找到原文", "",
            f"- 输入目录：`{case_dir}`",
            "- 找过：该目录下所有 `.pdf` / `.txt` / `.md` / `.tex` / `.docx`（递归）",
            "- 需要补：待解析的论文全文（PDF 需带文字层，或直接给 `.md`/`.txt`）",
            "- 缺什么才能出产物：没有原文就没有块、没有章节，`notes.md` 无从生成",
            "- 补齐后重跑：`python run.py --input <目录> --work <w> --out <o>`",
            "- 这次运行没有任何笔记产物，状态记为 blocked，不冒充部分完成",
        ]) + "\n", encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked", "error": "找不到原文文件"},
                         ensure_ascii=False))
        return 1

    gaps: list[str] = []
    parser = load(Path(__file__).parent / "scripts" / "pdfparse.py")
    engine = "layout"
    if src.suffix.lower() == ".pdf":
        parse = parser.parse(src)
        audit = parse.get("heading_audit") or {}
    else:
        parse = text_to_parse(src)
        audit = parser.prune_headings(parse["pages"])
        parse["heading_audit"] = audit
        engine = "text"
    (work / "parse.json").write_text(json.dumps(parse, ensure_ascii=False), encoding="utf-8")
    if parse.get("no_text_pages"):
        gaps.append("无文字层页：" + ",".join(map(str, parse["no_text_pages"])))

    notes_mod = load(Path(__file__).parent / "scripts" / "notes.py")
    auto_notes, trace = notes_mod.build(parse)
    (work / "notes_auto.json").write_text(json.dumps(auto_notes, ensure_ascii=False, indent=1),
                                          encoding="utf-8")

    notes_path = Path(args.notes) if args.notes else Path("")
    use = auto_notes
    status = "auto"
    if args.notes and notes_path.exists():
        use = json.loads(notes_path.read_text(encoding="utf-8-sig"))
        status = "agent"
        for field in ("sections", "card", "knowledge"):
            if field not in use:
                gaps.append(f"notes.json 缺 {field}，回退到自动抽取")
                use = auto_notes
                status = "auto"
                break
        if status == "agent":
            for key in ("conflicts", "coverage"):
                if key not in use:
                    gaps.append(f"notes.json 缺 {key}：按自动模式重算该部分后核验")
                    use[key] = auto_notes[key]
    # 没给 --notes 时不冒充结论：产物照出（规则草案），notes_status 与 判断层 都标 pending
    notes_status_out = "agent" if status == "agent" else "pending"
    use.setdefault("meta", {}).update({"version": VERSION, "notes_status": notes_status_out,
                                       "fields": list(CARD_ORDER),
                                       "thresholds": {"N03_heading_coverage": 0.8}})

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
        gaps.append("笔记由内置规则生成：标题块切节 → 六类字段信号挑句 → 卡片/知识点/冲突；"
                    "quote 与 span 指向原文逐字，未做人工改写")

    # ---------- 判断层：要点分类（模型给 --notes，脚本只核） ----------
    judgment = notes_judgment(use, status, notes_path, parse)
    if judgment["pending"]:
        gaps.append(f"判断层：{judgment['pending']} 条要点分类待模型判"
                    f"（未给 --notes，notes.md 是规则草案）")

    # 图表/公式锚点清单（caption 块本身就是锚点，标签从图注文本里取）
    ids = notes_mod.anchor_index(parse)
    floats = []
    for pg in parse["pages"]:
        for b in pg["blocks"]:
            if b["role"] != "caption":
                continue
            m = FLOAT_RE.match(b["text"] or "")
            aid = ids.get((pg["page"], id(b))) or {}
            floats.append({"kind": "表" if (m and m.group(1) in ("表", "Table")) else "图",
                           "label": (m.group(0).strip() if m else ""),
                           "page": pg["page"], "anchor": aid.get("p"),
                           "text": (b["text"] or "")[:120]})
    trace.update({"case_id": case_id, "source": str(src), "engine": engine,
                  "evidence_unit": "block id + span",
                  "pages": [{"page": p["page"], "columns": p.get("columns"),
                             "n_kept": sum(1 for b in p["blocks"] if b["role"] in KEEP),
                             "n_tables": p.get("n_tables") or 0,
                             "no_text": bool(p.get("empty")),
                             "headings": [b["text"][:80] for b in p["blocks"]
                                          if b["role"] == "heading"]}
                            for p in parse["pages"]],
                  "floats": floats[:60],
                  "heading_audit": {"原判": audit.get("n_before"), "保留": audit.get("n_after"),
                                    "by_reason": audit.get("by_reason") or {},
                                    "demoted": (audit.get("demoted") or [])[:40]},
                  "checks": {"判据": "N00–N09", "未过": ver.get("counts") or {}}})
    (out / "trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    index = build_index(use, case_id)
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1),
                                    encoding="utf-8")

    deliverables = []
    for name in ("notes.md", "notes.json", "card.json", "verify.json",
                 "index.json", "trace.json"):
        p = out / name
        deliverables.append({"kind": name.rsplit(".", 1)[0], "path": str(p),
                             "bytes": p.stat().st_size})
    checks = {
        "有章节": {"ok": not ver["counts"].get("N00"), "detail": ver["counts"].get("N00", 0)},
        "锚点": {"ok": not ver["counts"].get("N01"), "detail": ver["counts"].get("N01", 0)},
        "数字可回溯": {"ok": not ver["counts"].get("N02"), "detail": ver["counts"].get("N02", 0)},
        "章节对齐": {"ok": ver["sections_aligned"], "detail": ver["coverage"]},
        "字段完整": {"ok": not ver["counts"].get("N04"), "detail": ver["card_filled"]},
        "知识点不重复": {"ok": not ver["counts"].get("N05"), "detail": ver["counts"].get("N05", 0)},
        "冲突披露": {"ok": not ver["counts"].get("N06"), "detail": ver["n_conflicts_declared"]},
        "引用逐字": {"ok": not ver["counts"].get("N07"), "detail": ver["n_quote_verbatim"]},
        "章节承接": {"ok": not ver["counts"].get("N08"), "detail": ver["n_sections"]},
        "交接与未报告": {"ok": not ver["counts"].get("N09"), "detail": ver["n_handoff"]},
    }
    sec_list = use.get("sections") or []
    kn_list = use.get("knowledge") or []
    has_notes = bool(sec_list) and (bool(kn_list) or bool(ver["card_filled"]))
    result_status = "success" if ver["ok"] and has_notes else "partial"
    diag = diagnostics(parse, audit, use, ver)

    uncertainty: list[dict] = []
    for pgno in (parse.get("no_text_pages") or []):
        uncertainty.append({"项": f"第 {pgno} 页无文字层", "影响": "该页内容未进入笔记",
                            "处置": "需 OCR 或人工补读"})
    if diag["双栏"]["页数"]:
        uncertainty.append({"项": f"{diag['双栏']['页数']} 页为双栏",
                            "影响": "跨栏阅读顺序可能错位",
                            "处置": "锚点带页码，可逐页抽查"})
    for field, obj in (use.get("card") or {}).items():
        if (obj or {}).get("status") in ("not_reported", "undetermined"):
            hs = (obj or {}).get("handoff") or [{}]
            uncertainty.append({"项": f"{field}：{obj.get('status')}",
                                "影响": "该字段未由机器填出，不能写成事实",
                                "处置": hs[0].get("action", "人工确认"),
                                "检索词": hs[0].get("search_terms") or []})
    for c in use.get("conflicts") or []:
        uncertainty.append({"项": f"待核对：{c.get('field')} {c.get('kind')}",
                            "影响": c.get("machine_verdict"),
                            "处置": c.get("todo")})
    n_trunc = sum(1 for src_name, it in ver_mod.iter_items(use)
                  if it.get("truncated"))
    if n_trunc:
        uncertainty.append({"项": f"{n_trunc} 条 quote 按 240 字截断",
                            "影响": "长句后半段未进笔记", "处置": "按锚点回原文看全句"})
    if (audit.get("by_reason") or {}):
        uncertainty.append({"项": f"{audit.get('n_before', 0) - audit.get('n_after', 0)} 个标题被降级",
                            "影响": "疑表格首列/断行残片；若原文确有该节，骨架会缺",
                            "处置": "见 trace.json.heading_audit.demoted"})
    if not diag["图表"]["页面表格计数"] and diag["图表"]["图注表注块"]:
        uncertainty.append({"项": "未识别表格网格",
                            "影响": "表格单元格数据未结构化，只有表注锚点",
                            "处置": "需人工按页读表；本 Skill 不猜单元格"})

    change_records = [
        {"步骤": "0 读输入", "动作": "读 case.json 的 case_id / output_contract（字段名按别名匹配）",
         "产出": f"case_id={case_id}，契约字段 {len(contract)} 个", "脚本": "run.py"},
        {"步骤": "1 解析", "动作": f"源文件 {src.suffix} 按 {engine} 路径解析",
         "产出": f"{diag['篇幅']['页数']} 页 / {diag['篇幅']['正文块']} 正文块",
         "脚本": "scripts/pdfparse.py"},
        {"步骤": "2 切节", "动作": "标题块切节 + 承接边；假标题降级",
         "产出": f"保留标题 {audit.get('n_after', 0)} 个，成节 {len(sec_list)} 个，"
                 f"覆盖 {ver['coverage']['ratio']:.0%}", "脚本": "scripts/notes.py"},
        {"步骤": "3 抽取", "动作": "六类字段信号挑「信号所在句」，记 rule/匹配串/span",
         "产出": f"候选条目 {ver['n_items']} 条，知识点 {len(kn_list)} 条，"
                 f"冲突点 {len(use.get('conflicts') or [])} 个", "脚本": "scripts/notes.py"},
        {"步骤": "4 改写", "动作": "读调用方稿" if status == "agent" else "未给 --notes：notes.md 只是规则草案",
         "产出": f"notes_status={notes_status_out}，改写条数 {ver['n_rewritten']}，"
                 f"写明 reason {ver['n_reason']} 条"},
        {"步骤": "5 核验", "动作": "N00–N09 九项判据 + 阈值 N03≥80%",
         "产出": f"未过 {ver['counts'] or '无'}", "脚本": "scripts/verify_notes.py"},
        {"步骤": "6 落盘", "动作": "写 6 个产物 + result.json（含诊断/不确定性/变更记录）",
         "产出": ", ".join(d["kind"] for d in deliverables)},
    ]

    anchors = []
    for fld, obj in (use.get("card") or {}).items():
        for it in (obj or {}).get("items") or []:
            anchors.append({"来源": f"card.{fld}", "anchor": it.get("anchor"),
                            "page": it.get("page"), "span": it.get("span"),
                            "section": it.get("section")})
    for it in kn_list:
        anchors.append({"来源": f"knowledge.{it.get('kind')}", "anchor": it.get("anchor"),
                        "page": it.get("page"), "span": it.get("span"),
                        "section": it.get("section")})
    not_reported_rows = []
    for f in CARD_ORDER:
        obj = (use.get("card") or {}).get(f) or {}
        if obj.get("status") not in ("not_reported", "undetermined"):
            continue
        hs = (obj.get("handoff") or [{}])[0]
        not_reported_rows.append({"字段": f, "状态": obj.get("status"),
                                  "交接": hs.get("action"),
                                  "检索词": hs.get("search_terms") or [],
                                  "理由": hs.get("why")})
    contract_values = {
        "阅读/解析结果": {
            "节数": len(sec_list),
            "章节": [{"sec_id": s.get("sec_id"), "title": s.get("title"),
                      "role": s.get("role"), "page": s.get("page"),
                      "first_anchor": s.get("first_anchor"),
                      "carry_to": (s.get("carry") or {}).get("to")} for s in sec_list[:20]],
            "六字段状态": {f: {"status": (use.get("card") or {}).get(f, {}).get("status"),
                              "条数": len((use.get("card") or {}).get(f, {}).get("items") or [])}
                         for f in CARD_ORDER},
            "知识点": {"总数": len(kn_list),
                      "按 kind": {k: sum(1 for x in kn_list if x.get("kind") == k)
                                 for k in sorted({x.get("kind") for x in kn_list})}},
            "带锚点条目": ver["n_items"],
            "逐字证据条目": ver["n_quote_verbatim"],
            "标题覆盖": ver["coverage"],
            "可检索索引": {"词条数": index["n_tokens"], "条目数": index["n_items"]},
        },
        "证据与边界": {
            "证据单位": "block id + 字符区间 span（P<页>-<序> 段 / T<页>-<序> 表 / "
                        "F<页>-<序> 公式；span 为该块文本内的 [起,止)）",
            "锚点分布": ver["anchors_by_kind"], "锚点总数": len(anchors),
            "锚点明细": anchors[:20],
            "图表锚点": floats[:10],
            "逐字证据": {"条数": ver["n_quote_verbatim"],
                         "span 与 quote 对齐": ver["n_quote_verbatim"],
                         "改写条数": ver["n_rewritten"], "写明理由": ver["n_reason"]},
            "未报告与无法判断": [{"字段": f,
                                  "状态": (use.get("card") or {}).get(f, {}).get("status"),
                                  "交接": [(((use.get("card") or {}).get(f, {}) or {}).get("handoff") or [{}])[0].get("action"),
                                           (((use.get("card") or {}).get(f, {}) or {}).get("handoff") or [{}])[0].get("search_terms")]}
                                 for f in CARD_ORDER
                                 if (use.get("card") or {}).get(f, {}).get("status")
                                 in ("not_reported", "undetermined")],
            "冲突": {"派生": ver["n_conflicts_derived"], "已披露": ver["n_conflicts_declared"],
                     "条目": (use.get("conflicts") or [])[:6]},
            "缺口": gaps,
        },
        "运行状态": {
            "status": result_status, "notes_status": notes_status_out,
            "检查": {k: v["ok"] for k, v in checks.items()},
            "未过检查": [k for k, v in checks.items() if not v["ok"]],
            "耗时秒": round(time.time() - t0, 2),
            "变更记录": len(change_records),
            "判据统计": {"条目": ver["n_items"], "未报告字段": ver["n_not_reported"],
                         "无法判断字段": ver["n_undetermined"], "交接项": ver["n_handoff"]},
        },
    }
    contract_src = {"阅读/解析结果": "notes.md + notes.json + index.json",
                    "证据与边界": "notes.json 的 anchor/quote/span/conflicts + trace.json",
                    "运行状态": "verify.json 的 ok/counts + result.json.change_records"}
    alias = (("阅读", "解析"), ("证据", "边界"), ("运行", "状态"))
    echo, echo_status = {}, {}
    for key in contract:
        name = str(key)
        hit = None
        for canon, words in (("阅读/解析结果", alias[0]), ("证据与边界", alias[1]),
                             ("运行状态", alias[2])):
            if any(w in name for w in words):
                hit = canon
                break
        if hit:
            echo[key] = contract_values[hit]
            echo_status[key] = {"status": "full", "source": contract_src[hit]}
        else:
            echo[key] = {"note": "本字段无专门产出，见 notes.md / notes.json / trace.json",
                         "pointer": ["notes.md", "notes.json", "trace.json"]}
            echo_status[key] = {"status": "partial", "source": "pointer"}

    rules = {
        "方式": "标题块切节（假标题降级）→ 六类字段信号挑「信号所在句」→ 卡片/知识点/冲突",
        "切节": {"节数": len(sec_list), "标题块": [s.get("title") for s in sec_list[:20]],
                 "标题审计": {"原判": audit.get("n_before"), "保留": audit.get("n_after"),
                              "降级原因": audit.get("by_reason") or {}}},
        "字段命中": {f: sum(len(s.get("candidates", {}).get(k) or []) for s in sec_list)
                     for f, k in (("研究问题", "question"), ("数据", "data"),
                                  ("方法条件", "method"), ("主要发现", "finding"),
                                  ("结论", "conclusion"), ("局限", "limitation"))},
        "知识点": len(kn_list),
        "知识点类型": {k: sum(1 for x in kn_list if x.get("kind") == k)
                       for k in sorted({x.get("kind") for x in kn_list})},
        "卡片已填字段": ver["card_filled"],
        "索引词条": index["n_tokens"],
    }
    result = {"case_id": case_id,
              "status": result_status,
              "notes_status": notes_status_out,
              "判断层": judgment,
              "sources": {"input": str(case_dir), "source_file": str(src),
                          "engine": engine, "case_json": bool(contract),
                          "parser": "scripts/pdfparse.py", "version": VERSION},
              "source": {"path": str(src), "n_pages": parse.get("n_pages"),
                         "n_sections": len(sec_list)},
              "stats": {"sections": len(sec_list),
                        "knowledge": len(kn_list),
                        "items": ver["n_items"],
                        "card_filled": ver["card_filled"],
                        "quotes_verbatim": ver["n_quote_verbatim"],
                        "conflicts": len(use.get("conflicts") or []),
                        "index_tokens": index["n_tokens"],
                        "elapsed_s": round(time.time() - t0, 2)},
              "deliverables": deliverables,
              "evidence": {"unit": "block id + span",
                           "note": "P4-12 = 第 4 页第 12 个正文块；T4-1 = 第 4 页第 1 张表；"
                                   "F4-1 = 第 4 页第 1 个公式块；span 为块文本内 [起,止)",
                           "anchors_by_kind": ver["anchors_by_kind"],
                           "floats": floats[:10]},
              "checks": checks,
              "gaps": gaps,
              "uncertainty": uncertainty,
              "diagnostics": diag,
              "conflicts": use.get("conflicts") or [],
              "coverage": use.get("coverage") or ver["coverage"],
              "change_records": change_records,
              "规则命中": rules,
              "verify": {"ok": ver["ok"], "counts": ver["counts"],
                         "n_problems": ver["n_problems"],
                         "problems": ver["problems"][:20]},
              "budget": {"模型请求": 0, "网络": 0, "脚本": "scripts/*.py（纯规则）",
                         "上限参考": "平台 64 次请求 / 900 s"},
              "output_contract": echo, "output_contract_echo": echo_status}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": result_status, "sections": result["stats"]["sections"],
                      "knowledge": result["stats"]["knowledge"],
                      "quotes_verbatim": result["stats"]["quotes_verbatim"],
                      "notes_status": notes_status_out,
                      "judgment": {"unit": JUDGMENT_UNIT, "filled": judgment["filled"],
                                   "n": judgment["n"]},
                      "uncertainty": len(uncertainty),
                      "gaps": gaps}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
