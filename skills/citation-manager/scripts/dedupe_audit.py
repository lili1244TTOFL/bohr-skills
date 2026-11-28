#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dedupe_audit.py — 文献库内条目归并：合并成一条，并把「合并/不合并」的依据逐对留痕。

题面要求：同一篇文献以中英文题名、预印本与正式版、不同数据库导出等形式重复出现时，
合并成一条并说明合并依据；同时要避免误合并同名作者的不同文献。所以本脚本对每一对
候选都写一条判定（merge / keep / keep_confirm / conflict），不是只写合并了什么。

判据（命中即停，阈值与码表见 rules/dedupe_rules.md）：
  D1 同 DOI 且题名相似 ≥0.90                                   → 合并
  D2 题名归一化后完全相同                                       → 合并
  D3 题名相似 ≥0.92 且作者姓有交集且同年                         → 合并
  D4 中英题名（术语表覆盖 ≥0.60）且作者姓有交集且同年             → 合并
  D5 一方为预印本/网络首发且题名相似 ≥0.85 且作者姓有交集         → 合并，正式版为准
  N1 同作者同年但题名相似 <0.70                                 → 不合并（同作者同年不同篇）
  N2 两侧 DOI 均存在且不同、题名相似 <0.92                       → 不合并
  N3 题名相似 ≥0.92 但作者姓无交集                               → 不合并，标人工确认
  N4 中英题名但术语表覆盖 <0.50                                 → 不合并（跨语言证据不足）
  N5 题名相似 0.70–0.92、作者有交集、年份不同                     → 不合并，标人工确认
  X1 同 DOI 但题名相似 <0.90                                    → 不合并，记元数据错配

输出：
  <out-lib>      归并后的库（保留 canonical 条目，写 merged_from）
  <audit>        逐对判定、判据码、依据句、补齐了哪些字段
  <md>           人读版：合并依据表 / 保持分离表 / 待人工确认表
  <out-map>      可选：把原编号→库 id 映射改指到 canonical

用法：
  python dedupe_audit.py --lib work/library_merged.json --out work/library_deduped.json \
      --audit work/dedupe_audit.json --md output/merge_basis.md \
      --glossary rules/glossary_zh_en.md --orig-map work/orig_map.json \
      --out-map work/orig_map_dedup.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

PREPRINT_RE = re.compile(
    r"arxiv|biorxiv|medrxiv|chemrxiv|ssrn|research\s*square|preprint|预印本|网络首发|首发",
    re.I)
FILL_FIELDS = ("type", "authors", "title", "container", "year", "volume",
               "issue", "pages", "doi", "publisher", "place", "url")
CODE_TITLE = {"D1": "同 DOI 同题名", "D2": "题名归一化相同", "D3": "题名高相似+作者+年",
              "D4": "中英题名（术语表）", "D5": "预印本/正式版",
              "N1": "同作者同年不同篇", "N2": "DOI 不同题名不同",
              "N3": "同题名不同作者", "N4": "跨语言证据不足", "N5": "疑似不同版本",
              "X1": "同 DOI 不同题名"}


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def norm_title(t: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (t or "").lower())


