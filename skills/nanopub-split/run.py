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
import hashlib
import json
import re
import time
from pathlib import Path

VERSION = "1.1.0"

# ---------------------------------------------------------------------------
# 受控关系词表：句子命中其一才成为断言，并归一成 relation。
# 只收显式的科研关系，宁可少抽，不猜因果。
# 同一句命中多个关系词时，因果类优先（它通常是主断言），其余取句内最早命中。
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
    (r"\b(use|employ|adopt)(s|d|ed|ing)?\b", "uses", 0.7),
    (r"\b(measur|assess|evaluat|verif|quantif)(e|es|ed|ing)\b", "measures", 0.7),
    (r"\b(caus|lead to|result in|induc)(e|es|ed|ing)?\b", "causes", 0.7),
    (r"\b(correlat|associat)(e|es|ed|ing)\s+with\b", "correlates_with", 0.5),
    (r"\b(support)(s|ed|ing)?\b", "supports", 0.6),
    (r"\b(exhibit|display|show)(s|ed|ing)?\s+(significantly\s+)?(higher|lower|greater|smaller)\b",
     "compares", 0.7),
    (r"\b(show|indicat|suggest|demonstrat|reveal)(s|es|ed|ing)?\b", "shows", 0.6),
    (r"\bfinds?\b|\bfound\b", "shows", 0.6),
    (r"\b(is|are|was|were)\s+(significantly\s+)?(higher|lower|larger|smaller)\b", "compares", 0.7),
]
CAUSAL = {"causes"}

SPLIT = re.compile(r"(?<=[。！？；])|(?<=[.!?;])\s+(?=[A-Z0-9(])|\n+")
MIN_EVIDENCE = 8          # 证据片段最短字符数
WIDE_CJK = 80             # 中文句超过这个长度视为断言过宽
WIDE_LATIN = 160          # 英文句超过这个长度视为断言过宽
SIX = ["断言", "主体与关系", "证据片段", "证据位置", "来源元数据", "置信度依据", "标识符"]

CJK = re.compile(r"[\u4e00-\u9fff]")
# 转述前缀：说话人/来源 + 转述动词，单独记 attribution，不并进 subject
ATTRIBUTION = re.compile(
    r"^(?P<who>(?:(?:The|These|Our|This|Simple|Further|Overall|Previous|Prior|Earlier)\s+)?"
    r"(?:(?:experimental|behavioral|statistical|main|effect)\s+)?"
    r"(?:results?|findings?|data|analys[ie]s|authors?|study|research|work|investigators?"
    r"|研究|结果|作者|本文))"
    r"[^,.。；;]{0,50}?"
    r"(?P<verb>(?:(?:show|suggest|demonstrat|reveal|indicat)(?:s|ed|ing)?)|found|finds?"
    r"|表明|显示|说明|发现|指出|认为|主张)\s*(?:that\s+|：|:)?",
    re.I)
DISCOURSE = re.compile(
    r"^(?:In addition|However|Moreover|Furthermore|Specifically|Importantly|Thus|Therefore|"
    r"Overall|Finally|First|Second|Third|此外|然而|因此|总之|具体而言|同时)\s*[,，]\s*", re.I)
CONTEXT = re.compile(r"^(?:Among|For|In|Within|Across|Under)\s[^,，]{1,40}[,，]\s*", re.I)
SUBJ_TAIL = re.compile(
    r"\s+(?:was|were|is|are|has been|have been|can be|could be)\s+"
    r"(?:significantly\s+)?(?:negatively|positively)?\s*$", re.I)
SUBJ_EVID = re.compile(
    r"\s+(?:provides?|reports?|offers?)\s+(?:\w+\s+){0,2}evidence$", re.I)


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


# ---------------------------------------------------------------------------
# 句子切分：保留在全文中的绝对字符偏移，后面用来算行号与 char 区间。
# Markdown 标题不是句子，不进分母（否则覆盖率会被标题行稀释）。
# ---------------------------------------------------------------------------
def sentences(text: str) -> list[tuple[str, int, int]]:
    out, pos = [], 0
    for m in SPLIT.finditer(text):
        seg = text[pos:m.end()]
        body = seg.strip()
        if len(body) >= MIN_EVIDENCE and not body.startswith("#"):
            start = pos + (len(seg) - len(seg.lstrip()))
            out.append((body, start, start + len(body)))
        pos = m.end()
    return out


