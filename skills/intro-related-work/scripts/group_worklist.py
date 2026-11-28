#!/usr/bin/env python3
"""group_worklist.py —— 给出「分组判断工作单」，判断本身交给调用方模型。

题面要「相关工作按方法路线分组」。路线不在字面里：同一路线可能只有一段写了关键词，
其余段落靠内容判断。所以脚本只做三件确定的事：

    1. 切出相关工作段落的每一句
    2. 给每句附上可核线索：引用键、实词、是否含方法动词
    3. 留出 judgment 字段，由调用方模型填「属于哪一组、依据是哪半句」

脚本不猜分组。填完之后由 cite_audit.py 核两件事：组内引用键是否真实存在、
每组是否至少两句。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

STOP = set("的了和与及或在是为对从把被这那其之以及并而同也都很更最可以需要进行通过".split())
METHOD_VERB = re.compile(r"(提出|采用|使用|基于|通过|引入|构建|设计|propose|adopt|use|based on|introduce)", re.I)
KEY = re.compile(r"@([A-Za-z][\w:\-]*)|\[@?([A-Za-z][\w:\-]+(?:;\s*@?[A-Za-z][\w:\-]+)*)\]")
REL_HEAD = re.compile(r"(相关工作|related work|研究现状|文献综述|背景)", re.I)


def cut_related(text: str) -> str:
    """取出相关工作那一节；找不到就整篇交回并标注。"""
    lines = text.splitlines()
    start = None
    for i, l in enumerate(lines):
        if REL_HEAD.search(l) and (l.strip().startswith("#") or len(l.strip()) < 40):
            start = i
            break
    if start is None:
        return text
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip().startswith("#") and REL_HEAD.search(lines[j]):
            end = j
            break
        if lines[j].strip().startswith("#") and j > start + 2:
            end = j
            break
    return "\n".join(lines[start:end])


def words(s: str) -> list[str]:
    out = []
    for t in re.findall(r"[A-Za-z][A-Za-z\-]{2,}|[\u4e00-\u9fa5]{2,4}", s):
        if t in STOP or len(t) < 2:
            continue
        out.append(t)
    seen, uniq = set(), []
    for w in out:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq[:8]


def keys(s: str) -> list[str]:
    out = []
    for a, b in KEY.findall(s):
        if a:
            out.append(a)
        elif b:
            out += [x.strip().lstrip("@") for x in b.split(";") if x.strip()]
    return out


def parse_bib(p: str | None) -> dict:
    """取 bib 里每个键的标题与作者，用来给句子算候选引用。"""
    if not p:
        return {}
    path = Path(p)
    if not path.exists():
        return {}
    t = path.read_text(encoding="utf-8-sig", errors="replace")
    out = {}
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", t, re.S):
        key, body = m.group(2), m.group(3)
        title = re.search(r"title\s*=\s*[{\"](.+?)[}\"]\s*,\s*\n", body, re.S)
        out[key] = {"title": (title.group(1) if title else ""), "raw": body[:200]}
    return out


def build(text: str, bib: str | None = None) -> dict:
    sec = cut_related(text)
    # 先去掉标题行，再把被换行截断的句子接回去：病稿的引用标记常跨行。
    raw = [l for l in sec.splitlines() if l.strip() and not l.strip().startswith("#")]
    merged, buf = [], ""
    for l in raw:
        buf = (buf + " " + l.strip()).strip() if buf else l.strip()
        if re.search(r"[。！？;.!?]\s*$", buf):
            merged.append(buf)
            buf = ""
    if buf:
        merged.append(buf)
    sents = [s for s in merged if len(s) > 6]
    bib_entries = parse_bib(bib) if bib else {}
    items = []
    for i, s in enumerate(sents, 1):
        cands = []
        if bib_entries:
            sw = set(words(s))
            for k, e in bib_entries.items():
                tw = set(re.findall(r"[A-Za-z][A-Za-z\-]{2,}", e["title"]))
                ov = len(sw & tw)
                if ov:
                    cands.append({"key": k, "overlap": ov})
            cands.sort(key=lambda x: -x["overlap"])
        items.append({
            "id": f"S{i:02d}",
            "sentence": s,
            "cite_keys": keys(s),
            "candidate_keys": cands[:3],
            "content_words": words(s),
            "has_method_verb": bool(METHOD_VERB.search(s)),
            "judgment": {"group": None, "rationale": None, "cite": None, "filled_by": None},
        })
    return {
        "work_order": "按方法路线给每句归类",
        "how": {
            "group": "组名自定，同组用同一字符串；无法归类写 onb（out of scope）",
            "rationale": "写依据，必须是原句里的半句，不许另造",
            "filled_by": "填模型名或 caller",
        },
        "verify_after_fill": [
            "每组的引用键必须在 refs.bib 里真实存在",
            "每组至少两句，单句的组要在 rationale 里说明原因",
            "同一引用键跨组时，要说明为何同样一篇文献归到两组",
        ],
        "items": items,
        "n": len(items),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--bib", default=None, help="文献库，用来给每句算候选引用键")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    p = Path(a.input)
    if not p.exists():
        print(json.dumps({"ok": False, "error": f"输入不存在：{p}"}, ensure_ascii=False))
        return 1
    w = build(p.read_text(encoding="utf-8-sig", errors="replace"), a.bib)
    Path(a.out).write_text(json.dumps(w, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "items": w["n"],
                      "with_keys": sum(1 for i in w["items"] if i["cite_keys"]),
                      "with_method_verb": sum(1 for i in w["items"] if i["has_method_verb"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
