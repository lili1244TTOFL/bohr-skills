#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""renumber.py — 按正文首现顺序给文献编号，生成插入点表。

输入：prep.json（段/句/偏移）、pass3 匹配表（uid|编号|置信度|依据）、library.json、稿件参考文献表（可选）
输出：
  numbering.json  编号表 + 每句插入锚点（replace：已有标记；insert：新增）
  numbering.tsv   序号、id、首现 uid、国标条目文本

顺序规则：按句在正文中的先后，句内按锚点位置先后；首次出现的 id 取下一个号。
已有 [n] 的句子先用参考文献表把 n 换成库 id，再参与排序。

用法：
  python renumber.py --prep work/zh/prep.json --match work/zh/pass3_match.txt \
      --lib work/lib/library.json --refs work/zh/refs_zh.txt --out work/zh/numbering.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TAG_RE_REF = re.compile(r"\[[A-Z]{1,2}(?:/[A-Z]{2})?\]")
MARK_RE = re.compile(r"\[([0-9,\-–\s]+)\]")
END_PUNCT = "。！？；.!?;"


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def load_render():
    import importlib.util
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("check_gbt", here / "check_gbt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def norm_title(t: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (t or "").lower())


def clean_score(e: dict) -> float:
    s = 0.0
    au = e.get("authors") or []
    if au and all("," in a for a in au):
        s += 1.0
    if not (e.get("title") or "").rstrip().endswith("."):
        s += 1.0
    if "-" in (e.get("pages") or "").replace("--", "-"):
        s += 0.5
    if e.get("volume"):
        s += 0.5
    if e.get("doi"):
        s += 0.5
    return s


def build_refs_map(refs_path: str | None, entries: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    if not refs_path or not Path(refs_path).exists():
        return out
    by_title: dict[str, list[dict]] = {}
    by_doi: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("title"):
            by_title.setdefault(norm_title(e["title"]), []).append(e)
        if e.get("doi"):
            by_doi.setdefault(e["doi"].lower(), []).append(e)

    def best(lst: list[dict]) -> dict:
        return sorted(lst, key=lambda x: (-clean_score(x), x.get("seq", 0)))[0]

    for line in Path(refs_path).read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*\[(\d+)\]\s*(.+)$", line)
        if not m:
            continue
        num, body = m.group(1), m.group(2)
        dm = re.search(r"DOI:(10\.\S+?)\.?$", body)
        if dm and dm.group(1).lower() in by_doi:
            out[num] = best(by_doi[dm.group(1).lower()])["id"]
            continue
        tm = TAG_RE_REF.search(body)
        if tm:
            nt = norm_title(body[:tm.start()].split(". ", 1)[-1])
            if nt in by_title:
                out[num] = best(by_title[nt])["id"]
    return out


CJK_STOP = set("与和及、，,；;。！？：:()（）")
FUNC_STOP = set("常可能被将已会需应均都也则是有关随由并且但而或于其该此的了地得使让")


def _extend_cjk(text: str, pos: int) -> int:
    """挂点后接中文名词短语时整体包住：遇并列标点或功能字停，空格可穿过。"""
    while pos < len(text):
        ch = text[pos]
        if ch in CJK_STOP or ch in FUNC_STOP:
            break
        if ch == " ":
            pos += 1
            continue
        if re.match(r"[\u4e00-\u9fffA-Za-z0-9\-]", ch):
            pos += 1
            continue
        break
    while pos > 0 and text[pos - 1] == " ":
        pos -= 1
    return pos


def anchor_offset(text: str, entry: dict, extra_terms: list[str]) -> int:
    """新增引用挂点：句中已出现的匹配词之后 → 作者名/“等”之后 → 句末标点之前。"""
    low = text.lower()
    terms = sorted({t for t in (extra_terms or []) if t and len(t) >= 2},
                   key=lambda x: -len(x))
    for t in terms:
        if t.startswith("[") or not re.search(r"[\w\u4e00-\u9fff]", t):
            continue
        m = re.search(re.escape(t), text, re.I)
        if m:
            return _extend_cjk(text, m.end())
    sur = []
    for a in entry.get("authors", []):
        if re.search(r"[\u4e00-\u9fff]", a):
            sur.append(a.strip())
        elif "," in a:
            sur.append(a.split(",")[0].strip())
        elif a.strip():
            sur.append(a.split()[-1])
    for s in sur:
        if not s:
            continue
        m = re.search(re.escape(s) + r"\s*(等|et al\.?)?", text, re.I)
        if m:
            return _extend_cjk(text, m.end())
    kw = sorted([k for k in entry.get("keywords", []) if len(k) >= 2],
                key=lambda x: -len(x))
    for t in kw:
        m = re.search(re.escape(t), text, re.I)
        if m:
            return _extend_cjk(text, m.end())
    for i in range(len(text) - 1, -1, -1):
        if text[i] in END_PUNCT:
            return i
    return len(text)


def fmt_marker(nums: list[int]) -> str:
    """[1] / [1,3] / [1-3]"""
    ns = sorted(set(nums))
    if len(ns) > 1 and ns == list(range(ns[0], ns[-1] + 1)):
        return f"[{ns[0]}-{ns[-1]}]"
    return "[" + ",".join(str(n) for n in ns) + "]"


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="编号与插入点")
    ap.add_argument("--prep", required=True)
    ap.add_argument("--match", required=True)
    ap.add_argument("--lib", required=True)
    ap.add_argument("--refs", default=None, help="稿件参考文献表（现场解析）")
    ap.add_argument("--orig-map", default=None, help="ingest_refs 产出的原编号→库 id 映射")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cands", default=None, help="match_input.tsv，提供句中已命中的匹配词")
    ap.add_argument("--keep-orig-numbers", action="store_true",
                    help="原有文献保持原编号（按原序号排），新增编号接在末尾")
    ap.add_argument("--style", default="gbt7714", choices=["gbt7714", "apa7", "ieee"])
    ap.add_argument("--lang", default="zh", choices=["zh", "en"])
    ap.add_argument("--merge-new-into-mark", action="store_true",
                    help="句内已有标记时，新增编号并入该标记位置，不另起挂点")
    args = ap.parse_args(argv)

    gbt = load_render()
    import importlib.util as _u3
    _spec = _u3.spec_from_file_location("styles", Path(__file__).resolve().parent / "styles.py")
    styles = _u3.module_from_spec(_spec)
    _spec.loader.exec_module(styles)
    prep = json.loads(Path(args.prep).read_text(encoding="utf-8-sig"))
    sents = {s["uid"]: s for s in prep["sentences"]}
    lib = json.loads(Path(args.lib).read_text(encoding="utf-8-sig"))
    entries = {e["id"]: e for e in lib.get("entries", [])}
    if args.orig_map:
        rmap = json.loads(Path(args.orig_map).read_text(encoding="utf-8-sig"))
        rmap = {str(k): v for k, v in rmap.items()}
    else:
        rmap = build_refs_map(args.refs, list(entries.values()))

    cands: dict[str, dict[str, list[str]]] = {}
    if args.cands and Path(args.cands).exists():
        rows = Path(args.cands).read_text(encoding="utf-8-sig").splitlines()
        for ln in rows[1:]:
            f = ln.split("\t")
            if len(f) < 4:
                continue
            per: dict[str, list[str]] = {}
            for c in f[3].split(";"):
                parts = [x.strip() for x in c.split("|")]
                if len(parts) >= 4:
                    per[parts[0]] = [x for x in parts[3].split("/") if x]
            cands[f[0]] = per

    match: dict[str, dict] = {}
    for line in Path(args.match).read_text(encoding="utf-8-sig").splitlines():
        f = [x.strip() for x in line.split("|")]
        if len(f) != 4 or f[0].startswith("#"):
            continue
        ids = [] if f[1] == "NONE" else [x for x in f[1].split(",") if x.strip()]
        match[f[0]] = {"ids": ids, "conf": f[2], "basis": f[3]}

    order: dict[str, int] = {}
    seq: list[dict] = []
    cites: list[dict] = []
    unmapped: list[dict] = []

    per_items: dict[str, list[dict]] = {}
    appear: list[str] = []
    first_uid: dict[str, str] = {}

    for s in prep["sentences"]:
        uid = s["uid"]
        if uid not in match:
            continue
        text = s["text"]
        m = match[uid]

        existing = []
        for mk in MARK_RE.finditer(text):
            nums = []
            for part in mk.group(1).split(","):
                part = part.strip()
                rng = re.match(r"^(\d+)\s*[-\u2013]\s*(\d+)$", part)
                if rng:
                    nums.extend(str(x) for x in range(int(rng.group(1)), int(rng.group(2)) + 1))
                elif part.isdigit():
                    nums.append(part)
            ids = [rmap.get(n) for n in nums]
            if any(i is None for i in ids):
                unmapped.append({"uid": uid, "nums": nums})
            existing.append({"start": mk.start(), "end": mk.end(), "nums": nums,
                             "old": mk.group(0),
                             "ids": [i for i in ids if i]})

        covered = {i for e in existing for i in e["ids"]}
        new_ids = [i for i in m["ids"] if i not in covered]

        items: list[dict] = []
        for e in existing:
            items.append({"kind": "replace", "pos": e["start"], "end": e["end"],
                          "old": e["old"], "ids": e["ids"]})
        for i in new_ids:
            if i not in entries:
                continue
            if args.merge_new_into_mark and existing:
                off = existing[0]["start"]
            else:
                off = anchor_offset(text, entries[i], cands.get(uid, {}).get(i, []))
            items.append({"kind": "insert", "pos": off, "ids": [i]})
        items.sort(key=lambda x: (x["pos"], 0 if x["kind"] == "replace" else 1))
        per_items[uid] = items

        for it in items:
            for i in it["ids"]:
                if i not in appear:
                    appear.append(i)
                    first_uid[i] = uid

    inv_orig: dict[str, int] = {}
    for k, v in rmap.items():
        if str(k).isdigit():
            inv_orig.setdefault(v, int(k))

    if args.keep_orig_numbers and inv_orig:
        orig = sorted([i for i in appear if i in inv_orig], key=lambda x: inv_orig[x])
        others = [i for i in appear if i not in set(orig)]
        seq_ids = orig + others
        num_mode = "keep-orig"
    else:
        seq_ids = appear
        num_mode = "first-appearance"

    if args.style == "apa7":
        seq_tmp = [{"n": k + 1, "id": i} for k, i in enumerate(seq_ids)]
        seq_tmp = styles.list_order(seq_tmp, "apa7", entries)
        seq_ids = [x["id"] for x in seq_tmp]
        num_mode = "alphabetical"
    order = {i: n for n, i in enumerate(seq_ids, 1)}
    seq = [{"n": order[i], "id": i, "first_uid": first_uid.get(i, "")}
           for i in sorted(order, key=lambda x: order[x])]

    for s in prep["sentences"]:
        uid = s["uid"]
        if uid not in per_items:
            continue
        m = match[uid]
        anchors: list[dict] = []
        by_num = {order[i]: entries[i] for i in order}
        for it in per_items[uid]:
            nums = [order[i] for i in it["ids"] if i in order]
            marker = styles.intext(nums, args.style, by_num, args.lang)
            if args.style == "apa7" and marker.startswith(("（", "(")):
                lead = (s.get("text") or "")[:it["pos"]]
                names = [(entries[i].get("authors") or [""])[0] for i in it["ids"] if i in entries]
                surs = [styles._split_author(n)[0] for n in names]
                if any(s and (s in lead or f"{s} 等" in lead) for s in surs):
                    years = [styles._year(entries[i]) or "n.d." for i in it["ids"] if i in entries]
                    inner = ", ".join(years)
                    marker = f"（{inner}）" if args.lang == "zh" else f"({inner})"
            if it["kind"] == "replace":
                tend = it["end"]
                stxt = s.get("text") or ""
                if args.style == "apa7" and tend < len(stxt) and stxt[tend] == " ":
                    tend += 1
                anchors.append({"kind": "replace", "start": it["pos"], "end": tend,
                                "old": it["old"], "ids": it["ids"], "nums": nums,
                                "marker": marker, "sup": args.style != "apa7"})
            else:
                anchors.append({"kind": "insert", "offset": it["pos"],
                                "ids": it["ids"], "nums": nums, "marker": marker,
                                "sup": args.style != "apa7"})
        cites.append({"uid": uid, "pid": uid.split(":")[0], "para_index": s.get("para_index"),
                      "section": s.get("section"), "sentence_offset": s.get("offset"),
                      "text": s["text"], "conf": m["conf"], "basis": m["basis"],
                      "anchors": anchors})

    rendered = []
    for it in seq:
        e = entries[it["id"]]
        txt, errs = styles.render(e, args.style)
        if args.style == "gbt7714":
            errs = [x["code"] for x in gbt.check_entry(e)]
        rendered.append({**it, "text": txt, "fmt": errs})

    dropped = sorted({i for i in appear if i not in entries})
    out = {"generated_by": "renumber.py", "mode": num_mode, "style": args.style,
           "n_refs": len(seq),
           "dropped_ids": dropped, "order": seq,
           "map": order, "cites": cites, "unmapped_markers": unmapped,
           "refs": rendered}
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    tsv = p.with_suffix(".tsv")
    lines = ["n\tid\tfirst_uid\tfmt\ttext"]
    for r in rendered:
        lines.append("\t".join([str(r["n"]), r["id"], r["first_uid"],
                                ",".join(r["fmt"]) or "-", r["text"]]))
    tsv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    n_insert = sum(1 for c in cites for a in c["anchors"] if a["kind"] == "insert")
    n_replace = sum(1 for c in cites for a in c["anchors"] if a["kind"] == "replace")
    print(json.dumps({"ok": True, "n_refs": len(seq), "n_cites": len(cites),
                      "n_replace": n_replace, "n_insert": n_insert,
                      "unmapped": len(unmapped), "fmt_errors": sum(len(r["fmt"]) for r in rendered),
                      "out": str(p)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())









