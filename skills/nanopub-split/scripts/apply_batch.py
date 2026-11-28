#!/usr/bin/env python3
"""apply_batch.py —— 把模型对批次的判断接回结果文件。

输入：result.json（脚本原判）+ batch_filled.json（模型判定）
做的事：
    1. 断言项 ok    → 保留
    2. 断言项 fix   → 用 fix 里的主客关系替换
    3. 断言项 drop  → 删除
    4. 句子项含断言 → 追加为新断言（来自原句子串）
    5. 按 quote 去重
    6. 重算覆盖率与待复核数，写回 result.json 与 assertions.tsv

模型判断优先于脚本判断；脚本只做合并与计数。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--filled", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    res = load(Path(a.result))
    filled = load(Path(a.filled))
    # 批次里断言项 id 带 A- 前缀，结果文件里是 NP-xxxx，匹配时统一去前缀
    items = {}
    for x in filled.get("items") or []:
        k = str(x.get("id") or "")
        items[k] = x
        if k.startswith("A-"):
            items[k[2:]] = x

    parsed = res.get("阅读/解析结果") or {}
    old = parsed.get("items") or []
    by_id = {x.get("id"): x for x in old}

    kept, fixed, dropped, added = [], [], [], []
    for aid, asrt in by_id.items():
        j = (items.get(aid) or {}).get("judgment") or {}
        v = j.get("verdict")
        if v == "drop":
            dropped.append(aid)
            continue
        if v == "fix" and j.get("fix"):
            fix = j["fix"] if isinstance(j["fix"], dict) else {}
            new = dict(asrt)
            for k in ("subject", "relation", "object"):
                if fix.get(k):
                    new[k] = fix[k]
            new["review_codes"] = []
            new["review"] = ""
            new["fixed_by"] = j.get("filled_by") or "model"
            fixed.append(aid)
            kept.append(new)
            continue
        if v == "ok":
            asrt["review_codes"] = []
            asrt["review"] = ""
        kept.append(asrt)

    for item in filled.get("items") or []:
        if item.get("type") != "sentence":
            continue
        j = item.get("judgment") or {}
        for one in (j.get("assertions") or []):
            q = (one.get("quote") or "").strip()
            if not q:
                continue
            added.append({
                "id": None, "subject": one.get("subject"), "relation": one.get("relation"),
                "object": one.get("object"), "quote": q,
                "review_codes": [], "review": "", "confidence": "high",
                "source": "model", "filled_by": j.get("filled_by") or "model",
            })

    # 去重：同一 quote 只留一条，优先修好的
    def _q(x: dict) -> str:
        q = x.get("quote")
        if not q:
            ev = x.get("evidence") or {}
            if isinstance(ev, dict):
                q = ev.get("quote") or ev.get("span")
        if not q:
            q = f"{x.get('subject')}|{x.get('relation')}|{x.get('object')}"
        return str(q).strip()

    seen, merged = set(), []
    for x in kept + added:
        key = _q(x)[:120]
        if key in seen:
            continue
        seen.add(key)
        merged.append(x)

    for n, x in enumerate(merged, 1):
        x["id"] = f"NP-{n:04d}"

    sent_total = parsed.get("sentences") or parsed.get("sentence_count") or 0
    # 模型明确判为「无断言」的句子不该留在分母里，否则覆盖率永远偏低
    no_assert = 0
    for item in filled.get("items") or []:
        if item.get("type") != "sentence":
            continue
        j = item.get("judgment") or {}
        if j.get("assertions") == []:
            no_assert += 1
    denom = max(1, sent_total - no_assert)
    parsed["items"] = merged
    parsed["assertions"] = len(merged)
    parsed["coverage"] = round(min(1.0, len(merged) / sent_total), 3) if sent_total else None
    parsed["coverage_effective"] = round(min(1.0, len(merged) / denom), 3)
    parsed["coverage_basis"] = {
        "sentences": sent_total, "no_assertion_by_model": no_assert,
        "denominator": denom,
        "note": "coverage 用总句数；coverage_effective 扣掉模型确认无断言的句子"}
    res["阅读/解析结果"] = parsed
    res["判断层"] = {
        "item": "断言拆分与主客判定",
        "n": len(filled.get("items") or []),
        "filled": sum(1 for i in filled.get("items") or []
                      if (i.get("judgment") or {}).get("assertions") is not None
                      or (i.get("judgment") or {}).get("verdict")),
        "pending": 0,
        "applied": {"kept": len(kept) - len(fixed), "fixed": len(fixed),
                    "dropped": len(dropped), "added": len(added), "final": len(merged)},
        "filled_by": "ds-base",
        "note": "合并由脚本做，判断由模型给；两者分开记",
    }

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    lines = ["\t".join(["id", "subject", "relation", "object", "quote", "confidence"])]
    for x in merged:
        lines.append("\t".join(str(x.get(k) or "") for k in
                               ("id", "subject", "relation", "object", "quote", "confidence")))
    (out / "assertions.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "kept": len(kept) - len(fixed), "fixed": len(fixed),
                      "dropped": len(dropped), "added": len(added), "final": len(merged),
                      "coverage": parsed.get("coverage")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
