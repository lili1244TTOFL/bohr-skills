#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_notes.py — 笔记核验 v2：锚点、逐字证据、对齐、字段、冲突、承接、交接。

判据（只收紧，不放宽；N03 阈值沿用 80%）：
  N00 有章节：sections 非空
  N01 锚点存在：anchor 必须是 P<页>-<序> / T<页>-<序> / F<页>-<序> 之一且块存在
  N02 数字可回溯：条目文本里的每个数字都要出现在锚点块原文
  N03 章节对齐：原文 heading 命中笔记 title 的比例 ≥80%（分母含同名重复），且顺序一致
  N04 字段完整：卡片六字段（研究问题/数据/方法条件/主要发现/结论/局限）齐全，
      每项要么有 items，要么 status 为 not_reported / undetermined
  N05 条目重复：归一化 key 相同、或术语别名相交，判重复
  N06 冲突披露：机器派生出的多值点/极性并存点必须写进 notes.conflicts，不得静默丢
  N07 引用逐字：quote 必须是锚点原文的连续子串；给了 span 就要与 quote 对齐；
      text 与 quote 不一致时必须有 reason（逐句改写理由）
  N08 章节承接：每节 role/role_basis 齐全；carry 链指向下一节且锚点存在
  N09 交接与未报告：not_reported / undetermined 字段必须有 handoff（含检索词），
      且 handoff 里给出的锚点存在

用法：
  python verify_notes.py --parse work/parse.json --notes notes.json --out verify.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

