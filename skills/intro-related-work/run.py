#!/usr/bin/env python3
"""run.py —— 引言与相关工作：四部分骨架 + 引用核验 + 贡献对账。

设计对应题面四关注点：
    立得住  背景必须引到具体文献，套话直接报出来
    分得清  相关工作按分组维度组织，逐篇摘要拼接会被检出
    说得实  空白段必须引用文献，无引用的空白判为"未推导"
    对得上  每条贡献要有 Fig./Table/Section 指向，引用的键必须存在于 bib

不调用模型：抽取与核验都是规则。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

VERSION = "1.0.0"
SECTIONS = ["研究背景", "相关工作", "研究空白", "贡献"]
CLICHE = [
    r"近年来.{0,20}(受到|引起|备受).{0,6}关注", r"随着.{0,12}的(快速)?发展",
    r"具有(重要|深远)意义", r"引起了(广泛|极大)关注", r"备受关注",
]
CITE = re.compile(r"\[([^\]]+)\]|\\cite\{([^}]*)\}")
EVID = re.compile(r"\b(Fig(?:ure)?\.?\s*\d+|Table\s*\d+|Section\s*\d+(?:\.\d+)*|式\s*\(?\d+\)?)\b", re.I)
BIB = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,(.*?)\}\s*(?=@|\Z)", re.S)


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


def bib_entries(text: str) -> dict:
    out = {}
    for key, body in BIB.findall(text):
        out[key] = {
            "author": bool(re.search(r"author\s*=", body, re.I)),
            "year": bool(re.search(r"year\s*=", body, re.I)),
            "title": bool(re.search(r"title\s*=", body, re.I)),
        }
    return out


def cited_keys(text: str) -> list:
    keys = []
    for a, b in CITE.findall(text):
        raw = a or b
        for k in re.split(r"[,;\s]+", raw):
            k = k.strip().lstrip("@")
            if k:
                keys.append(k)
    return keys


def split_sections(draft: str) -> dict:
    """按二级标题切段；没有标题时按关键词定位。"""
    got = {}
    for name in SECTIONS:
        m = re.search(rf"^#{{1,3}}\s*.*{name}.*$", draft, re.M)
        if m:
            start = m.end()
            nxt = re.search(r"^#{1,3}\s", draft[start:], re.M)
            got[name] = draft[start:start + nxt.start()] if nxt else draft[start:]
        else:
            got[name] = ""
    return got


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None)
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="output")
    ap.add_argument("--case-id", default=None)
    a = ap.parse_args(argv)

    t0 = time.perf_counter()
    work, out = Path(a.work), Path(a.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    root = Path(a.input) if a.input else Path(".")

    case_id = a.case_id or root.name or "case"
    cj = root / "case.json"
    if cj.exists():
        try:
            case = json.loads(read(cj))
            case_id = a.case_id or str(case.get("case_id") or case_id)
        except Exception:
            pass

    draft_p = next(iter(sorted(root.rglob("draft*.md"))), None)
    bib_p = next(iter(sorted(root.rglob("*.bib"))), None)
    mat_p = next((p for p in sorted(root.rglob("*.md")) if "draft" not in p.name), None)

    if draft_p is None and mat_p is None:
        res = {"case_id": case_id, "status": "blocked",
               "gaps": ["未找到 draft.md 或材料 .md"], "uncertainty": ["无输入"]}
        (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked"}, ensure_ascii=False))
        return 1

    draft = read(draft_p) if draft_p else ""
    material = read(mat_p) if mat_p else ""
    bib = bib_entries(read(bib_p)) if bib_p else {}

    secs = split_sections(draft)
    cites = cited_keys(draft)
    problems, gaps = [], []

    # C01 四部分齐全
    missing = [s for s, body in secs.items() if not body.strip()]
    if missing:
        problems.append({"code": "C01", "detail": "缺章节：" + "、".join(missing)})

    # C02 相关工作分组：出现"分组维度"或分点比较
    rel = secs.get("相关工作", "")
    if rel and not re.search(r"(按|从)(问题|方法|路线|技术|数据)|分为\s*\d+\s*(类|组)|一类|另一类", rel):
        problems.append({"code": "C02", "detail": "相关工作未见分组或比较维度，疑似逐篇罗列"})

    # C03 引用的键是否都在 bib
    unknown = [k for k in cites if bib and k not in bib]
    if unknown:
        problems.append({"code": "C03", "detail": "引用键不在 bib：" + "、".join(sorted(set(unknown)))})

    # C04 bib 未被引用
    unused = [k for k in bib if k not in cites]
    if unused:
        problems.append({"code": "C04", "detail": "bib 条目未被引用：" + "、".join(sorted(unused)[:8])})

    # C05 空白段必须带引用
    gap_sec = secs.get("研究空白", "")
    if gap_sec and not cited_keys(gap_sec):
        problems.append({"code": "C05", "detail": "研究空白段无引用，空白未由已引文献推出"})

    # C06 贡献条目要有证据指向
    contrib = secs.get("贡献", "")
    items = [l for l in contrib.splitlines() if re.match(r"\s*(?:[-*]|\d+[.)])\s+", l)]
    no_ev = [l.strip()[:30] for l in items if not EVID.search(l)]
    if items and no_ev:
        problems.append({"code": "C06",
                         "detail": f"{len(no_ev)}/{len(items)} 条贡献无 Fig./Table/Section 指向"})
    if not items and contrib.strip():
        problems.append({"code": "C06", "detail": "贡献段未见分条，无法逐条对账"})

    # C07 套话
    for pat in CLICHE:
        if re.search(pat, draft):
            problems.append({"code": "C07", "detail": "空转铺垫：" + re.search(pat, draft).group(0)})

    # C08 元数据字段
    thin = [k for k, v in bib.items() if not all(v.values())]
    if thin:
        problems.append({"code": "C08", "detail": "元数据不全（缺 author/year/title）：" + "、".join(thin[:6])})

    for s in missing:
        gaps.append(f"缺章节：{s}")
    if not bib:
        gaps.append("无 bib 文件，引用无法核验")
    if not draft:
        gaps.append("无 draft.md，仅按材料生成骨架")

    status = "success" if not problems and draft else ("partial" if problems else "partial")

    # 四部分骨架（缺哪段补空模板，不编内容）
    outline = ["# 引言与相关工作（骨架）", ""]
    for s in SECTIONS:
        body = secs.get(s, "").strip()
        outline.append(f"## {s}")
        outline.append(body if body else f"> 待补：材料中未见该部分。可用材料行数 {len(material.splitlines())}。")
        outline.append("")
    (out / "outline.md").write_text("\n".join(outline), encoding="utf-8")

    (out / "citations.json").write_text(json.dumps({
        "case_id": case_id,
        "cited": sorted(set(cites)),
        "in_bib_not_cited": sorted(set(bib) - set(cites)),
        "cited_not_in_bib": sorted(set(cites) - set(bib)) if bib else [],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    result = {
        "case_id": case_id,
        "skill_version": VERSION,
        "status": status,
        "目标产物": "outline.md（四部分骨架，缺段标待补）",
        "事实与来源映射": "citations.json（引用键 ↔ bib 双向差集）",
        "版本与边界": {"gaps": gaps, "problems": len(problems)},
        "stats": {"problems": len(problems), "sections_missing": len(missing),
                  "cited": len(set(cites)), "bib": len(bib),
                  "elapsed_s": round(time.perf_counter() - t0, 3)},
        "checks": {p["code"]: p["detail"] for p in problems},
        "gaps": gaps,
        "uncertainty": gaps,
    }
    for k in ("目标产物", "事实与来源映射", "版本与边界"):
        result.setdefault(k, None)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "problems": len(problems),
                      "codes": [p["code"] for p in problems]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
