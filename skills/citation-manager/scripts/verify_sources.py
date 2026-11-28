#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_sources.py — 来源核验与「正文论断 ↔ 所引文献」逐句对账。

题面两块要求落在这里：
  · 对得上：每条引用是否真实存在、元数据准确，正文论断与所引文献的主题和结论是否真的
    匹配，能否逐句说清 → 句级判定表（S 码）+ 条目元数据核验（E 码）。
  · 来源核验：检出编造条目与元数据错配，标记证据不足的句子。

离线能判的（本脚本做）：
  E1 DOI 缺失            E2 DOI 形态非法
  E3 该类型必填字段缺失   E4 年份非法/未来年
  E5 页码倒置             E6 同 DOI 不同题名（元数据错配）
  E7 同题名不同 DOI/年（冲突）  E8 作者缺失
  E9 类型标识缺失/未知     E10 无法离线判定真伪（声明，不是缺陷）

句级（命中即停，S 码见 rules/source_audit.md）：
  S1 已有标记且句中点名作者与条目一致        → match
  S2 已有标记（编号由稿件文献表映射）        → match
  S3 语义分 ≥0.30 且命中词 ≥2 且无并列       → match
  S4 语义分 0.18–0.30 或命中词 <2            → weak（标证据不足）
  S5 语义分 <0.18                            → mismatch（标证据不足）
  S6 无候选                                  → no_source
  S7 并列候选（前两名差 <0.05）              → weak
  S8 句中点名的作者未被该句引用覆盖           → 建议补引
  S9 引用落到有 error 级问题的条目上          → 建议人工核对条目

用法：
  python verify_sources.py --lib work/library_deduped.json \
      --numbering work/numbering.json --cands work/match_input.json \
      --out work/source_audit.json --tsv output/evidence_map.tsv \
      --md output/evidence_gaps.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
DOI_IN = re.compile(r"10\.\d{4,9}/[^\s，。；;]+")
PAGES_IN = re.compile(r"([A-Za-z]?\d+)\s*[-–—]\s*([A-Za-z]?\d+)")
VOLISSUE_IN = re.compile(r"(\d+)\s*[（(]\s*(\d+)\s*[)）]")
YEAR_IN = re.compile(r"(?:18|19|20)\d{2}")
REQUIRED = {
    "J": ("authors", "title", "container", "year", "volume", "pages"),
    "C": ("authors", "title", "container", "year", "pages"),
    "A": ("authors", "title", "container", "year", "publisher"),
    "M": ("authors", "title", "publisher", "year"),
    "D": ("authors", "title", "publisher", "year"),
    "R": ("authors", "title", "publisher", "year"),
    "S": ("title", "publisher", "year"),
    "P": ("authors", "title", "year"),
    "EB/OL": ("title", "year", "url"),
    "Z": ("authors", "title", "year"),
}
KIND = {"J": "期刊", "C": "会议", "A": "析出", "M": "专著", "D": "学位论文",
        "R": "报告", "S": "标准", "P": "专利", "EB/OL": "电子资源", "Z": "其他"}


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def surname(a: str) -> str:
    a = (a or "").strip()
    if not a:
        return ""
    if re.search(r"[\u4e00-\u9fff]", a):
        return a
    if "," in a:
        return a.split(",")[0].strip()
    parts = a.split()
    return parts[-1] if parts else ""


def page_range(p: str) -> tuple[str, str] | None:
    m = re.match(r"^\s*([A-Za-z]?)(\d+)\s*[-–—]\s*([A-Za-z]?)(\d+)\s*$", p or "")
    if not m:
        return None
    return m.group(2), m.group(4)


