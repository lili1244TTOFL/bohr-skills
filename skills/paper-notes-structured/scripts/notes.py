#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notes.py — 章节对齐与结构化笔记生成。

三步，都不调用模型：
  1 sectionize：按标题块把正文切成节，节内保留段落顺序与页码
  2 extract：按字段信号在节内挑候选句，附块 id
  3 card/auto：把候选整理成论文卡片与知识点，字段缺失标 not_reported

调用方模型只需在候选基础上改写，块 id 不变。

用法：
  python notes.py --parse work/parse.json --out work/notes.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SIGNALS = {
    "question": r"(?i)\b(we (?:ask|investigate|examine|study|test)|this (?:paper|study) "
                r"(?:asks|investigates|examines)|aim(?:s)? (?:to|of)|research question|"
                r"本文|本研究|旨在|考察|检验)",
    "data": r"(?i)\b(dataset|corpus|participants?|subjects?|samples?|n\s*=\s*\d+|"
            r"\d+\s*(?:participants|subjects|patients|samples|papers)|data(?:set)? (?:from|of))",
    "method": r"(?i)\b(we (?:use|used|apply|applied|train|trained|measure|measured|"
              r"compute|computed|implement|implemented)|method|procedure|protocol|"
              r"experimental (?:setup|design)|模型|方法|实验|流程|测量)",
    "finding": r"(?i)\b(we (?:find|found|show|showed|observe|observed|demonstrate|"
               r"report)|results? (?:show|indicate|suggest|reveal)|p\s*[<>=]\s*0?\.\d+|"
               r"significant(?:ly)?|发现|结果表明|显著)",
    "limitation": r"(?i)\b(limitation|limitations|future work|caveat|not (?:tested|covered)|"
                  r"remains? (?:unclear|unknown)|however,|drawback|局限|不足|未来工作|尚不清楚)",
}
FIELD_CN = {"question": "研究问题", "data": "数据", "method": "方法条件",
            "finding": "主要发现", "limitation": "局限"}
CITE_KEEP = re.compile(r"\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]")
NUM = re.compile(r"\d+(?:[.,]\d+)*")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


KEEP = ("body", "caption", "table", "reference", "formula")


def page_ids(parse: dict) -> dict[tuple[int, int], str]:
    """与 verify_notes.text_of 一致的块 id：P{page}-{该页正文块序号}。"""
    ids = {}
    for page in parse["pages"]:
        i = 0
        for b in page["blocks"]:
            if b["role"] not in KEEP:
                continue
            ids[(page["page"], id(b))] = f"P{page['page']}-{i}"
            i += 1
    return ids


def sectionize(parse: dict) -> list[dict]:
    secs: list[dict] = []
    ids = page_ids(parse)
    cur = {"title": "（题名与摘要）", "level": 0, "page": 1, "paras": [], "tables": []}
    for page in parse["pages"]:
        for b in page["blocks"]:
            if b["role"] in ("header", "footer", "page_number"):
                continue
            if b["role"] == "heading":
                title = b["text"].strip()
                if cur["paras"] or cur["tables"] or cur["title"] not in ("（题名与摘要）", title):
                    if not secs or secs[-1]["title"] != cur["title"]:
                        secs.append(cur)
                cur = {"title": b["text"], "level": b.get("level", 1), "page": b["page"],
                       "paras": [], "tables": []}
            elif b["role"] == "table":
                cur["tables"].append({"page": b["page"], "text": b["text"],
                                      "id": ids[(page["page"], id(b))]})
            elif b["role"] in KEEP:
                cur["paras"].append({"page": b["page"], "text": b["text"],
                                     "role": b["role"],
                                     "id": ids[(page["page"], id(b))]})
    if cur["paras"] or cur["tables"]:
        secs.append(cur)
    for i, s in enumerate(secs, 1):
        s["sec_id"] = f"S{i:02d}"
    return secs


def candidates(sec: dict) -> dict:
    out = {k: [] for k in SIGNALS}
    for p in sec["paras"]:
        for field, pat in SIGNALS.items():
            if re.search(pat, p["text"]):
                sent = re.split(r"(?<=[.。!?！？])\s+", p["text"])[0][:240]
                out[field].append({"id": p["id"], "page": p["page"], "text": sent})
    return out


def auto_card(secs: list[dict]) -> dict:
    card = {v: {"status": "not_reported", "items": []} for v in FIELD_CN.values()}
    for s in secs:
        cand = s["candidates"]
        for field in SIGNALS:
            if cand[field]:
                for it in cand[field][:2]:
                    card[FIELD_CN[field]]["items"].append(
                        {"text": it["text"], "anchor": it["id"], "page": it["page"],
                         "section": s["title"]})
    for k, v in card.items():
        if v["items"]:
            v["status"] = "auto"
    return card


def knowledge_items(secs: list[dict]) -> list[dict]:
    items = []
    for s in secs:
        for field in ("finding", "method"):
            for it in s["candidates"][field][:1]:
                key = re.sub(r"\s+", " ", it["text"])[:60]
                items.append({"kind": field, "section": s["title"], "page": it["page"],
                              "anchor": it["id"], "text": it["text"], "key": key})
    seen, uniq = set(), []
    for it in items:
        if it["key"] in seen:
            continue
        seen.add(it["key"])
        uniq.append(it)
    return uniq


def build(parse: dict) -> dict:
    secs = sectionize(parse)
    for s in secs:
        s["candidates"] = candidates(s)
    card = auto_card(secs)
    return {"sections": [{"sec_id": s["sec_id"], "title": s["title"], "level": s["level"],
                          "page": s["page"], "n_paras": len(s["paras"]),
                          "n_tables": len(s["tables"]),
                          "candidates": s["candidates"]} for s in secs],
            "card": card, "knowledge": knowledge_items(secs)}


def to_markdown(res: dict) -> str:
    lines = ["# 结构化笔记", ""]
    lines.append("## 论文卡片")
    lines.append("")
    for field, v in res["card"].items():
        if v["items"]:
            for it in v["items"]:
                lines.append(f"- **{field}**（{it['section']}，p.{it['page']}）：{it['text']}"
                             f"  `[{it['anchor']}]`")
        else:
            lines.append(f"- **{field}**：not_reported")
    lines.append("")
    lines.append("## 章节笔记")
    lines.append("")
    for s in res["sections"]:
        lines.append(f"### {s['title']}（p.{s['page']}，{s['n_paras']} 段）")
        for field in ("question", "data", "method", "finding", "limitation"):
            for it in s["candidates"][field][:2]:
                lines.append(f"- {field}: {it['text']} `[{it['id']}]`")
        lines.append("")
    lines.append("## 知识点清单")
    lines.append("")
    for k in res["knowledge"]:
        lines.append(f"- [{k['kind']}] {k['text']} `[{k['anchor']}]` p.{k['page']} · {k['section']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="结构化笔记")
    ap.add_argument("--parse", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    res = build(load(Path(args.parse)))
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "sections": len(res["sections"]),
                      "knowledge": len(res["knowledge"]),
                      "filled": sum(1 for v in res["card"].values() if v["items"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