NUM = re.compile(r"\d+(?:[.,]\d+)?")
KEEP = ("body", "caption", "table", "reference", "formula")
SIX = ("研究问题", "数据", "方法条件", "主要发现", "结论", "局限")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _notes_mod():
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("notes_lib", here / "notes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def resolve(parse: dict, anchor: str) -> str | None:
    """锚点 → 原文。P 正文档、T 表（含单元格）、F 公式，三种都在同一块序列里。"""
    m = re.match(r"([PTF])(\d+)-(\d+)$", anchor or "")
    if not m:
        return None
    kind, pno, idx = m.group(1), int(m.group(2)), int(m.group(3))
    page_blocks = [b for pg in parse["pages"] if pg["page"] == pno
                   for b in pg["blocks"] if b["role"] in KEEP]
    if kind == "P":
        pool = page_blocks
    elif kind == "T":
        pool = [b for b in page_blocks if b["role"] == "table"]
    else:
        pool = [b for b in page_blocks if b["role"] == "formula"]
    if not (0 <= idx < len(pool)):
        return None
    b = pool[idx]
    if b["role"] == "table" and b.get("markdown"):
        return (b.get("text") or "") + "\n" + b["markdown"]
    return b.get("text") or ""


def iter_items(notes: dict):
    """(来源, 条目) — 章节候选、卡片条目、知识点。"""
    for s in notes.get("sections") or []:
        for field, lst in (s.get("candidates") or {}).items():
            for it in lst or []:
                yield f"candidates.{field}", it
    for field, v in (notes.get("card") or {}).items():
        for it in (v or {}).get("items") or []:
            yield f"card.{field}", it
    for k in notes.get("knowledge") or []:
        yield f"knowledge.{k.get('kind')}", k


def verify(parse: dict, notes: dict) -> dict:
    nb = _notes_mod()
    problems: list[dict] = []
    secs = notes.get("sections") or []
    if not secs:
        problems.append({"code": "N00", "detail": "没有章节笔记"})

    # ---- N03 章节对齐（阈值 80% 不变，顺序也要一致）
    titles_parse = [b["text"] for pg in parse["pages"] for b in pg["blocks"]
                    if b["role"] == "heading"]
    titles_notes = [s.get("title") for s in secs]
    notes_set = set(titles_notes)
    common = [t for t in titles_parse if t in notes_set]
    in_order = [t for t in titles_notes if t in set(titles_parse)] == common
    n03 = (not titles_parse or len(common) >= max(1, len(titles_parse) * 0.8)) and in_order
    if titles_parse and not n03:
        problems.append({"code": "N03",
                         "detail": f"章节对齐未过：命中 {len(common)}/{len(titles_parse)}，"
                                   f"顺序一致={in_order}"})

    # ---- N01/N02/N07 逐条目
    anchors: dict[str, int] = {"P": 0, "T": 0, "F": 0}
    n_quote = n_reason = n_rewritten = 0
    for src, it in iter_items(notes):
        anchor = it.get("anchor") or it.get("id") or ""
        text = resolve(parse, anchor)
        if text is None:
            problems.append({"code": "N01", "detail": f"锚点无对应原文：{anchor}（{src}）"})
            continue
        anchors[anchor[:1]] = anchors.get(anchor[:1], 0) + 1
        body = it.get("text") or ""
        for n in NUM.findall(body):
            if n not in text and n.replace(",", "") not in text.replace(",", ""):
                problems.append({"code": "N02",
                                 "detail": f"数字不可回溯：{n} 不在 {anchor} 原文中（{src}）"})
                break
        quote = it.get("quote")
        if quote is None:
            # 兼容旧稿：文本本身是原文就补成 quote，否则判 N07
            if body and body in text:
                quote = body
            else:
                problems.append({"code": "N07",
                                 "detail": f"条目无原文逐字 quote：{anchor}（{src}）"})
                continue
        if quote and quote not in text:
            problems.append({"code": "N07",
                             "detail": f"quote 不是 {anchor} 的原文子串（{src}）"})
            continue
        n_quote += 1
        span = it.get("span")
        if isinstance(span, list) and len(span) == 2:
            a, b = int(span[0]), int(span[1])
            if not (0 <= a < b <= len(text)):
                problems.append({"code": "N07",
                                 "detail": f"span {span} 越界（{anchor} 长度 {len(text)}）"})
            elif text[a:b] != quote:
                problems.append({"code": "N07",
                                 "detail": f"span {span} 与 quote 不对齐：{anchor}（{src}）"})
        if body and quote and body != quote:
            n_rewritten += 1
            if not (it.get("reason") or "").strip():
                problems.append({"code": "N07",
                                 "detail": f"改写了 text 但没写 reason：{anchor}（{src}）"})
            else:
                n_reason += 1

    # ---- N04 六字段
    card = notes.get("card") or {}
    for field in SIX:
        v = card.get(field)
        if not isinstance(v, dict):
            problems.append({"code": "N04", "detail": f"卡片缺字段：{field}"})
            continue
        if not v.get("items") and v.get("status") not in ("not_reported", "undetermined"):
            problems.append({"code": "N04",
                             "detail": f"{field} 为空且未标 not_reported/undetermined"})

    # ---- N05 重复（归一化 key + 术语别名）
    seen: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for k in notes.get("knowledge") or []:
        key = nb.norm_key(k.get("text") or k.get("quote") or "")
        if key and key in seen:
            problems.append({"code": "N05", "detail": f"知识点重复：{key[:40]}"})
        seen.setdefault(key, k.get("id") or "")
        for a in k.get("alias") or []:
            a = nb.norm_key(a)
            if not a:
                continue
            if a in aliases and aliases[a] != (k.get("id") or ""):
                problems.append({"code": "N05",
                                 "detail": f"术语别名冲突：{a[:40]}（{aliases[a]} 与 "
                                           f"{k.get('id')}）"})
            aliases.setdefault(a, k.get("id") or "")

    # ---- N06 冲突披露
    derived = nb.derive_conflicts(card)
    declared = notes.get("conflicts") or []
    if derived and not declared:
        problems.append({"code": "N06",
                         "detail": f"派生 {len(derived)} 个待核对点未写进 conflicts"})
    else:
        def sig(c: dict) -> tuple:
            pairs = tuple(sorted((i.get("anchor") or "", str(i.get("quote") or "")[:40])
                                 for i in c.get("items") or []))
            return (c.get("field") or "", c.get("kind") or "", pairs)
        have = {sig(c) for c in declared}
        for c in derived:
            if sig(c) not in have:
                problems.append({"code": "N06",
                                 "detail": f"待核对点未披露：{c.get('field')} {c.get('kind')} "
                                           f"{[i.get('anchor') for i in c.get('items') or []]}"})

    # ---- N08 章节承接
    roles = {"abstract", "intro", "related", "method", "result", "discussion",
             "conclusion", "limitation", "reference", "appendix", "other"}
    for i, s in enumerate(secs):
        if (s.get("role") or "") not in roles:
            problems.append({"code": "N08",
                             "detail": f"{s.get('sec_id')} 缺 role 或取值不在码表内：{s.get('role')}"})
        if not (s.get("role_basis") or {}).get("source"):
            problems.append({"code": "N08", "detail": f"{s.get('sec_id')} 缺 role_basis"})
        for key in ("first_anchor", "last_anchor"):
            a = s.get(key)
            if a and resolve(parse, a) is None:
                problems.append({"code": "N08", "detail": f"{s.get('sec_id')} 的 {key} 无原文：{a}"})
        carry = s.get("carry")
        if i + 1 < len(secs):
            if not isinstance(carry, dict):
                problems.append({"code": "N08", "detail": f"{s.get('sec_id')} 缺 carry 承接边"})
                continue
            if carry.get("to") != secs[i + 1].get("sec_id"):
                problems.append({"code": "N08",
                                 "detail": f"{s.get('sec_id')} 的 carry.to 不是下一节"
                                           f"（{carry.get('to')}）"})
            ta = carry.get("to_anchor")
            if ta and resolve(parse, ta) is None:
                problems.append({"code": "N08",
                                 "detail": f"{s.get('sec_id')} 的 carry.to_anchor 无原文：{ta}"})
        elif carry:
            problems.append({"code": "N08", "detail": f"末节 {s.get('sec_id')} 不该有 carry"})

    # ---- N09 交接与未报告
    n_handoff = n_not_reported = n_undetermined = 0
    for field in SIX:
        v = card.get(field) or {}
        st = v.get("status")
        if st == "not_reported":
            n_not_reported += 1
        if st == "undetermined":
            n_undetermined += 1
        if st in ("not_reported", "undetermined"):
            hs = v.get("handoff") or []
            if not hs:
                problems.append({"code": "N09", "detail": f"{field} 为 {st} 但没写 handoff"})
                continue
            for h in hs:
                n_handoff += 1
                if not (h.get("action") or "").strip():
                    problems.append({"code": "N09", "detail": f"{field} 的 handoff 缺 action"})
                if not (h.get("search_terms") or []):
                    problems.append({"code": "N09", "detail": f"{field} 的 handoff 缺检索词"})
                for a in h.get("anchors") or []:
                    if resolve(parse, a) is None:
                        problems.append({"code": "N09",
                                         "detail": f"{field} 的 handoff 锚点无原文：{a}"})
        elif st == "auto" and v.get("machine_checkable") is False:
            problems.append({"code": "N09", "detail": f"{field} 有 items 但标了机器不可判"})

    counts: dict[str, int] = {}
    for p in problems:
        counts[p["code"]] = counts.get(p["code"], 0) + 1
    return {"ok": not problems, "counts": counts, "problems": problems,
            "n_problems": len(problems), "n_sections": len(secs),
            "n_items": sum(1 for _ in iter_items(notes)),
            "sections_aligned": n03,
            "coverage": {"headings": len(titles_parse), "matched": len(common),
                         "ratio": round(len(common) / len(titles_parse), 4) if titles_parse else 1.0,
                         "order_ok": in_order},
            "card_filled": [k for k, v in card.items() if (v or {}).get("items")],
            "n_not_reported": n_not_reported, "n_undetermined": n_undetermined,
            "n_handoff": n_handoff,
            "anchors_by_kind": anchors, "n_quote_verbatim": n_quote,
            "n_rewritten": n_rewritten, "n_reason": n_reason,
            "n_conflicts_derived": len(derived), "n_conflicts_declared": len(declared),
            "n_knowledge": len(notes.get("knowledge") or [])}


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="笔记核验")
    ap.add_argument("--parse", required=True)
    ap.add_argument("--notes", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    res = verify(load(Path(args.parse)), load(Path(args.notes)))
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("ok", "n_sections", "n_items", "counts", "coverage",
                                          "n_quote_verbatim", "n_conflicts_declared")},
                     ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
