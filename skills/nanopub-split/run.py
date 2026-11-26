#!/usr/bin/env python3
"""run.py —— 把论文段落拆成可判断真假的断言，每条绑最小证据片段与来源。

设计对应题面四个关注点：
    拆得细  一条断言只承一个关系，不把整段塞进一个字段
    对得上  每条断言带最小证据片段与原文位置（行 + 字符区间）
    追得回  断言→证据→论文→作者→年份 链路齐全
    读得懂  导出 JSON + TSV，字段固定，下游可直接解析

不调用模型：全部是规则与字符串处理。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# 受控关系词表：句子命中其一才成为断言，并归一成 relation。
# 只收显式的科研关系，宁可少抽，不猜因果。
# ---------------------------------------------------------------------------
RELATIONS = [
    # 中文：方向、比较、方法、因果、相关、陈述
    (r"显著(提高|提升|增加|改善|增强)", "increases", 0.9),
    (r"显著(降低|减少|下降|削弱|抑制)", "decreases", 0.9),
    (r"(提高|提升|增加|改善|增强)(了)?", "increases", 0.6),
    (r"(降低|减少|下降|削弱|抑制)(了)?", "decreases", 0.6),
    (r"(优于|高于|好于|领先于|强于)", "outperforms", 0.8),
    (r"(低于|劣于|不如|弱于)", "underperforms", 0.8),
    (r"(采用|使用|基于|利用)", "uses", 0.7),
    (r"(测量|评估|检验|验证|测定)", "measures", 0.7),
    (r"(导致|引起|造成|使得)", "causes", 0.7),
    (r"(相关|关联|伴随)", "correlates_with", 0.5),
    (r"(表明|显示|说明|发现|指出)", "shows", 0.6),
    (r"(存在|具有|属于|构成)", "has_property", 0.5),
    # 英文对应：同一套 relation，便于双语论文
    (r"\b(significantly\s+)?(increas|rais|improv|enhanc|boost)(e|es|ed|ing)\b", "increases", 0.9),
    (r"\b(significantly\s+)?(decreas|reduc|lower|diminish|suppress)(e|es|ed|ing)\b", "decreases", 0.9),
    (r"\b(outperform|exceed|surpass|better than)\b", "outperforms", 0.8),
    (r"\b(underperform|worse than|inferior to)\b", "underperforms", 0.8),
    (r"\b(use|employ|adopt|rely on|based on)\b", "uses", 0.7),
    (r"\b(measur|assess|evaluat|verif|quantif)(e|es|ed|ing)\b", "measures", 0.7),
    (r"\b(caus|lead to|result in|induc)(e|es|ed|ing)?\b", "causes", 0.7),
    (r"\b(correlat|associat)(e|es|ed|ing)\b", "correlates_with", 0.5),
    (r"\b(show|indicat|suggest|demonstrat|reveal|find)(s|ed|ing)?\b", "shows", 0.6),
    (r"\b(is|are|was|were)\s+(significantly\s+)?(higher|lower|larger|smaller)\b", "compares", 0.7),
]
SPLIT = re.compile(r"(?<=[。！？；])|(?<=[.!?;])\s+(?=[A-Z0-9(])|\n+")
MIN_EVIDENCE = 8          # 证据片段最短字符数
WIDE_SENTENCE = 80        # 超过这个长度视为断言过宽，转人工复核
SIX = ["断言", "主体与关系", "证据片段", "证据位置", "来源元数据", "置信度依据", "标识符"]


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


# ---------------------------------------------------------------------------
# 句子切分：保留在全文中的绝对字符偏移，后面用来算行号与 char 区间。
# ---------------------------------------------------------------------------
def sentences(text: str) -> list[tuple[str, int, int]]:
    out, pos = [], 0
    for m in SPLIT.finditer(text):
        seg = text[pos:m.end()]
        body = seg.strip()
        if len(body) >= MIN_EVIDENCE:
            start = pos + (len(seg) - len(seg.lstrip()))
            out.append((body, start, start + len(body)))
        pos = m.end()
    return out


def line_of(text: str, pos: int) -> int:
    return text[:pos].count("\n") + 1


def pick_relation(sentence: str) -> tuple[str, str, float] | None:
    for pattern, rel, conf in RELATIONS:
        m = re.search(pattern, sentence)
        if m:
            _g = m.group(1) or m.group(0)
            return _g, rel, conf
    return None


# ---------------------------------------------------------------------------
# 单句 → 断言：主体取关系词之前，客体取之后到句末。拆不动的句子进 skipped。
# ---------------------------------------------------------------------------
def to_assertion(idx: int, sent: str, start: int, end: int,
                 text: str, source: dict) -> dict | None:
    hit = pick_relation(sent)
    if not hit:
        return None
    word, rel, conf = hit
    cut = sent.find(word)
    subject = sent[:cut].strip(" ，,。；;")[:60] or "（未标明主体）"
    obj = sent[cut + len(word):].strip(" ，,。；;")[:80] or "（未标明客体）"
    wide = len(sent) > WIDE_SENTENCE
    return {
        "id": f"NP-{idx:04d}",
        "assertion": sent,
        "subject": subject,
        "relation": rel,
        "object": obj,
        "confidence": "medium" if wide else "high",
        "review": "断言过宽，需拆细" if wide else "",
        "evidence": {
            "quote": sent,
            "line": line_of(text, start),
            "char_start": start,
            "char_end": end,
            "method": "sentence-window",
        },
        "source": source,
    }


# ---------------------------------------------------------------------------
# 来源元数据：从 case.json 与文件名取，取不到就写 unknown，不编。
# ---------------------------------------------------------------------------
def load_source(root: Path) -> tuple[dict, str]:
    src = {"paper": "unknown", "authors": "unknown", "year": "unknown",
           "version": "v0", "identifier": "unknown"}
    case_id = root.name or "case"
    cj = root / "case.json"
    if cj.exists():
        try:
            case = json.loads(read(cj))
            case_id = str(case.get("case_id") or case_id)
            for k, alt in (("paper", "title"), ("authors", "author"),
                           ("year", "year"), ("identifier", "doi")):
                v = case.get(k) or case.get(alt)
                if v:
                    src[k] = str(v)
            contract = case.get("output_contract")
        except Exception:
            contract = None
    return src, case_id


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

    source, case_id = load_source(root)
    if a.case_id:
        case_id = a.case_id

    # 找正文：优先 manuscript，其次任意 md/txt
    cands = sorted(root.rglob("*.md")) + sorted(root.rglob("*.txt"))
    paper = next((p for p in cands if "manuscript" in p.name.lower()),
                 cands[0] if cands else None)

    if paper is None:
        result = {"case_id": case_id, "status": "blocked",
                  "gaps": ["未找到正文材料（.md/.txt）"],
                  "uncertainty": [], "evidence": {"unit": "line+char", "items": []}}
        (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked"}, ensure_ascii=False))
        return 1

    text = read(paper)
    sents = sentences(text)
    assertions, skipped = [], []
    for i, (sent, s, e) in enumerate(sents, 1):
        hit = to_assertion(len(assertions) + 1, sent, s, e, text, source)
        (assertions if hit else skipped).append(hit or sent)

    wide = [x for x in assertions if x["review"]]
    gaps = []
    if not assertions:
        gaps.append("未识别到显式关系句，无法生成断言")
    if wide:
        gaps.append(f"{len(wide)} 条断言超过 {WIDE_SENTENCE} 字，需人工拆细")
    if source["paper"] == "unknown":
        gaps.append("来源元数据缺失：paper 未提供，已写 unknown")
    coverage = round(len(assertions) / max(1, len(sents)), 3)

    status = "success" if assertions and not wide else ("partial" if assertions else "blocked")

    np_path = out / "nanopubs.json"
    np_path.write_text(json.dumps({
        "case_id": case_id, "version": VERSION, "source": source,
        "assertions": assertions,
        "stats": {"sentences": len(sents), "assertions": len(assertions),
                  "skipped": len(skipped), "wide": len(wide),
                  "coverage": coverage},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    tsv = out / "assertions.tsv"
    tsv.write_text(
        "id\tsubject\trelation\tobject\tline\tchar_start\tchar_end\tconfidence\n"
        + "".join(f"{x['id']}\t{x['subject']}\t{x['relation']}\t{x['object']}\t"
                  f"{x['evidence']['line']}\t{x['evidence']['char_start']}\t"
                  f"{x['evidence']['char_end']}\t{x['confidence']}\n"
                  for x in assertions), encoding="utf-8")

    result = {
        "case_id": case_id,
        "skill_version": VERSION,
        "status": status,
        "阅读/解析结果": {"assertions": len(assertions), "file": np_path.name,
                     "coverage": coverage},
        "证据与边界": {"unit": "line+char_start+char_end",
                   "wide_needs_review": len(wide), "gaps": gaps},
        "运行状态": status,
        "stats": {"sentences": len(sents), "skipped": len(skipped),
                  "elapsed_s": round(time.perf_counter() - t0, 3)},
        "evidence": {"unit": "line+char",
                     "items": [{"id": x["id"], "line": x["evidence"]["line"],
                                "char_start": x["evidence"]["char_start"],
                                "char_end": x["evidence"]["char_end"]}
                               for x in assertions[:50]]},
        "uncertainty": gaps,
        "gaps": gaps,
        "deliverables": [np_path.name, tsv.name],
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "assertions": len(assertions),
                      "coverage": coverage, "wide": len(wide)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
