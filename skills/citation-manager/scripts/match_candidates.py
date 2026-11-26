#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""match_candidates.py — 为每个 CITE 句检索库内候选条目，生成匹配请求的输入。

打分：中文按字二元组重合率，英文按词重合率；句中出现作者姓时加权。
不使用模型。

用法：
  python match_candidates.py --prep work/zh/prep.json --pass1 work/zh/pass1_out.txt \
      --lib work/lib/library.json --tsv work/zh/match_input.tsv
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

EN_STOP = set("""a an the of in on for to with and or by from at as is are was were be
been this that these those using based via under over between among into its their our we
study studies research analysis method methods approach results effect effects new novel
review case cases toward towards show shows showed reported reports find found""".split())


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def zh_bigrams(text: str) -> Counter:
    out = Counter()
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        for i in range(len(run) - 1):
            out[run[i:i + 2]] += 1
    return out


def en_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z][a-z'-]{2,}", text.lower()) if t not in EN_STOP}


def surname(author: str) -> str:
    a = author.strip()
    if re.search(r"[\u4e00-\u9fff]", a):
        return a
    if "," in a:
        return a.split(",")[0].strip().lower()
    return a.split()[-1].lower()


def load_glossary(path: str | None) -> dict[str, list[str]]:
    g: dict[str, list[str]] = {}
    if not path:
        return g
    p = Path(path)
    if not p.exists():
        return g
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or "\t" not in line:
            continue
        zh, en = line.split("\t", 1)
        g.setdefault(zh.strip(), []).append(en.strip().lower())
    return g


def expand_glossary(text: str, gloss: dict[str, list[str]]) -> str:
    extra = []
    for zh, ens in gloss.items():
        if zh in text:
            extra.extend(ens)
    return text + " " + " ".join(extra) if extra else text


def norm_title(t: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (t or "").lower())


def _clean_score(e: dict) -> float:
    """同一文献多条目时，选字段最完整的一条（作者带逗号、页码含连字符、有卷期、题名无尾点）。"""
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
    if re.search(r"[，．：；]", e.get("raw", "") or ""):
        s -= 0.5
    return s


def refs_map(refs_path: str | None, entries: list[dict]) -> dict[str, str]:
    """稿件参考文献表 [n] → 库 id，按 DOI 优先、题名次之；同文献多条目取最完整的一条。"""
    out: dict[str, str] = {}
    if not refs_path:
        return out
    p = Path(refs_path)
    if not p.exists():
        return out
    cand: dict[str, list[dict]] = {}
    doi_idx: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("title"):
            cand.setdefault(norm_title(e["title"]), []).append(e)
        if e.get("doi"):
            doi_idx.setdefault(e["doi"].lower(), []).append(e)

    def best(lst: list[dict]) -> dict:
        return sorted(lst, key=lambda x: (-_clean_score(x), x.get("seq", 0)))[0]

    for line in p.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*\[(\d+)\]\s*(.+)$", line)
        if not m:
            continue
        num, body = m.group(1), m.group(2)
        dm = re.search(r"DOI:(10\.\S+?)\.?$", body)
        if dm and dm.group(1).lower() in doi_idx:
            out[num] = best(doi_idx[dm.group(1).lower()])["id"]
            continue
        tm = TAG_RE_REF.search(body)
        if not tm:
            continue
        nt = norm_title(body[:tm.start()].split(". ", 1)[-1])
        if nt in cand:
            out[num] = best(cand[nt])["id"]
            continue
        near = [lst for k, lst in cand.items() if k and (k in nt or nt in k)]
        if near:
            out[num] = best([x for lst in near for x in lst])["id"]
    return out


TAG_RE_REF = re.compile(r"\[[A-Z]{1,2}(?:/[A-Z]{2})?\]")


def markers(text: str) -> list[str]:
    nums: list[str] = []
    for m in re.finditer(r"\[([0-9,\-–\s]+)\]", text):
        for part in m.group(1).split(","):
            part = part.strip()
            rng = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
            if rng:
                a, b = int(rng.group(1)), int(rng.group(2))
                nums.extend(str(x) for x in range(a, b + 1))
            elif part.isdigit():
                nums.append(part)
    return nums


def score(sent: str, entry: dict) -> tuple[float, list[str]]:
    profile = " ".join([entry.get("title", ""), entry.get("container", ""),
                        " ".join(entry.get("keywords", []))])
    terms: list[str] = []
    s_zh, p_zh = zh_bigrams(sent), zh_bigrams(profile)
    s_en, p_en = en_tokens(sent), en_tokens(profile)
    total, hit = 0, 0
    if s_zh:
        total += sum(s_zh.values())
        for g, c in s_zh.items():
            if g in p_zh:
                hit += c
                terms.append(g)
    if s_en:
        total += len(s_en)
        for w in s_en:
            if w in p_en:
                hit += 1
                terms.append(w)
    base = hit / math.sqrt(max(total, 1)) if total else 0.0
    boost, extra = 0.0, []
    low = sent.lower()
    for a in entry.get("authors", []):
        sn = surname(a)
        if sn and (sn in low or sn in sent):
            boost = 0.5
            extra.append(sn)
            break
    terms = sorted(set(terms), key=lambda x: (-len(x), x))[:4]
    return round(min(base + boost, 1.0), 3), extra + terms


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="CITE 句候选检索")
    ap.add_argument("--prep", required=True)
    ap.add_argument("--pass1", required=True)
    ap.add_argument("--lib", required=True)
    ap.add_argument("--tsv", required=True)
    ap.add_argument("--refs", default=None, help="稿件参考文献表，用于把已有 [n] 映射到库条目")
    ap.add_argument("--glossary", default=None, help="中英术语表，跨语言匹配用")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--min-score", type=float, default=0.12)
    args = ap.parse_args(argv)

    prep = json.loads(Path(args.prep).read_text(encoding="utf-8-sig"))
    sent = {s["uid"]: s for s in prep["sentences"]}
    lib = json.loads(Path(args.lib).read_text(encoding="utf-8-sig"))
    entries = [dict(e) for e in lib.get("entries", [])]
    for e in entries:
        e["keywords"] = e.get("keywords") or []
    by_id = {e["id"]: e for e in entries}

    gloss = load_glossary(args.glossary)
    rmap = refs_map(args.refs, entries)

    cite: list[tuple[str, str, str]] = []
    for line in Path(args.pass1).read_text(encoding="utf-8-sig").splitlines():
        f = [x.strip() for x in line.split("|")]
        if len(f) >= 4 and f[1] == "CITE":
            uid = f[0]
            cite.append((uid, sent.get(uid, {}).get("section", ""), sent.get(uid, {}).get("text", "")))

    rows, n_no, n_mark = [], 0, 0
    for uid, sec, text in cite:
        expanded = expand_glossary(text, gloss)
        picked: dict[str, dict] = {}
        for num in markers(text):
            eid = rmap.get(num)
            if eid and eid not in picked:
                e = by_id[eid]
                picked[eid] = {"id": eid, "score": 1.0, "src": "MARK",
                               "terms": [f"[{num}]"], "title": e.get("title", ""),
                               "year": e.get("year", ""), "kw": e.get("keywords", [])}
                n_mark += 1
        sem = []
        for e in entries:
            sc, terms = score(expanded, e)
            if sc >= args.min_score and e["id"] not in picked:
                sem.append((sc, e, terms))
        sem.sort(key=lambda x: -x[0])
        for sc, e, terms in sem[: max(0, args.top - len(picked))]:
            picked[e["id"]] = {"id": e["id"], "score": sc, "src": "SEM", "terms": terms,
                               "title": e.get("title", ""), "year": e.get("year", ""),
                               "kw": e.get("keywords", [])}
        cands = sorted(picked.values(), key=lambda x: (x["src"] != "MARK", -x["score"]))
        if not cands:
            n_no += 1
        rows.append({"uid": uid, "section": sec, "text": text, "cands": cands,
                     "status": "NO_CAND" if not cands else "OK"})

    out = Path(args.tsv)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["uid\tsection\tsentence\tcands\tstatus"]
    for r in rows:
        cell = "; ".join(f"{c['id']}|{c['score']}|{c['src']}|{'/'.join(c['terms'])}"
                         for c in r["cands"])
        lines.append("\t".join([r["uid"], r["section"], r["text"].replace("\t", " "),
                                cell, r["status"]]))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                        encoding="utf-8")

    print(json.dumps({"ok": True, "n_cite": len(rows), "n_no_cand": n_no,
                      "n_mark": n_mark, "refs_mapped": len(rmap), "out": str(out)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
