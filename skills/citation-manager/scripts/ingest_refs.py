#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest_refs.py — 把稿件原有参考文献表并入文献库，并做查重。

处理：
  1 解析稿件参考文献表（[n] 行），拆成字段，保留原编号 orig_no；
  2 与库内条目比对：DOI → 题名归一化 → 作者+年+刊名；
  3 重复时以库内条目为准（canonical = 库 id），只用稿件条目补空缺字段；
  4 不重复的作为新条目入库（id = m01…，source = manuscript）。

输出：
  library_merged.json  库 + 稿件条目（含 canonical 指针）
  orig_map.json        原编号 → 规范 id
  ingest_report.json   每条判定依据与得分

用法：
  python ingest_refs.py --refs work/zh/refs_zh.txt --lib work/lib/library.json \
      --out-lib work/lib/library_merged.json --out-map work/zh/orig_map.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

TAG_RE = re.compile(r"\[([A-Z]{1,2})(?:/([A-Z]{2}))?\]")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def norm_title(t: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (t or "").lower())


def surname(a: str) -> str:
    a = a.strip()
    if re.search(r"[\u4e00-\u9fff]", a):
        return a
    if "," in a:
        return a.split(",")[0].strip().lower()
    return a.split()[-1].lower() if a.split() else ""


def split_authors(s: str) -> list[str]:
    """国标风格作者串 → 列表。SMITH J, BROWN L, et al → 三个（末尾 et al/等 丢弃）。"""
    s = s.strip().rstrip(".")
    parts = re.split(r"\s*[,;、]\s*|\s+and\s+", s)
    out = []
    for p in parts:
        p = p.strip().rstrip(".")
        if not p or p.lower() in {"等", "et al", "etal"}:
            continue
        out.append(p)
    return out


def parse_refs(path: str) -> list[dict]:
    out = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*\[(\d+)\]\s*(.+)$", line)
        if not m:
            continue
        no, body = int(m.group(1)), m.group(2).strip()
        tm = TAG_RE.search(body)
        tag = tm.group(1) if tm else ""
        head = body[:tm.start()] if tm else body
        tail = body[tm.end():] if tm else ""
        au_part, _, title = head.partition(". ")
        title = title.strip().rstrip(".")
        doi = ""
        dm = re.search(r"DOI:(10\.\S+?)\.?$", tail)
        if dm:
            doi = dm.group(1)
            tail = tail[:dm.start()]
        year = ""
        ym = re.search(r"(18|19|20)\d{2}", tail)
        if ym:
            year = ym.group(0)
        vol = issue = pages = place = publisher = ""
        pm = re.search(r":\s*([A-Za-z]?\d+(?:-[A-Za-z]?\d+)?)", tail)
        if pm:
            pages = pm.group(1)
        # 卷期只在年份之后找，避免把年份当卷号
        rest = tail[ym.end():] if ym else tail
        vm = re.match(r"\s*[,，]\s*(\d+)\s*(?:\(([^)]*)\))?", rest)
        if vm:
            vol = vm.group(1)
            issue = vm.group(2) or ""
        cont = re.split(r",\s*(?:18|19|20)\d{2}", tail)[0].strip(" .")
        # 专著/学位论文/会议：出版地: 出版者, 年
        mm = re.match(r"^[.．]?\s*([^:：]+)[:：]\s*([^,]+),\s*(18|19|20)\d{2}", tail)
        if mm and tag in {"M", "D", "C", "R", "S"}:
            place = mm.group(1).strip()
            publisher = mm.group(2).strip()
            cont = ""
        out.append({"orig_no": no, "type": tag, "authors": split_authors(au_part),
                    "title": title, "container": cont, "year": year,
                    "volume": vol, "issue": issue, "pages": pages, "doi": doi,
                    "place": place, "publisher": publisher, "raw": line.strip()})
    return out


def score(me: dict, le: dict) -> tuple[float, str]:
    if me["doi"] and le.get("doi") and me["doi"].lower() == le["doi"].lower():
        return 1.0, "D1"
    a, b = norm_title(me["title"]), norm_title(le.get("title", ""))
    if a and a == b:
        return 0.95, "D2"
    if a and b:
        r = difflib.SequenceMatcher(None, a, b).ratio()
        if r >= 0.85:
            return round(0.8 + 0.1 * r, 3), "D2"
    sa = {surname(x) for x in me["authors"]}
    sb = {surname(x) for x in (le.get("authors") or [])}
    same_au = bool(sa & sb)
    same_y = me["year"] and me["year"] == str(le.get("year", ""))
    same_c = bool(me["container"]) and me["container"].lower()[:8] == \
        (le.get("container") or "").lower()[:8]
    if same_au and same_y and same_c:
        return 0.7, "D4"
    if same_au and same_y:
        return 0.3, "A1"
    return 0.0, "-"


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="稿件参考文献入库与查重")
    ap.add_argument("--refs", required=True)
    ap.add_argument("--lib", required=True)
    ap.add_argument("--out-lib", required=True)
    ap.add_argument("--out-map", required=True)
    ap.add_argument("--report", default=None)
    ap.add_argument("--threshold", type=float, default=0.7)
    args = ap.parse_args(argv)

    lib = json.loads(Path(args.lib).read_text(encoding="utf-8-sig"))
    entries: list[dict] = [dict(e) for e in lib.get("entries", [])]
    mine = parse_refs(args.refs)

    orig_map: dict[str, str] = {}
    report: list[dict] = []
    added = 0
    for me in mine:
        best, best_s, best_basis = None, 0.0, "-"
        for le in entries:
            s, basis = score(me, le)
            if s > best_s:
                best, best_s, best_basis = le, s, basis
        if best is not None and best_s >= args.threshold:
            for k in ("volume", "issue", "pages", "doi", "place", "publisher", "container"):
                if not best.get(k) and me.get(k):
                    best[k] = me[k]
            orig_map[str(me["orig_no"])] = best["id"]
            report.append({"orig_no": me["orig_no"], "canonical": best["id"],
                           "score": best_s, "basis": best_basis, "action": "merged",
                           "title": me["title"][:50]})
        else:
            eid = f"m{me['orig_no']:02d}"
            entry = {"id": eid, "type": me["type"] or "Z", "authors": me["authors"],
                     "title": me["title"], "container": me["container"],
                     "year": me["year"], "volume": me["volume"], "issue": me["issue"],
                     "pages": me["pages"], "doi": me["doi"],
                     "publisher": me.get("publisher", ""), "place": me.get("place", ""),
                     "url": "", "keywords_raw": "", "raw": me["raw"],
                     "source": "manuscript", "orig_no": me["orig_no"]}
            entries.append(entry)
            orig_map[str(me["orig_no"])] = eid
            added += 1
            report.append({"orig_no": me["orig_no"], "canonical": eid,
                           "score": best_s, "basis": best_basis, "action": "added",
                           "title": me["title"][:50]})

    for i, e in enumerate(entries, 1):
        e.setdefault("seq", i)

    out_lib = Path(args.out_lib)
    out_lib.parent.mkdir(parents=True, exist_ok=True)
    out_lib.write_text(json.dumps({"generated_by": "ingest_refs.py",
                                   "n": len(entries), "entries": entries},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
    Path(args.out_map).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_map).write_text(json.dumps(orig_map, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    rep_path = Path(args.report) if args.report else Path(args.out_map).with_name("ingest_report.json")
    rep_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({"ok": True, "n_refs": len(mine), "merged": len(mine) - added,
                      "added": added, "n_lib": len(entries),
                      "map": str(args.out_map), "report": str(rep_path)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

