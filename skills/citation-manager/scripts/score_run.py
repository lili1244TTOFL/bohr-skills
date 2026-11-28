#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""score_run.py — 同口径客观度量：一份成品（谁做的都行）能被打出同一套数。

题面要求「通过公平对照证明质量或效率改善」，对照要用同一把尺子。本脚本只读成品，
不读 Skill 自己的日志，因此对基础 Agent 的成品同样可用；能算的算，算不了写 null
并在 `unavailable` 里写原因，不编数。

度量口径（全部可从文件复核，无人工打分）：
  文档层  上标标记数 / 非上标标记数 / 上标率 / 文末条目数 / 编号连续 / 首现递增 / 参考文献表存在
  编号层  引用点数 / 新增 vs 换号 / 唯一编号数 / 单句最多编号数 / 多句一引数
  条目层  库内条目数 / 被引 / 未被引 / 合并掉的重复 / 字段填充率(doi,volume,issue,pages,year,type)
  质量层  match / weak / mismatch / no_source 句数、并列候选数、点名作者未覆盖数、条目 error 数
  成本层  模型请求数 / 各步合计耗时 / 步骤数 / 缺口数 / 不确定项数
  人工量  需人工确认项总数与分类（这是「人工核对量」的可核验代理量）

用法：
  python score_run.py --docx output/manuscript_cited.docx --result output/result.json \
      --numbering work/numbering.json --lib work/library_deduped.json \
      --audit work/source_audit.json --coverage work/coverage.json --out output/metrics.json
  # 对基础 Agent 的成品只需给 --docx，缺的项记 null 并写 unavailable
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MARK = re.compile(r"\[([0-9,\-–\s]+)\]")
FIELDS = ("doi", "volume", "issue", "pages", "year", "type")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def parse_nums(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        part = part.strip()
        rng = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if rng:
            out.extend(range(int(rng.group(1)), int(rng.group(2)) + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def doc_metrics(path: Path) -> dict:
    from docx import Document
    doc = Document(str(path))
    sup_nums: list[int] = []
    n_sup = n_plain = 0
    refs: list[int] = []
    refs_at = None
    for i, p in enumerate(doc.paragraphs):
        t = p.text or ""
        if refs_at is None and t.strip().startswith("参考文献"):
            refs_at = i
            continue
        if refs_at is not None:
            m = re.match(r"^\s*\[(\d+)\]\s*(.+)$", t.strip())
            if m:
                refs.append(int(m.group(1)))
            continue
        for r in p.runs:
            if not (r.text or ""):
                continue
            hits = MARK.findall(r.text)
            if r.font.superscript and hits:
                n_sup += len(hits)
                for h in hits:
                    sup_nums.extend(parse_nums(h))
            elif hits and not r.font.superscript:
                n_plain += len(hits)
    first_seq: list[int] = []
    for n in sup_nums:
        if n not in first_seq:
            first_seq.append(n)
    expect = list(range(1, max(refs) + 1)) if refs else []
    return {"n_paras": len(doc.paragraphs),
            "refs_section": refs_at is not None,
            "n_ref_entries": len(refs),
            "n_markers_sup": n_sup, "n_markers_plain": n_plain,
            "n_markers": n_sup + n_plain,
            "superscript_ratio": round(n_sup / (n_sup + n_plain), 3) if (n_sup + n_plain) else None,
            "n_unique_nums": len(set(sup_nums)),
            "numbering_continuous": (refs == expect) if refs else None,
            "first_appearance_increasing": (first_seq == sorted(first_seq)) if first_seq else None,
            "first_seq": first_seq, "ref_numbers": refs}


def _read_json(p) -> dict | None:
    if not p:
        return None
    q = Path(p)
    if not q.exists():
        return None
    try:
        return json.loads(q.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def assemble(docx, numbering=None, lib=None, audit=None, coverage=None,
             result=None) -> dict:
    """把成品与（可选的）中间产物折成一份度量。run.py 与命令行共用同一实现。"""
    docp = Path(docx)
    if not docp.exists():
        return {"generated_by": "score_run.py", "ok": False,
                "error": f"找不到成品：{docp}"}
    m: dict = {"generated_by": "score_run.py", "ok": True, "docx": str(docp)}
    unavailable: list[dict] = []
    m["doc"] = doc_metrics(docp)

    num = _read_json(numbering)
    if num:
        points = [a for c in num.get("cites", []) for a in c.get("anchors", [])]
        per_sent = [len({n for a in c.get("anchors", []) for n in (a.get("nums") or [])})
                    for c in num.get("cites", [])]
        ids_multi: dict[str, set] = {}
        for c in num.get("cites", []):
            for a in c.get("anchors", []):
                for i in a.get("ids") or []:
                    ids_multi.setdefault(i, set()).add(c["uid"])
        m["numbering"] = {
            "mode": num.get("mode"), "style": num.get("style"),
            "n_cite_points": len(points),
            "n_insert": sum(1 for a in points if a.get("kind") == "insert"),
            "n_replace": sum(1 for a in points if a.get("kind") == "replace"),
            "n_refs": num.get("n_refs", len(num.get("refs", []))),
            "max_nums_per_sentence": max(per_sent) if per_sent else 0,
            "n_multi_sentence_refs": sum(1 for v in ids_multi.values() if len(v) > 1),
            "n_unmapped_markers": len(num.get("unmapped_markers") or []),
            "n_dropped_ids": len(num.get("dropped_ids") or []),
        }
    else:
        unavailable.append({"metric": "numbering",
                            "reason": "未给 numbering：对照方可从成品 docx 反推编号，"
                                      "但拿不到锚点与匹配依据"})

    libd = _read_json(lib)
    if libd:
        entries = libd.get("entries", [])
        fill = {}
        for f in FIELDS:
            fill[f] = (round(sum(1 for e in entries if (e.get(f) or "")) / len(entries), 3)
                       if entries else None)
        cited_ids = set()
        if num:
            for c in num.get("cites", []):
                for a in c.get("anchors", []):
                    cited_ids.update(a.get("ids") or [])
        m["library"] = {"n_entries": len(entries),
                        "n_cited": len(cited_ids) if num else None,
                        "n_uncited": (len([e for e in entries if e["id"] not in cited_ids])
                                      if num else None),
                        "n_merged_duplicates": len(libd.get("merged_index") or []),
                        "field_fill": fill}
    else:
        unavailable.append({"metric": "library", "reason": "未给库快照"})

    auditd = _read_json(audit)
    if auditd:
        m["quality"] = {"verdicts": auditd.get("verdict_counts"),
                        "codes": auditd.get("code_counts"),
                        "n_ambiguous": auditd.get("n_ambiguous"),
                        "n_missing_author": auditd.get("n_missing_author"),
                        "n_entry_error": auditd.get("n_error"),
                        "n_entry_warn": auditd.get("n_warn")}
    else:
        unavailable.append({"metric": "quality",
                            "reason": "未给核验产物（句级判定需要 Skill 侧的 source_audit）"})

    cov = _read_json(coverage)
    if cov:
        m["coverage"] = cov.get("totals")

    manual: dict[str, int] = {}
    src_items = (cov or {}).get("manual") if cov else None
    if src_items is None and auditd:
        src_items = auditd.get("manual_items")
    for it in src_items or []:
        k = it.get("kind", "other")
        manual[k] = manual.get(k, 0) + 1
    m["manual_review"] = {"n_items": sum(manual.values()), "by_kind": manual}

    res = result if isinstance(result, dict) else _read_json(result)
    if res:
        steps = res.get("steps") or []
        rule_hits = res.get("规则命中") or {}
        m["cost"] = {"status": res.get("status"),
                     "n_steps": len(steps),
                     "wall_ms_total": round(sum(float(s.get("ms") or 0) for s in steps), 1),
                     "n_model_requests": 2 - sum(1 for k in ("pass1", "match")
                                                 if rule_hits.get(k)),
                     "n_gaps": len((res.get("version_boundary") or {}).get("material_gaps") or []),
                     "n_uncertainty": len((res.get("version_boundary") or {}).get("uncertainty") or []),
                     "n_checks": len(res.get("checks") or {})}
    else:
        unavailable.append({"metric": "cost", "reason": "未给 result.json（成本项来自运行日志）"})

    m["unavailable"] = unavailable
    m["note"] = ("同一套口径可对基础 Agent 成品重算：只要给 --docx；"
                 "其它项缺输入即 null，不用 0 冒充")
    return m


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="同口径度量")
    ap.add_argument("--docx", required=True)
    ap.add_argument("--result", default=None)
    ap.add_argument("--numbering", default=None)
    ap.add_argument("--lib", default=None)
    ap.add_argument("--audit", default=None)
    ap.add_argument("--coverage", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    m = assemble(args.docx, args.numbering, args.lib, args.audit,
                 args.coverage, args.result)
    if not m.get("ok"):
        print(json.dumps({"ok": False, "error": m.get("error")}, ensure_ascii=False))
        return 2
    op = Path(args.out) if args.out else Path(args.docx).with_name("metrics.json")
    op.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True,
                      "doc": {k: m["doc"][k] for k in
                              ("n_ref_entries", "superscript_ratio",
                               "numbering_continuous", "first_appearance_increasing")},
                      "manual_items": m["manual_review"]["n_items"],
                      "unavailable": len(m["unavailable"]), "out": str(op)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