def line_of(text: str, pos: int) -> int:
    return text[:pos].count("\n") + 1


def sentence_wide(sent: str) -> bool:
    cjk = len(CJK.findall(sent))
    limit = WIDE_CJK if cjk >= max(4, len(sent) // 10) else WIDE_LATIN
    return len(sent) > limit


def wide_limit(sent: str) -> int:
    cjk = len(CJK.findall(sent))
    return WIDE_CJK if cjk >= max(4, len(sent) // 10) else WIDE_LATIN


# 长句切点：分号、逗号、英文 and/but 之后。只在"过宽"时才切，切完仍逐块判关系词。
CLAUSE_CUT = re.compile(r"(?<=[；;])|(?<=，)|(?<=[,;])\s+|(?<=\band\b)\s+|(?<=\bbut\b)\s+")


def clause_chunks(sent: str, limit: int) -> list[str]:
    """把过宽的长句在分句处贪心拼成 ≤limit 的块；块太短就并回前一块。"""
    parts = [p for p in CLAUSE_CUT.split(sent) if p]
    chunks: list[str] = []
    buf = ""
    for p in parts:
        if buf and len(buf) + len(p) > limit:
            chunks.append(buf)
            buf = p
        else:
            buf += p
    if buf:
        chunks.append(buf)
    out: list[str] = []
    for c in chunks:
        if out and len(c.strip()) < MIN_EVIDENCE:
            out[-1] += c
        else:
            out.append(c)
    return out


def pick_relation(sentence: str) -> tuple[str, float, int, int] | None:
    """返回 (relation, 置信度, 起, 止)。因果优先，其余取句内最早命中。"""
    hits = []
    for pattern, rel, conf in RELATIONS:
        for m in re.finditer(pattern, sentence, re.I):
            if m.end() > m.start():
                hits.append((m.start(), m.end(), rel, conf))
    if not hits:
        return None
    causal = [h for h in hits if h[2] in CAUSAL]
    pool = causal or hits
    start, end, rel, conf = min(pool, key=lambda h: (h[0], -(h[1] - h[0])))
    return rel, conf, start, end


def _strip_discourse(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = DISCOURSE.sub("", s).strip()
        s = CONTEXT.sub("", s).strip()
    return s


# ---------------------------------------------------------------------------
# 单句 → 断言：转述前缀进 attribution；主体取关系词之前，客体取之后到句末；
# 证据片段取「主体→客体」这一段连续原文，不是整句。拆不动的句子进 skipped。
# ---------------------------------------------------------------------------
def to_assertion(idx: int, sent: str, start: int, end: int,
                 text: str, source: dict) -> dict | None:
    attribution, offset = "", 0
    m = ATTRIBUTION.match(sent)
    if m and m.end() < len(sent) - MIN_EVIDENCE:
        attribution = m.group(0).strip(" :：")
        offset = m.end()
    rest = sent[offset:]
    hit = pick_relation(rest)
    if not hit:
        return None
    rel, conf, r0, r1 = hit
    s_raw = rest[:r0]
    o_raw = rest[r1:]
    lead = len(s_raw) - len(s_raw.lstrip(" ,，。;；:："))
    s0 = offset + lead
    subject = s_raw.strip(" ,，。;；:：")
    subject = _strip_discourse(subject)
    while SUBJ_TAIL.search(subject):
        subject = SUBJ_TAIL.sub("", subject).strip()
    subject = SUBJ_EVID.sub("", subject).strip()
    subject = re.sub(r",?\s*which\s+(?:were|was|is|are|has been|have been)\s*$", "",
                     subject, flags=re.I).strip()
    subject = re.sub(r"^(?:that|which)\s+", "", subject, flags=re.I).strip()
    s_clean = s_raw.strip(" ,，。;；:：")
    if subject and subject != "（未标明主体）" and s_clean.endswith(subject):
        s0 = offset + lead + len(s_clean) - len(subject)
    obj = o_raw.strip(" ,，。;；:：")
    obj = re.sub(r"^(?:than|as)\s+", "", obj, flags=re.I).strip()
    trail = len(o_raw) - len(o_raw.rstrip(" ,，。;；:："))
    e0 = offset + r1 + len(o_raw) - trail
    if not subject:
        subject = "（未标明主体）"
        s0 = offset + r0
    if not obj:
        obj = "（未标明客体）"
        e0 = offset + r1
    quote_raw = sent[s0:e0]
    quote = quote_raw.strip()
    q0 = s0 + (len(quote_raw) - len(quote_raw.lstrip()))
    q1 = q0 + len(quote)
    if q1 - q0 < MIN_EVIDENCE:
        quote, q0, q1 = sent, 0, len(sent)
    wide = sentence_wide(sent)
    basis = f"关系词 {rel} 命中（表内置信 {conf}）；主体与客体在同一句"
    if attribution:
        basis = f"转述前缀「{attribution}」已单列；" + basis
    basis += "；证据片段为最小主客段" if q1 - q0 < len(sent) else "；证据片段为整句"
    ident = "np:" + hashlib.sha1(
        f"{source.get('identifier', 'unknown')}|{start + q0}|{start + q1}".encode("utf-8")
    ).hexdigest()[:12]
    return {
        "id": f"NP-{idx:04d}",
        "identifier": ident,
        "assertion": sent,
        "subject": subject,
        "relation": rel,
        "object": obj,
        "attribution": attribution,
        "confidence": "medium" if wide or conf < 0.6 else "high",
        "confidence_basis": basis,
        "review": "断言过宽，需拆细" if wide else "",
        "evidence": {
            "quote": quote,
            "line": line_of(text, start + q0),
            "char_start": start + q0,
            "char_end": start + q1,
            "method": "attribution+subject-object-window",
        },
        "source": source,
    }


# ---------------------------------------------------------------------------
# 来源元数据：从 case.json 与文件名取，取不到就写 unknown，不编。
# ---------------------------------------------------------------------------
def load_source(root: Path) -> tuple[dict, str, list]:
    src = {"paper": "unknown", "authors": "unknown", "year": "unknown",
           "version": "v0", "identifier": "unknown"}
    case_id = root.name or "case"
    contract: list = []
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
            oc = case.get("output_contract")
            if isinstance(oc, dict):
                contract = list(oc)
            elif isinstance(oc, list):
                contract = [str(k) for k in oc]
        except Exception:
            pass
    return src, case_id, contract


def six_missing(a: dict) -> list[str]:
    """逐项检查题面第 33 行列的七个字段，返回缺项名。"""
    checks = {
        "断言": a.get("assertion"),
        "主体与关系": a.get("subject") and a.get("relation"),
        "证据片段": (a.get("evidence") or {}).get("quote"),
        "证据位置": (a.get("evidence") or {}).get("char_start") is not None,
        "来源元数据": bool(a.get("source")),
        "置信度依据": a.get("confidence_basis"),
        "标识符": a.get("identifier"),
    }
    return [k for k in SIX if not checks.get(k)]


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

    source, case_id, contract = load_source(root)
    if a.case_id:
        case_id = a.case_id

    # 找正文：优先 manuscript，其次任意 md/txt
    cands = sorted(root.rglob("*.md")) + sorted(root.rglob("*.txt"))
    paper = next((p for p in cands if "manuscript" in p.name.lower()),
                 cands[0] if cands else None)

    if paper is None:
        # 找不到正文也不空手返回：写 result.json + 可读缺料说明
        msg = "未找到正文材料（.md/.txt）"
        result = {"case_id": case_id, "status": "blocked",
                  "gaps": [msg], "uncertainty": [msg],
                  "evidence": {"unit": "line+char", "items": []},
                  "output_contract": {k: None for k in contract},
                  "output_contract_echo": {k: {"status": "partial", "source": "pointer"}
                                           for k in contract},
                  "deliverables": ["missing_input.md"]}
        (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        (out / "missing_input.md").write_text("\n".join([
            "# 缺料说明：没有找到正文", "",
            f"- 输入目录：`{root}`",
            "- 找过：该目录下所有 `.md`、`.txt`（优先 `manuscript*`）",
            "- 需要补：待拆断言的论文正文；可选 `case.json` 提供 paper/authors/year/doi",
            "- 缺什么才能出产物：没有正文就没有句子可拆，`nanopubs.json` 与 "
            "`assertions.tsv` 无从生成",
            "- 补齐后重跑：`python run.py --input <目录> --work <w> --out <o>`",
        ]) + "\n", encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked"}, ensure_ascii=False))
        return 1

    text = read(paper)
    sents = sentences(text)
    assertions, skipped, splits = [], [], []
    covered = 0

    def stamp(a: dict, n: int) -> dict:
        a["id"] = f"NP-{n:04d}"
        a["identifier"] = "np:" + hashlib.sha1(
            f"{source.get('identifier', 'unknown')}|{a['evidence']['char_start']}|"
            f"{a['evidence']['char_end']}".encode("utf-8")).hexdigest()[:12]
        return a

    for sent, s, e in sents:
        first = to_assertion(len(assertions) + 1, sent, s, e, text, source)
        if first is None:
            skipped.append(sent)
            continue
        parts: list[dict] = []
        if first["review"]:
            # 过宽的长句先按分句切开，每块各自判关系词（题面"拆得细"）。
            # 切不出可判关系词时保留原断言，仍标 review，交人工。
            chunks = clause_chunks(sent, wide_limit(sent))
            if len(chunks) > 1:
                pos = 0
                for ch in chunks:
                    ci = sent.find(ch, pos)
                    if ci < 0:
                        ci = pos
                    pos = ci + len(ch)
                    a = to_assertion(len(parts) + 1, ch, s + ci, s + ci + len(ch),
                                     text, source)
                    if a:
                        parts.append(a)
                if parts:
                    splits.append({"原句": sent[:50], "切块数": len(parts),
                                   "规则": "过长句在「；，、and」处分句后逐块判关系词"})
        if not parts:
            parts = [first]
        for a in parts:
            assertions.append(stamp(a, len(assertions) + 1))
        covered += 1

    wide = [x for x in assertions if x["review"]]
    gaps = []
    for x in assertions:
        miss = six_missing(x)
        if miss:
            gaps.append(f"{x['id']} 缺字段：" + "、".join(miss))
    if splits:
        gaps.append(f"{len(splits)} 句过长已按分句切开，共多出 "
                    f"{sum(s['切块数'] for s in splits) - len(splits)} 条断言（见 规则命中.切分）")
    if wide:
        gaps.append(f"{len(wide)} 条断言超过长度阈值（中文 {WIDE_CJK} 字 / 英文 {WIDE_LATIN} 字），"
                    "需人工拆细")
    if skipped:
        gaps.append(f"{len(skipped)} 句未命中受控关系词，未生成断言（逐句列在 unresolved.md）")
    if not assertions:
        gaps.append("未识别到显式关系句，无法生成断言；已交 unresolved.md 逐句说明")
    if source["paper"] == "unknown":
        gaps.append("来源元数据缺失：paper 未提供，已写 unknown")
    for k in contract:
        if k not in ("阅读/解析结果", "证据与边界", "运行状态"):
            gaps.append(f"契约字段「{k}」未识别，已置 null")
    coverage = round(covered / max(1, len(sents)), 3)
    status = "success" if assertions and not wide else "partial"

    # 没有断言或断言不全时，也要交一件能读的东西：逐句说明为什么没成断言
    unresolved = ["# 未成断言的句子", "",
                  f"- 正文：`{paper.name}`",
                  f"- 句子总数：{len(sents)}；成断言 {covered} 句（共 {len(assertions)} 条）；"
                  f"未命中关系词 {len(skipped)} 句",
                  "", "| # | 原句 | 原因 |", "|-|-|-|"]
    for i, s in enumerate(skipped, 1):
        unresolved.append(f"| {i} | {s.strip().replace('|', '/')[:90]} | "
                          f"未命中 run.py 的 RELATIONS 受控关系词，不猜关系 |")
    if not skipped:
        unresolved.append("| — | — | 所有句子都成了断言 |")
    if splits:
        unresolved += ["", "## 已按分句切开的过宽长句", "",
                       "| # | 原句 | 切块数 | 规则 |", "|-|-|-|-|"]
        for i, s in enumerate(splits, 1):
            unresolved.append(f"| {i} | {s['原句'].replace('|', '/')} | {s['切块数']} | {s['规则']} |")
    (out / "unresolved.md").write_text("\n".join(unresolved) + "\n", encoding="utf-8")

    np_path = out / "nanopubs.json"
    np_path.write_text(json.dumps({
        "case_id": case_id, "version": VERSION, "source": source,
        "assertions": assertions,
        "unresolved": skipped,
        "规则命中": {"关系词命中": {rel: sum(1 for x in assertions if x["relation"] == rel)
                               for rel in sorted({x["relation"] for x in assertions})},
                  "切分": splits,
                  "未命中句子数": len(skipped),
                  "关系词表": "run.py 的 RELATIONS（副本见 rules/relations.md）"},
        "stats": {"sentences": len(sents), "assertions": len(assertions),
                  "covered_sentences": covered,
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

    payload = {
        "parsed": {"assertions": len(assertions), "coverage": coverage,
                   "items": [{"id": x["id"], "subject": x["subject"],
                              "relation": x["relation"], "object": x["object"]}
                             for x in assertions]},
        "evidence": {"unit": "line+char_start+char_end",
                     "quotes": [{"id": x["id"], "quote": x["evidence"]["quote"],
                                 "line": x["evidence"]["line"],
                                 "char_start": x["evidence"]["char_start"],
                                 "char_end": x["evidence"]["char_end"]}
                                for x in assertions],
                     "wide_needs_review": len(wide), "gaps": gaps},
        "status": {"status": status,
                   "reason": gaps[0] if gaps else "全部断言通过七字段检查",
                   "assertions": len(assertions), "n_gaps": len(gaps)},
    }
    echo, echo_status = {}, {}
    for k in contract:
        if k == "阅读/解析结果":
            echo[k], echo_status[k] = payload["parsed"], {"status": "full", "source": "nanopubs.json"}
        elif k == "证据与边界":
            echo[k], echo_status[k] = payload["evidence"], {"status": "full", "source": "evidence"}
        elif k == "运行状态":
            echo[k], echo_status[k] = payload["status"], {"status": "full", "source": "status"}
        else:
            echo[k], echo_status[k] = None, {"status": "partial", "source": "pointer"}

    result = {
        "case_id": case_id,
        "skill_version": VERSION,
        "status": status,
        "阅读/解析结果": payload["parsed"],
        "证据与边界": payload["evidence"],
        "运行状态": payload["status"],
        "output_contract": echo,
        "output_contract_echo": echo_status,
        "stats": {"sentences": len(sents), "skipped": len(skipped),
                  "covered_sentences": covered, "splits": len(splits),
                  "wide": len(wide), "coverage": coverage,
                  "elapsed_s": round(time.perf_counter() - t0, 3)},
        "evidence": {"unit": "line+char",
                     "items": [{"id": x["id"], "line": x["evidence"]["line"],
                                "char_start": x["evidence"]["char_start"],
                                "char_end": x["evidence"]["char_end"]}
                               for x in assertions[:50]]},
        "规则命中": {"关系词": {rel: sum(1 for x in assertions if x["relation"] == rel)
                           for rel in sorted({x["relation"] for x in assertions})},
                  "切分": splits,
                  "未命中句子数": len(skipped)},
        "uncertainty": gaps,
        "gaps": gaps,
        "deliverables": [np_path.name, tsv.name, "unresolved.md", "result.json"],
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "assertions": len(assertions),
                      "coverage": coverage, "wide": len(wide), "splits": len(splits),
                      "sentences": len(sents)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
