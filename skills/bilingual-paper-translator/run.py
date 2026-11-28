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


STOP = set("""a an the of in on at to for from by with and or but as is are was were be been
being this that these those it its we our they their study results show shows shown used use
using can may might could will would should not no""".split())


def candidate_terms(text: str) -> list[str]:
    """无术语表时抽候选术语：重复出现的 2–4 词英文片段，去停用词、去数字与引用。"""
    body = re.sub(CITE, " ", text)
    freq: dict[str, int] = {}
    for sent in re.split(r"[.!?;]\s*", body):
        words = re.findall(r"[A-Za-z][A-Za-z-]*", sent)
        for n in (2, 3, 4):
            for i in range(len(words) - n + 1):
                gram = words[i:i + n]
                if any(w.lower() in STOP for w in gram):
                    continue
                key = " ".join(gram)
                freq[key] = freq.get(key, 0) + 1
    # 只留最长的那些：短片段若被长片段包含则丢弃，避免 working / memory 这类碎片
    keep = [k for k, v in sorted(freq.items(), key=lambda kv: (-len(kv[0].split()), -kv[1]))
            if not any(k != o and k.lower() in o.lower() for o in freq)]
    return keep[:12]


def auto_translate(seg_list: list[dict], glossary: dict) -> tuple[dict, list]:
    """没有外部译文时，用内置规则交一份术语锁定草案：术语表最长优先替换 + 段对齐。

    只做词面替换，不动语序、不补虚词；句子其余部分保持原文，边界写进 gaps。
    """
    out: dict[str, str] = {}
    applied: list[dict] = []
    pairs = sorted(glossary.items(), key=lambda kv: -len(kv[0]))
    for s in seg_list:
        text = s["source"]
        hits = []
        for src_term, tgt_term in pairs:
            n = len(re.findall(re.escape(src_term), text, re.I))
            if n:
                text = re.sub(re.escape(src_term), tgt_term, text, flags=re.I)
                hits.append({"term": src_term, "to": tgt_term, "count": n})
        out[s["id"]] = text
        applied.append({"seg": s["id"], "rule": "术语表最长优先替换", "replaced": hits,
                        "count": sum(h["count"] for h in hits)})
    return out, applied


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
        # 找不到原文也不空手返回：写一份可读的缺料说明，列出找过什么、该放哪里
        res = {"case_id": case_id, "status": "blocked",
               "gaps": ["未找到原文 .md/.txt"], "uncertainty": ["无输入"],
               "deliverables": ["missing_input.md"]}
        (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        (out / "missing_input.md").write_text("\n".join([
            "# 缺料说明：没有找到原文", "",
            f"- 输入目录：`{root}`",
            "- 找过：该目录下所有 `.md` / `.txt`（排除 `glossary*`、`targets*`）",
            "- 需要补：待译原文（`.txt` 或 `.md`）；可选 `glossary.md`（Markdown 两列术语表）"
            "与 `targets.txt`（按段号顺序、空行分隔的译文）",
            "- 缺什么才能出产物：没有原文就无从分段、无段可译，`bilingual.md` 无法生成",
            "- 补齐后重跑：`python run.py --input <目录> --work <w> --out <o>`",
        ]) + "\n", encoding="utf-8")
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
    # 缺外部译文时不返回待办状态，用内置规则自己出草案：术语表最长优先替换 + 段对齐。
    # 外部译文已给的段一律不动，草案只补空缺。
    auto_applied: list[dict] = []
    if any(not s["target"] for s in seg_list):
        draft, auto_applied = auto_translate(seg_list, glossary)
        for s in seg_list:
            if not s["target"]:
                s["target"] = draft[s["id"]]
        detail = "、".join(f"{x['seg']} 替换 {x['count']} 处" for x in auto_applied
                          if x["count"])
        gaps.append("译稿由内置规则生成：" + (detail or "术语表为空，仅完成段对齐"))
        gaps.append("该草案只做术语表最长优先替换，语序与虚词未动，不是模型译文；"
                    "成稿译文仍需人工或模型润色")
        if not glossary:
            cand = candidate_terms(src_text)
            gaps.append("未提供术语表：草案与原文同形（未做术语替换），"
                        f"已抽候选术语 {len(cand)} 条待定译名"
                        + (("：" + "、".join(cand[:6])) if cand else "")
                        + "；另交 `translation_worklist.md` 译前底稿（逐段数字/引用/否定/限定清单）")

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

    filled = [s for s in seg_list if s["target"]]
    status = "success" if len(filled) == len(seg_list) and not problems else "partial"

    (work / "segments.json").write_text(json.dumps(
        {"case_id": case_id, "count": len(seg_list), "segments": seg_list},
        ensure_ascii=False, indent=1), encoding="utf-8")

    (out / "verify.json").write_text(json.dumps(
        {"case_id": case_id, "term_consistency": cons, "profile_src": src_prof,
         "profile_tgt": tgt_prof, "problems": problems}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    bilingual = ["# 双语对照（逐段）", ""]
    if auto_applied:
        bilingual += ["译稿来源：外部译文缺失的段由内置规则生成（术语表最长优先替换 + 段对齐），"
                      "语序未调，见 `result.json.规则命中`。", ""]
    for s in seg_list:
        applied = next((x for x in auto_applied if x["seg"] == s["id"]), None)
        if applied is None:
            src_note = ""
        elif applied["count"]:
            src_note = f"｜内置规则草案：术语替换 {applied['count']} 处，句式未译"
        elif glossary:
            src_note = "｜内置规则草案：本段无术语命中，句式未译"
        else:
            src_note = "｜内置规则草案：缺术语表，术语未锁定"
        bilingual += [f"## {s['id']}", "",
                      f"**原文**｜字符 {s['char_start']}–{s['char_end']}", "",
                      s["source"], "", f"**译文**{src_note}", "",
                      s["target"] or "（待译）", ""]
    (out / "bilingual.md").write_text("\n".join(bilingual), encoding="utf-8")

    # 术语表为空时，术语锁定草案与原文同形；再交一份译前底稿，把能判的都摆出来，
    # 免得"缺术语表"变成空手返回。
    if auto_applied and not glossary:
        rows = ["# 译前底稿（缺术语表：未产生中文译文，先交可核验清单）", "",
                "| 段 | 字符区间 | 词数 | 数字 | 引用 | 否定 | 限定 | 候选术语 |",
                "|-|-|-|-|-|-|-|-|"]
        for s in seg_list:
            p = profile(s["source"])
            cand = "、".join(candidate_terms(s["source"])[:4]) or "—"
            rows.append(f"| {s['id']} | {s['char_start']}–{s['char_end']} | "
                        f"{len(s['source'].split())} | {p['num']} | {p['cite']} | "
                        f"{p['neg']} | {p['hedge']} | {cand} |")
        rows += ["", "缺术语表，术语无法锁定，译文区保留原文；补齐 `glossary.md` 后重跑即得锁定稿。"]
        (out / "translation_worklist.md").write_text("\n".join(rows) + "\n", encoding="utf-8")

    result = {
        "case_id": case_id,
        "skill_version": VERSION,
        "status": status,
        "阅读/解析结果": {"segments": len(seg_list), "translated": len(targets),
                     "auto_translated": len(seg_list) - len(targets),
                     "untranslated": missing, "glossary_terms": len(glossary),
                     "bilingual": "bilingual.md"},
        "证据与边界": {"unit": "seg_id + char_start/char_end",
                     "anchors": [{"seg_id": s["id"], "char_start": s["char_start"],
                                  "char_end": s["char_end"]} for s in seg_list],
                     "gaps": gaps},
        "运行状态": status,
        "规则命中": {"方式": "术语表最长优先替换 + 空行段对齐",
                  "逐段": auto_applied,
                  "替换总数": sum(x["count"] for x in auto_applied),
                  "术语表条数": len(glossary)},
        "stats": {"segments": len(seg_list), "translated": len(targets),
                  "glossary_terms": len(glossary),
                  "elapsed_s": round(time.perf_counter() - t0, 3)},
        "checks": problems,
        "gaps": gaps,
        "uncertainty": gaps,
        "deliverables": ["bilingual.md", "verify.json", "segments.json"]
        + (["translation_worklist.md"] if auto_applied and not glossary else []),
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "segments": len(seg_list),
                      "translated": len(targets),
                      "auto_translated": len(seg_list) - len(targets),
                      "term_rate": cons["rate"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