def entry_issues(e: dict, year_now: int) -> list[dict]:
    out: list[dict] = []
    t = (e.get("type") or "").upper()
    if not t:
        out.append({"code": "E9", "severity": "warn", "note": "缺文献类型标识"})
    elif t not in REQUIRED:
        out.append({"code": "E9", "severity": "warn", "note": f"类型标识 {t} 不在国标表内"})
    missing = [f for f in REQUIRED.get(t, ("authors", "title", "year"))
               if not (e.get(f) or ([] if f == "authors" else ""))]
    if missing:
        out.append({"code": "E3", "severity": "warn",
                    "note": f"该类型必填字段缺：{missing}"})
    if not e.get("authors"):
        out.append({"code": "E8", "severity": "warn", "note": "作者为空，国标须以题名起首"})
    doi = (e.get("doi") or "").strip()
    if not doi:
        out.append({"code": "E1", "severity": "info",
                    "note": "无 DOI：离线无法判定真伪，需联网或人工核实"})
    elif not DOI_RE.match(doi):
        out.append({"code": "E2", "severity": "error",
                    "note": f"DOI 形态非法：{doi}"})
    y = str(e.get("year") or "")
    if y and (not y.isdigit() or not (1500 <= int(y) <= year_now)):
        out.append({"code": "E4", "severity": "error", "note": f"年份不可信：{y}"})
    pg = page_range(str(e.get("pages") or ""))
    if pg and int(pg[0]) > int(pg[1]):
        out.append({"code": "E5", "severity": "error",
                    "note": f"页码起页大于止页：{e.get('pages')}"})
    return out


def suggest_from_raw(e: dict, field: str) -> str:
    raw = e.get("raw") or ""
    if field == "doi":
        m = DOI_IN.search(raw)
        return m.group(0).rstrip(".") if m else ""
    if field == "pages":
        m = PAGES_IN.search(raw)
        return f"{m.group(1)}-{m.group(2)}" if m else ""
    if field == "volume":
        m = VOLISSUE_IN.search(raw)
        if m:
            return m.group(1)
        return ""
    if field == "issue":
        m = VOLISSUE_IN.search(raw)
        return m.group(2) if m else ""
    if field == "year":
        m = YEAR_IN.search(raw)
        return m.group(0) if m else ""
    return ""