def has_cjk(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", s or ""))


def surname(a: str) -> str:
    a = (a or "").strip()
    if not a:
        return ""
    if has_cjk(a):
        return a
    if "," in a:
        return a.split(",")[0].strip().lower()
    parts = a.split()
    return parts[-1].lower() if parts else ""


def surnames(e: dict) -> set[str]:
    return {s for s in (surname(a) for a in (e.get("authors") or [])) if s}


def completeness(e: dict) -> int:
    return sum(1 for f in FILL_FIELDS if (e.get(f) or ([] if f == "authors" else "")))


def ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    sm = difflib.SequenceMatcher(None, a, b)
    if sm.real_quick_ratio() < 0.55:
        return round(sm.real_quick_ratio(), 3)
    return round(sm.ratio(), 3)


def load_glossary(path: str | None) -> dict[str, list[str]]:
    """复用 match_candidates.load_glossary（同一份术语表，两种落盘形态都认）。"""
    if not path:
        return {}
    try:
        from match_candidates import load_glossary as _lg
        return _lg(path, {})
    except Exception:
        return {}


def glossary_cov(a: dict, b: dict, gloss: dict[str, list[str]]) -> float | None:
    """中文题名里的术语，能在英文题名里找到对应英文词的比例。判断不了返回 None。"""
    if not gloss:
        return None
    ta, tb = a.get("title") or "", b.get("title") or ""
    if has_cjk(ta) == has_cjk(tb):
        return None
    zh, en = (ta, tb) if has_cjk(ta) else (tb, ta)
    if not has_cjk(zh) or has_cjk(en):
        return None
    low = en.lower()
    hit = tot = 0
    for z, ens in gloss.items():
        if not z or z == "中文" or z not in zh:
            continue
        tot += 1
        if any(e and re.search(rf"\b{re.escape(e)}\b", low) for e in ens):
            hit += 1
    if tot < 2:                     # 术语太少，覆盖率没有判别力
        return None
    return round(hit / tot, 2)


def signals(a: dict, b: dict, gloss: dict[str, list[str]]) -> dict | None:
    """只有出现候选信号才留痕，避免把无关的两条也写成一行。"""
    ta, tb = norm_title(a.get("title")), norm_title(b.get("title"))
    da = (a.get("doi") or "").strip().lower()
    db = (b.get("doi") or "").strip().lower()
    doi_same = bool(da) and da == db
    doi_pair_diff = bool(da) and bool(db) and da != db
    au = sorted(surnames(a) & surnames(b))
    ya, yb = str(a.get("year") or ""), str(b.get("year") or "")
    year_diff = abs(int(ya) - int(yb)) if ya.isdigit() and yb.isdigit() else None
    r = ratio(ta, tb)
    pre_a = bool(PREPRINT_RE.search(f"{a.get('container','')} {a.get('raw','')}"))
    pre_b = bool(PREPRINT_RE.search(f"{b.get('container','')} {b.get('raw','')}"))
    gl = glossary_cov(a, b, gloss)
    sig = {
        "doi_same": doi_same, "doi_pair_diff": doi_pair_diff,
        "title_same": bool(ta) and ta == tb, "ratio": r,
        "author_overlap": au, "year_diff": year_diff,
        "type_pair": sorted({str(a.get("type") or "?"), str(b.get("type") or "?")}),
        "lang_pair": sorted({"zh" if has_cjk(ta) else "en",
                             "zh" if has_cjk(tb) else "en"}),
        "preprint_pair": pre_a != pre_b, "preprint_a": pre_a, "preprint_b": pre_b,
        "gloss_cov": gl,
    }
    gate = (doi_same or (ta and tb and r >= 0.55) or (bool(au) and year_diff == 0)
            or (bool(au) and gl is not None and gl >= 0.5))
    return sig if gate else None


def decide(sig: dict) -> tuple[str, str, str, bool]:
    """→ (decision, code, basis, need_human)。decision: merge/keep/keep_confirm/conflict。"""
    if sig["doi_same"]:
        if sig["title_same"] or sig["ratio"] >= 0.90:
            return ("merge", "D1", f"两侧 DOI 相同（题名相似 {sig['ratio']}）", False)
        return ("conflict", "X1",
                f"两侧 DOI 相同但题名相似仅 {sig['ratio']}：判为元数据错配，不合并", True)
    if sig["title_same"]:
        return ("merge", "D2", "题名归一化后完全相同", False)
    if sig["ratio"] >= 0.92 and sig["author_overlap"] and sig["year_diff"] == 0:
        return ("merge", "D3",
                f"题名相似 {sig['ratio']}、作者姓交集 {sig['author_overlap']}、同年", False)
    if sig["gloss_cov"] is not None and sig["gloss_cov"] >= 0.60 \
            and sig["author_overlap"] and sig["year_diff"] == 0:
        return ("merge", "D4",
                f"中英题名术语覆盖 {sig['gloss_cov']}、作者姓交集 "
                f"{sig['author_overlap']}、同年", False)
    if sig["preprint_pair"] and sig["ratio"] >= 0.85 and sig["author_overlap"]:
        return ("merge", "D5",
                f"一方为预印本/网络首发、题名相似 {sig['ratio']}、作者姓交集 "
                f"{sig['author_overlap']}", False)
    if sig["author_overlap"] and sig["year_diff"] == 0 and sig["ratio"] < 0.70:
        return ("keep", "N1",
                f"同作者同年但题名相似仅 {sig['ratio']}：判为不同文献，不合并", False)
    if sig["doi_pair_diff"] and sig["ratio"] < 0.92:
        return ("keep", "N2",
                f"两侧 DOI 不同且题名相似 {sig['ratio']} <0.92：不合并", False)
    if sig["ratio"] >= 0.92 and not sig["author_overlap"]:
        return ("keep_confirm", "N3",
                f"题名相似 {sig['ratio']} 但作者姓无交集：不合并，待人工确认", True)
    if sig["gloss_cov"] is not None and sig["gloss_cov"] < 0.50:
        return ("keep", "N4",
                f"中英题名但术语覆盖仅 {sig['gloss_cov']}：跨语言证据不足，不合并", False)
    return ("keep_confirm", "N5",
            f"题名相似 {sig['ratio']}、作者姓交集 {sig['author_overlap'] or '无'}、"
            f"年份差 {sig['year_diff']}：疑似不同版本，待人工确认", True)


def pick_canonical(group: list[dict]) -> dict:
    """正式版优于预印本；字段更全者优先；仍相同取库内靠前的一条。"""
    def key(e: dict):
        pre = bool(PREPRINT_RE.search(f"{e.get('container','')} {e.get('raw','')}"))
        return (pre, -completeness(e), e.get("seq", 0))
    return sorted(group, key=key)[0]


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="库内去重与判据留痕")
    ap.add_argument("--lib", required=True)
    ap.add_argument("--out", required=True, help="归并后的库")
    ap.add_argument("--audit", default=None, help="逐对判定 JSON")
    ap.add_argument("--md", default=None, help="人读版合并依据")
    ap.add_argument("--glossary", default=None, help="中英术语表（跨语言判据用）")
    ap.add_argument("--orig-map", default=None, help="ingest_refs 的原编号映射")
    ap.add_argument("--out-map", default=None, help="改写后的原编号映射")
    ap.add_argument("--no-apply", action="store_true", help="只出报告，不合并")
    args = ap.parse_args(argv)

    lib = json.loads(Path(args.lib).read_text(encoding="utf-8-sig"))
    entries: list[dict] = [dict(e) for e in lib.get("entries", [])]
    gloss = load_glossary(args.glossary)

    pairs: list[dict] = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            a, b = entries[i], entries[j]
            sig = signals(a, b, gloss)
            if sig is None:
                continue
            decision, code, basis, need_human = decide(sig)
            pairs.append({"a": a["id"], "b": b["id"], "decision": decision,
                          "code": code, "basis": basis, "need_human": need_human,
                          "signals": sig})

    # 合并连通分量（同一条文献可能经多对判定聚到一起）
    parent = {e["id"]: e["id"] for e in entries}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        if p["decision"] == "merge":
            ra, rb = find(p["a"]), find(p["b"])
            if ra != rb:
                parent[rb] = ra

    groups: dict[str, list[dict]] = {}
    for e in entries:
        groups.setdefault(find(e["id"]), []).append(e)

    alias: dict[str, str] = {}
    merged_index: list[dict] = []
    out_entries: list[dict] = []
    for root, group in groups.items():
        canon = pick_canonical(group)
        if len(group) > 1:
            canon = dict(canon)
            canon["merged_from"] = sorted(x["id"] for x in group if x["id"] != canon["id"])
            filled: list[str] = []
            for loser in sorted(group, key=lambda x: x["id"]):
                if loser["id"] == canon["id"]:
                    continue
                for f in FILL_FIELDS:
                    if not canon.get(f) and loser.get(f):
                        canon[f] = loser[f]
                        filled.append(f)
                alias[loser["id"]] = canon["id"]
                hit = next((p for p in pairs if p["decision"] == "merge"
                            and loser["id"] in (p["a"], p["b"])
                            and canon["id"] in (p["a"], p["b"])), None)
                merged_index.append({"id": loser["id"], "canonical": canon["id"],
                                     "code": (hit or {}).get("code", "D2"),
                                     "basis": (hit or {}).get("basis", "题名归一化相同")})
                canon.setdefault("filled_from", {})[loser["id"]] = list(filled)
        out_entries.append(canon)

    out_entries.sort(key=lambda e: e.get("seq", 0))
    for n, e in enumerate(out_entries, 1):
        e["seq"] = n

    applied = not args.no_apply
    out = {"generated_by": "dedupe_audit.py", "n": len(out_entries),
           "entries": out_entries, "applied": applied,
           "merged_index": merged_index, "alias": alias}
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    n_merge = sum(1 for x in pairs if x["decision"] == "merge")
    n_keep = sum(1 for x in pairs if x["decision"] == "keep")
    n_conf = sum(1 for x in pairs if x["decision"] == "keep_confirm")
    n_conflict = sum(1 for x in pairs if x["decision"] == "conflict")
    by_code: dict[str, int] = {}
    for x in pairs:
        by_code[x["code"]] = by_code.get(x["code"], 0) + 1

    audit = {"generated_by": "dedupe_audit.py", "applied": applied,
             "source_lib": str(args.lib), "glossary": bool(gloss),
             "n_in": len(entries), "n_out": len(out_entries),
             "n_pairs_examined": len(pairs), "n_merged_pairs": n_merge,
             "n_kept": n_keep, "n_keep_confirm": n_conf, "n_conflict": n_conflict,
             "by_code": by_code, "merged_index": merged_index,
             "pairs": pairs,
             "rule_file": "rules/dedupe_rules.md",
             "note": "每对候选都留判定；keep/keep_confirm 是「避免误合并」的证据"}
    apath = Path(args.audit) if args.audit else Path(args.out).with_name("dedupe_audit.json")
    apath.write_text(json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8")

    remapped = 0
    if args.orig_map and Path(args.orig_map).exists():
        om = json.loads(Path(args.orig_map).read_text(encoding="utf-8-sig"))
        new_om = {}
        for k, v in om.items():
            nv = alias.get(v, v)
            remapped += int(nv != v)
            new_om[str(k)] = nv
        mp = Path(args.out_map) if args.out_map else Path(args.orig_map).with_name("orig_map_dedup.json")
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(json.dumps(new_om, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.md:
        def rows(sel: list[dict]) -> str:
            if not sel:
                return "| — | — | — | — | — |\n"
            return "\n".join(
                f"| {x['a']} | {x['b']} | {x['code']} | {x['basis']} | "
                f"{'是' if x['need_human'] else '否'} |" for x in sel)
        head = "| 条目 A | 条目 B | 判据码 | 依据 | 需人工确认 |\n|-|-|-|-|-|\n"
        merge_rows = []
        for m in merged_index:
            hit = next((x for x in pairs if x["decision"] == "merge"
                        and m["id"] in (x["a"], x["b"])
                        and m["canonical"] in (x["a"], x["b"])), {})
            merge_rows.append({"a": m["id"], "b": m["canonical"], "code": m["code"],
                               "basis": m["basis"], "need_human": False})
        md = ["# 文献库去重依据", "",
              f"- 输入 `{args.lib}`：{len(entries)} 条；输出 `{args.out}`：{len(out_entries)} 条"
              f"（合并 {len(merged_index)} 条重复，撤销的编号已改指 canonical）",
              f"- 共比较 {len(pairs)} 对候选：合并 {n_merge} 对、判为不同 {n_keep} 对、"
              f"待人工确认 {n_conf} 对、元数据冲突 {n_conflict} 对",
              f"- 判据码定义见 `rules/dedupe_rules.md`；不合并的记录同样保留，"
              "用来证明没有把同名作者的不同文献并掉",
              "", "## 1 合并（同一文献只留一条）", "", head + rows(merge_rows),
              "", "## 2 判为不同文献（不合并）", "",
              head + rows([x for x in pairs if x["decision"] == "keep"]),
              "", "## 3 待人工确认（证据不足，未自动合并）", "",
              head + rows([x for x in pairs if x["decision"] in ("keep_confirm", "conflict")]),
              ""]
        mdp = Path(args.md)
        mdp.parent.mkdir(parents=True, exist_ok=True)
        mdp.write_text("\n".join(md), encoding="utf-8")

    print(json.dumps({"ok": True, "n_in": len(entries), "n_out": len(out_entries),
                      "n_pairs": len(pairs), "n_merged_pairs": n_merge,
                      "n_kept": n_keep, "n_keep_confirm": n_conf,
                      "n_conflict": n_conflict, "by_code": by_code,
                      "remapped": remapped, "glossary": bool(gloss),
                      "out": str(p), "audit": str(apath)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
