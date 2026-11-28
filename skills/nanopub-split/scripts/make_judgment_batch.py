#!/usr/bin/env python3
"""make_judgment_batch.py —— 把所有待判断项整合成一份批次，一次交给模型。

整合两类：
    sentence  未产出断言的句子：模型判「有没有断言、拆几条、主客是什么」
    assertion 已产出的断言：模型判「这条复合不复合、过宽不过宽、主客体对不对」

输出一份 batch.json，模型一次性填完；再用 apply_batch.py 合并回结果。

用法：
    python make_judgment_batch.py --result out/result.json --out batch.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    d = json.loads(Path(a.result).read_text(encoding="utf-8-sig"))

    parsed = d.get("阅读/解析结果") or {}
    assertions = parsed.get("items")
    if not isinstance(assertions, list):
        assertions = []
        for k, v in d.items():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "subject" in v[0]:
                assertions = v
                break

    judge = d.get("判断层") or {}
    rows = judge.get("rows") or []

    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "type": "sentence",
            "text": r.get("sentence") or "",
            "ask": "这句含不含可判断真假的断言？含就拆成 assertions（subject/relation/object/quote），"
                   "不含就 assertions=[] 并在 basis 写为什么",
            "judgment": {"assertions": None, "basis": None, "filled_by": None},
        })
    for a_ in assertions:
        items.append({
            "id": f"A-{a_.get('id') or a_.get('assertion_id') or len(items)}",
            "type": "assertion",
            "text": a_.get("quote") or a_.get("object") or "",
            "meta": {"subject": a_.get("subject"), "relation": a_.get("relation"),
                     "object": a_.get("object")},
            "ask": "这条断言是否只承一个关系、是否过宽、主客是否切对；"
                   "有问题就在 fix 里写正确的主客与关系，没问题写 ok",
            "judgment": {"verdict": None, "fix": None, "basis": None, "filled_by": None},
        })

    batch = {
        "how": "一次填完所有 items；每条必须给 judgment；basis 必须能回指原文",
        "n_sentence": sum(1 for i in items if i["type"] == "sentence"),
        "n_assertion": sum(1 for i in items if i["type"] == "assertion"),
        "items": items,
    }
    Path(a.out).write_text(json.dumps(batch, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "sentences": batch["n_sentence"],
                      "assertions": batch["n_assertion"], "total": len(items)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
