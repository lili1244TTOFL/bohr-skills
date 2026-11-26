#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py — 文献翻译 Skill 的编排入口。

执行分两段，模型只做"逐段翻译"这一件事：
  准备：读 /input，切段、抽术语、生成 glossary.md 与 segments.jsonl
  翻译：由调用方（宿主 Agent）按 segments.jsonl 逐段产出 targets.jsonl
  复核：校验数字/引用/公式/图表号/否定/术语/完整性，输出双语对照与核验报告

没有 targets.jsonl 时停在 needs_translation，并给出待译段数与文件路径，不伪造译文。

用法：
  python run.py --input /input --work /app/work --out /app/output
  python run.py --input /input --targets work/targets.jsonl --out /app/output
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
import time
from pathlib import Path

SRC_EXT = (".pdf", ".docx", ".txt", ".md", ".tex")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_source(d: Path) -> Path | None:
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.suffix.lower() in SRC_EXT:
            return p
    return None


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8-sig").splitlines() if x.strip()]


def bilingual(segs: list[dict], tg: dict[str, str]) -> str:
    lines = ["# 双语对照（段号与原文一致；未译段标 ⟦未译⟧）", ""]
    cur = None
    for s in segs:
        if s["pid"] != cur:
            cur = s["pid"]
            lines.append(f"\n## {cur}　第 {s['page']} 页\n")
        zh = tg.get(s["uid"]) or "⟦未译⟧"
        lines.append(f"**{s['uid']}**　{s['text']}")
        lines.append(f"> {zh}\n")
    return "\n".join(lines)


