#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""insert_refs.py — 按 numbering.json 把编号写进 docx，编号为上标 [n]。

输入：numbering.json（编号表 + 每句锚点）、prep.json（句→段落、句内偏移）、原稿 docx
输出：新 docx、changelog.jsonl（每处改动）、可选重建参考文献表

规则：
  已有 [n] → 原位替换为新编号（不清除其余文字）
  新增编号 → 挂在锚点处，多个同位置合并（[1] / [1,3] / [1-3]）
  编号一律作为上标 run 写入，正文字号不变

用法：
  python insert_refs.py --docx work/zh/manuscript.docx --prep work/zh/prep_docx.json \
      --numbering work/zh/numbering.json --out work/zh/manuscript_cited.docx
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import docx_io


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def fmt(nums: list[int]) -> str:
    ns = sorted(set(nums))
    if not ns:
        return ""
    if len(ns) > 1 and ns == list(range(ns[0], ns[-1] + 1)):
        return f"[{ns[0]}-{ns[-1]}]"
    return "[" + ",".join(str(x) for x in ns) + "]"


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="编号插入 docx")
    ap.add_argument("--docx", required=True)
    ap.add_argument("--prep", required=True)
    ap.add_argument("--numbering", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-refs", action="store_true", help="不重建文末参考文献表")
    ap.add_argument("--changelog", default=None)
    args = ap.parse_args(argv)

    prep = json.loads(Path(args.prep).read_text(encoding="utf-8-sig"))
    num = json.loads(Path(args.numbering).read_text(encoding="utf-8-sig"))
    paras = {p["pid"]: p for p in prep["paragraphs"] if p.get("pid")}
    doc = docx_io.open_doc(args.docx)

    # 段落文本一致性：docx 段落与 prep 记录的段落应当逐字相同
    mismatch = []
    for pid, p in paras.items():
        idx = p.get("para_index")
        if idx is None:
            continue
        if idx >= len(doc.paragraphs):
            mismatch.append({"pid": pid, "para_index": idx, "why": "段落越界"})
            continue
        if (doc.paragraphs[idx].text or "") != p["text"]:
            mismatch.append({"pid": pid, "para_index": idx, "why": "文本不一致",
                             "docx": (doc.paragraphs[idx].text or "")[:40],
                             "prep": p["text"][:40]})

    ops: dict[int, dict] = {}
    log = []
    bad = {m["para_index"] for m in mismatch}
    skipped = 0
    for c in num["cites"]:
        pid = c.get("pid")
        p = paras.get(pid)
        if not p or p.get("para_index") is None:
            continue
        idx = p["para_index"]
        if idx in bad:
            skipped += 1
            log.append({"uid": c["uid"], "para_index": idx, "kind": "skipped",
                        "reason": "段落文本与 prep 不一致"})
            continue
        base = c.get("sentence_offset") or 0
        slot = ops.setdefault(idx, {"del": [], "ins": {}, "marks": {}})
        for a in c["anchors"]:
            if a["kind"] == "replace":
                s, e = base + a["start"], base + a["end"]
                slot["del"].append((s, e))
                pos = s
            else:
                pos = base + a["offset"]
            slot["ins"].setdefault(pos, []).extend(a["nums"])
            slot["marks"].setdefault(pos, []).append(a.get("marker") or fmt(a["nums"]))
            slot.setdefault("sup", {})[pos] = a.get("sup", True)
            log.append({"uid": c["uid"], "para_index": idx, "kind": a["kind"],
                        "offset": pos, "old": a.get("old"), "nums": a["nums"],
                        "marker": a.get("marker") or fmt(a["nums"]), "section": c.get("section")})

    for idx, slot in ops.items():
        para = doc.paragraphs[idx]
        dels = sorted(slot["del"])
        for s, e in reversed(dels):
            docx_io.delete_range(para, s, e)

        def shift(pos: int, dels=dels) -> int:
            return pos - sum(e - s for s, e in dels if e <= pos)

        for pos in sorted(slot["ins"], reverse=True):
            marks = slot["marks"].get(pos) or []
            if len(marks) <= 1:
                text = marks[0] if marks else fmt(slot["ins"][pos])
            elif all(m.startswith("[") for m in marks):
                text = fmt(slot["ins"][pos])
            else:
                uniq = []
                for m in marks:
                    if m not in uniq:
                        uniq.append(m)
                text = "; ".join(uniq)
            docx_io.insert_superscript(para, shift(pos), text,
                                      superscript=slot["sup"].get(pos, True))

    n_refs = 0
    n_removed = 0
    if not args.no_refs:
        texts = [r["text"] for r in num["refs"]]
        numbered = (num.get("style") or "gbt7714") != "apa7"
        n_removed, n_refs = docx_io.replace_reference_section(doc, texts, numbered=numbered)

    doc.save(args.out)

    cl = Path(args.changelog) if args.changelog else Path(args.out).with_suffix(".changelog.jsonl")
    cl.parent.mkdir(parents=True, exist_ok=True)
    cl.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in log) + "\n",
                  encoding="utf-8")

    markers = docx_io.superscript_markers(docx_io.open_doc(args.out))
    print(json.dumps({"ok": True, "n_ops": sum(1 for x in log if x.get("kind") != "skipped"), "n_paragraphs": len(ops),
                      "n_superscript": len(markers), "n_refs_written": n_refs,
                      "mismatch": len(mismatch), "skipped": skipped, "out": args.out,
                      "changelog": str(cl), "mismatch_detail": mismatch[:3]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