def author_hit(e: dict, text: str) -> list[str]:
    low = (text or "").lower()
    out = []
    for a in e.get("authors") or []:
        s = surname(a)
        if len(s) >= 2 and s.lower() in low:
            out.append(s)
    return out


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="来源核验与逐句对账")
    ap.add_argument("--lib", required=True)
    ap.add_argument("--numbering", required=True)
    ap.add_argument("--cands", default=None, help="match_input.json（含候选分与命中词）")
    ap.add_argument("--out", default=None, help="source_audit.json")
    ap.add_argument("--tsv", default=None, help="逐句判定表 evidence_map.tsv")
    ap.add_argument("--md", default=None, help="证据不足与待确认清单")
    ap.add_argument("--year-now", type=int, default=2026)
    args = ap.parse_args(argv)

    from datetime import date
    year_now = min(args.year_now, date.today().year + 1)
    lib = json.loads(Path(args.lib).read_text(encoding="utf-8-sig"))
    entries = {e["id"]: e for e in lib.get("entries", [])}
    num = json.loads(Path(args.numbering).read_text(encoding="utf-8-sig"))
    cands = {}
    if args.cands and Path(args.cands).exists():
        for r in json.loads(Path(args.cands).read_text(encoding="utf-8-sig")):
            cands[r["uid"]] = r

    # ---------------------------------------------------------------- 条目级
    ent_rows: list[dict] = []
    for eid, e in entries.items():
        issues = entry_issues(e, year_now)
        filled, manual = {}, []
        typ = (e.get("type") or "Z").upper()
        want = [f for f in REQUIRED.get(typ, ()) if not (e.get(f) or "")]
        if not (e.get("doi") or ""):
            want.append("doi")           # DOI 是「保留关键字段」里点名的一项
        for f in dict.fromkeys(want):
            v = suggest_from_raw(e, f)
            if v:
                filled[f] = v
            else:
                manual.append(f)
        ent_rows.append({"id": eid, "type": e.get("type") or "",
                         "kind": KIND.get((e.get("type") or "").upper(), ""),
                         "title": (e.get("title") or "")[:60],
                         "year": e.get("year") or "", "doi": e.get("doi") or "",
                         "issues": issues,
                         "missing_required": [i["note"] for i in issues if i["code"] == "E3"],
                         "fillable_from_raw": filled,
                         "needs_manual": manual})

    # 跨条目错配：同 DOI 不同题名 / 同题名不同 DOI
    def norm(t: str) -> str:
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (t or "").lower())

    by_doi: dict[str, list[dict]] = {}
    by_title: dict[str, list[dict]] = {}
    for e in entries.values():
        if e.get("doi"):
            by_doi.setdefault(str(e["doi"]).lower(), []).append(e)
        if e.get("title"):
            by_title.setdefault(norm(e["title"]), []).append(e)
    cross: list[dict] = []
    for doi, es in by_doi.items():
        if len(es) < 2:
            continue
        worst = min(difflib_ratio(norm(es[0].get("title")), norm(x.get("title")))
                    for x in es[1:])
        if worst < 0.90:
            cross.append({"code": "E6", "severity": "error",
                          "note": f"同一 DOI 对应不同题名（相似 {round(worst, 2)}）：{doi}",
                          "ids": [e["id"] for e in es]})
    for t, es in by_title.items():
        if len(es) < 2:
            continue
        dois = {str(e.get("doi") or "").lower() for e in es}
        years = {str(e.get("year") or "") for e in es}
        if len([d for d in dois if d]) > 1:
            cross.append({"code": "E7", "severity": "warn",
                          "note": f"同一题名对应 {len(dois)} 个 DOI：{[e['id'] for e in es]}",
                          "ids": [e["id"] for e in es]})
        elif len(years) > 1:
            cross.append({"code": "E7", "severity": "warn",
                          "note": f"同一题名对应多个年份 {sorted(years)}："
                                  f"{[e['id'] for e in es]}", "ids": [e["id"] for e in es]})

    err_ids = {r["id"] for r in ent_rows
               if any(i["severity"] == "error" for i in r["issues"])}
    err_ids |= {i for c in cross if c["severity"] == "error" for i in c["ids"]}

    # ---------------------------------------------------------------- 句级
    srows: list[dict] = []
    counts = {k: 0 for k in ("match", "weak", "mismatch", "no_source")}
    code_counts: dict[str, int] = {}
    manual_items: list[dict] = []
    for c in num.get("cites", []):
        uid = c["uid"]
        text = c.get("text") or ""
        ids: list[str] = []
        for a in c.get("anchors", []):
            for i in a.get("ids") or []:
                if i not in ids:
                    ids.append(i)
        nums = sorted({n for a in c.get("anchors", []) for n in (a.get("nums") or [])})
        cr = cands.get(uid) or {}
        cmap = {x["id"]: x for x in cr.get("cands") or []}
        tops = sorted((x for x in cr.get("cands") or []), key=lambda x: -x.get("score", 0))
        best_sc = float(tops[0]["score"]) if tops else None
        second_sc = float(tops[1]["score"]) if len(tops) > 1 else None
        hit_terms = [t for t in (cmap.get(ids[0], {}).get("terms") if ids else []) or []]
        src = cmap.get(ids[0], {}).get("src") if ids else None
        ambiguous = (src != "MARK" and second_sc is not None and best_sc is not None
                     and abs(best_sc - second_sc) < 0.05 and second_sc >= 0.18)
        named = []
        for eid, e in entries.items():
            hs = author_hit(e, text)
            if hs:
                named.append({"id": eid, "hits": hs})
        covered = set(ids)
        uncovered = [x for x in named if x["id"] not in covered]
        author_ok = any(x["id"] in covered for x in named)

        reasons: list[str] = []
        if not ids:
            verdict, code = "no_source", "S6"
        elif src == "MARK" and author_ok:
            verdict, code = "match", "S1"
        elif src == "MARK":
            verdict, code = "match", "S2"
        elif ambiguous:
            verdict, code = "weak", "S7"
        elif best_sc is not None and best_sc >= 0.30 and len(hit_terms) >= 2:
            verdict, code = "match", "S3"
        elif best_sc is not None and best_sc >= 0.18:
            verdict, code = "weak", "S4"
        else:
            verdict, code = "mismatch", "S5"
        reasons.append(code)
        if uncovered:
            reasons.append("S8")
        if covered & err_ids:
            reasons.append("S9")
        counts[verdict] += 1
        for r in reasons:
            code_counts[r] = code_counts.get(r, 0) + 1

        need = verdict in ("weak", "mismatch", "no_source") or "S8" in reasons or "S9" in reasons
        action = {"match": "引用依据可追溯",
                  "weak": "人工确认该句是否真由此文支撑（命中词偏少或候选并列）",
                  "mismatch": "证据不足：改挂更贴合的条目，或标为该句暂无来源",
                  "no_source": "证据不足：库内无候选，需补文献或标为该句暂无来源"}[verdict]
        if "S8" in reasons:
            action += f"；句中还点名 {[u['id'] for u in uncovered]}，建议补引或说明"
        if "S9" in reasons:
            action += "；所引条目有 error 级核验问题（见条目表）"
        row = {"uid": uid, "pid": c.get("pid"), "para_index": c.get("para_index"),
               "section": c.get("section"), "head": text[:24], "nums": nums,
               "ids": ids, "verdict": verdict, "codes": reasons,
               "conf": c.get("conf"), "basis": c.get("basis"),
               "top_score": best_sc, "second_score": second_sc,
               "ambiguous": ambiguous, "terms": hit_terms[:4],
               "named_authors": [u["hits"] for u in named],
               "uncovered": [u["id"] for u in uncovered],
               "need_human": need, "action": action}
        srows.append(row)
        if need:
            manual_items.append({"kind": "sentence", "uid": uid, "verdict": verdict,
                                 "codes": reasons, "action": action})

    for r in ent_rows:
        if any(i["severity"] == "error" for i in r["issues"]):
            manual_items.append({"kind": "entry", "id": r["id"],
                                 "codes": [i["code"] for i in r["issues"]
                                           if i["severity"] == "error"],
                                 "action": "核对元数据后修正该条目"})
        elif r["needs_manual"]:
            manual_items.append({"kind": "field", "id": r["id"],
                                 "codes": ["E1"], "action": f"缺 {r['needs_manual']}，需联网或查原文补齐"})

    n_err = sum(1 for r in ent_rows for i in r["issues"] if i["severity"] == "error")
    n_err += sum(1 for c in cross if c["severity"] == "error")
    n_warn = sum(1 for r in ent_rows for i in r["issues"] if i["severity"] == "warn")
    n_warn += sum(1 for c in cross if c["severity"] == "warn")
    audit = {"generated_by": "verify_sources.py", "ok": n_err == 0,
             "n_entries": len(entries), "n_error": n_err, "n_warn": n_warn,
             "n_cites": len(srows), "verdict_counts": counts,
             "code_counts": code_counts,
             "n_need_human": len(manual_items),
             "n_refs_with_error_entry": sum(1 for r in srows if "S9" in r["codes"]),
             "n_missing_author": sum(1 for r in srows if "S8" in r["codes"]),
             "n_ambiguous": sum(1 for r in srows if r["ambiguous"]),
             "entries": ent_rows, "cross_issues": cross, "sentences": srows,
             "manual_items": manual_items,
             "limits": ["离线不联网：条目的存在性（是否编造）只能靠形态与交叉一致性推断",
                        "题名与论断的主题贴合度按词面重合判定，语义高度改写的句子会判为 weak"],
             "rule_file": "rules/source_audit.md"}
    op = Path(args.out) if args.out else Path(args.numbering).with_name("source_audit.json")
    op.write_text(json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.tsv:
        cols = ["uid", "pid", "section", "句首", "编号", "条目", "判定", "理由码",
                "置信度", "首位分", "次位分", "并列", "命中词", "未覆盖作者",
                "需人工确认", "处置建议"]
        lines = ["\t".join(cols)]
        for r in srows:
            lines.append("\t".join([
                r["uid"], str(r["pid"] or ""), str(r["section"] or ""),
                r["head"].replace("\t", " "),
                "[" + ",".join(str(x) for x in r["nums"]) + "]" if r["nums"] else "—",
                ",".join(r["ids"]) or "—", r["verdict"], "/".join(r["codes"]),
                str(r["conf"] or ""),
                "" if r["top_score"] is None else f"{r['top_score']}",
                "" if r["second_score"] is None else f"{r['second_score']}",
                "是" if r["ambiguous"] else "否",
                "/".join(r["terms"]), ",".join(r["uncovered"]) or "—",
                "是" if r["need_human"] else "否", r["action"]])
            )
        tp = Path(args.tsv)
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if args.md:
        def table(rows: list[list[str]], head: list[str]) -> str:
            out = ["| " + " | ".join(head) + " |", "|" + "|".join("-" for _ in head) + "|"]
            out += ["| " + " | ".join(x) + " |" for x in rows]
            return "\n".join(out)

        md = ["# 来源核验与逐句对账", "",
              f"- 条目 {len(entries)} 条：error {n_err} 项、warn {n_warn} 项；"
              f"引用句 {len(srows)} 句："
              + "、".join(f"{k} {v}" for k, v in counts.items()),
              f"- 需人工确认 {len(manual_items)} 项（下文逐条列）；判据码定义见 `rules/source_audit.md`",
              "", "## 1 逐句判定（正文论断是否真由此文支撑）", "",
              table([[r["uid"], r["head"], "[" + ",".join(str(x) for x in r["nums"]) + "]"
                      if r["nums"] else "—", ",".join(r["ids"]) or "—",
                      r["verdict"], "/".join(r["codes"]),
                      "—" if r["top_score"] is None else f"{r['top_score']}",
                      "是" if r["need_human"] else "否"]
                     for r in srows],
                    ["uid", "句首", "编号", "条目", "判定", "理由码", "首位分", "需人工确认"]),
              "", "## 2 证据不足 / 待确认", "",
              table([[m.get("uid") or m.get("id"), m["kind"], "/".join(m["codes"]), m["action"]]
                     for m in manual_items],
                    ["对象", "类型", "理由码", "处置"]) if manual_items
              else "本次无：所有引用句均有可追溯的匹配依据。",
              "", "## 3 条目元数据核验", "",
              table([[r["id"], r["kind"] or r["type"], r["title"],
                      "; ".join(f"{i['code']}({i['severity']}) {i['note']}" for i in r["issues"]) or "—"]
                     for r in ent_rows],
                    ["条目", "类型", "题名", "问题"]),
              "", "## 4 字段补齐建议（离线可从 raw 抽出的值）", "",
              table([[r["id"], ", ".join(r["needs_manual"]) or "—",
                      ", ".join(f"{k}={v}" for k, v in r["fillable_from_raw"].items()) or "—"]
                     for r in ent_rows if r["needs_manual"] or r["fillable_from_raw"]],
                    ["条目", "仍需人工", "可从 raw 补"]) if any(
                  r["needs_manual"] or r["fillable_from_raw"] for r in ent_rows) else "无缺字段。",
              "", "## 5 离线不能判的", "",
              "- 条目是否真实存在（编造）只做形态与交叉一致性推断，未联网核验 DOI 是否注册。",
              "- 主题贴合度按词面与作者名重合判定；改写幅度大的句子会被判 weak 并列入人工确认。",
              ""]
        mp = Path(args.md)
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text("\n".join(md), encoding="utf-8")

    print(json.dumps({"ok": n_err == 0, "n_entries": len(entries), "n_error": n_err,
                      "n_warn": n_warn, "n_cites": len(srows),
                      "verdicts": counts, "codes": code_counts,
                      "n_need_human": len(manual_items), "out": str(op)},
                     ensure_ascii=False))
    return 0


def difflib_ratio(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()


if __name__ == "__main__":
    raise SystemExit(main())
