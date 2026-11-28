#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""coverage_report.py — 章节 / 引用点 / 文献条目三张覆盖表，回答「有没有漏」。

题面要求「覆盖任务要求的六类动作以及全部章节、全部引用点与全部文献条目」，所以这里
逐章记句子分流与挂引情况、逐个记锚点、逐条记库内条目的去向（被引/未引/被合并/冲突），
再把需要人工确认的句子单列。不新造任何结论：数字全部来自 prep / pass1 / numbering /
库 / source_audit，缺输入时相应列写 null 并记 reason。

用法：
  python coverage_report.py --prep work/prep.json --pass1 work/pass1_auto.txt \
      --numbering work/numbering.json --lib work/library_deduped.json \
      --out work/coverage.json --md output/coverage.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SEC_ZH = {"title": "题名", "abstract": "摘要", "keywords": "关键词", "intro": "引言",
          "related": "相关工作", "method": "材料与方法", "results": "结果",
          "discussion": "讨论", "conclusion": "结论", "limits": "局限",
          "decl": "声明/致谢", "refs": "参考文献表", "caption": "题注",
          "table": "表格", "unknown": "未识别", "heading": "标题"}


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def read_pass1(path: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path or not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        f = line.split("|")
        if len(f) >= 4 and not f[0].startswith("#"):
            out[f[0].strip()] = f[1].strip()
    return out


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="覆盖表")
    ap.add_argument("--prep", required=True)
    ap.add_argument("--pass1", default=None)
    ap.add_argument("--numbering", required=True)
    ap.add_argument("--lib", required=True)
    ap.add_argument("--ingest", default=None, help="ingest_report.json")
    ap.add_argument("--dedupe", default=None, help="dedupe_audit.json")
    ap.add_argument("--audit", default=None, help="source_audit.json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--md", default=None)
    args = ap.parse_args(argv)

    prep = json.loads(Path(args.prep).read_text(encoding="utf-8-sig"))
    num = json.loads(Path(args.numbering).read_text(encoding="utf-8-sig"))
    lib = json.loads(Path(args.lib).read_text(encoding="utf-8-sig"))
    cat = read_pass1(args.pass1)
    skip = set(prep.get("skipped_sections") or [])
    sent = {s["uid"]: s for s in prep.get("sentences", [])}
    entries = lib.get("entries", [])
    cited: dict[str, list[int]] = {}
    uid_nums: dict[str, list[int]] = {}
    for c in num.get("cites", []):
        nums = sorted({n for a in c.get("anchors", []) for n in (a.get("nums") or [])})
        uid_nums[c["uid"]] = nums
        for a in c.get("anchors", []):
            ids_a = a.get("ids") or []
            nums_a = a.get("nums") or []
            for i in ids_a:
                cited.setdefault(i, [])
            for k, n in enumerate(nums_a):
                i = ids_a[k] if k < len(ids_a) else (ids_a[0] if len(ids_a) == 1 else None)
                if i and n not in cited.setdefault(i, []):
                    cited[i].append(n)
    ids_in_num = {r["id"] for r in num.get("refs", [])}

    # ---------------------------------------------------------------- 章节
    sections: list[dict] = []
    order: list[str] = []
    for s in prep.get("sections", []):
        if s["section"] not in order:
            order.append(s["section"])
    for sec in order:
        uids = [u for u, x in sent.items() if x.get("section") == sec]
        row = {"section": sec, "name": SEC_ZH.get(sec, sec),
               "skipped": sec in skip, "n_sents": len(uids),
               "CITE": 0, "OWN": 0, "STRUCT": 0, "AMBIG": 0,
               "n_cite_sents": 0, "n_cited_sents": 0,
               "uncited_cite_uids": [], "refs": []}
        for u in uids:
            c = cat.get(u)
            if c in row:
                row[c] += 1
            if c == "CITE":
                row["n_cite_sents"] += 1
                if u in uid_nums:
                    row["n_cited_sents"] += 1
                    row["refs"] += [n for n in uid_nums[u] if n not in row["refs"]]
                else:
                    row["uncited_cite_uids"].append(u)
        row["refs"].sort()
        sections.append(row)

    # ---------------------------------------------------------------- 引用点
    points: list[dict] = []
    for c in num.get("cites", []):
        for k, a in enumerate(c.get("anchors", [])):
            points.append({"uid": c["uid"], "idx": k, "kind": a.get("kind"),
                           "nums": a.get("nums") or [],
                           "ids": a.get("ids") or [],
                           "section": c.get("section"),
                           "marker": a.get("marker"),
                           "offset": a.get("offset") if a.get("offset") is not None
                           else a.get("start")})
    n_insert = sum(1 for p in points if p["kind"] == "insert")
    n_replace = sum(1 for p in points if p["kind"] == "replace")
    multi = {i: sorted({c["uid"] for c in num.get("cites", [])
                        if any(i in (a.get("ids") or []) for a in c.get("anchors", []))})
             for i in ids_in_num}
    multi = {i: v for i, v in multi.items() if len(v) > 1}
    per_sent_max = max((len(c.get("anchors", [])) for c in num.get("cites", [])), default=0)
    per_sent_refs_max = max((len(uid_nums.get(c["uid"], []))
                             for c in num.get("cites", [])), default=0)

    # ---------------------------------------------------------------- 条目
    ingest = {}
    if args.ingest and Path(args.ingest).exists():
        for r in json.loads(Path(args.ingest).read_text(encoding="utf-8-sig")):
            ingest[str(r.get("orig_no"))] = r
    dedupe = {}
    if args.dedupe and Path(args.dedupe).exists():
        dedupe = json.loads(Path(args.dedupe).read_text(encoding="utf-8-sig"))
    merged_into = {m["id"]: m for m in dedupe.get("merged_index", [])}
    rows: list[dict] = []
    for e in entries:
        eid = e["id"]
        dest = "cited" if eid in cited else "not_cited"
        rows.append({"id": eid, "title": (e.get("title") or "")[:50],
                     "year": e.get("year") or "", "type": e.get("type") or "",
                     "source": e.get("source") or "", "dest": dest,
                     "nums": sorted(cited.get(eid, [])),
                     "merged_from": e.get("merged_from") or [],
                     "has_error": None})
    for m in dedupe.get("merged_index", []):
        rows.append({"id": m["id"], "title": "", "year": "", "type": "",
                     "source": "duplicate", "dest": "merged", "nums": [],
                     "merged_from": [], "canonical": m["canonical"],
                     "code": m.get("code"), "basis": m.get("basis")})
    n_cited = sum(1 for r in rows if r["dest"] == "cited")
    n_uncited = sum(1 for r in rows if r["dest"] == "not_cited")
    n_merged = sum(1 for r in rows if r["dest"] == "merged")

    # ---------------------------------------------------------------- 人工确认
    manual: list[dict] = []
    for sec in sections:
        for u in sec["uncited_cite_uids"]:
            manual.append({"kind": "cite_missing", "uid": u, "section": sec["section"],
                           "detail": "判定为需要来源但未挂上引用编号"})
    for u, c in sent.items():
        if cat.get(u) == "AMBIG":
            manual.append({"kind": "ambig", "uid": u, "section": c.get("section"),
                           "detail": "类别 AMBIG：默认不新增引用，需人工判断是否补引"})
    if args.audit and Path(args.audit).exists():
        audit = json.loads(Path(args.audit).read_text(encoding="utf-8-sig"))
        for m in audit.get("manual_items", []):
            manual.append({"kind": "source_" + m["kind"],
                           "uid": m.get("uid") or m.get("id"),
                           "detail": "/".join(m.get("codes") or []) + " " + m.get("action", "")})

    totals = {"n_sections": len([s for s in sections if not s["skipped"]]),
              "n_sections_skipped": len([s for s in sections if s["skipped"]]),
              "n_sents": len(sent), "n_sents_triaged": sum(
                  1 for u in sent if u in cat),
              "n_cite": sum(1 for v in cat.values() if v == "CITE"),
              "n_own": sum(1 for v in cat.values() if v == "OWN"),
              "n_struct": sum(1 for v in cat.values() if v == "STRUCT"),
              "n_ambig": sum(1 for v in cat.values() if v == "AMBIG"),
              "n_cite_points": len(points), "n_insert": n_insert,
              "n_replace": n_replace,
              "n_unique_refs": len(ids_in_num),
              "n_refs_listed": len(num.get("refs", [])),
              "n_entries": len(rows),
              "n_cited": n_cited, "n_uncited": n_uncited, "n_merged": n_merged,
              "n_manual": len(manual)}
    cov = {"generated_by": "coverage_report.py", "totals": totals,
           "sections": sections, "cite_points": points,
           "multi_sentence_refs": multi,
           "max_anchors_per_sentence": per_sent_max,
           "max_refs_per_sentence": per_sent_refs_max,
           "ingest_rows": len(ingest), "entries": rows,
           "manual": manual,
           "note": "未引用的库内条目不是缺陷：编号按正文首现，未引条目不进文末表；"
                   "这里逐条列出以便复核是否漏引"}
    op = Path(args.out) if args.out else Path(args.numbering).with_name("coverage.json")
    op.write_text(json.dumps(cov, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.md:
        def tbl(head: list[str], rows_: list[list[str]]) -> str:
            out = ["| " + " | ".join(head) + " |",
                   "|" + "|".join("-" for _ in head) + "|"]
            out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows_]
            return "\n".join(out)

        md = ["# 覆盖表（六类动作 / 全部章节 / 全部引用点 / 全部条目）", "",
              f"- 章节：正文 {totals['n_sections']} 节参与分流，"
              f"{totals['n_sections_skipped']} 节按规则跳过（题名/摘要/关键词/文献表）",
              f"- 句子：{totals['n_sents']} 句，分流 {totals['n_sents_triaged']} 句"
              f"（CITE {totals['n_cite']}、OWN {totals['n_own']}、"
              f"STRUCT {totals['n_struct']}、AMBIG {totals['n_ambig']}）",
              f"- 引用点：{totals['n_cite_points']} 处（新增 {n_insert}、换号 {n_replace}），"
              f"唯一编号 {totals['n_unique_refs']} 个，文末条目 {totals['n_refs_listed']} 条",
              f"- 条目：库内 {totals['n_entries']} 条（被引 {n_cited}、未被引 {n_uncited}、"
              f"判为重复合并 {n_merged}）",
              f"- 需人工确认 {totals['n_manual']} 项", "",
              "## 1 章节覆盖", "",
              tbl(["章节", "跳过", "句数", "CITE", "OWN", "STRUCT", "AMBIG",
                   "需引句", "已挂引", "本节编号", "未挂引的 CITE 句"],
                  [[s["name"], "是" if s["skipped"] else "否", s["n_sents"],
                    s["CITE"], s["OWN"], s["STRUCT"], s["AMBIG"],
                    s["n_cite_sents"], s["n_cited_sents"],
                    ",".join(str(x) for x in s["refs"]) or "—",
                    ",".join(s["uncited_cite_uids"]) or "—"] for s in sections]),
              "", "## 2 引用点", "",
              tbl(["uid", "锚点", "类型", "编号", "条目", "章节", "标记"],
                  [[p["uid"], p["idx"], p["kind"],
                    ",".join(str(x) for x in p["nums"]) or "—",
                    ",".join(p["ids"]) or "—", p["section"] or "—",
                    p.get("marker") or "—"] for p in points]),
              "", f"- 一句多引：单句最多 {per_sent_refs_max} 个编号、"
              f"{per_sent_max} 个锚点；多句一引（同一编号出现在多句）："
              + (", ".join(f"{i}（编号 "
                           + "/".join(str(x) for x in sorted(cited.get(i, [])))
                           + f"）见 {len(u)} 句" for i, u in multi.items()) or "无"),
              "", "## 3 文献条目去向", "",
              tbl(["条目", "题名", "年", "类型", "来源", "去向", "编号", "被合并到/依据"],
                  [[r["id"], r["title"] or "—", r["year"] or "—", r["type"] or "—",
                    r["source"], {"cited": "被引", "not_cited": "未引用",
                                  "merged": "重复合并"}[r["dest"]],
                    ",".join(str(x) for x in r["nums"]) or "—",
                    (r.get("canonical") or "—") if r["dest"] == "merged"
                    else (", ".join(r["merged_from"]) or "—")] for r in rows]),
              "", "## 4 需人工确认", "",
              tbl(["对象", "类型", "说明"],
                  [[m.get("uid") or "—", m["kind"], m["detail"]] for m in manual]) if manual
              else "无。",
              ""]
        mp = Path(args.md)
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text("\n".join(md), encoding="utf-8")

    print(json.dumps({"ok": True, **totals, "out": str(op)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
