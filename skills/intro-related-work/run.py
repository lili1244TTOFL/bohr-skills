#!/usr/bin/env python3
"""run.py —— 引言与相关工作：四部分骨架 + 引用核验 + 贡献对账 + 改稿。

设计对应题面四关注点：
    立得住  背景必须引到具体文献，套话直接报出来
    分得清  相关工作按分组维度组织，逐篇摘要拼接会被检出
    说得实  空白段必须引用文献，无引用的空白判为"未推导"
    对得上  每条贡献要有 Fig./Table/Section 指向，引用的键必须存在于 bib

产出两份正文：`outline.md`（四部分骨架，缺段标待补）与 `revised.md`
（`compose.py` 改出来的稿：相关工作按维度重组、缺引论断补引、贡献补证据指向）。

不调用模型：抽取与核验都是规则。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import compose

VERSION = "1.1.0"
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
    """按二级标题切段；所有章节都用二级标题时才按 `##` 切，否则退回 1–3 级。

    这样"# 引言与相关工作（改写稿）"这类总标题不会被误当成「相关工作」段。
    """
    got = {}
    lvl2 = any(re.search(rf"^##\s*.*{n}.*$", draft, re.M) for n in SECTIONS)
    head = r"^##\s" if lvl2 else r"^#{1,3}\s"
    for name in SECTIONS:
        pat = rf"^##\s*.*{name}.*$" if lvl2 else rf"^#{{1,3}}\s*.*{name}.*$"
        m = re.search(pat, draft, re.M)
        if m:
            start = m.end()
            nxt = re.search(head, draft[start:], re.M)
            got[name] = draft[start:start + nxt.start()] if nxt else draft[start:]
        else:
            got[name] = ""
    return got


def audit(secs: dict, text: str, bib: dict) -> list[dict]:
    """C01–C08 八条判据，对任意一份稿子（原稿或改稿）都能跑。判据本身不区分稿子。"""
    problems = []
    # C01 四部分齐全
    missing = [s for s, body in secs.items() if not body.strip()]
    if missing:
        problems.append({"code": "C01", "detail": "缺章节：" + "、".join(missing)})

    cites = cited_keys(text)

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
        m = re.search(pat, text)
        if m:
            problems.append({"code": "C07", "detail": "空转铺垫：" + m.group(0)})

    # C08 元数据字段
    thin = [k for k, v in bib.items() if not all(v.values())]
    if thin:
        problems.append({"code": "C08", "detail": "元数据不全（缺 author/year/title）：" + "、".join(thin[:6])})
    return problems


# 改稿上仍然残留时，这几码是要作者补材料/补编号的，不是改稿本身没做完；
# 其余（C01/C02/C03/C05/C07）是文字层判据，改稿里还存在就说明这轮没改到位。
AUTHOR_CODES = {"C04", "C06", "C08"}


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
    contract_keys: list[str] = []
    cj = root / "case.json"
    if cj.exists():
        try:
            case = json.loads(read(cj))
            case_id = a.case_id or str(case.get("case_id") or case_id)
            oc = case.get("output_contract")
            if isinstance(oc, dict):
                contract_keys = [str(k) for k in oc]
            elif isinstance(oc, list):
                contract_keys = [str(k) for k in oc]
        except Exception:
            pass

    draft_p = next(iter(sorted(root.rglob("draft*.md"))), None)
    bib_p = next(iter(sorted(root.rglob("*.bib"))), None)
    mat_p = next((p for p in sorted(root.rglob("*.md")) if "draft" not in p.name), None)

    if draft_p is None and mat_p is None:
        msg = "未找到 draft.md 或材料 .md"
        res = {"case_id": case_id, "skill_version": VERSION, "status": "blocked",
               "目标产物": [], "事实与来源映射": [],
               "版本与边界": {"changed": {}, "manual_checks": [msg],
                              "gaps": [msg], "uncertainty": [msg]},
               "output_contract": {}, "gaps": [msg], "uncertainty": [msg],
               "deliverables": ["missing_input.md"]}
        (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "missing_input.md").write_text("\n".join([
            "# 缺料说明：没有找到稿子", "",
            f"- 输入目录：`{root}`",
            "- 找过：`draft*.md`（原稿）、其余 `.md`（材料）、`*.bib`（文献库）",
            "- 至少需要一样：`draft.md` 或材料 `.md`；`refs.bib` 可选，缺了就核不了引用",
            "- 缺什么才能出产物：没有稿子就没有四部分可拆、没有原句可改，`revised.md` 无从生成",
            "- 补齐后重跑：`python run.py --input <目录> --work <w> --out <o>`",
        ]) + "\n", encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked"}, ensure_ascii=False))
        return 1

    draft = read(draft_p) if draft_p else ""
    material = read(mat_p) if mat_p else ""
    bib = bib_entries(read(bib_p)) if bib_p else {}

    # 没有 draft.md 时用材料 .md 当底稿：空手返回不如按材料出一稿
    src_text = draft or material
    src_label = "draft.md" if draft else "材料 .md（无 draft.md）"

    secs = split_sections(src_text)
    cites = cited_keys(src_text)
    problems = audit(secs, src_text, bib)
    gaps = []

    missing = [s for s, body in secs.items() if not body.strip()]
    for s in missing:
        gaps.append(f"缺章节：{s}")
    if not bib:
        gaps.append("无 bib 文件，引用无法核验")
    if not draft:
        gaps.append("无 draft.md：用材料 .md 当底稿改稿，章节与论断均取自材料原句")
    if not src_text.strip():
        gaps.append("底稿为空：outline/revised 只有骨架，没有任何原句")

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

    # 改稿：去套话、相关工作按维度重组、缺引论断补引、贡献补证据指向（compose.py）
    facts = compose.build(src_text, read(bib_p) if bib_p else "")
    revised = facts["revised_md"]
    (out / "revised.md").write_text(revised, encoding="utf-8")

    # 同一套判据对改稿再跑一遍：状态看交付物，不只看输入稿。
    # 只看四段正文，不看附录（附录里的"去套话记录"会照抄原句，那不是正文）。
    revised_body = "\n\n".join(facts["audit_sections"].values())
    residual = audit(facts["audit_sections"], revised_body, bib)
    blocking = [p for p in residual if p["code"] not in AUTHOR_CODES]
    for p in residual:
        if p["code"] in AUTHOR_CODES:
            gaps.append(f"改稿残留（需作者补，不压状态）：{p['code']} {p['detail']}")
    status = "success" if revised.strip() and not blocking else "partial"

    targets = []
    for kind, name in (("outline", "outline.md"), ("revised", "revised.md"),
                       ("citations", "citations.json")):
        p = out / name
        if p.exists():
            targets.append({"kind": kind, "path": str(p), "format": "md",
                            "bytes": p.stat().st_size,
                            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16],
                            "editable": True})

    # 事实与来源映射：每条引用键落到哪一段哪一句、bib 字段齐不齐；补引与证据指向逐条给依据
    fact_rows = []
    for s in facts["cite_sites"]:
        e = bib.get(s["key"], {})
        fact_rows.append({"kind": "引用", "key": s["key"], "section": s["section"],
                          "sentence": s["sentence"],
                          "in_bib": s["key"] in bib,
                          "fields_ok": all(e.get(f) for f in ("author", "year", "title"))
                          if e else None,
                          "source": f"refs.bib:{s['key']}"})
    for r in facts["cites_added"]:
        fact_rows.append({"kind": "补引", "record": f"B{r['index']}", "section": r["section"],
                          "sentence": r["sentence"], "added": r["added"],
                          "rule": r["rule"], "source": r["basis"]})
    for r in facts["evidence_rows"]:
        fact_rows.append({"kind": "图表/章节指向", "record": f"C{r['index']}",
                          "item": r["item"], "pointer": r["marker"], "source": r["basis"]})

        changes = {
            "works_regrouped": {"n_groups": facts["stats"]["n_groups"],
                                "n_sentences": facts["stats"]["n_group_sentences"],
                                "groups": facts["groups"]},
            "despin": facts["despin_rows"],
            "cites_added": facts["cites_added"],
            "evidence_added": [r for r in facts["evidence_rows"] if "原文自带" not in r["basis"]],
            "evidence_kept": [r for r in facts["evidence_rows"] if "原文自带" in r["basis"]],
            "lines": {"draft": len(src_text.splitlines()),
                      "revised": len(revised.splitlines())},
        }
    manual = list(facts["unresolved"]) + [f"{p['code']}：{p['detail']}" for p in problems]

    result = {
        "case_id": case_id,
        "skill_version": VERSION,
        "status": status,
        "目标产物": targets,
        "事实与来源映射": fact_rows,
        "版本与边界": {"changed": changes, "manual_checks": manual,
                       "gaps": gaps, "uncertainty": gaps,
                       "改稿残留": residual},
        "stats": {"problems": len(problems), "sections_missing": len(missing),
                  "cited": len(set(cites)), "bib": len(bib),
                  "groups": facts["stats"]["n_groups"],
                  "cites_added": facts["stats"]["n_cites_added"],
                  "evidence_added": facts["stats"]["n_evidence_added"],
                  "despin": facts["stats"]["n_despin"],
                  "residual": len(residual), "blocking": len(blocking),
                  "elapsed_s": round(time.perf_counter() - t0, 3)},
        "checks": {p["code"]: p["detail"] for p in problems},
        "改稿残留": residual,
        "状态口径": "success = 改稿写出且 C01/C02/C03/C05/C07 在改稿上全过；"
                "C04/C06/C08 残留属于待作者补材料，只进 gaps",
        "规则命中": {"去套话": facts["despin_rows"],
                  "分组": [{"维度": g["label"], "句数": g["n_items"], "依据": g["basis"]}
                         for g in facts["groups"]],
                  "补引": facts["cites_added"],
                  "证据指向": facts["evidence_rows"],
                  "底稿来源": src_label},
        "gaps": gaps,
        "uncertainty": gaps,
        "compose": {k: facts[k] for k in ("groups", "statements", "cites_added",
                                          "cites_pending", "evidence_rows",
                                          "evidence_pending", "unresolved", "stats")},
        "inputs": {"draft": str(draft_p) if draft_p else None,
                   "bib": str(bib_p) if bib_p else None,
                   "material": str(mat_p) if mat_p else None},
        "sources": {"bib": str(bib_p) if bib_p else None,
                    "draft": str(draft_p) if draft_p else None},
    }
    # case.json 给了 output_contract 就按字段名逐条回显真实内容，没给也照常输出三字段
    echo, echo_status = {}, {}
    alias = {"目标产物": "目标产物", "事实与来源映射": "事实与来源映射",
             "版本与边界": "版本与边界"}
    for key in contract_keys or list(alias):
        hit = alias.get(key)
        if hit:
            echo[key] = result[hit]
            echo_status[key] = {"status": "full", "source": hit}
        else:
            echo[key] = {"note": "本字段无专门产出，内容见 result.json 的 "
                                 "目标产物 / 事实与来源映射 / 版本与边界",
                         "pointer": ["目标产物", "事实与来源映射", "版本与边界"]}
            echo_status[key] = {"status": "partial", "source": "pointer"}
    result["output_contract"] = echo
    result["output_contract_echo"] = echo_status
    for k, v in echo.items():
        result.setdefault(k, v)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "problems": len(problems),
                      "codes": [p["code"] for p in problems],
                      "revised": str(out / "revised.md"),
                      "bytes": (out / "revised.md").stat().st_size,
                      **facts["stats"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
