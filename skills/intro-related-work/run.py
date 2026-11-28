#!/usr/bin/env python3
"""run.py —— 引言与相关工作：四部分骨架 + 引用核验 + 论断来源映射 + 改稿。

设计对应题面四关注点：
    立得住  背景必须引到具体文献，套话直接删掉并留记录
    分得清  相关工作按原文维度组织，并按「结论/适用条件/局限」同一维度逐工作比较
    说得实  空白段必须引用文献，且要由已引工作的局限推出（推导链见 §G 与 result.json）
    对得上  每条贡献要有 Fig./Table/Section 指向，引用的键必须存在于 bib，引用覆盖按路线盘点

产物：`outline.md`（四部分骨架 + 承接链）、`revised.md`（改稿）、`citations.json`（引用核验 +
覆盖）、`result.json`（三契约字段 + 判据结果 + 依据行）。

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

VERSION = "2.0.0"
SECTIONS = compose.SECTIONS

# 改稿上仍然残留、但属于"要作者补材料"的码：进 gaps 与 §D，不压状态（阈值不变）
AUTHOR_CODES = {"C04", "C06", "C08", "C09", "C10", "C11", "C12"}
BLOCKING_CODES = {"C01", "C02", "C03", "C05", "C07"}

NOISE_DIRS = {"work", "out", "output", "__pycache__", ".git", "_o", "o", "w",
              "examples", "cases", "templates", "checklists", "docs", "environment"}
DOC_NAMES = {"skill.md", "call.md", "readme.md", "task.md", "resources.yaml"}


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


# ------------------------------------------------------------------ 判据 C01–C12
def prose_lines(body: str) -> list:
    """段正文 → 折行合并后的行表（行号取自原稿，合并行为 `L10–11`）。"""
    return compose.reflow(list(enumerate(body.splitlines(), 1)))


def stems(text: str) -> set:
    """英文词干近似（前 5 字符）+ 中文二元组：aligns/alignment 算同一个词。"""
    return {t[:5] for t in compose.en_tokens(text)} | compose.zh_bigrams(text)


def audit(secs: dict, text: str, bib: dict) -> list[dict]:
    """C01–C12 十二条判据，对任意一份稿子（原稿或改稿）都能跑。判据本身不区分稿子。"""
    problems = []

    def add(code, detail):
        problems.append({"code": code, "detail": detail})

    # C01 四部分齐全
    missing = [s for s, body in secs.items() if not body.strip()]
    if missing:
        add("C01", "缺章节：" + "、".join(missing))

    cites = compose.cite_keys(text)

    # C02 相关工作分组：出现"分组维度"或分点比较（中英文都认）
    rel = secs.get("相关工作", "")
    GROUP_CUE = re.compile(
        r"(按|从)(问题|方法|路线|技术|数据|维度)|分为\s*\d+\s*(类|组)|一类|另一类|"
        r"归为[^。；]{0,8}(类|条|组|路线)|[两三四]条路线|"
        r"two (lines|families|groups|strands|categories)|three (lines|families|groups|categories)|"
        r"(grouped|organiz\w+|divided) (by|into)|families of (methods|approaches)|"
        r"by (approach|method|problem)|categories of (methods|approaches)", re.I)
    if rel and not GROUP_CUE.search(rel):
        add("C02", "相关工作未见分组或比较维度，疑似逐篇罗列")

    # C03 引用的键是否都在 bib
    unknown = [k for k in cites if bib and k not in bib]
    if unknown:
        add("C03", "引用键不在 bib：" + "、".join(sorted(set(unknown))))

    # C04 bib 未被引用
    unused = [k for k in bib if k not in cites]
    if unused:
        add("C04", "bib 条目未被引用：" + "、".join(sorted(unused)[:8]))

    # C05 空白段必须带引用
    gap_sec = secs.get("研究空白", "")
    if gap_sec and not compose.cite_keys(gap_sec):
        add("C05", "研究空白段无引用，空白未由已引文献推出")

    # C06 贡献条目要有证据指向 + 不夸大适用范围
    contrib = secs.get("贡献", "")
    items = [l for l in contrib.splitlines() if re.match(r"\s*(?:[-*]|\d+[.)])\s+", l)]
    no_ev = [l.strip()[:30] for l in items if not compose.EVID.search(l)]
    if items and no_ev:
        add("C06", f"{len(no_ev)}/{len(items)} 条贡献无 Fig./Table/Section 指向")
    if not items and contrib.strip():
        add("C06", "贡献段未见分条，无法逐条对账")
    loud = [l.strip()[:26] for l in items if compose.OVERCLAIM.search(l)]
    if loud:
        add("C06", f"{len(loud)} 条贡献含绝对化表述（首次/全部/唯一/最优 等），未附适用范围限定")

    # C07 套话
    for pat, why in compose.CLICHE:
        m = re.search(pat, text, re.I)
        if m:
            add("C07", "空转铺垫：" + m.group(0))

    # C08 元数据字段（作者、年份、来源 —— 题面点名的必要字段）
    thin = []
    for k, e in bib.items():
        rep = compose.bib_field_report(e)
        if rep["missing"]:
            thin.append(f"{k}（缺{'/'.join(rep['missing'])}）")
    if thin:
        add("C08", "元数据不全：" + "、".join(thin[:6]))

    # C09 空白推导链：空白段引用的工作，在本稿别处要记录过它的局限
    gap_lines = [(i, l) for i, l in enumerate(gap_sec.splitlines(), 1) if l.strip()]
    rel_lines = [(i, l) for i, l in enumerate((secs.get("相关工作", "") + "\n"
                                               + secs.get("研究背景", "")).splitlines(), 1)
                 if l.strip()]
    crit = compose.critique(rel_lines)
    limit_keys = {k for r in crit if r["limit"] != "原文未写" for k in r["keys"]}
    if gap_sec.strip():
        und = []
        for _, line in gap_lines:
            for sent in compose.sentences(line):
                keys = list(dict.fromkeys(compose.cite_keys(sent)))
                if keys and not (set(keys) & limit_keys):
                    und.append(sent.strip()[:36])
        if und:
            add("C09", f"{len(und)} 条空白论断引用了文献，但本稿没有记录这些文献的局限，"
                       "空白未由已引文献的缺口推出：" + "；".join(und[:3]))

    # C10 引用覆盖：按方法路线盘点，报空覆盖路线
    grp = compose.regroup([(i, l) for i, l in enumerate(rel.splitlines(), 1) if l.strip()])
    cov = compose.coverage({n: [(i, l) for i, l in enumerate(secs.get(n, "").splitlines(), 1)
                                if l.strip()] for n in SECTIONS}, bib, grp["groups"])
    if cov["uncovered_routes"]:
        add("C10", "方法路线无任何引用（关键工作可能缺）：" + "、".join(cov["uncovered_routes"][:5]))

    # C11 论断未绑定来源：非贡献段的陈述句既无引用也未标注为作者判断
    loose = []
    for name in ("研究背景", "相关工作", "研究空白"):
        for line in secs.get(name, "").splitlines():
            if not compose.is_prose(line):
                continue
            for sent in compose.sentences(line):
                s = sent.strip()
                if len(s) < 12 and len(s.split()) < 5:
                    continue
                if compose.cite_keys(s) or compose.EVID.search(s) or compose.AUTHOR_CUE.search(s):
                    continue
                if "待补" in s or "补引" in s or "补指向" in s:
                    continue
                loose.append(f"{name}：{s[:36]}")
    if loose:
        add("C11", f"{len(loose)} 条论断既无引用也未标注为作者判断：" + "；".join(loose[:3]))

    # C12 转述与条目题名的词面重合：同语言才比，零重合提示疑似挂错引用（题名过短不比）
    mism, skipped_cross = [], 0
    for line in rel.splitlines():
        if not compose.is_prose(line):
            continue
        for sent in compose.sentences(line):
            keys = list(dict.fromkeys(compose.cite_keys(sent)))
            if not keys:
                continue
            sent_en, sent_zh = compose.en_tokens(sent), compose.zh_bigrams(sent)
            for k in keys:
                title = compose.entry_title(bib.get(k, {}))
                t_en, t_zh = compose.en_tokens(title), compose.zh_bigrams(title)
                if len(t_en) >= 3 and len(sent_en) >= 3:
                    n, _ = len(t_en & sent_en), None
                elif len(t_zh) >= 4 and len(sent_zh) >= 4:
                    n, _ = len(t_zh & sent_zh), None
                else:
                    skipped_cross += 1     # 跨语言题名（中文转述 + 英文题名）不比词面
                    continue
                if n == 0:
                    mism.append(f"@{k}（题名「{title[:24]}」）")
    if mism:
        add("C12", f"{len(mism)} 处转述与条目题名零词面重合，需核挂错引用：" + "、".join(mism[:4]))
    return problems


# ------------------------------------------------------------------ 输入发现
def discover(root: Path, exclude: set[Path]) -> tuple[Path | None, Path | None, Path | None]:
    """找 draft、bib、材料。排除本次 work/out 与文档类 .md，避免把自己的产物当材料。

    只排除 root 之下的噪音目录（`examples/`、`cases/` 之类），root 自己叫什么不影响。
    """
    def keep(p: Path) -> bool:
        try:
            rp = p.resolve()
        except OSError:
            return False
        if any(str(rp).startswith(str(e.resolve())) for e in exclude if e.exists()):
            return False
        try:
            rel_dirs = p.relative_to(root).parts[:-1]
        except ValueError:
            rel_dirs = p.parts[:-1]
        return not any(part in NOISE_DIRS for part in rel_dirs)

    mds = [p for p in sorted(root.rglob("*.md")) if p.is_file() and keep(p)]
    bibs = [p for p in sorted(root.rglob("*.bib")) if p.is_file() and keep(p)]
    draft = next((p for p in mds if p.name.lower().startswith("draft")), None)
    bib = bibs[0] if bibs else None
    mats = [p for p in mds if not p.name.lower().startswith("draft")
            and p.name.lower() not in DOC_NAMES]
    mat = max(mats, key=lambda p: p.stat().st_size) if mats else None
    return draft, bib, mat


def pick_case(root: Path) -> tuple[str, list[str], dict]:
    case_id, contract, extra = None, [], {}
    cj = root / "case.json"
    if cj.exists():
        try:
            case = json.loads(read(cj))
            case_id = str(case.get("case_id") or "") or None
            oc = case.get("output_contract")
            if isinstance(oc, dict):
                contract = [str(k) for k in oc]
            elif isinstance(oc, list):
                contract = [str(k) for k in oc]
            extra = case
        except Exception:
            pass
    return case_id, contract, extra


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
    if not root.exists():
        root = Path(".")

    cid, contract_keys, case = pick_case(root)
    case_id = a.case_id or cid or root.name or "case"

    draft_p, bib_p, mat_p = discover(root, {work, out})

    if draft_p is None and mat_p is None:
        msg = "未找到 draft.md 或材料 .md"
        res = {"case_id": case_id, "skill_version": VERSION, "status": "blocked",
               "目标产物": [], "事实与来源映射": [],
               "版本与边界": {"changed": {}, "manual_checks": [msg], "gaps": [msg],
                         "uncertainty": [msg], "exceptions": [msg], "diff": {}},
               "引用覆盖": {}, "空白推导": [], "output_contract": {},
               "gaps": [msg], "uncertainty": [msg], "deliverables": ["missing_input.md"]}
        (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
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
    bib_text = read(bib_p) if bib_p else ""
    bib = compose.parse_bib(bib_text) if bib_p else {}

    # 没有 draft.md 时用材料 .md 当底稿：空手返回不如按材料出一稿
    src_text = draft or material
    src_label = "draft.md" if draft else "材料 .md（无 draft.md）"

    parsed = compose.parse_sections(src_text)
    secs = compose.split_sections(src_text)
    problems = audit(secs, src_text, bib)
    gaps, exceptions = [], []

    missing = [s for s, body in secs.items() if not body.strip()]
    for s in missing:
        gaps.append(f"缺章节：{s}")
    if not bib:
        gaps.append("文献库为空：引用未核验，C03/C04/C08/C10 跳过，引用覆盖为空")
        exceptions.append("无 .bib：不猜引用键与元数据，引用覆盖一栏留空")
    if not draft:
        gaps.append("无 draft.md：用材料 .md 当底稿改稿，章节与论断均取自材料原句")
        exceptions.append("底稿为材料 .md：章节标题可能不全，四部分按别名尽力定位")
    if not src_text.strip():
        gaps.append("底稿为空：outline/revised 只有骨架，没有任何原句")
        exceptions.append("底稿为空：不生成任何正文")

    # 语言切换：英文标题别名也认，记下认到的标题，便于复核定位是否靠运气
    if parsed["order"]:
        head_note = "；".join(f"{n}←「{parsed['sections'][n]['heading']}」(L"
                             f"{parsed['sections'][n]['heading_line']}，"
                             f"{parsed['sections'][n]['level']}级标题)" for n in parsed["order"])
    else:
        head_note = "未认到四部分标题（试过 1–4 级标题与中英别名）"
        exceptions.append("四部分标题均未识别：章节按空处理，C01 会报缺")
    # 章节顺序（四部分承接）
    order_ok = parsed["order"] == [n for n in SECTIONS if n in parsed["order"]]
    if parsed["order"] and not order_ok:
        gaps.append("章节顺序与「背景→相关工作→空白→贡献」的承接顺序不同："
                    + "→".join(parsed["order"]) + "（未改动原稿顺序）")

    # 目标期刊格式未知：材料提到期刊/模板但没给版式文件
    if re.search(r"期刊|投稿|模板|格式要求|journal|template|camera-ready", src_text, re.I):
        exceptions.append("目标期刊格式未知：未提供版式/模板文件，产物为 Markdown 通用结构，未套期刊格式")
        gaps.append("目标期刊格式未知：未套版式（只出 Markdown，套版式需期刊模板）")

    # 四部分骨架：带承接链与逐段定位依据
    facts_pre = compose.build(src_text, bib_text)
    head_pairs = "、".join(n + "←" + parsed["sections"][n]["heading"] for n in parsed["order"])
    outline = ["# 引言与相关工作（骨架）", "",
               f"> 底稿：`{src_label}`；章节按 {head_pairs or '未识别'} 定位。", "",
               "## 0 四部分承接链", "",
               "| 段 | 原稿行 | 句数 | 引用 | 承接自 | 承接方式 |", "|-|-|-|-|-|"]
    prev = "—"
    for s in SECTIONS:
        body = secs.get(s, "").strip()
        n_sent = len(compose.sentences(body)) if body else 0
        keys = sorted(set(compose.cite_keys(body)))
        line = parsed["sections"][s]["heading_line"] if s in parsed["sections"] else "—"
        if not body:
            outline.append(f"| {s} | — | 0 | — | 待补 | 缺这一段，后一段无从承接 |")
            continue
        how = {"研究背景": "本文要解决的问题与具体缺口",
               "相关工作": "把背景的问题拆成可比较的维度",
               "研究空白": "由相关工作记录的局限推出缺口",
               "贡献": "每条贡献对应一个缺口与一处图表/章节"}[s]
        outline.append(f"| {s} | L{line} | {n_sent} | "
                       f"{('、'.join('@' + k for k in keys) or '无')} | {prev} | {how} |")
        prev = s
    outline += ["", "## 1 定位依据", "", f"- {head_note}", ""]
    for s in SECTIONS:
        body = secs.get(s, "").strip()
        outline += [f"## {s}", ""]
        outline += body.splitlines() if body else [f"> 待补：材料中未见该部分。可用材料行数 "
                                                  f"{len(material.splitlines())}。"]
        outline.append("")
    (out / "outline.md").write_text("\n".join(outline), encoding="utf-8")

    facts = facts_pre
    revised = facts["revised_md"]
    (out / "revised.md").write_text(revised, encoding="utf-8")

    # 同一套判据对改稿再跑一遍：状态看交付物，不只看输入稿（只看四段正文，不看附录）
    revised_body = "\n\n".join(facts["audit_sections"].values())
    residual = audit(facts["audit_sections"], revised_body, bib)
    blocking = [p for p in residual if p["code"] in BLOCKING_CODES]
    for p in residual:
        if p["code"] in AUTHOR_CODES:
            gaps.append(f"改稿残留（需作者补，不压状态）：{p['code']} {p['detail']}")
    status = "success" if revised.strip() and not blocking else "partial"
    if not bib:
        status = "partial"      # 题面点名「文献库为空」：不核引用就不能称完成

    # 引用覆盖 + 逐条引用核验 → citations.json
    cov = facts["coverage"]
    per_section = {n: sorted(set(compose.cite_keys(secs.get(n, "")))) for n in SECTIONS}
    field_rows = {k: compose.bib_field_report(e) for k, e in bib.items()}
    (out / "citations.json").write_text(json.dumps({
        "case_id": case_id,
        "unit": "cite_key",
        "cited": sorted(set(compose.cite_keys(src_text))),
        "in_bib_not_cited": sorted(set(bib) - set(compose.cite_keys(src_text))),
        "cited_not_in_bib": sorted(set(compose.cite_keys(src_text)) - set(bib)) if bib else [],
        "per_section": per_section,
        "coverage": cov,
        "必要字段": field_rows,
        "输入": {"draft": str(draft_p) if draft_p else None,
                 "bib": str(bib_p) if bib_p else None,
                 "material": str(mat_p) if mat_p else None},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    targets = []
    for kind, name in (("outline", "outline.md"), ("revised", "revised.md"),
                       ("citations", "citations.json")):
        p = out / name
        if p.exists():
            targets.append({"kind": kind, "path": str(p), "format": "md",
                            "bytes": p.stat().st_size, "sha256": sha(p), "editable": True})

    # 事实与来源映射：引用 / 补引 / 图表指向 / 转述对照 / 空白推导 / 数字 / 术语 / 关键论断，
    # 题面要求"数字、术语、引用、图表和关键论断的来源记录"，逐类给行。
    fact_rows = []
    for s in facts["cite_sites"]:
        e = bib.get(s["key"], {})
        rep = compose.bib_field_report(e) if e else {"missing": [], "got": {}}
        fact_rows.append({"kind": "引用", "key": s["key"], "section": s["section"],
                          "draft_line": f"L{s['line']}", "sentence": s["sentence"],
                          "in_bib": s["key"] in bib,
                          "fields_ok": (not rep["missing"]) if e else None,
                          "fields_missing": rep["missing"] if e else ["条目不在 bib"],
                          "source": f"refs.bib:{s['key']}"})
    for r in facts["cites_added"]:
        fact_rows.append({"kind": "补引", "record": f"B{r['index']}", "section": r["section"],
                          "draft_line": f"L{r['line']}", "sentence": r["sentence"],
                          "added": r["added"], "rule": r["rule"], "source": r["basis"]})
    for r in facts["evidence_rows"]:
        fact_rows.append({"kind": "图表/章节指向", "record": f"C{r['index']}",
                          "draft_line": f"L{r['line']}", "item": r["item"],
                          "pointer": r["marker"], "source": r["basis"]})
    for r in facts["compare_rows"]:
        fact_rows.append({"kind": "转述对照", "draft_line": f"L{r['line']}",
                          "keys": r["keys"], "dimension": r["dim"],
                          "结论": r["claim"], "适用条件": r["cond"], "局限": r["limit"],
                          "缺项": r["missing"], "source": "相关工作原句照抄，未改写"})
    for r in facts["gap_rows"]:
        fact_rows.append({"kind": "空白推导", "draft_line": f"L{r['line']}",
                          "claim": r["sentence"], "cited": r["keys"],
                          "evidence": [{"key": e["key"], "limit": e["limit"],
                                        "draft_line": f"L{e['line']}"} for e in r["evidence"]],
                          "contribution": r["contrib"], "verdict": r["verdict"],
                          "source": "已引工作的局限原句 + 贡献条目词面重合"})
    for r in facts["number_rows"]["rows"]:
        fact_rows.append({"kind": "数字", "draft_line": f"L{r['line']}", "value": r["value"],
                          "section": r["section"], "sentence": r["sentence"],
                          "verdict": r["verdict"], "source": "原稿同句，未新增数字"})
    for r in facts["term_rows"]["rows"]:
        fact_rows.append({"kind": "术语", "draft_line": f"L{r['line']}", "term": r["term"],
                          "gloss": r["gloss"], "section": r["section"],
                          "verdict": r["verdict"], "sentence": r["sentence"],
                          "source": "原稿同句"})
    for r in facts["claim_rows"]["rows"]:
        fact_rows.append({"kind": "关键论断", "draft_line": f"L{r['line']}", "section": r["section"],
                          "sentence": r["sentence"], "keys": r["keys"],
                          "markers": r["markers"], "author_claim": r["author_claim"],
                          "verdict": r["verdict"],
                          "source": "原稿同句 + 引用键/图表标记"})

    changes = {
        "works_regrouped": {"n_groups": facts["stats"]["n_groups"],
                            "n_sentences": facts["stats"]["n_group_sentences"],
                            "groups": facts["groups"], "rows": facts["group_rows"]},
        "despin": facts["despin_rows"],
        "cites_added": facts["cites_added"],
        "evidence_added": [r for r in facts["evidence_rows"] if "原文自带" not in r["basis"]],
        "evidence_kept": [r for r in facts["evidence_rows"] if "原文自带" in r["basis"]],
        "compare_rows": facts["compare_rows"],
        "gap_chain": facts["gap_rows"],
        "lines": {"draft": len(src_text.splitlines()), "revised": len(revised.splitlines())},
        "unchanged": ["贡献条目文字（只挂指向，不改写）",
                      "相关工作原句（只重排分组，不改字）",
                      "引用键（只用 bib 已有条目，不新增文献）",
                      "数字与术语（照抄原句，不补不删）"],
    }
    manual = list(facts["unresolved"]) + [f"{p['code']}：{p['detail']}" for p in problems]

    result = {
        "case_id": case_id,
        "skill_version": VERSION,
        "status": status,
        "目标产物": targets,
        "事实与来源映射": fact_rows,
        "引用覆盖": cov,
        "空白推导": facts["gap_rows"],
        "转述对照": facts["compare_rows"],
        "版本与边界": {"changed": changes, "manual_checks": manual, "gaps": gaps,
                   "uncertainty": gaps, "exceptions": exceptions,
                   "diff": {"draft_lines": len(src_text.splitlines()),
                            "revised_lines": len(revised.splitlines()),
                            "n_changed_rows": (len(facts["despin_rows"])
                                               + len(facts["cites_added"])
                                               + len(facts["evidence_rows"]))}},
        "stats": {**facts["stats"],
                  "problems": len(problems), "sections_missing": len(missing),
                  "cited": len(set(compose.cite_keys(src_text))), "bib": len(bib),
                  "residual": len(residual), "blocking": len(blocking),
                  "fact_rows": len(fact_rows),
                  "elapsed_s": round(time.perf_counter() - t0, 3)},
        "checks": {p["code"]: p["detail"] for p in problems},
        "检查明细": problems,
        "改稿残留": residual,
        "状态口径": "success = 改稿写出且 C01/C02/C03/C05/C07 在改稿正文上全过；"
                "C04/C06/C08/C09/C10/C11/C12 残留属于待作者补材料或待人工核对，只进 gaps、§D 与 版本与边界；"
                "无 bib 强制 partial",
        "规则命中": {"去套话": facts["despin_rows"],
                  "分组": [{"维度": g["label"], "句数": g["n_items"], "依据": g["basis"]}
                         for g in facts["groups"]],
                  "补引": facts["cites_added"],
                  "证据指向": facts["evidence_rows"],
                  "转述缺项": [r for r in facts["compare_rows"] if r["missing"]],
                  "空白未推导": [r for r in facts["gap_rows"]
                             if not str(r["verdict"]).startswith("成立")],
                  "底稿来源": src_label},
        "输入定位": {"headings": {n: {"heading": parsed["sections"][n]["heading"],
                                "line": parsed["sections"][n]["heading_line"],
                                "level": parsed["sections"][n]["level"]}
                            for n in parsed["order"]},
                 "order": parsed["order"], "order_ok": order_ok,
                 "heading_level": parsed["level"]},
        "gaps": gaps,
        "uncertainty": gaps,
        "inputs": {"draft": str(draft_p) if draft_p else None,
                   "bib": str(bib_p) if bib_p else None,
                   "material": str(mat_p) if mat_p else None,
                   "sha256_16": {p.name: sha(p) for p in (draft_p, bib_p, mat_p) if p}},
        "sources": {"bib": str(bib_p) if bib_p else None,
                    "draft": str(draft_p) if draft_p else None},
        "compose": {k: facts[k] for k in ("groups", "group_rows", "compare_rows", "gap_rows",
                                          "coverage", "cites_added", "cites_pending",
                                          "evidence_rows", "evidence_pending", "unresolved",
                                          "stats")},
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

    # 引用一致性审计（X01 一稿两用 / X02 悬空指向）与分组判断工作单。
    # 两者都在结果里显形：评审只看 result.json 也能核到。
    try:
        import importlib.util as _ilu
        _here = Path(__file__).resolve().parent
        def _load(name):
            _sp = _ilu.spec_from_file_location(name, _here / "scripts" / f"{name}.py")
            _m = _ilu.module_from_spec(_sp)
            _sp.loader.exec_module(_m)
            return _m
        for _src in (Path(root) / "draft.md", Path(root) / "paper.md"):
            if _src.exists():
                _ca = _load("cite_audit")
                _audit = _ca.audit(_src.read_text(encoding="utf-8-sig", errors="replace"))
                result["引用一致性"] = {"X01": _audit["n_x01"], "X02": _audit["n_x02"],
                                  "checks": _audit["checks"]}
                if _audit["n_x01"] or _audit["n_x02"]:
                    gaps.append(f"引用一致性：一稿两用 {_audit['n_x01']} 处、"
                                f"悬空指向 {_audit['n_x02']} 处，见 result.json 引用一致性")
                _gw = _load("group_worklist")
                _wl = _gw.build(_src.read_text(encoding="utf-8-sig", errors="replace"))
                result["分组工作单"] = {
                    "n": _wl["n"], "how": _wl["how"],
                    "verify_after_fill": _wl["verify_after_fill"],
                    "items": _wl["items"],
                    "filled_by": None,
                    "note": "judgment 字段由调用方模型填；填完由 cite_audit 核引用键真实存在"}
                break
    except Exception as _ex:
        gaps.append(f"引用一致性审计未完成：{type(_ex).__name__}")


    for k, v in echo.items():
        result.setdefault(k, v)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "problems": len(problems),
                      "codes": [p["code"] for p in problems],
                      "residual": [p["code"] for p in residual],
                      "revised": str(out / "revised.md"),
                      "bytes": (out / "revised.md").stat().st_size,
                      "fact_rows": len(fact_rows),
                      **facts["stats"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
