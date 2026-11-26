#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""keywords.py — 文献库关键词候选与索引，附查重候选对。

关键词优先取库内字段；无字段时从题名抽取：中文取 2–6 字连续片段，英文取词与相邻二元组。
细节字段（作者、刊名、年、DOI）原样带入，供查重比对。

用法：
  python keywords.py work/lib/library.json --out work/lib/keywords.json --tsv work/lib/keywords.tsv
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ZH_STOP_BOUNDARY = set("的了与和及在对中为是等把被从向于以其之或而则下上")
ZH_PREFIX = ("关于", "基于", "面向", "一种", "一类", "针对", "利用", "采用", "通过", "结合")
ZH_SUFFIX = (
    "增强", "抑制", "检测", "识别", "分割", "预测", "评估", "评价", "分类", "聚类",
    "优化", "调度", "控制", "学习", "网络", "算法", "模型", "方法", "技术", "系统",
    "机制", "效应", "影响", "分析", "研究", "进展", "综述", "评析", "建议", "对策",
    "应用", "设计", "实现", "实验", "测量", "重建", "融合", "配准", "跟踪", "定位",
    "检索", "问答", "翻译", "生成", "诊断", "治疗", "护理", "干预", "调查", "验证",
    "规律", "特性", "性能", "质量", "安全", "误差", "参数", "结构", "工艺", "材料",
)
ZH_GENERIC = {"研究", "分析", "方法", "影响", "应用", "进展", "综述", "评析"}
EN_STOP = set("""a an the of in on for to with and or by from at as is are was were be been
this that these those using based via under over between among into its their our we study
studies research analysis method methods approach approaches results effect effects new novel
review case cases towards toward""".split())


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def zh_candidates(title: str) -> list[str]:
    """按题名边界取片段：只从合法起点到合法终点（前缀词后、后缀词末、标点）。"""
    out: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+", title):
        n = len(run)
        starts = {0}
        for i, ch in enumerate(run):
            if ch in ZH_STOP_BOUNDARY:
                starts.add(i + 1)
        for w in ZH_PREFIX:
            p = run.find(w)
            if p >= 0:
                starts.add(p + len(w))
        ends = {n}
        for w in ZH_SUFFIX:
            p = run.find(w)
            while p >= 0:
                ends.add(p + len(w))
                p = run.find(w, p + 1)
        for i in sorted(starts):
            for j in sorted(ends):
                if j - i < 2 or j - i > 8:
                    continue
                g = run[i:j]
                if g in ZH_GENERIC:
                    continue
                if g[0] in ZH_STOP_BOUNDARY or g[-1] in ZH_STOP_BOUNDARY:
                    continue
                out.append(g)
        if not any(True for _ in out):
            # 兜底：题名过长或术语跨词时，取 4/3 字滑窗
            for size in (4, 3):
                for i in range(n - size + 1):
                    g = run[i:i + size]
                    if g[0] in ZH_STOP_BOUNDARY or g[-1] in ZH_STOP_BOUNDARY:
                        continue
                    if g in ZH_GENERIC:
                        continue
                    out.append(g)
    return out


def en_candidates(title: str) -> list[str]:
    toks = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z'-]*", title)]
    keep = [t for t in toks if t not in EN_STOP and len(t) >= 3]
    out = list(keep)
    for a, b in zip(toks, toks[1:]):
        if a in EN_STOP or b in EN_STOP:
            continue
        if len(a) >= 3 and len(b) >= 3:
            out.append(f"{a} {b}")
    return out


def split_field(v: str) -> list[str]:
    parts = re.split(r"[;；,，/|]", v or "")
    return [p.strip() for p in parts if p.strip()]


def norm_title(t: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (t or "").lower())


def pick(cands: Counter, df: Counter, total: int, limit: int = 8) -> list[str]:
    scored = []
    for g, tf in cands.items():
        d = df[g]
        if total >= 4 and d == total:
            continue                      # 全库通用词
        scored.append((g, d, tf))
    scored.sort(key=lambda x: (-len(x[0]), -x[1], -x[2], x[0]))
    chosen: list[str] = []
    for g, d, tf in scored:
        if len(chosen) >= limit:
            break
        if any(g in c for c in chosen):
            continue
        chosen.append(g)
    return chosen


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="关键词候选与索引")
    ap.add_argument("lib", help="library.json")
    ap.add_argument("--out", default="work/lib/keywords.json")
    ap.add_argument("--tsv", default=None)
    ap.add_argument("--limit", type=int, default=8)
    args = ap.parse_args(argv)

    lib = json.loads(Path(args.lib).read_text(encoding="utf-8"))
    ents = lib.get("entries", [])
    raw_kw = {e["id"]: split_field(e.get("keywords_raw", "")) for e in ents}

    pool: dict[str, Counter] = {}
    df: Counter = Counter()
    for e in ents:
        if raw_kw[e["id"]]:
            continue
        c = Counter(zh_candidates(e.get("title", "")) + en_candidates(e.get("title", "")))
        pool[e["id"]] = c
        for g in c:
            df[g] += 1

    entries, index = [], {}
    for e in ents:
        eid = e["id"]
        if raw_kw[eid]:
            kws, source = raw_kw[eid][: args.limit], "field"
        else:
            kws, source = pick(pool.get(eid, Counter()), df, len(pool)), "title"
        for k in kws:
            index.setdefault(k, []).append(eid)
        entries.append({
            "id": eid,
            "keywords": kws,
            "kw_source": source,
            "details": {
                "authors": e.get("authors", []),
                "title": e.get("title", ""),
                "container": e.get("container", ""),
                "year": e.get("year", ""),
                "volume": e.get("volume", ""),
                "issue": e.get("issue", ""),
                "pages": e.get("pages", ""),
                "doi": e.get("doi", ""),
                "type": e.get("type", ""),
            },
        })

    dup: list[dict] = []
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            basis = []
            if a["details"]["doi"] and a["details"]["doi"].lower() == b["details"]["doi"].lower():
                basis.append("D1")
            if norm_title(a["details"]["title"]) and \
                    norm_title(a["details"]["title"]) == norm_title(b["details"]["title"]):
                basis.append("D2")
            if (a["details"]["year"] == b["details"]["year"]
                    and a["details"]["container"].lower() == b["details"]["container"].lower()
                    and a["details"]["authors"] and
                    a["details"]["authors"][0].split(",")[-1].strip().lower()
                    == b["details"]["authors"][0].split(",")[-1].strip().lower()):
                basis.append("D4")
            if basis:
                dup.append({"a": a["id"], "b": b["id"], "basis": basis})

    out = {"generated_by": "keywords.py", "n": len(entries),
           "entries": entries, "index": index, "dup_candidates": dup}
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.tsv:
        lines = ["id\tkw_source\tkeywords\ttitle\tauthors\tcontainer\tyear\tdoi"]
        for x in entries:
            d = x["details"]
            lines.append("\t".join([
                x["id"], x["kw_source"], "; ".join(x["keywords"]),
                d["title"].replace("\t", " "), "; ".join(d["authors"]).replace("\t", " "),
                d["container"].replace("\t", " "), str(d["year"]), d["doi"],
            ]))
        Path(args.tsv).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({"ok": True, "n": len(entries),
                      "kw_field": sum(1 for x in entries if x["kw_source"] == "field"),
                      "kw_title": sum(1 for x in entries if x["kw_source"] == "title"),
                      "n_uniq_kw": len(index), "dup_pairs": len(dup),
                      "out": str(p)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
