#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py — 文献文档解析 Skill 的编排入口。

输入：/input（case.json、task.md、PDF）
输出：/app/output（parsed.md、blocks.json、sections.json、tables/、formulas.md、
      figures.md、footnotes.md、result.json）

步骤：
  0 读输入：case.json 的 case_id 与 output_contract，嗅探 PDF
  1 解析：pdfparse（栏、行、页眉页脚、标题层级、表格网格、公式行重建、图片对象框）
  2 后处理：postpass（脚注、段落与跨页续接、图与题注配对、表格未归属文字）
  3 组装：parsed.md（阅读顺序）+ sections.json（层级）+ tables/*.md + 三份清单
  4 自检：页覆盖、表格列数、表格归属、公式原文保真、页眉页脚剔除、块依据
  5 落盘：result.json（契约回显 + 证据 + 边界）

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
    spec = importlib.util.spec_from_file_location(path.stem, path)
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


# ---------------------------------------------------------------------------
# 判断层：块归哪一节。脚本给候选（阅读顺序上最近的两个标题），模型判；
# 图表与公式按题面「不要求理解科学含义」保持脚本判（阅读顺序），不进判断项。
# ---------------------------------------------------------------------------
JUDGMENT_UNIT = "块归属章节"
FRONT_SECTION = "（题名与前置部分）"
SCRIPT_ROLES = ("caption", "table", "formula")
JUDGE_ROLES = ("body", "reference", "title")


def _block_uid(b: dict, seq: int) -> str:
    return f"P{b['page']}-{seq}"


def block_section_plan(pages: list[dict]) -> list[dict]:
    """逐块给候选章节：阅读顺序上最近的标题、下一个标题，以及脚本的默认归属。"""
    flat = [(p, b) for p in pages for b in p["blocks"]]
    nxt = [None] * len(flat)
    seen = None
    for i in range(len(flat) - 1, -1, -1):
        nxt[i] = seen
        if flat[i][1]["role"] == "heading":
            seen = flat[i][1]["text"]
    rows: list[dict] = []
    cur = None
    for i, (_p, b) in enumerate(flat):
        if b["role"] == "heading":
            cur = b["text"]
            continue
        cols: list[str] = []
        if cur:
            cols.append(cur)
        else:
            cols.append(FRONT_SECTION)
        if nxt[i] and nxt[i] not in cols:
            cols.append(nxt[i])
        rows.append({
            "id": _block_uid(b, i + 1), "page": b["page"], "role": b["role"],
            "text": (b.get("text") or "")[:120],
            "候选": cols, "脚本归属": (cur or FRONT_SECTION),
            "判断": b["role"] in JUDGE_ROLES,
        })
    return rows


def load_block_judgments(p: Path | None) -> tuple:
    """读 judgments.json：items[{id, section, basis, filled_by}]。读不到返回空表。"""
    if not p or not p.exists():
        return {}, (f"未找到 {p}" if p else "未提供 judgments.json")
    try:
        obj = json.loads(p.read_text(encoding="utf-8-sig"))
        raw = obj.get("items") if isinstance(obj, dict) else obj
        got = {str(it["id"]): it for it in (raw or [])
               if isinstance(it, dict) and it.get("id")}
        return got, f"{p}（{len(got)} 条）"
    except Exception as e:
        return {}, f"{p} 读不动：{type(e).__name__}: {e}"


def block_section_layer(rows: list[dict], decisions: dict, dec_src: str, out: Path) -> dict:
    """判断层：把模型给的行级归属合成进 result.json；没填的标 pending。"""
    items, exempt, filled = [], [], 0
    mapping = []
    for r in rows:
        if not r["判断"]:
            exempt.append({"id": r["id"], "role": r["role"],
                           "脚本归属": r["脚本归属"],
                           "理由": "图表/公式不要求理解科学含义，归节按阅读顺序由脚本定"})
            mapping.append({"id": r["id"], "page": r["page"], "role": r["role"],
                            "section": r["脚本归属"], "by": "脚本（阅读顺序）"})
            continue
        got = decisions.get(r["id"]) or {}
        val = str(got.get("section") or got.get("decision") or "").strip()
        text = r["text"]
        basis = str(got.get("basis") or "")
        if val:
            filled += 1
        mapping.append({"id": r["id"], "page": r["page"], "role": r["role"],
                        "section": val or "pending",
                        "by": "调用方模型（judgments.json）" if val else ""})
        items.append({
            "id": r["id"], "item": JUDGMENT_UNIT, "page": r["page"], "role": r["role"],
            "原文": text,
            "候选": r["候选"], "脚本归属": r["脚本归属"],
            "value": val or "pending",
            "basis": basis[:120],
            "filled_by": str(got.get("filled_by") or "caller") if val else "",
            "status": "filled" if val else "pending",
            "in_candidates": (val in r["候选"] or val in (got.get("candidates") or []))
                             if val else None,
            "basis_verbatim": (basis in text) if (val and basis) else None,
        })
    n = len(items)
    L = [f"# 判断单：{JUDGMENT_UNIT}", "",
         f"- 待判 {n} 块；`exempt` 另有 {len(exempt)} 块（图表/公式，脚本按阅读顺序定）",
         "- 填到：`judgments.json`：`{\"items\":[{\"id\",\"section\",\"basis\",\"filled_by\"}]}`",
         "- `section` 只能取该块候选里的标题；`basis` 必须是该块原文的逐字片段",
         "- 不填的块由 `run.py` 标 `pending`，不按脚本默认归属冒充模型结论", "",
         "| id | 页 | 角色 | 块文本 | 候选 | 脚本归属 |", "|-|-|-|-|-|-|"]
    for it in items:
        c = str(it["原文"]).replace("|", "\\|")[:60]
        L.append(f"| `{it['id']}` | {it['page']} | {it['role']} | {c} | "
                 f"{' / '.join(it['候选']).replace('|', '/')} | {it['脚本归属']} |")
    (out / "block_section_worklist.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (out / "block_sections.json").write_text(
        json.dumps({"unit": JUDGMENT_UNIT, "blocks": mapping}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return {
        "unit": JUDGMENT_UNIT,
        "who_fills": "调用方模型：读 block_section_worklist.md，把块→节写进 judgments.json",
        "who_checks": "run.py 核 section 是否在该块候选内、basis 是否该块原文子串；"
                      "图表/公式不进判断项，按阅读顺序由脚本定",
        "source": dec_src if decisions else "未填：只给候选与脚本默认归属",
        "n": n, "filled": filled, "pending": n - filled,
        "exempt": exempt,
        "items": items,
        "mapping": "block_sections.json（全量块→节，含脚本定的图表/公式块）",
    }


def docmeta(pages: list[dict]) -> dict:
    """取文档级元数据：标题、作者、单位、摘要、关键词。

    只用版面块的角色与顺序，不做语义猜测：标题取 role=title（字号 ≥ 正文 1.5 倍
    的合并块），作者与单位取标题后两块，摘要取 Abstract/摘要 标题到下一个标题
    之间的正文。取不到的字段留空串，不编造。
    """
    meta = {"title": "", "authors": "", "affiliation": "",
            "abstract": "", "keywords": ""}
    if not pages:
        return meta
    p1 = pages[0]
    blocks = [b for b in p1["blocks"]
              if b["role"] not in ("header", "footer", "page_number", "table")]
    titles = [b["text"] for b in blocks if b["role"] == "title"]
    if titles:
        meta["title"] = " ".join(titles).strip()
        idx = max(i for i, b in enumerate(blocks) if b["role"] == "title")
        after = blocks[idx + 1: idx + 4]
        if after:
            meta["authors"] = after[0]["text"].strip()
        for b in after[1:]:
            t = b["text"].strip()
            if (re.search(r"(?i)(university|institute|department|college|laborator|"
                          r"大学|学院|研究所|实验室|@)", t)
                    or re.search(r"\d{5,6}", t)):
                meta["affiliation"] = t
                break
    for i, b in enumerate(blocks):
        if (re.match(r"(?i)^(abstract|摘要)\b", b["text"].strip())
                and b["role"] in ("heading", "body")):
            buf = []
            for c in blocks[i + 1:]:
                if c["role"] in ("heading", "title"):
                    break
                buf.append(c["text"])
            meta["abstract"] = " ".join(buf).strip()[:1200]
            break
    for b in blocks:
        if re.match(r"(?i)^(keywords?|关键词)", b["text"].strip()):
            meta["keywords"] = b["text"].strip()
            break
    return meta


def _bbox(bb) -> str:
    try:
        return "[" + ", ".join(("%.1f" % float(v)) for v in bb) + "]"
    except Exception:
        return "[]"


def to_markdown(pages: list[dict], figures: list[dict],
                notes: list[dict]) -> tuple[str, list[str]]:
    """阅读顺序正文。表格给网格，公式给"行内标记 + 逐字原文"，图给位置，脚注按页列。"""
    lines: list[str] = []
    gaps: list[str] = []
    for p in pages:
        lines.append(f"\n<!-- page {p['page']} -->\n")
        if p["empty"]:
            gaps.append(f"第 {p['page']} 页没有文字层")
            lines.append("_（本页无可提取文字层）_\n")
            continue
        for f in figures:
            if f.get("page") != p["page"] or f.get("kind") != "image":
                continue
            lines.append(f"<!-- 图 页 {f['page']} bbox={_bbox(f['bbox'])} "
                         f"{f.get('width')}×{f.get('height')} pt "
                         f"题注={f.get('caption') or '（60 pt 内无图类题注）'} -->")
        for b in p["blocks"]:
            role = b["role"]
            if role in ("header", "footer", "page_number", "footnote"):
                continue                                    # 脚注在页末单独列
            if b.get("continued_from"):
                lines.append(f"<!-- 跨页续接：页 {b['continued_from']} → 页 {p['page']}，"
                             f"段 {b.get('para')} 接着上页未收尾的正文 -->")
            if role == "title":
                lines.append("# " + b["text"] + "\n")
            elif role == "heading":
                lines.append("#" * min(int(b.get("level") or 3), 6) + " " + b["text"] + "\n")
            elif role == "table":
                lines.append(b.get("markdown") or "")
                lines.append("")
            elif role == "caption":
                lines.append("**" + b["text"] + "**\n")
            elif role == "formula":
                lines.append("$$ " + (b.get("inline") or b["text"]) + " $$")
                lines.append("")
                lines.append(f"> 公式 ({b.get('formula_no') or '?'}) 原文："
                             f"{b.get('original') or b['text']}｜bbox {_bbox(b['bbox'])}｜"
                             f"上下标 {len(b.get('scripts') or [])} 个｜"
                             f"特殊符号 {len(b.get('symbols') or [])} 个\n")
            else:
                lines.append(b["text"])
        fn = [n for n in notes if n["page"] == p["page"]]
        for n in fn:
            lines.append(f"<!-- 脚注 页 {n['page']} 标记 {n['marker']} "
                         f"{n['size']}pt bbox={_bbox(n['bbox'])} -->")
            lines.append(f"- 脚注 {n['marker']}：{n['text']}")
        lines.append("")
    return "\n".join(lines), gaps


def _pointer(out: Path, kind: str) -> dict:
    p = out / kind
    return {"kind": kind, "path": str(p), "bytes": p.stat().st_size if p.exists() else 0}


def write_md(out: Path, name: str, body: str) -> Path:
    p = out / name
    p.write_text(body, encoding="utf-8")
    return p


def formulas_md(formulas: list[dict]) -> str:
    head = ["# 公式原文对照（按编号）", "",
            "编号取自行尾 `(n)`；`逐字原文` 是字符块按 x 顺序的原样拼接（不改字），",
            "`行内标记` 把小于正文 88% 字号、且基线抬高/降低的字符块标成 `^{}` / `_{}`。", "",
            "| 编号 | 页 | bbox | 逐字原文 | 行内标记 | 上下标 | 特殊符号 | 字符块 |",
            "|-|-|-|-|-|-|-|-|"]
    for f in formulas:
        scr = ", ".join("%s%s" % (s["text"], "↑" if s["kind"] == "sup" else "↓")
                        for s in f.get("scripts") or []) or "—"
        sym = ", ".join("%s(%s)" % (s["char"], s["cp"]) for s in f.get("symbols") or []) or "—"
        head.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            f.get("no") or "?", f["page"], _bbox(f["bbox"]),
            (f.get("original") or "").replace("|", "\\|"),
            (f.get("inline") or "").replace("|", "\\|"), scr, sym,
            len(f.get("chunks") or [])))
    if not formulas:
        head.append("| — | — | — | 本文未检出带编号的公式 | — | — | — | 0 |")
    for f in formulas:
        head += ["", "## 公式 (%s)｜页 %s" % (f.get("no") or "?", f["page"]), "",
                 "字符块（依据，坐标为 visitor 实测）：", "",
                 "| x | y | 字号 | 文本 |", "|-|-|-|-|"]
        for c in f.get("chunks") or []:
            head.append("| %s | %s | %s | %s |" % (c["x"], c["y"], c["size"],
                                                   (c["text"] or "").replace("|", "\\|")))
    return "\n".join(head) + "\n"


def figures_md(figures: list[dict]) -> str:
    head = ["# 图表清单（位置 + 题注配对）", "",
            "`image` 行是内容流 `cm`/`Do` 还原出的图片对象框；`caption-only` 行只有题注、",
            "本页未检出图片对象（矢量图形），如实标注不补框。", "",
            "| 页 | 类型 | bbox | 宽×高(pt) | 题注 | 关系 | 间距(pt) | 说明 |",
            "|-|-|-|-|-|-|-|-|"]
    for f in figures:
        head.append("| %s | %s | %s | %s×%s | %s | %s | %s | %s |" % (
            f.get("page"), f.get("kind"), _bbox(f.get("bbox") or []),
            f.get("width"), f.get("height"),
            (f.get("caption") or "—").replace("|", "\\|"),
            f.get("caption_relation") or "—",
            "—" if f.get("caption_gap") is None else f.get("caption_gap"),
            (f.get("note") or "").replace("|", "\\|")))
    if not figures:
        head.append("| — | — | — | — | 本文未检出图片对象与图类题注 | — | — | — |")
    return "\n".join(head) + "\n"


def footnotes_md(notes: list[dict]) -> str:
    head = ["# 脚注清单", "",
            "判据：页面下部 20% 以内、字号 ≤ 正文 92%、行首带 `*` `†` `‡` `§` `¶` 或 1–2 位数字标记。",
            "`正文是否引用` 一列：符号标记回正文找 `字+标记`；数字标记与上标无法区分，记 `?` 并写明原因。", "",
            "| 页 | 标记 | 字号 | 占比 | bbox | 原文 | 正文是否引用 |",
            "|-|-|-|-|-|-|-|"]
    for n in notes:
        head.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            n["page"], n["marker"], n["size"], n["size_ratio"], _bbox(n["bbox"]),
            (n["text"] or "").replace("|", "\\|"),
            "?" if n["referenced_in_body"] is None else ("是" if n["referenced_in_body"] else "否")))
    if not notes:
        head.append("| — | — | — | — | — | 本文未检出脚注 | — |")
    return "\n".join(head) + "\n"


def write_result(out: Path, case_id: str, contract: dict, res: dict, pack: dict) -> Path:
    """result.json：主结果 + 证据 + 边界 + 规则命中 + 契约回显。"""
    sections, tables, blocks = pack["sections"], pack["tables"], pack["blocks"]
    gaps, stats, checks, doc = pack["gaps"], pack["stats"], pack["checks"], pack["doc"]
    refs, status = pack["refs"], pack["status"]
    formulas, figures = pack["formulas"], pack["figures"]
    notes, paras = pack["footnotes"], pack["paragraphs"]
    orphans = pack["orphans"]
    deliverables = [_pointer(out, "parsed.md"), _pointer(out, "blocks.json"),
                    _pointer(out, "sections.json"), _pointer(out, "formulas.md"),
                    _pointer(out, "figures.md"), _pointer(out, "footnotes.md")]
    for t in tables:
        p = out / t["path"]
        if p.exists():
            deliverables.append({"kind": "table", "path": str(p), "bytes": p.stat().st_size,
                                 "table_no": t["no"], "n_rows": t["n_rows"],
                                 "n_cols": t["n_cols"]})
    measured = sum(1 for b in blocks if not b.get("bbox_est"))
    estimated = sum(1 for b in blocks if b.get("bbox_est"))
    why = {}
    for b in blocks:
        why[b.get("why") or "?"] = why.get(b.get("why") or "?", 0) + 1
    # 契约字段装真实内容：解析出的标题/章节/表/公式/图表/脚注与样本框，不是文件名。
    payload = {
        "parsed": {"pages": res["n_pages"],
                   "title": doc["title"], "authors": doc["authors"],
                   "affiliation": doc["affiliation"],
                   "abstract_chars": len(doc["abstract"]),
                   "headings": [b["text"] for b in blocks if b["role"] == "heading"][:12],
                   "tables": [{"page": t["page"], "n_rows": t["n_rows"],
                               "n_cols": t["n_cols"],
                               "cells_filled": t.get("cells_filled")} for t in tables],
                   "formulas": [{"page": f["page"], "no": f.get("no"),
                                 "original": f.get("original"),
                                 "inline": f.get("inline"),
                                 "scripts": len(f.get("scripts") or []),
                                 "symbols": len(f.get("symbols") or [])}
                                for f in formulas],
                   "figures": [{"page": f["page"], "kind": f["kind"],
                                "bbox": f["bbox"], "caption": f.get("caption")}
                               for f in figures],
                   "footnotes": [{"page": n["page"], "marker": n["marker"],
                                  "text": n["text"]} for n in notes],
                   "paragraphs": len(paras),
                   "references": len(refs)},
        "evidence": {"unit": "page+bbox+para",
                     "measured_blocks": measured, "estimated_blocks": estimated,
                     "sample": [{"page": b["page"], "role": b["role"],
                                 "bbox": b["bbox"], "para": b.get("para"),
                                 "why": b.get("why")} for b in blocks[:3]],
                     "formula_evidence": "formulas.md 逐条列字符块的 x/y/字号/文本",
                     "checks": checks, "gaps": gaps},
        "status": {"status": status,
                   "reason": gaps[0] if gaps else "全部页面解析成功，无 gap",
                   "n_pages": res["n_pages"], "n_gaps": len(gaps)},
        "goals": [{"kind": d["kind"], "bytes": d["bytes"]} for d in deliverables],
        "map": {"sections": [{"title": s["title"], "level": s["level"], "page": s["page"]}
                             for s in sections],
                "references": refs[:8],
                "figures": [{"page": f["page"], "caption": f.get("caption")} for f in figures[:8]],
                "footnotes": [{"page": n["page"], "marker": n["marker"]} for n in notes[:8]]},
        "bounds": {"skill_version": "1.2.0", "gaps": gaps,
                   "bbox_estimated_blocks": estimated,
                   "no_ocr": True, "no_cross_page_table_merge": True},
    }
    rules = [
        # 平台字段名精确匹配（先于模糊规则）
        (r"^阅读[/／]解析结果", "parsed", "full" if (doc["title"] or sections
                                                   or tables or res["n_pages"]) else "partial"),
        (r"^证据与边界", "evidence", "full" if measured else "partial"),
        (r"^运行状态", "status", "full"),
        (r"^目标产物", "goals", "full" if deliverables else "partial"),
        (r"^事实与来源映射", "map", "full" if (sections or refs or figures) else "partial"),
        (r"^版本与边界", "bounds", "full"),
        (r"公式|formula|equation", "parsed", "full" if formulas else "partial"),
        (r"图表|插图|figure", "parsed", "full" if figures else "partial"),
        (r"脚注|footnote", "parsed", "full" if notes else "partial"),
        (r"段落|paragraph|跨页|continuation", "parsed", "full" if paras else "partial"),
        (r"text|markdown|正文|全文|parsed", "parsed",
         "full" if res["n_pages"] else "partial"),
        (r"(?<!re)table|表格", "tables", "full" if tables else "partial"),
        (r"section|章节|结构|heading", "map", "full" if sections else "partial"),
        (r"block|布局|layout|span|bbox|定位", "evidence", "full" if measured else "partial"),
    ]
    echo, echo_status = {}, {}
    for key in contract:
        kind, st = "parsed", "partial"
        for pat, k, s in rules:
            if re.search(pat, key, re.I):
                kind, st = k, s
                break
        if kind == "tables":
            echo[key] = payload["parsed"]["tables"] if tables else \
                {"note": "本文未检出表格网格", "count": 0}
        else:
            echo[key] = payload.get(kind) or {
                "note": "本字段无专门产出", "parsed": payload["parsed"]}
        echo_status[key] = {"status": st, "source": kind}

    result = {
        "output_contract_echo": echo_status,
        "case_id": case_id,
        "status": status,
        "source": {"path": res["source"], "sha256": sha(Path(res["source"])),
                   "n_pages": res["n_pages"], "parsed_pages": res["pages_parsed"],
                   "encrypted": res["encrypted"]},
        "docmeta": doc,
        "stats": stats,
        "sections": sections,
        "tables": [{k: t[k] for k in ("no", "page", "n_rows", "n_cols", "path",
                                      "cells_total", "cells_filled", "n_assigned",
                                      "lines", "bbox") if k in t}
                   for t in tables],
        "table_unassigned": orphans,
        "formulas": formulas,
        "figures": figures,
        "footnotes": notes,
        "paragraphs": paras,
        "references": [{"page": b["page"], "text": b["text"]}
                       for b in blocks if b["role"] == "reference"],
        "deliverables": deliverables,
        "evidence": {"unit": "page+bbox+para", "blocks": "blocks.json",
                     "note": "每个 block 带页码、bbox 与 why（判据名）：未标 bbox_est 的是 "
                             "visitor_text 实测坐标，标 bbox_est 的是段文本未能匹配字符块的"
                             "推算框；para 是段号，continued_from 记跨页续接的上一页"},
        "gaps": gaps,
        "checks": checks,
        "规则命中": {
            "块角色": {r: sum(1 for b in blocks if b["role"] == r)
                    for r in sorted({b["role"] for b in blocks})},
            "依据分布": why,
            "栏数": [p.get("columns") for p in res.get("pages", [])],
            "表格网格": [{"page": t["page"], "n_rows": t["n_rows"], "n_cols": t["n_cols"],
                      "lines": t.get("lines"), "cells_filled": t.get("cells_filled"),
                      "n_assigned": t.get("n_assigned")} for t in tables],
            "公式行重建": [{"page": f["page"], "no": f.get("no"),
                       "scripts": f.get("scripts"), "chunks": len(f.get("chunks") or [])}
                      for f in formulas],
            "图片对象": [{"page": f["page"], "name": f.get("name"), "bbox": f["bbox"],
                      "caption_gap": f.get("caption_gap")} for f in figures
                     if f.get("kind") == "image"],
            "脚注判据": "页下部 20%、字号 ≤ 正文 92%、行首符号标记；符号回正文找引用",
            "bbox": {"实测": measured, "推算": estimated},
            "规则": "块角色按字号/字号比与位置判；表格按线框连通区域 + 中心点入格；"
                    "公式按字符块字号与基线重建上下标；图片框按内容流 CTM 还原",
        },
    }
    result["阅读/解析结果"] = payload["parsed"]
    result["证据与边界"] = payload["evidence"]
    result["运行状态"] = payload["status"]
    result["判断层"] = pack.get("judgment") or {
        "unit": JUDGMENT_UNIT, "n": 0, "filled": 0, "pending": 0,
        "source": "未跑到组装步骤，没有可判块", "items": [], "exempt": [],
        "who_fills": "调用方模型", "who_checks": "run.py"}
    result.setdefault("uncertainty", list(gaps))
    p = out / "result.json"
    p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="文献文档解析")
    ap.add_argument("--input", default=None, help="平台输入目录（含 case.json / PDF）")
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--judgments", default=None,
                    help="块归属章节的判断（judgments.json）；不给则找输入目录/PDF 同目录的 judgments.json")
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
            # 失败也要留下契约载体与可读说明：不能只写 stdout。
            cid = case_id or (Path(args.input).name if args.input else "case")
            blocked = {
                "case_id": cid,
                "status": "blocked",
                "error": "输入目录里没有 PDF",
                "deliverables": ["missing_input.md"],
                "evidence": {"unit": "page+bbox+para", "note": "未解析，无证据"},
                "checks": {},
                "gaps": ["输入目录里没有 PDF"],
                "判断层": {"unit": JUDGMENT_UNIT, "n": 0, "filled": 0, "pending": 0,
                         "source": "没有 PDF，没有可判块", "items": [], "exempt": [],
                         "who_fills": "调用方模型", "who_checks": "run.py"},
                "output_contract": {k: {"note": "未运行", "pointer": []} for k in contract},
                "output_contract_echo": {k: {"status": "partial", "source": "pointer"}
                                         for k in contract},
            }
            (out / "result.json").write_text(
                json.dumps(blocked, ensure_ascii=False, indent=1), encoding="utf-8")
            (out / "missing_input.md").write_text("\n".join([
                "# 缺料说明：输入目录里没有 PDF", "",
                f"- 输入目录：`{args.input}`" if args.input else "- 未给 `--input`，也没有 `--pdf`",
                "- 找过：该目录（含子目录）下所有以 `%PDF-` 开头的文件",
                "- 需要补：待解析的 PDF（需带文字层；扫描件无文字层，本 Skill 不内置 OCR）",
                "- 缺什么才能出产物：没有 PDF 就没有页面与版面块，`parsed.md` / "
                "`blocks.json` / `tables/` 无从生成",
                "- 补齐后重跑：`python run.py --input <目录> --work <w> --out <o>`",
            ]) + "\n", encoding="utf-8")
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
        if not pdf.exists():
            # 指了路径但文件不在：走同一套缺料分支，不要抛栈
            (out / "result.json").write_text(json.dumps(
                {"case_id": case_id, "status": "blocked",
                 "error": f"PDF 不存在：{pdf}", "deliverables": ["missing_input.md"],
                 "gaps": [f"PDF 不存在：{pdf}"],
                 "output_contract": {k: {"note": "未运行", "pointer": []} for k in contract},
                 "output_contract_echo": {k: {"status": "partial", "source": "pointer"}
                                          for k in contract}}, ensure_ascii=False, indent=1),
                encoding="utf-8")
            (out / "missing_input.md").write_text("\n".join([
                "# 缺料说明：指定的 PDF 不存在", "",
                f"- 指定路径：`{pdf}`",
                "- 需要补：一份带文字层的 PDF；或把 PDF 放进 `--input` 目录后重跑",
                "- 缺什么才能出产物：没有 PDF 就没有页面与版面块，`parsed.md` 无从生成",
                "- 补齐后重跑：`python run.py --pdf <文件> --work <w> --out <o>`",
            ]) + "\n", encoding="utf-8")
            print(json.dumps({"ok": False, "status": "blocked", "error": f"PDF 不存在：{pdf}"},
                             ensure_ascii=False))
            return 1

    parser = load(Path(__file__).parent / "scripts" / "pdfparse.py")
    post = load(Path(__file__).parent / "scripts" / "postpass.py")
    res = parser.parse(pdf)
    (work / "parse_raw.json").write_text(json.dumps(res, ensure_ascii=False),
                                         encoding="utf-8")

    pages = res["pages"]
    # 后处理只标关系，不改字：单页页眉页脚、脚注、段落与跨页续接、
    # 图与题注配对、表格未归属文字
    single_heads = post.single_page_heads(pages)
    notes = post.footnotes(pages)
    paras = post.paragraphs(pages)
    figures = post.link_figures(res.get("figures") or [], pages)
    orphans = post.table_orphans(pages)

    blocks = [b for p in pages for b in p["blocks"]]
    sections = build_sections(pages)
    doc = docmeta(pages)
    refs = [b["text"] for b in blocks if b["role"] == "reference"]
    md, gaps = to_markdown(pages, figures, notes)
    (out / "parsed.md").write_text(md, encoding="utf-8")

    keys = ("page", "role", "text", "bbox", "size", "line", "col", "why",
            "bbox_est", "level", "formula_no", "para", "continued_from", "marker",
            "original", "inline")
    flat = [{k: b[k] for k in keys if k in b} for b in blocks]
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
                           "cells_total": b.get("cells_total"),
                           "cells_filled": b.get("cells_filled"),
                           "n_assigned": b.get("n_assigned"),
                           "lines": b.get("lines"),
                           "unassigned": b.get("unassigned") or [],
                           "why": b.get("why"),
                           "path": f"tables/table{len(tables) + 1}.md"})
            tdir.mkdir(parents=True, exist_ok=True)
            (out / tables[-1]["path"]).write_text(b["markdown"] + "\n", encoding="utf-8")

    formulas = [{"page": b["page"], "no": b.get("formula_no"), "bbox": b["bbox"],
                 "text": b["text"], "original": b.get("original") or b["text"],
                 "inline": b.get("inline") or b["text"],
                 "scripts": b.get("scripts") or [], "symbols": b.get("symbols") or [],
                 "chunks": b.get("chunks") or [], "why": b.get("why")}
                for b in blocks if b["role"] == "formula"]

    if res["n_pages"] == 0:
        gaps.append("PDF 未解析出任何页面（页树为空或页面对象损坏）")
    if res["no_text_pages"]:
        gaps.append("无文字层页：" + ",".join(str(x) for x in res["no_text_pages"])
                    + "（需 OCR，本 Skill 未内置 OCR）")
    if res["encrypted"]:
        gaps.append("PDF 加密，已尝试空口令解密")
    # 只有"单栏且正文只占页宽 <60%"才提示栏序可能未验证；整宽单栏页不提示
    narrow = []
    for p in pages:
        if p.get("columns", 1) != 1 or p.get("n_lines", 0) < 20:
            continue
        bs = [b for b in p["blocks"] if b.get("bbox")
              and b["role"] in ("body", "heading", "title", "caption", "reference")]
        if not bs:
            continue
        x0 = min(b["bbox"][0] for b in bs)
        x1 = max(b["bbox"][2] for b in bs)
        if (x1 - x0) < float(p.get("width", 595)) * 0.45:
            narrow.append(p["page"])
    if narrow:
        gaps.append("以下页判为单栏且正文宽度不足页宽 45%，若原文为多栏则栏序未验证："
                    + ",".join(map(str, narrow[:8])))
    cjk_chars = sum(len(CJK.findall(b.get("text") or "")) for b in blocks)
    if cjk_chars > 0:
        gaps.append(f"中文页共 {cjk_chars} 字：CJK 无词间空格，标题行分段以字距为准，"
                    "标题层级需人工核对")
    # 表格里落不进任何单元格的文字：如实列出，不猜、不并进相邻格
    if orphans:
        by_page = {}
        for o in orphans:
            by_page.setdefault(o["page"], 0)
            by_page[o["page"]] += 1
        gaps.append("表格未归属文字：" + "；".join(
            f"第 {pg} 页 {n} 块" for pg, n in sorted(by_page.items()))
            + "（原文列出，未并入相邻单元格）")
    for n in notes:
        if n["referenced_in_body"] is False:
            gaps.append(f"第 {n['page']} 页脚注标记 {n['marker']} 未在正文块中找到引用")

    body = [b for b in blocks if b["role"] == "body"]
    moji = sum(len(MOJI.findall(b["text"])) for b in body)
    chars = sum(len(b["text"]) for b in body) or 1
    stats = {"pages": res["n_pages"], "parsed_pages": res["pages_parsed"],
             "blocks": len(blocks), "body_blocks": len(body),
             "headings": sum(1 for b in blocks if b["role"] == "heading"),
             "tables": len(tables), "formulas": len(formulas),
             "formula_scripts": sum(len(f["scripts"]) for f in formulas),
             "formula_symbols": sum(len(f["symbols"]) for f in formulas),
             "captions": sum(1 for b in blocks if b["role"] == "caption"),
             "figures": sum(1 for f in figures if f["kind"] == "image"),
             "figure_caption_only": sum(1 for f in figures if f["kind"] == "caption-only"),
             "footnotes": len(notes),
             "paragraphs": len(paras),
             "cross_page_paragraphs": sum(1 for r in paras if r["cross_page"]),
             "references": sum(1 for b in blocks if b["role"] == "reference"),
             "headers": sum(1 for b in blocks if b["role"] == "header"),
             "footers": sum(1 for b in blocks if b["role"] == "footer"),
             "bbox_estimated": sum(1 for b in blocks if b.get("bbox_est")),
             "table_unassigned": len(orphans),
             "columns": [p["columns"] for p in pages],
             "chars": chars, "mojibake_ratio": round(moji / chars, 4),
             "elapsed_s": round(time.time() - t0, 2)}
    if stats["mojibake_ratio"] > 0.02:
        gaps.append(f"疑似缺字/乱码比例 {stats['mojibake_ratio']:.2%}，字体可能未嵌入")

    hf = [b["text"] for b in blocks if b["role"] in ("header", "footer")]
    leaked = [t for t in hf if t and t in md]
    ok_formula = sum(1 for f in formulas if f["original"] in md)
    checks = {
        "页覆盖": {"ok": res["pages_parsed"] == res["n_pages"],
                   "detail": f"{res['pages_parsed']}/{res['n_pages']}"},
        "表格列数一致": {"ok": all(len({len(r) for r in t["rows"]}) == 1 for t in tables),
                         "detail": [t["n_cols"] for t in tables]},
        "有正文": {"ok": len(body) > 0, "detail": len(body)},
        "页眉页脚已剔除": {"ok": (stats["headers"] + stats["footers"]) > 0 and not leaked,
                           "detail": f"header={stats['headers']} footer={stats['footers']}；"
                                     f"parsed.md 残留={len(leaked)}；"
                                     "为 0 表示未检出（无页眉页脚或未识别），不等同于已剔除"},
        "公式原文保真": {"ok": ok_formula == len(formulas),
                         "detail": f"parsed.md 里原文逐字出现的公式 {ok_formula}/{len(formulas)}；"
                                   "上下标 " + str(stats["formula_scripts"]) + " 个"},
        "表格单元归属": {"ok": not orphans,
                         "detail": f"未归属文字 {len(orphans)} 块"
                                   + ("（已列进 gaps 与 result.json）" if orphans else "")},
        "区块均有依据": {"ok": all(b.get("why") for b in blocks),
                         "detail": f"{sum(1 for b in blocks if b.get('why'))}/{len(blocks)} 块带 why"},
    }

    # 读不出的页要有一份可读清单：哪几页、为什么读不出、需要什么才能继续。
    # 状态看交付物：只要有页解析出来且抽到了文字，parsed.md 就是真产物；
    # 无文字层页、栏序未验证、CJK 提示这些都是版面边界，进 gaps 不压状态。
    unread = ([{"page": p, "reason": "无文字层（扫描件/字体未嵌入），需 OCR"}
               for p in res.get("no_text_pages") or []]
              + ([{"page": 0, "reason": "整份 PDF 未解析出页面（页树为空或页面对象损坏）"}]
                 if res["n_pages"] == 0 else []))
    text_found = bool(body) or any(b["role"] in ("title", "heading", "table", "caption",
                                                 "reference", "formula", "footnote")
                                   for b in blocks)
    status = ("success" if res["ok"] and res["pages_parsed"] > 0 and text_found
              else "partial")
    if unread or status != "success":
        lines = ["# 读不出或读不全的页", "",
                 f"- 源文件：`{pdf.name}`",
                 f"- 页数 {res['n_pages']}，成功解析 {res['pages_parsed']} 页，"
                 f"版面块 {len(blocks)} 个，正文块 {len(body)} 个", "",
                 "| 页码 | 原因 |", "|-|-|"]
        for u in unread:
            lines.append(f"| {u['page'] if u['page'] else '（整份）'} | {u['reason']} |")
        if not unread:
            lines.append("| — | 页面都能解析，但没有抽到正文块 |")
        lines += ["", "读不出的部分不做推测：`parsed.md` 只写真实抽到的文字，"
                      "缺的部分按页留空并在此说明。"]
        (out / "unreadable.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 三份清单：公式编号对照、图表位置、脚注
    write_md(out, "formulas.md", formulas_md(formulas))
    write_md(out, "figures.md", figures_md(figures))
    write_md(out, "footnotes.md", footnotes_md(notes))

    # 判断层：块归哪一节（脚本给候选，模型判；图表/公式不进判断项）
    jp = Path(args.judgments) if args.judgments else (Path(args.pdf).parent / "judgments.json"
                                                     if args.pdf else
                                                     (Path(args.input) / "judgments.json"
                                                      if args.input else Path("judgments.json")))
    decisions, dec_src = load_block_judgments(jp)
    judgment = block_section_layer(block_section_plan(pages), decisions, dec_src, out)
    if judgment["pending"]:
        gaps.append(f"判断层：{judgment['pending']} 块归属待模型判"
                    f"（候选见 block_section_worklist.md）")

    pack = {"sections": sections, "tables": tables, "blocks": blocks, "gaps": gaps,
            "stats": stats, "checks": checks, "doc": doc, "refs": refs,
            "status": status, "formulas": formulas, "figures": figures,
            "footnotes": notes, "paragraphs": paras, "orphans": orphans,
            "judgment": judgment}
    rp = write_result(out, case_id, contract, res, pack)
    print(json.dumps({"ok": True, "status": status,
                      "case_id": case_id, "result": str(rp),
                      "tables": len(tables), "formulas": len(formulas),
                      "figures": stats["figures"], "footnotes": len(notes),
                      "paragraphs": len(paras), "gaps": len(gaps),
                      "elapsed_s": stats["elapsed_s"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
