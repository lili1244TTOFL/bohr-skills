#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""provenance.py — 输入快照 + 成品逐条回溯链。

题面要求「能否从成品回溯到原始稿件、文献库快照、每条引用的匹配依据和每一步改动」。
这里给三样东西：
  1. 输入快照：每个输入文件的字节数与 sha256（稿件、稿件文献表、每个文献库文件）；
  2. 条目指纹：文末每条参考文献对应的库条目，按字段排序后算 sha256 前 12 位，
     这样「这条文献当时长什么样」可被独立复核；
  3. 回溯链：编号 → 条目 id → 首现段/句/句内偏移 → 改动处数与改动明细 → 匹配依据
     → 条目指纹 → 源文件。
只读不写业务文件；缺哪个输入就在 gaps 里写明，不编造。

用法：
  python provenance.py --numbering work/numbering.json --lib work/library_deduped.json \
      --changelog work/changelog.jsonl --input docx=/in/manuscript.docx \
      --input refs=/in/refs.txt --input lib=/in/library.bib \
      --out work/provenance.json --md output/trace.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(e: dict) -> str:
    payload = json.dumps({k: e.get(k) for k in
                          ("type", "authors", "title", "container", "year",
                           "volume", "issue", "pages", "doi", "publisher", "place")},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def parse_inputs(pairs: list[str]) -> list[dict]:
    out = []
    for item in pairs or []:
        kind, _, path = item.partition("=")
        if not path:
            kind, path = "input", item
        p = Path(path)
        if not p.exists():
            out.append({"kind": kind, "path": path, "exists": False})
            continue
        row = {"kind": kind, "path": str(p), "name": p.name, "exists": True,
               "bytes": p.stat().st_size, "sha256": sha_file(p)[:16]}
        if p.suffix.lower() == ".json":
            try:
                obj = json.loads(p.read_text(encoding="utf-8-sig"))
                if isinstance(obj, dict) and "entries" in obj:
                    row["n_entries"] = len(obj["entries"])
            except Exception:
                pass
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="快照与回溯链")
    ap.add_argument("--numbering", required=True)
    ap.add_argument("--lib", required=True)
    ap.add_argument("--changelog", default=None)
    ap.add_argument("--input", action="append", default=[], help="kind=路径，可多次")
    ap.add_argument("--out", default=None)
    ap.add_argument("--md", default=None)
    args = ap.parse_args(argv)

    num = json.loads(Path(args.numbering).read_text(encoding="utf-8-sig"))
    lib = json.loads(Path(args.lib).read_text(encoding="utf-8-sig"))
    entries = {e["id"]: e for e in lib.get("entries", [])}
    changes: list[dict] = []
    gaps: list[str] = []
    cl = Path(args.changelog) if args.changelog else None
    if cl and cl.exists():
        for line in cl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                changes.append(json.loads(line))
    elif cl:
        gaps.append(f"改动明细文件不存在：{cl}")

    inputs = parse_inputs(args.input)
    for i in inputs:
        if not i.get("exists"):
            gaps.append(f"输入快照缺文件：{i['path']}")

    by_uid: dict[str, list[dict]] = {}
    for c in changes:
        by_uid.setdefault(c.get("uid") or "", []).append(c)

    chain: list[dict] = []
    n_orphan = 0
    for r in num.get("refs", []):
        eid = r["id"]
        e = entries.get(eid)
        first = r.get("first_uid") or ""
        uid_changes = by_uid.get(first, [])
        cite = next((c for c in num.get("cites", []) if c["uid"] == first), None)
        head_anchor = (cite or {}).get("anchors") or [{}]
        off = head_anchor[0].get("offset")
        if off is None:
            off = head_anchor[0].get("start")
        if e is None:
            n_orphan += 1
            gaps.append(f"编号 [{r['n']}] 的条目 {eid} 不在库内快照")
        row = {"n": r["n"], "id": eid, "first_uid": first,
               "pid": (cite or {}).get("pid"),
               "para_index": (cite or {}).get("para_index"),
               "sentence_offset": (cite or {}).get("sentence_offset"),
               "anchor_offset": off,
               "n_changes": len(uid_changes),
               "change_kinds": [c.get("kind") for c in uid_changes],
               "conf": (cite or {}).get("conf"),
               "match_basis": (cite or {}).get("basis"),
               "entry_fingerprint": fingerprint(e) if e else None,
               "entry_source_file": (e or {}).get("source_file"),
               "entry_source": (e or {}).get("source"),
               "title": (e or {}).get("title", "")[:80],
               "raw_head": ((e or {}).get("raw") or "")[:80],
               "render_fmt_errors": r.get("fmt") or []}
        chain.append(row)

    prov = {"generated_by": "provenance.py",
            "inputs": inputs, "n_inputs": len(inputs),
            "outputs": {"numbering": str(args.numbering), "changelog": str(cl) if cl else None},
            "chain": chain, "n_refs": len(chain), "n_orphan": n_orphan,
            "n_changes_total": len(changes), "gaps": gaps,
            "note": "指纹=条目关键字段排序后 sha256 前 12 位；"
                    "输入 sha256 为前 16 位，用于证明「成品对的是哪一份快照」"}
    op = Path(args.out) if args.out else Path(args.numbering).with_name("provenance.json")
    op.write_text(json.dumps(prov, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.md:
        def tbl(head: list[str], rows: list[list]) -> str:
            out = ["| " + " | ".join(head) + " |",
                   "|" + "|".join("-" for _ in head) + "|"]
            out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
            return "\n".join(out)

        md = ["# 回溯链：成品 → 稿件 → 匹配依据 → 文献库快照", "",
              "## 1 输入快照", "",
              tbl(["类", "文件", "字节", "sha256(前16)"],
                  [[i["kind"], i.get("name") or i["path"], i.get("bytes", "缺"),
                    i.get("sha256", "缺")] for i in inputs]),
              "", "## 2 每条参考文献的回溯链", "",
              tbl(["编号", "条目 id", "首现段/句", "句内偏移", "改动处", "匹配依据",
                   "条目指纹", "库源文件"],
                  [[r["n"], r["id"], f"{r['pid']}/{r['first_uid']}",
                    "" if r["anchor_offset"] is None else r["anchor_offset"],
                    r["n_changes"], r["match_basis"] or "—",
                    r["entry_fingerprint"] or "缺", r["entry_source_file"] or "—"]
                   for r in chain]),
              "", "## 3 怎么复核", "",
              "1. 用 `sha256` 对回手里那份稿件与文献库；",
              "2. 按「首现段/句 + 句内偏移」在稿件里找到改动位置；",
              "3. 用条目 id + 指纹回查库条目；",
              "4. 每个改动点的类型/原标记/新标记见 `changelog.md`。",
              ""] + ([f"- 缺口：{g}" for g in gaps] if gaps else [])
        mp = Path(args.md)
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text("\n".join(md), encoding="utf-8")

    print(json.dumps({"ok": True, "n_inputs": len(inputs), "n_refs": len(chain),
                      "n_orphan": n_orphan, "n_gaps": len(gaps),
                      "n_changes_total": len(changes), "out": str(op)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
