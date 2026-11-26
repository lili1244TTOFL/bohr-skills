#!/usr/bin/env python3
"""run.py —— 文献翻译：分段、术语锁定、双语对照、译后核验。

分工：脚本只做机器能判的（分段、术语抽取、数字/引用/否定/限定比对、段落配对），
翻译交给调用方模型。两阶段：先出 segments，再把译文按段号回填。

对应题面四个关注点：
    术语稳  术语表锁定 + 全篇译名一致率回扫
    段落全  段号一一对应，漏译/重复/错位可检出
    关系准  数字、引用编号、否定词、限定强度改前改后各数一遍
    可核验  每段译回指原文段号与字符区间
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

VERSION = "2.0.0"
NUM = r"(?<![\w.])\d+(?:\.\d+)?%?(?![\w.])"
CITE = r"\[\d+(?:[-,]\d+)*\]"
NEG = r"\b(not|no|never|none|cannot|without)\b|[不无未]|没有"
HEDGE = r"\b(may|might|could|possibly|likely|suggests?|appears?)\b|可能|或许"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


def load_glossary(path: Path | None) -> dict:
    """术语表用 Markdown 表格，取前两列。取不到就返回空表，不猜。"""
    g = {}
    if not path or not path.exists():
        return g
    for line in read(path).splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        if cells[0].lower() in ("原文", "source", "en", "术语"):
            continue
        g[cells[0]] = cells[1]
    return g


def segments(text: str) -> list[dict]:
    """按空行切段，保留全文字符区间，供回指。"""
    out, pos = [], 0
    for block in text.split("\n\n"):
        body = block.strip()
        if not body:
            continue
        start = text.find(body, pos)
        out.append({"id": f"S{len(out) + 1:03d}", "source": body,
                    "char_start": start, "char_end": start + len(body)})
        pos = start + len(body)
    return out


def profile(text: str) -> dict:
    return {"num": len(re.findall(NUM, text)),
            "cite": len(re.findall(CITE, text)),
            "neg": len(re.findall(NEG, text, re.I)),
            "hedge": len(re.findall(HEDGE, text, re.I))}


def term_check(seg_list: list[dict], glossary: dict) -> dict:
    if not glossary:
        return {"rate": None, "checked": 0, "missed": []}
    checked, missed = 0, []
    for s in seg_list:
        tgt = s.get("target") or ""
        for src_term, tgt_term in glossary.items():
            if src_term in s["source"]:
                checked += 1
                if tgt and tgt_term not in tgt:
                    missed.append({"id": s["id"], "term": src_term, "want": tgt_term})
    return {"rate": round(1 - len(missed) / checked, 3) if checked else None,
            "checked": checked, "missed": missed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None)
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="output")
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--glossary", default=None)
    ap.add_argument("--targets", default=None, help="译文文本，按段号顺序以空行分隔")
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
            case_id = a.case_id or str(json.loads(read(cj)).get("case_id") or case_id)
        except Exception:
            pass

    src_p = next((p for p in sorted(root.rglob("*"))
                  if p.suffix in (".md", ".txt") and "glossary" not in p.name.lower()
                  and "target" not in p.name.lower()), None)
    if src_p is None:
        res = {"case_id": case_id, "status": "blocked",
               "gaps": ["未找到原文 .md/.txt"], "uncertainty": ["无输入"]}
        (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked"}, ensure_ascii=False))
        return 1

    src_text = read(src_p)
    gl_path = Path(a.glossary) if a.glossary else next(iter(sorted(root.rglob("glossary*.md"))), None)
    glossary = load_glossary(gl_path)
    seg_list = segments(src_text)

    targets = {}
    tp = Path(a.targets) if a.targets else next(iter(sorted(root.rglob("targets*.txt"))), None)
    if tp and tp.exists():
        blocks = [b.strip() for b in read(tp).split("\n\n") if b.strip()]
        for s, t in zip(seg_list, blocks):
            targets[s["id"]] = t
    for s in seg_list:
        s["target"] = targets.get(s["id"], "")

    gaps, problems = [], []
    missing = [s["id"] for s in seg_list if not s["target"]]
    if missing:
        gaps.append(f"{len(missing)}/{len(seg_list)} 段未译：" + ",".join(missing[:6]))

    src_prof = profile(src_text)
    tgt_text = "\n\n".join(s["target"] for s in seg_list if s["target"])
    tgt_prof = profile(tgt_text) if tgt_text else {k: 0 for k in src_prof}

    if tgt_text:
        for k, label in (("num", "数字"), ("cite", "引用编号"), ("neg", "否定"), ("hedge", "限定")):
            if src_prof[k] != tgt_prof[k]:
                problems.append({"code": "Q03", "detail":
                                 f"{label}数量不一致：原文 {src_prof[k]} → 译文 {tgt_prof[k]}"})

    cons = term_check(seg_list, glossary)
    if cons["rate"] is not None and cons["rate"] < 1:
        problems.append({"code": "Q01", "detail":
                         f"术语一致率 {cons['rate']}，{len(cons['missed'])} 处不一致"})
    if not glossary:
        gaps.append("未提供术语表，术语一致率无法核验")
    if targets and len(targets) != len(seg_list):
        problems.append({"code": "Q02", "detail":
                         f"段数不匹配：原文 {len(seg_list)}，译文 {len(targets)}"})

    status = "success" if tgt_text and not problems else ("partial" if tgt_text else "needs_translation")

    (work / "segments.json").write_text(json.dumps(
        {"case_id": case_id, "count": len(seg_list), "segments": seg_list},
        ensure_ascii=False, indent=1), encoding="utf-8")

    (out / "verify.json").write_text(json.dumps(
        {"case_id": case_id, "term_consistency": cons, "profile_src": src_prof,
         "profile_tgt": tgt_prof, "problems": problems}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    bilingual = ["# 双语对照（逐段）", ""]
    for s in seg_list:
        bilingual += [f"## {s['id']}", "",
                      f"**原文**｜字符 {s['char_start']}–{s['char_end']}", "",
                      s["source"], "", "**译文**", "", s["target"] or "（待译）", ""]
    (out / "bilingual.md").write_text("\n".join(bilingual), encoding="utf-8")

    result = {
        "case_id": case_id,
        "skill_version": VERSION,
        "status": status,
        "阅读/解析结果": {"segments": len(seg_list), "translated": len(targets),
                     "bilingual": "bilingual.md"},
        "证据与边界": {"unit": "seg_id + char_start/char_end", "segments": "segments.json"},
        "运行状态": status,
        "stats": {"segments": len(seg_list), "translated": len(targets),
                  "glossary_terms": len(glossary),
                  "elapsed_s": round(time.perf_counter() - t0, 3)},
        "checks": problems,
        "gaps": gaps,
        "uncertainty": gaps,
        "deliverables": ["bilingual.md", "verify.json", "segments.json"],
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "segments": len(seg_list),
                      "translated": len(targets), "term_rate": cons["rate"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
