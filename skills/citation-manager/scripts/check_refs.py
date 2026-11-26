#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_refs.py — 成品稿的引用一致性检查（正文 ↔ 文末列表 ↔ 编号表）。

C1 编号连续 1..N
C2 每个编号都被正文引用
C3 正文无列表外编号
C4 文末列表条数 = 编号数，且按编号升序
C5 所有正文标记为上标，且无遗留的普通文字标记
C6 标记格式：仅 [n] / [n,m] / [n-m]，无空格、无全角括号
C7 无重复编号，同一编号不重复占位
C8 文末条目符合 GB/T 7714—2015
C9 首次出现顺序与编号递增一致
C10 标记不落在句末标点之后（info）

用法：
  python check_refs.py --docx work/zh/manuscript_cited.docx --numbering work/zh/numbering.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

MARK = re.compile(r"\[([0-9,\-–\s]+)\]")


CAP_RE = re.compile(r"^\s*(图|表|Figure|Fig\.?|Table)\s*([0-9]+(?:[.\-][0-9]+)*)")
REF_RE = re.compile(r"(图|表|Figure|Fig\.?|Table|式)\s*[（(]?([0-9]+(?:[.\-][0-9]+)*)")
KIND = {"图": "figure", "fig": "figure", "figure": "figure",
        "表": "table", "table": "table", "式": "equation"}


def _kind(s: str) -> str:
    return KIND.get(s.lower(), KIND.get(s, "other"))