def write_glossary(work: Path, case_dir: Path) -> Path:
    provided = None
    for name in ("glossary.md", "terms.tsv", "glossary.txt"):
        p = case_dir / name
        if p.exists():
            provided = p
            break
    out = work / "glossary.md"
    if provided:
        shutil.copyfile(provided, out)
        return out
    cands = work / "term_candidates.tsv"
    lines = ["term\ttarget\tnote"]
    if cands.exists():
        for i, ln in enumerate(cands.read_text(encoding="utf-8").splitlines()[1:]):
            term = ln.split("\t")[0].strip()
            if term:
                lines.append(f"{term}\t\t待定")     # 译名留空，由调用方填写
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="文献翻译")
    ap.add_argument("--input", default=None)
    ap.add_argument("--src", default=None)
    ap.add_argument("--targets", default=None)
    ap.add_argument("--glossary", default=None)
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="output")
    args = ap.parse_args(argv)

    t0 = time.time()
    work, out = Path(args.work), Path(args.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case_id, contract, case_dir = args.case_id or "case", {}, Path(".")
    if args.input:
        case_dir = Path(args.input)
        cj = case_dir / "case.json"
        if cj.exists():
            try:
                obj = json.loads(cj.read_text(encoding="utf-8-sig"))
                case_id = str(obj.get("case_id") or obj.get("id") or case_id)
                oc = obj.get("output_contract")
                contract = oc if isinstance(oc, dict) else (
                    {str(k): "" for k in oc} if isinstance(oc, list) else {})
            except Exception:
                pass
    src = Path(args.src) if args.src else find_source(case_dir)
    if not src or not src.exists():
        print(json.dumps({"ok": False, "status": "blocked", "error": "找不到原文文件"},
                         ensure_ascii=False))
        return 1

    seg_mod = load(Path(__file__).parent / "scripts" / "segments.py")
    res = seg_mod.build(src)
    segs = res["segments"]
    (work / "segments.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in segs) + "\n", encoding="utf-8")
    terms = seg_mod.term_candidates(segs)
    (work / "term_candidates.tsv").write_text(
        "term\tcount\n" + "\n".join(f"{t['term']}\t{t['count']}" for t in terms) + "\n",
        encoding="utf-8")
    gl_path = Path(args.glossary) if args.glossary else write_glossary(work, case_dir)

    tgt_path = Path(args.targets) if args.targets else (work / "targets.jsonl")
    tg: dict[str, str] = {}
    if tgt_path.exists():
        tg = {r["uid"]: r.get("text", "") for r in load_jsonl(tgt_path)}

    gaps: list[str] = []
    checks: dict[str, dict] = {}
    status = "partial"
    if not segs:
        gaps.append("原文为空或无法切段")
        status = "partial"
    ver = None
    if tg and segs:
        ver_mod = load(Path(__file__).parent / "scripts" / "verify.py")
        ver = ver_mod.verify(segs, tg, ver_mod.load_glossary(gl_path))
        (work / "verify.json").write_text(json.dumps(ver, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        checks = {
            "完整性": {"ok": not ver["missing"] and not ver["duplicates"],
                       "detail": f"缺译 {len(ver['missing'])}，重复 {len(ver['duplicates'])}"},
            "术语一致": {"ok": not ver["counts"].get("V06"),
                         "detail": f"术语表 {ver['glossary_terms']} 条，违规 {ver['counts'].get('V06', 0)}"},
            "数字引用公式": {"ok": not any(ver["counts"].get(c) for c in ("V01", "V02", "V03", "V04")),
                             "detail": ver["counts"]},
            "否定关系": {"ok": not ver["counts"].get("V05"),
                         "detail": f"不一致 {ver['counts'].get('V05', 0)}"},
        }
        if ver["missing"]:
            gaps.append(f"缺译 {len(ver['missing'])} 段")
        if ver["counts"].get("V07"):
            gaps.append(f"含未定标记 {ver['counts']['V07']} 段，需人工确认")
        status = "success" if ver["ok"] and not gaps else "partial"
    else:
        gaps.append(f"尚未提供译文：待译 {len(segs)} 段，翻译文件 {tgt_path}")
        checks = {"准备": {"ok": True, "detail": f"段 {len(segs)}，术语候选 {len(terms)}"}}
        if segs:
            status = "needs_translation"

    md = bilingual(segs, tg)
    (out / "bilingual.md").write_text(md, encoding="utf-8")
    shutil.copyfile(work / "segments.jsonl", out / "segments.jsonl")
    shutil.copyfile(gl_path, out / "glossary.md")

    deliverables = []
    for name in ("bilingual.md", "segments.jsonl", "glossary.md"):
        p = out / name
        deliverables.append({"kind": name.rsplit(".", 1)[0], "path": str(p),
                             "bytes": p.stat().st_size})
    if ver is not None:
        (out / "verify.json").write_text(json.dumps(ver, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        deliverables.append({"kind": "verify", "path": str(out / "verify.json"),
                             "bytes": (out / "verify.json").stat().st_size})

    rules = [
        (r"^运行状态", "verify.json"),
        # 平台字段名精确匹配（先于模糊规则；改动见 review/ISSUES.md X-01）
        (r"^阅读[/／]解析结果", "bilingual.md"),
        (r"^证据与边界", "verify.json"),
        (r"^目标产物", "bilingual.md"),
        (r"^事实与来源映射", "segments.jsonl"),
        (r"^版本与边界", "verify.json"),
        (r"bilingual|对照|译文|translation|target", "bilingual.md"),
        (r"glossary|术语", "glossary.md"),
        (r"segment|段落|对齐|align", "segments.jsonl"),
        (r"verif|核验|check|quality", "verify.json"),
    ]
    echo, echo_status = {}, {}
    for key in contract:
        hit = None
        for pat, f in rules:
            if re.search(pat, key, re.I):
                hit = f
                break
        if hit == "verify.json" and ver is None:
            echo[key] = {"note": "尚未翻译，无核验结果", "pointer": ["segments.jsonl"]}
            echo_status[key] = {"status": "partial", "source": "pointer"}
        elif hit:
            echo[key] = hit
            echo_status[key] = {"status": "full", "source": hit}
        else:
            echo[key] = {"note": "本字段无专门产出，见 bilingual.md / segments.jsonl",
                         "pointer": ["bilingual.md", "segments.jsonl"]}
            echo_status[key] = {"status": "partial", "source": "pointer"}

    result = {
        "output_contract_echo": echo_status,
        "case_id": case_id,
        "status": status,
        "source": {"path": str(src), "n_pages": res["n_pages"], "n_paras": res["n_paras"],
                   "n_segments": len(segs), "lang": "zh" if segs and segs[0]["cjk"] else "en"},
        "stats": {"segments": len(segs), "translated": len(tg),
                  "glossary_terms": len(load_glossary_safe(gl_path)),
                  "term_candidates": len(terms),
                  "elapsed_s": round(time.time() - t0, 2)},
        "deliverables": deliverables,
        "evidence": {"unit": "segment+page", "note": "每段带 uid、页码；译文按 uid 对齐"},
        "checks": checks,
        "gaps": gaps,
        "output_contract": echo,

        "next_step": (None if tg else
                      f"按 rules/translate_rules.md 逐段翻译 {work / 'segments.jsonl'}，"
                      f"产出 {tgt_path} 后重跑本脚本"),
    }
    result["阅读/解析结果"] = result.get("translation") or result.get("deliverables") or []
    result["证据与边界"] = result.get("evidence") or {}
    result["运行状态"] = result.get("status")
    result.setdefault("uncertainty", list(result.get("gaps") or []))
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "segments": len(segs),
                      "translated": len(tg), "result": str(out / "result.json")},
                     ensure_ascii=False))
    return 0


def load_glossary_safe(p: Path) -> dict:
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].strip() and not parts[0].lower().startswith("term"):
                out[parts[0].strip()] = parts[1].strip()
    return out


if __name__ == "__main__":
    raise SystemExit(main())
