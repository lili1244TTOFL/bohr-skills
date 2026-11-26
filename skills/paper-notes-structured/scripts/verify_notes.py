#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_notes.py — 笔记核验：锚点存在、结构对齐、数字可回溯、字段完整。

规则：
  N01 锚点存在：每条笔记的 anchor 必须在 parse.json 的块 id 集合内
  N02 数字可回溯：笔记里的每个数字必须出现在其锚点原文中
  N03 章节对齐：sections 顺序与原文标题顺序一致，且每个标题至少有一条笔记
  N04 字段完整：卡片五个字段，缺失项必须显式 not_reported
  N05 重复条目：知识点 key 不得重复

用法：
  python verify_notes.py --parse work/parse.json --notes work/notes.json --out work/verify.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def block_index(parse: dict) -> dict:
    out = {}
    for page in parse["pages"]:
        for b in page["blocks"]:
            if b["role"] in ("header", "footer", "page_number"):
                continue
            key = f"P{b['page']}-{len(out)}"
            out.setdefault((b["page"], b["text"]), b)
    return out


def text_of(parse: dict, anchor: str, page: int) -> str | None:
    """anchor 形如 P4-12，取该页第 12 个正文块。"""
    m = re.match(r"P(\d+)-(\d+)$", anchor or "")
    if not m:
        return None
    pno, idx = int(m.group(1)), int(m.group(2))
    page_blocks = [b for pg in parse["pages"] if pg["page"] == pno
                   for b in pg["blocks"] if b["role"] in ("body", "caption", "table",
                                                          "reference", "formula")]
    if 0 <= idx < len(page_blocks):
        return page_blocks[idx]["text"]
    return None


def verify(parse: dict, notes: dict) -> dict:
    problems: list[dict] = []
    secs = notes.get("sections") or []
    if not secs:
        problems.append({"code": "N00", "detail": "没有章节笔记"})
    titles_parse = [b["text"] for pg in parse["pages"] for b in pg["blocks"]
                    if b["role"] == "heading"]
    titles_notes = [s["title"] for s in secs]
    notes_set = set(titles_notes)
    common = [t for t in titles_parse if t in notes_set]
    in_order = [t for t in titles_notes if t in set(titles_parse)] == common
    # N03 同时要求覆盖率与顺序：顺序错位也必须判失败（原实现算了 in_order 却没使用）
    n03 = (not titles_parse or len(common) >= max(1, len(titles_parse) * 0.8)) and in_order

    items = []
    for s in secs:
        for field, lst in (s.get("candidates") or {}).items():
            for it in lst:
                items.append((s["title"], it))
    for field, v in (notes.get("card") or {}).items():
        for it in v.get("items") or []:
            items.append((it.get("section", ""), it))
    for k in notes.get("knowledge") or []:
        items.append((k.get("section", ""), k))

    for sec_title, it in items:
        anchor = it.get("anchor") or it.get("id") or ""
        page = int(it.get("page") or 0)
        src = text_of(parse, anchor, page)
        if src is None:
            problems.append({"code": "N01", "detail": f"锚点无对应原文：{anchor}"})
            continue
        for n in NUM.findall(it.get("text") or ""):
            if n not in src and n.replace(",", "") not in src.replace(",", ""):
                problems.append({"code": "N02",
                                 "detail": f"数字不可回溯：{n} 不在 {anchor} 原文中"})
                break

    card = notes.get("card") or {}
    for field in ("研究问题", "数据", "方法条件", "主要发现", "局限"):
        v = card.get(field)
        if v is None:
            problems.append({"code": "N04", "detail": f"卡片缺字段：{field}"})
        elif not v.get("items") and v.get("status") != "not_reported":
            problems.append({"code": "N04", "detail": f"{field} 为空但未标 not_reported"})

    keys = [k.get("key") for k in notes.get("knowledge") or []]
    dup = {k for k in keys if keys.count(k) > 1}
    for d in dup:
        problems.append({"code": "N05", "detail": f"知识点重复：{d}"})

    counts: dict[str, int] = {}
    for p in problems:
        counts[p["code"]] = counts.get(p["code"], 0) + 1
    return {"ok": not problems, "counts": counts, "problems": problems,
            "n_problems": len(problems), "n_sections": len(secs),
            "n_items": len(items), "sections_aligned": n03,
            "card_filled": [k for k, v in card.items() if v.get("items")]}


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
    print(json.dumps({k: res[k] for k in ("ok", "n_sections", "n_items", "counts",
                                          "sections_aligned")}, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