def check_captions(doc, listed: list[int], style: str) -> list[dict]:
    """C11 图/表/式编号与正文提及；C12 题注标记；C13 附录引用。"""
    probs: list[dict] = []
    caps: dict[str, set] = {}
    cap_para: dict[int, tuple[str, str]] = {}
    for i, p in enumerate(doc.paragraphs):
        text = (p.text or "").strip()
        m = CAP_RE.match(text)
        if not m:
            continue
        k, num = _kind(m.group(1)), m.group(2)
        caps.setdefault(k, set()).add(num)
        cap_para[i] = (k, num)
    if not caps:
        return probs

    mentioned: dict[str, set] = {}
    for i, p in enumerate(doc.paragraphs):
        if i in cap_para:
            continue
        for m in REF_RE.finditer(p.text or ""):
            mentioned.setdefault(_kind(m.group(1)), set()).add(m.group(2))

    for k, nums in caps.items():
        miss = sorted(n for n in nums if n not in mentioned.get(k, set()))
        if miss:
            probs.append({"code": "C11", "severity": "info",
                          "note": f"{k} 题注存在但正文未提及：{miss}"})
    for k, nums in mentioned.items():
        miss = sorted(n for n in nums if n not in caps.get(k, set()))
        if miss:
            probs.append({"code": "C11", "severity": "warn",
                          "note": f"正文提到的 {k} 编号无对应题注：{miss}"})

    if style == "gbt7714" and listed:
        for i, (k, num) in cap_para.items():
            for m in MARK.finditer(doc.paragraphs[i].text or ""):
                bad = [n for n in parse_nums(m.group(1)) if n not in listed]
                if bad:
                    probs.append({"code": "C12", "severity": "error",
                                  "note": f"{k} {num} 题注标记超出文献表：{bad}"})

    app_start = None
    for i, p in enumerate(doc.paragraphs):
        if re.match(r"^\s*(附录|Appendix)", p.text or "", re.I):
            app_start = i if app_start is None else app_start
    if app_start is not None:
        app_end = len(doc.paragraphs)
        for i in range(app_start + 1, len(doc.paragraphs)):
            if re.match(r"^\s*(参考文献|References|Bibliography)",
                        doc.paragraphs[i].text or "", re.I):
                app_end = i
                break
        body_nums, app_nums = set(), set()
        for i, p in enumerate(doc.paragraphs):
            nums = set()
            for m in MARK.finditer(p.text or ""):
                nums.update(parse_nums(m.group(1)))
            if i < app_start:
                body_nums |= nums
            elif i < app_end:
                app_nums |= nums
        only_app = sorted(app_nums - body_nums)
        if only_app:
            probs.append({"code": "C13", "severity": "info",
                          "note": f"仅在附录出现的引用：{only_app}"})
    return probs


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def load_mod(name: str):
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(name, here / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_nums(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        part = part.strip()
        rng = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if rng:
            out.extend(range(int(rng.group(1)), int(rng.group(2)) + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="引用一致性检查")
    ap.add_argument("--docx", required=True)
    ap.add_argument("--numbering", default=None)
    args = ap.parse_args(argv)

    from docx import Document

    gbt = load_mod("check_gbt")
    doc = Document(args.docx)
    num = json.loads(Path(args.numbering).read_text(encoding="utf-8-sig")) if args.numbering else None

    problems: list[dict] = []
    sup_order: list[int] = []
    sup_seen: list[dict] = []
    plain_marks: list[dict] = []
    refs_at = None
    refs: list[dict] = []

    for i, p in enumerate(doc.paragraphs):
        text = p.text or ""
        if text.strip().startswith("参考文献"):
            refs_at = i
            continue
        if refs_at is not None:
            if (num or {}).get("style", "gbt7714") != "gbt7714":
                if text.strip():
                    refs.append({"n": len(refs) + 1, "text": text.strip(), "para": i})
                continue
            m = re.match(r"^\s*\[(\d+)\]\s*(.+)$", text.strip())
            if m:
                refs.append({"n": int(m.group(1)), "text": m.group(2), "para": i})
            continue
        for r in p.runs:
            t = r.text or ""
            if not t:
                continue
            if MARK.search(t) and not r.font.superscript:
                plain_marks.append({"para": i, "text": t[:40]})
            if r.font.superscript:
                for m in MARK.finditer(t):
                    nums = parse_nums(m.group(1))
                    sup_order.extend(nums)
                    sup_seen.append({"para": i, "marker": m.group(0), "nums": nums})
                    if not re.fullmatch(r"\[\d+(?:[,-]\d+)*\]", m.group(0)):
                        problems.append({"code": "C6", "para": i, "marker": m.group(0)})

        # C10：上标标记紧跟句末标点
        idx = 0
        for r in p.runs:
            if r.font.superscript and MARK.search(r.text or ""):
                if idx > 0 and text[idx - 1] in "。！？":
                    problems.append({"code": "C10", "para": i, "marker": r.text})
            idx += len(r.text or "")

    if not refs:
        problems.append({"code": "C4", "note": "未找到参考文献表"})

    style = (num or {}).get("style", "gbt7714")
    if style != "gbt7714":
        # 非数字制：只查条目是否在正文出现，跳过编号连续/首现顺序
        body_text = "\n".join(p.text or "" for p in doc.paragraphs)
        missing = []
        for r in refs:
            head = r["text"][:24]
            if head and head not in body_text:
                missing.append(head)
        if missing:
            problems.append({"code": "C14", "note": f"文献表条目未在正文出现：{missing[:5]}"})
        problems.append({"code": "C9", "severity": "info",
                         "note": f"样式 {style}：不适用编号连续性检查"})
        counts = {}
        for x in problems:
            counts[x["code"]] = counts.get(x["code"], 0) + 1
        fatal = [x for x in problems if x.get("severity") != "info"]
        out = {"ok": not fatal, "style": style, "n_paras": len(doc.paragraphs),
               "n_refs": len(refs), "n_markers": len(sup_seen), "counts": counts,
               "problems": problems[:20], "n_problems": len(problems),
               "n_fatal": len(fatal)}
        print(json.dumps(out, ensure_ascii=False))
        return 0 if not fatal else 1

    listed = [r["n"] for r in refs]
    problems.extend(check_captions(doc, listed, (num or {}).get("style", "gbt7714")))
    if listed != sorted(listed):
        problems.append({"code": "C4", "note": f"列表未按编号升序：{listed}"})
    if len(set(listed)) != len(listed):
        problems.append({"code": "C7", "note": f"列表编号重复：{listed}"})

    if listed:
        n_max = max(listed)
        expect = list(range(1, n_max + 1))
        if listed != expect:
            problems.append({"code": "C1", "note": f"编号不连续：{listed} vs {expect}"})

    used = sorted(set(sup_order))
    if listed:
        missing = [n for n in listed if n not in used]
        if missing:
            problems.append({"code": "C2", "note": f"列表内未被引用：{missing}"})
        extra = [n for n in used if n not in listed]
        if extra:
            problems.append({"code": "C3", "note": f"正文编号超出列表：{extra}"})
        if len(refs) != n_max:
            problems.append({"code": "C4", "note": f"条目数 {len(refs)} ≠ 最大编号 {n_max}"})

    if plain_marks:
        problems.append({"code": "C5", "note": f"非上标标记 {len(plain_marks)} 处",
                         "sample": plain_marks[:3]})

    first_seq: list[int] = []
    for n in sup_order:
        if n not in first_seq:
            first_seq.append(n)
    mode = (num or {}).get("mode", "first-appearance")
    if mode == "keep-orig":
        problems.append({"code": "C9", "severity": "info",
                         "note": f"按原编号排序，首现序列不保证递增：{first_seq}"})
    else:
        if first_seq and first_seq != sorted(first_seq):
            problems.append({"code": "C9", "note": f"首现顺序非递增：{first_seq}"})
    if num and mode != "keep-orig":
        want = [x["n"] for x in num["order"]]
        if first_seq and first_seq != want:
            problems.append({"code": "C9", "note": f"正文首现序列与编号表不一致：{first_seq} vs {want}"})

    for r in refs:
        issues, _ = gbt.check_line(f"[{r['n']}] {r['text']}")
        for x in issues:
            if x["severity"] == "error":
                problems.append({"code": "C8", "n": r["n"], "note": x["note"]})

    counts: dict[str, int] = {}
    for p in problems:
        counts[p["code"]] = counts.get(p["code"], 0) + 1

    fatal = [p for p in problems if p.get("severity") != "info"]
    out = {"ok": not fatal, "n_paras": len(doc.paragraphs), "n_refs": len(refs),
           "n_markers": len(sup_seen), "first_seq": first_seq,
           "counts": counts, "problems": problems[:30], "n_problems": len(problems),
           "n_fatal": len(fatal)}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if not fatal else 1


if __name__ == "__main__":
    raise SystemExit(main())


