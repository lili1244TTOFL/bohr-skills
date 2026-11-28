#!/usr/bin/env python3
"""check_style.py —— 参考文献样式合规检查（GB/T 7714 / APA 7 / IEEE）。

题面「格式转换与字段补齐」要求：在格式之间转换时，保留 DOI、卷期页、文献类型标识等关键字段。
原来只做渲染，不检查渲染结果是否合规、字段是否缺。本脚本补这一层：

    对每条文献按目标样式渲染一次，然后检查该样式的必备字段与格式特征：
        必备字段缺 → 记 gap，并按缺的字段降低合规分
        渲染结果与样式特征不符 → 记 style_mismatch

输出：每条文献一行结论 + 汇总，可直接并入 report。

用法：
    python check_style.py --bib examples/refs.bib --style apa7
    python check_style.py --bib examples/refs.bib --style gbt7714 --json out.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STYLES = ("gbt7714", "apa7", "ieee")

# 各样式的必备字段与格式特征
REQUIRED = {
    "gbt7714": ["authors", "title", "year"],          # 其余按类型补
    "apa7": ["authors", "year", "title"],
    "ieee": ["authors", "title", "year"],
}
# 格式特征：正则命中说明符合该样式
FEATURE = {
    "gbt7714": [(r"^\[\d+\]", "编号方括号起头"), (r"\[[A-Z](/[A-Z])?\]", "文献类型标识如 [J]/[M]")],
    "apa7": [(r"\(\d{4}\)", "年份入括号"), (r"^[A-Z][a-z]+,", "作者姓起头，逗号分隔")],
    "ieee": [(r'^\[\d+\]', "编号方括号起头"), (r'",\s*vol\.', "引号后接 vol.")],
}


def load_styles():
    import importlib.util
    spec = importlib.util.spec_from_file_location("styles", HERE / "styles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_bib(text: str) -> dict:
    out = {}
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", text, re.S):
        kind, key, body = m.group(1).lower(), m.group(2), m.group(3)
        fields = dict(re.findall(r"(\w+)\s*=\s*[{\"](.*?)[}\"]\s*,?\s*\n", body + "\n"))
        fields["_type"] = kind
        out[key] = fields
    return out


def norm_entry(f: dict) -> dict:
    """把 bib 字段转成 styles.render 要的形状。"""
    au = [a.strip() for a in re.split(r"\s+and\s+", f.get("author", "")) if a.strip()]
    return {"authors": au, "year": f.get("year", ""), "title": f.get("title", ""),
            "journal": f.get("journal", "") or f.get("booktitle", ""),
            "volume": f.get("volume", ""), "number": f.get("number", ""),
            "pages": f.get("pages", ""), "doi": f.get("doi", ""),
            "type": f.get("_type", "article")}


def check_one(entry: dict, style: str, rendered: str) -> dict:
    gaps = []
    for k in REQUIRED[style]:
        if not entry.get(k):
            gaps.append(f"缺 {k}")
    # 字段补齐检查：题面点名的 DOI / 卷期页 / 类型标识
    if not entry.get("doi"):
        gaps.append("缺 doi")
    if entry.get("type") == "article" and not (entry.get("volume") or entry.get("pages")):
        gaps.append("缺 卷期页")
    if style == "gbt7714" and not re.search(r"\[[A-Z](/[A-Z])?\]", rendered):
        gaps.append("缺 文献类型标识")
    feats = []
    for pat, label in FEATURE[style]:
        feats.append({"feature": label, "ok": bool(re.search(pat, rendered))})
    return {"rendered": rendered, "gaps": gaps, "features": feats,
            "ok": not gaps and all(f["ok"] for f in feats)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bib", required=True)
    ap.add_argument("--style", default="gbt7714", choices=STYLES)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    bib_p = Path(a.bib)
    if not bib_p.exists():
        print(json.dumps({"ok": False, "error": f"bib 不存在：{bib_p}"}, ensure_ascii=False))
        return 1

    styles = load_styles()
    entries = parse_bib(bib_p.read_text(encoding="utf-8-sig", errors="replace"))
    rows, ok_n = [], 0
    for key, f in entries.items():
        e = norm_entry(f)
        try:
            rendered, _errs = styles.render(e, a.style)
        except Exception as ex:
            rows.append({"key": key, "ok": False, "gaps": [f"渲染失败：{type(ex).__name__}"],
                         "rendered": None, "features": []})
            continue
        r = check_one(e, a.style, rendered)
        r["key"] = key
        ok_n += 1 if r["ok"] else 0
        rows.append(r)

    summary = {"style": a.style, "entries": len(rows), "ok": ok_n,
               "not_ok": len(rows) - ok_n,
               "rate": round(ok_n / len(rows), 3) if rows else None,
               "gap_kinds": sorted({g for r in rows for g in r["gaps"]})}
    out = {"ok": True, "summary": summary, "rows": rows}
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if a.json:
        Path(a.json).write_text(text, encoding="utf-8")
    print(json.dumps({"ok": True, **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
