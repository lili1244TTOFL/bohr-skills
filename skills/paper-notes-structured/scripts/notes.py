#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notes.py — 章节对齐、字段抽取与结构化笔记生成（v2）。

四步，都不调用模型：
  1 sectionize：按标题块把正文切成节；节带 first/last 锚点与承接边
  2 extract：在节内按字段信号挑句，取「信号所在句」，记 rule/匹配串/字符区间
  3 card/knowledge：六字段卡片（含结论）、多类型知识点、可检索 token、冲突点
  4 coverage/conflicts：覆盖统计与多值/极性待核点，供核验与交接

所有条目都带原文逐字 quote 与字符区间 span，笔记文本可改写，证据不随改写变。
调用方模型只需在候选基础上改写并写 reason，锚点、quote、span 不动。

用法：
  python notes.py --parse work/parse.json --out work/notes.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KEEP = ("body", "caption", "table", "reference", "formula")
FIELDS = ("question", "data", "method", "finding", "conclusion", "limitation")
FIELD_CN = {"question": "研究问题", "data": "数据", "method": "方法条件",
            "finding": "主要发现", "conclusion": "结论", "limitation": "局限"}
CARD_ORDER = tuple(FIELD_CN[f] for f in FIELDS)

# 字段信号：键是字段，值是「规则名 → 正则」。规则名进 why.rule，供独立复核。
SIGNALS: dict[str, dict[str, str]] = {
    "question": {
        "1_自述研究动作": r"(?i)\b(we (?:ask|investigate|examine|study|test|address|explore)|"
                          r"this (?:paper|study|work) (?:asks|investigates|examines|addresses)|"
                          r"aim(?:s)? (?:to|of)|research question|our goal|我们|本文|本研究|旨在|考察|检验)",
        "2_目的状语": r"(?i)\b(to (?:investigate|examine|test|evaluate|understand))\b|(?:为了|目的是)",
    },
    "data": {
        "1_数据与样本": r"(?i)\b(dataset|datasets|corpus|benchmark|participants?|subjects?|"
                      r"patients?|samples?|n\s*=\s*\d+|"
                      r"\d+\s*(?:participants|subjects|patients|samples|papers|images|videos|"
                      r"volunteers|adults|children))\b|(?:数据集|数据库|样本量?|测试集|"
                      r"实验对象|参与者|算例|实例集)",
        "2_数据来源": r"(?i)\b(data(?:set)? (?:from|of|were|was)|collected (?:from|in)|"
                    r"publicly available|open[- ]source)\b|(?:样本来自|数据来自|公开数据集|"
                    r"采集自)",
    },
    "method": {
        "1_方法动作": r"(?i)\b(we (?:use|used|apply|applied|train|trained|measure|measured|"
                   r"compute|computed|implement|implemented|build|built|design|designed|"
                   r"randomi[sz]e|recruit|recruited|analy[sz]e|analy[sz]ed)|"
                   r"method(?:s|ology)?|procedure|protocol|experimental (?:setup|design)|"
                   r"we adopt|trained (?:on|with))\b|(?:采用|提出|构建|设计了?|算法流程|"
                   r"实验设置|参数设置|建模)",
        "2_条件限定": r"(?i)\b(for \d+|under (?:the )?(?:condition|setting)|"
                   r"(?:learning rate|batch size|epochs?|temperature|threshold)\b)|"
                   r"(?:实验条件|样本条件|测量工具|约束条件|参数取值)",
    },
    "finding": {
        "1_结果自述": r"(?i)\b(we (?:find|found|show|showed|observe|observed|demonstrate|"
                  r"report|achieve|achieved|obtain|obtained)|results? (?:show|indicate|"
                  r"suggest|reveal|demonstrate)|outperform(?:s|ed)?|improve[sd]? (?:by|over))\b",
        "2_统计表述": r"(?i)(p\s*[<>=]\s*0?\.\d+|significant(?:ly)?|\d+(?:\.\d+)?\s*%|"
                   r"accuracy (?:of )?\d|误差|准确率|显著|结果表明|实验表明|优于|提升了?)",
    },
    "conclusion": {
        "1_结论自述": r"(?i)\b(in conclusion|we conclude|conclusions?|in summary|overall,|"
                   r"taken together|these (?:results|findings) (?:suggest|indicate|show)|"
                   r"we (?:have )?(?:shown|demonstrated))\b",
        "2_总结语": r"(?:综上所述|总之|结论|研究表明|本文认为|由此可见)",
    },
    "limitation": {
        "1_局限自述": r"(?i)\b(limitation(?:s)?|future work|future research|caveat|drawback|"
                   r"not (?:tested|covered|addressed)|remains? (?:unclear|unknown)|"
                   r"further (?:work|study|research) (?:is|will be) (?:needed|required))\b",
        "2_否定表述": r"(?i)\b(only (?:young|adults?|males?|females?|a single)|small sample|"
                   r"cannot|did not|could not|no significant)\b|(?:局限|不足|尚不清楚|仅|未能)",
    },
}
SIGNAL_IDS = {f: tuple(SIGNALS[f].keys()) for f in FIELDS}

ROLE_RULES = (
    ("abstract", r"(?i)^(abstract|摘要)\b"),
    ("limitation", r"(?i)(limitation|局限|不足|限制)"),
    ("reference", r"(?i)^(references?|bibliography|参考文献)"),
    ("appendix", r"(?i)^(appendix|附录|acknowledge?ments?|致谢)"),
    ("related", r"(?i)(related work|prior work|相关工作|文献综述)"),
    ("conclusion", r"(?i)(conclusions?|concluding|结论|总结|结语)"),
    ("discussion", r"(?i)(discussions?|讨论)"),
    ("result", r"(?i)(results?|experiments?|evaluation|结果|实验|评估)"),
    ("method", r"(?i)(method|approach|model|architect|implementation|materials and methods|"
               r"方法|模型|架构|实现|实验设置)"),
    ("intro", r"(?i)(introduction|background|引言|前言|背景|研究背景)"),
)
SIGNAL_TO_ROLE = {"question": "intro", "data": "method", "method": "method",
                  "finding": "result", "conclusion": "conclusion",
                  "limitation": "limitation"}
CITE_KEEP = re.compile(r"\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]")
NUM = re.compile(r"\d+(?:[.,]\d+)?")
STOP = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "have",
    "has", "had", "not", "but", "which", "their", "these", "those", "such", "using",
    "used", "our", "its", "can", "may", "also", "more", "than", "into", "between",
    "within", "based", "both", "each", "other", "however", "table", "figure", "fig",
}
TERM_RE = re.compile(r"\b([A-Z][A-Za-z]{1,9}[A-Z])\b\s*[（(]([^)）]{2,90})[)）]")
TERM_DEF_RE = re.compile(r"(?i)\b(?:we )?(?:call|define|term|refer to) (?:it|this|the [\w\- ]{2,30})?"
                         r" as ([A-Z][\w\-]{2,30})")
VARIABLE_RE = re.compile(r"(?i)\b([A-Za-z][\w\-]{1,30}(?: [A-Za-z][\w\-]{1,30}){0,2}) "
                         r"(task|test|scale|questionnaire|index|score|measure|paradigm|"
                         r"questionnaire)\b")
NEG_RE = re.compile(r"(?i)\b(no|not|never|cannot|didn't|did not|without|lack(?:s|ed)?|"
                    r"failed to|no significant)\b|(?:未|无|不|没有|未能)")
TRUNC = 240


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def norm_key(s: str, limit: int = 60) -> str:
    """去重用的归一化键：小写、标点折成空格、多空白压一。"""
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (s or "")).strip().lower()
    return re.sub(r"\s+", " ", t)[:limit]


def tokenize(s: str, limit: int = 24) -> list[str]:
    """倒排索引用词条：英文词（≥3）、缩写、中文 2-gram。顺序稳定。"""
    s = s or ""
    out: list[str] = []
    out.extend(w for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", s.lower())
               if w not in STOP)
    out.extend(a.lower() for a in re.findall(r"\b[A-Z]{2,6}\b", s))
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]+", s))
    out.extend(cjk[i:i + 2] for i in range(0, max(0, len(cjk) - 1)))
    seen, uniq = set(), []
    for t in out:
        if t in seen or len(t) < 2:
            continue
        seen.add(t)
        uniq.append(t)
    return uniq[:limit]


# ---------------------------------------------------------------- 锚点与块索引
def anchor_index(parse: dict) -> dict:
    """块 → 锚点：P（正文档）、T（表）、F（公式）。返回 {(page, id(block)): anchor}。

    同一块可同时有 P 与 T/F 两个锚点，两种写法都指同一个块。
    """
    ids: dict[tuple[int, int], dict] = {}
    for page in parse["pages"]:
        pno = page["page"]
        i = t = f = 0
        for b in page["blocks"]:
            if b["role"] not in KEEP:
                continue
            pid = f"P{pno}-{i}"
            i += 1
            ids[(pno, id(b))] = {"p": pid}
            if b["role"] == "table":
                t += 1
                ids[(pno, id(b))]["t"] = f"T{pno}-{t}"
            if b["role"] == "formula":
                f += 1
                ids[(pno, id(b))]["f"] = f"F{pno}-{f}"
    return ids


def block_text(b: dict) -> str:
    """锚点解析用的块文本：表用 markdown（含单元格原文），其余用 text。"""
    if b["role"] == "table":
        md = b.get("markdown")
        if md:
            return b.get("text", "") + "\n" + md
    return b.get("text") or ""


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """按句末标点切句，返回 [start, end) 列表，覆盖全文。"""
    if not text:
        return []
    spans, start = [], 0
    for m in re.finditer(r"[.!?。！？](?=\s|$)", text):
        spans.append((start, m.end()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def pick_sentence(text: str, pos: int) -> tuple[int, int]:
    """取包含匹配位置的句子，超长按 240 字截断（仍是连续原文）。"""
    for s, e in sentence_spans(text):
        if s <= pos < e or (pos >= e and e == len(text)):
            return s, min(e, s + TRUNC)
    return 0, min(len(text), TRUNC)


# ---------------------------------------------------------------- 2 抽候选
def sectionize(parse: dict) -> list[dict]:
    secs: list[dict] = []
    ids = anchor_index(parse)
    cur = {"title": "（题名与摘要）", "level": 0, "page": 1, "paras": [], "tables": []}
    for page in parse["pages"]:
        for b in page["blocks"]:
            if b["role"] in ("header", "footer", "page_number"):
                continue
            if b["role"] == "heading":
                title = (b["text"] or "").strip()
                if cur["paras"] or cur["tables"] or cur["title"] not in ("（题名与摘要）", title):
                    if not secs or secs[-1]["title"] != cur["title"]:
                        secs.append(cur)
                cur = {"title": b["text"], "level": b.get("level", 1), "page": b["page"],
                       "paras": [], "tables": [], "heading_anchor": None}
                continue
            aid = ids[(page["page"], id(b))]
            if b["role"] == "table":
                cur["tables"].append({"page": b["page"], "text": b["text"],
                                      "id": aid["p"], "table_id": aid.get("t"),
                                      "markdown": b.get("markdown")})
            else:
                cur["paras"].append({"page": b["page"], "text": block_text(b),
                                     "role": b["role"], "id": aid["p"],
                                     "table_id": aid.get("t"), "formula_id": aid.get("f")})
    if cur["paras"] or cur["tables"]:
        secs.append(cur)
    for i, s in enumerate(secs, 1):
        s["sec_id"] = f"S{i:02d}"
        s.setdefault("heading_anchor", (s["paras"] or s["tables"] or [{}])[0].get("id"))
    return secs


def candidates(sec: dict) -> dict:
    """每字段最多留 3 条候选；每条带 rule / 匹配串 / 字符区间 / 逐字 quote。"""
    out: dict[str, list[dict]] = {k: [] for k in FIELDS}
    for p in sec["paras"]:
        text = p["text"]
        for field in FIELDS:
            hit = None
            for rule, pat in SIGNALS[field].items():
                m = re.search(pat, text)
                if m:
                    hit = (rule, m)
                    break
            if not hit:
                continue
            rule, m = hit
            s, e = pick_sentence(text, m.start())
            quote = text[s:e]
            if not quote.strip():
                continue
            why = {"rule": rule, "match": m.group(0)[:60],
                   "match_span": [m.start(), m.end()]}
            item = {"id": p["id"], "page": p["page"], "span": [s, e], "quote": quote,
                    "text": quote, "why": why,
                    "weak": p["role"] in ("reference", "caption"),
                    "truncated": (e - s) == TRUNC and e < len(text)}
            if p.get("table_id"):
                item["table_id"] = p["table_id"]
            if len(out[field]) >= 3:
                continue
            if any(x["quote"] == quote for x in out[field]):
                continue
            out[field].append(item)
    return out


def classify_role(sec: dict) -> tuple[str, dict]:
    """章节角色：标题命中优先，否则按节内字段信号计数。返回 (role, basis)。"""
    title = sec["title"] or ""
    for role, pat in ROLE_RULES:
        m = re.search(pat, title)
        if m:
            return role, {"source": "title", "match": m.group(0)}
    counts = {f: len(sec["candidates"][f]) for f in FIELDS}
    prio = ("finding", "conclusion", "limitation", "method", "question", "data")
    best = max(FIELDS, key=lambda f: (counts[f], -prio.index(f)))
    if counts.get(best):
        return SIGNAL_TO_ROLE.get(best, "other"), {"source": "signals", "field": best,
                                                   "hits": counts[best]}
    return "other", {"source": "none", "field": "", "hits": 0}


def build_sections(parse: dict) -> list[dict]:
    secs = sectionize(parse)
    for s in secs:
        s["candidates"] = candidates(s)
        s["role"], s["role_basis"] = classify_role(s)
        anchors = [p["id"] for p in s["paras"]] + [t["id"] for t in s["tables"]]
        s["block_anchors"] = anchors
        s["first_anchor"] = anchors[0] if anchors else None
        s["last_anchor"] = anchors[-1] if anchors else None
    for i, s in enumerate(secs):
        nxt = secs[i + 1] if i + 1 < len(secs) else None
        s["carry"] = None if not nxt else {
            "to": nxt["sec_id"],
            "to_title": (nxt["title"] or "")[:80],
            "to_anchor": nxt.get("first_anchor"),
            "to_first": (nxt["paras"][0]["text"][:120] if nxt["paras"] else ""),
            "from_anchor": s.get("last_anchor"),
            "from_last": (s["paras"][-1]["text"][-120:] if s["paras"] else ""),
        }
    return secs


# ---------------------------------------------------------------- 3 卡片/知识点
def pick_strong(secs: list[dict], field: str, cap: int = 6,
                roles: tuple[str, ...] | None = None) -> list[dict]:
    items = []
    for s in secs:
        if roles and s["role"] not in roles:
            continue
        for it in s["candidates"][field]:
            if it["weak"]:
                continue
            items.append({**it, "section": s["title"], "sec_id": s["sec_id"]})
    seen, uniq = set(), []
    for it in items:
        if norm_key(it["quote"]) in seen:
            continue
        seen.add(norm_key(it["quote"]))
        uniq.append(it)
    return uniq[:cap]


def section_lead(s: dict, rule: str) -> dict | None:
    """取一节首段的首句作为条目：「结论节本身即是结论」这类场景用。"""
    if not s["paras"]:
        return None
    p = s["paras"][0]
    text = p["text"]
    spans = sentence_spans(text)
    if not spans:
        return None
    e = min(spans[0][1], TRUNC)
    return {"id": p["id"], "page": p["page"], "span": [0, e], "quote": text[:e],
            "text": text[:e], "weak": p["role"] in ("reference", "caption"),
            "truncated": e < len(text),
            "why": {"rule": rule, "match": text[:40], "match_span": [0, min(40, e)]}}


def auto_card(secs: list[dict]) -> dict:
    card: dict[str, dict] = {}
    for field in FIELDS:
        name = FIELD_CN[field]
        items = pick_strong(secs, field, cap=6)
        if field == "conclusion":
            # 结论节的首句本身就是结论，不要求命中信号词
            for s in secs:
                if s["role"] != "conclusion":
                    continue
                lead = section_lead(s, "3_结论节首句")
                if lead and not any(norm_key(x["quote"]) == norm_key(lead["quote"])
                                    for x in items):
                    items.append({**lead, "section": s["title"], "sec_id": s["sec_id"]})
                if len(items) >= 6:
                    break
        weak = [it for s in secs for it in s["candidates"][field] if it["weak"]]
        rows = [{"text": it["quote"], "quote": it["quote"], "anchor": it["id"],
                 "span": it["span"], "page": it["page"], "section": it["section"],
                 "sec_id": it["sec_id"], "why": it["why"],
                 **({"table_id": it["table_id"]} if it.get("table_id") else {})}
                for it in items]
        if rows:
            status = "auto"
        elif weak:
            status = "undetermined"
        else:
            status = "not_reported"
        card[name] = {
            "status": status,
            "items": rows,
            "signal_rules": list(SIGNAL_IDS[field]),
            "machine_checkable": bool(rows),
            "handoff": [] if rows else [{
                "action": "人工确认" if status == "undetermined" else "人工补齐或确认确实未报告",
                "why": {"undetermined": "仅在图注/参考文献里命中信号，证据不足以下结论",
                        "not_reported": "全篇未命中该字段信号；可能未报告，也可能信号表未覆盖"}
                       [status],
                "sections_checked": [f"{s['sec_id']} {s['title']}"[:60] for s in secs][:12],
                "search_terms": {
                    "question": ["we ask", "aim", "objective", "研究问题"],
                    "data": ["dataset", "participants", "N =", "样本"],
                    "method": ["we use", "method", "protocol", "方法"],
                    "finding": ["we found", "results show", "p<", "结果"],
                    "conclusion": ["in conclusion", "overall", "结论"],
                    "limitation": ["limitation", "future work", "局限"],
                }[field],
            }],
        }
    return card


def knowledge_items(secs: list[dict]) -> list[dict]:
    """知识点：概念/术语、变量、方法、发现、结论、局限，一条一义。"""
    raw: list[dict] = []

    def add(kind: str, sec: dict, it: dict, text: str | None = None,
            quotes: str | None = None, extra: dict | None = None,
            reason: str | None = None) -> None:
        q = quotes if quotes is not None else it["quote"]
        entry = {"kind": kind, "section": sec["title"], "sec_id": sec["sec_id"],
                 "page": it["page"], "anchor": it["id"], "span": it.get("span"),
                 "quote": q, "text": text or q, "key": norm_key(text or q),
                 "why": it.get("why")}
        if reason:
            entry["reason"] = reason
        if extra:
            entry.update(extra)
        raw.append(entry)

    # 方法 / 发现 / 结论 / 局限：按章节角色取首条
    for s in secs:
        for field, kinds in (("method", ({"method"}, "method", "method")),
                             ("finding", ({"result"}, "finding", "result")),
                             ("conclusion", ({"conclusion"}, "conclusion", "conclusion")),
                             ("limitation", ({"limitation"}, "limitation", "limitation"))):
            roles, kind, _ = kinds
            if s["role"] not in roles:
                continue
            for it in s["candidates"][field][:1]:
                add(kind, s, it)
    # 结论节：无信号也取首句（结论节本身即是结论）
    for s in secs:
        if s["role"] != "conclusion" or not s["paras"]:
            continue
        p = s["paras"][0]
        text = p["text"]
        spans = sentence_spans(text)
        if not spans:
            continue
        e = min(spans[0][1], TRUNC)
        add("conclusion", s, {"id": p["id"], "page": p["page"], "span": [0, e],
                              "quote": text[:e], "why": {"rule": "3_结论节首句",
                                                         "match": text[:40], "match_span": [0, 40]}})
    # 术语与缩写：X (展开) 与 define ... as X
    seen_term: set[str] = set()
    for s in secs:
        for p in s["paras"]:
            for m in TERM_RE.finditer(p["text"]):
                abbr, exp = m.group(1), m.group(2).strip()
                if len(abbr) < 2 or len(exp) < 2 or norm_key(abbr) in seen_term:
                    continue
                seen_term.add(norm_key(abbr))
                sp = [max(0, m.start() - 40), min(len(p["text"]), m.end() + 40)]
                add("term", s, {"id": p["id"], "page": p["page"], "span": sp,
                                "quote": p["text"][sp[0]:sp[1]],
                                "why": {"rule": "4_缩写展开", "match": m.group(0)[:60],
                                        "match_span": [m.start(), m.end()]}},
                    text=f"{abbr} = {exp}", quotes=p["text"][sp[0]:sp[1]],
                    reason="术语条：text 写「缩写 = 展开」，quote 取原文定义处上下文",
                    extra={"alias": [norm_key(abbr), norm_key(exp)],
                           "expansion": exp})
            m = TERM_DEF_RE.search(p["text"])
            if m:
                term = m.group(1)
                if norm_key(term) not in seen_term:
                    seen_term.add(norm_key(term))
                    sp = pick_sentence(p["text"], m.start())
                    add("term", s, {"id": p["id"], "page": p["page"], "span": [sp[0], sp[1]],
                                    "quote": p["text"][sp[0]:sp[1]],
                                    "why": {"rule": "5_定义句", "match": m.group(0)[:60],
                                            "match_span": [m.start(), m.end()]}},
                        text=term,
                        reason="术语条：text 为定义句抽出的术语名，quote 为原句",
                        extra={"alias": [norm_key(term)]})
    # 变量与测量工具
    seen_var: set[str] = set()
    for s in secs:
        for p in s["paras"]:
            m = VARIABLE_RE.search(p["text"])
            if not m:
                continue
            phrase = m.group(0)
            if norm_key(phrase) in seen_var:
                continue
            seen_var.add(norm_key(phrase))
            sp = pick_sentence(p["text"], m.start())
            add("variable", s, {"id": p["id"], "page": p["page"], "span": [sp[0], sp[1]],
                                "quote": p["text"][sp[0]:sp[1]],
                                "why": {"rule": "6_测量工具", "match": phrase[:60],
                                        "match_span": [m.start(), m.end()]}},
                text=phrase,
                reason="变量条：text 为抽出的测量工具/变量名，quote 为原句",
                extra={"alias": [norm_key(phrase)]})
            if len([r for r in raw if r["kind"] == "variable"]) >= 4:
                break
    # 去重与编号（key 去重后，再按术语别名去重，避免同义表述重复立项）
    seen, uniq = set(), []
    used_alias: set[str] = set()
    for it in raw:
        k = (it["kind"], it["key"])
        if not it["key"] or k in seen:
            continue
        als = [a for a in (it.get("alias") or []) if a]
        if any(a in used_alias for a in als):
            continue
        used_alias.update(als)
        seen.add(k)
        uniq.append(it)
    for i, it in enumerate(uniq, 1):
        it["id"] = f"K{i:02d}"
        it["tokens"] = tokenize(it["text"] + " " + (it.get("expansion") or ""))
        it.setdefault("alias", [it["key"]])
    return uniq


# ---------------------------------------------------------------- 4 冲突与覆盖
def derive_conflicts(card: dict) -> list[dict]:
    """机器判不了的多值点与极性并存点；只陈述「材料给了多个说法」，不下结论。"""
    out: list[dict] = []
    for name, obj in card.items():
        items = [it for it in obj.get("items") or [] if it.get("quote")]
        if len(items) < 2:
            continue
        num_items = [(it, NUM.findall(it["quote"])) for it in items]
        num_items = [(it, ns) for it, ns in num_items if ns]
        if len(num_items) >= 2:
            vals = sorted({n for _, ns in num_items for n in ns})
            if len(vals) >= 2:
                out.append({
                    "field": name,
                    "kind": "多值点",
                    "values": vals[:8],
                    "items": [{"anchor": it["anchor"], "page": it["page"],
                               "quote": it["quote"][:120], "numbers": ns[:6]}
                              for it, ns in num_items][:4],
                    "machine_verdict": "无法判定哪个取值对应哪个条件",
                    "todo": "人工核对：确认每个数值的口径（数据集/设置/指标）",
                })
        neg = [it for it in items if NEG_RE.search(it["quote"])]
        pos = [it for it in items if not NEG_RE.search(it["quote"])]
        if neg and pos:
            out.append({
                "field": name,
                "kind": "极性并存",
                "items": [{"anchor": neg[0]["anchor"], "page": neg[0]["page"],
                           "quote": neg[0]["quote"][:120], "polarity": "否定"},
                          {"anchor": pos[0]["anchor"], "page": pos[0]["page"],
                           "quote": pos[0]["quote"][:120], "polarity": "肯定"}],
                "machine_verdict": "同一字段下同时出现否定与肯定表述",
                "todo": "人工核对：确认二者是否指同一对象/条件",
            })
    return out


def coverage(parse: dict, secs: list[dict]) -> dict:
    headings = [b["text"] for pg in parse["pages"] for b in pg["blocks"]
                if b["role"] == "heading"]
    titles = [s["title"] for s in secs]
    tset = set(titles)
    common = [h for h in headings if h in tset]
    excl: dict[str, int] = {}
    for pg in parse["pages"]:
        for b in pg["blocks"]:
            if b["role"] in ("header", "footer", "page_number"):
                excl[b["role"]] = excl.get(b["role"], 0) + 1
    kept = sum(1 for pg in parse["pages"] for b in pg["blocks"] if b["role"] in KEEP)
    assigned = sum(len(s["block_anchors"]) for s in secs)
    return {"headings": len(headings), "sections": len(secs),
            "matched_titles": len(common),
            "ratio": round(len(common) / len(headings), 4) if headings else 1.0,
            "order_ok": [t for t in titles if t in set(headings)] == common,
            "blocks_kept": kept, "blocks_assigned": assigned,
            "excluded_by_role": excl,
            "missing_headings": [h for h in headings if h not in tset][:12]}


def build(parse: dict) -> tuple[dict, dict]:
    secs = build_sections(parse)
    card = auto_card(secs)
    know = knowledge_items(secs)
    conflicts = derive_conflicts(card)
    cov = coverage(parse, secs)
    notes = {
        "sections": [{"sec_id": s["sec_id"], "title": s["title"], "level": s["level"],
                      "page": s["page"], "role": s["role"], "role_basis": s["role_basis"],
                      "first_anchor": s["first_anchor"], "last_anchor": s["last_anchor"],
                      "heading_anchor": s.get("heading_anchor"),
                      "n_paras": len(s["paras"]), "n_tables": len(s["tables"]),
                      "carry": s["carry"], "candidates": s["candidates"]}
                     for s in secs],
        "card": card,
        "knowledge": know,
        "conflicts": conflicts,
        "coverage": cov,
        "meta": {"version": "2.0.0", "fields": list(CARD_ORDER),
                 "evidence_unit": "block id + span（P<页>-<序> / T<页>-<序> / F<页>-<序>）",
                 "notes_status": "auto",
                 "thresholds": {"N03_heading_coverage": 0.8}},
    }
    trace = {
        "sections": [{"sec_id": s["sec_id"], "title": s["title"], "page": s["page"],
                      "role": s["role"], "role_basis": s["role_basis"],
                      "heading_anchor": s.get("heading_anchor"),
                      "block_anchors": s["block_anchors"][:200],
                      "n_blocks": len(s["block_anchors"]),
                      "carry_to": (s["carry"] or {}).get("to")} for s in secs],
        "extraction": [
            {"field": f, "sec_id": s["sec_id"], "anchor": it["id"], "span": it["span"],
             "rule": it["why"]["rule"], "match": it["why"]["match"],
             "match_span": it["why"]["match_span"], "truncated": it["truncated"]}
            for s in secs for f in FIELDS for it in s["candidates"][f]
        ],
        "coverage": cov,
    }
    return notes, trace


# ---------------------------------------------------------------- 落盘渲染
def to_markdown(res: dict) -> str:
    card = res.get("card") or {}
    secs = res.get("sections") or []
    know = res.get("knowledge") or []
    conf = res.get("conflicts") or []
    cov = res.get("coverage") or {}
    lines = ["# 结构化笔记", ""]
    lines.append(f"> 版本 {res.get('meta', {}).get('version', '2.0.0')}｜"
                 f"{len(secs)} 节｜{len(know)} 条知识点｜{len(conf)} 个待核对点｜"
                 f"标题覆盖 {cov.get('ratio', 0):.0%}｜证据单位：块 id + 字符区间")
    lines.append("")
    lines.append("## 论文卡片（六字段，可并排比较）")
    lines.append("")
    for field in CARD_ORDER:
        obj = card.get(field) or {}
        items = obj.get("items") or []
        if not items:
            lines.append(f"- **{field}**：`{obj.get('status', 'not_reported')}`")
            for h in obj.get("handoff") or []:
                lines.append(f"  - 交接：{h.get('action')}（{h.get('why')}）；"
                             f"检索词 {', '.join(h.get('search_terms') or [])}")
            continue
        for it in items:
            sp = it.get("span") or [0, 0]
            lines.append(f"- **{field}**（{it.get('sec_id')} {it.get('section')}，"
                         f"p.{it.get('page')}，{it.get('anchor')} @{sp[0]}-{sp[1]}）："
                         f"{it.get('text')}")
            lines.append(f"  - 原文 `{it.get('anchor')}`：{it.get('quote')}")
    lines.append("")
    lines.append("## 章节骨架（按原文标题顺序，含承接边）")
    lines.append("")
    lines.append("| 节 | 标题 | 角色 | 页 | 段 | 承接下一节 |")
    lines.append("|-|-|-|-|-|-|")
    for s in secs:
        carry = s.get("carry") or {}
        lines.append(f"| {s['sec_id']} | {(s['title'] or '')[:48]} | {s.get('role')} | "
                     f"p.{s.get('page')} | {s.get('n_paras')} | {carry.get('to') or '—'} |")
    lines.append("")
    lines.append("## 章节笔记")
    lines.append("")
    for s in secs:
        lines.append(f"### {s['sec_id']} {s['title']}（p.{s['page']} · role={s.get('role')}"
                     f" · {s.get('n_paras')} 段 · {s.get('n_tables')} 表）")
        basis = s.get("role_basis") or {}
        if basis:
            lines.append(f"- 角色依据：{basis.get('source')} {basis.get('match') or basis.get('field') or ''}")
        carry = s.get("carry")
        if carry:
            lines.append(f"- 承接：→ {carry.get('to')} {carry.get('to_title')}"
                         f"（`{carry.get('from_anchor')}` → `{carry.get('to_anchor')}`）")
        for field in FIELDS:
            for it in (s.get("candidates") or {}).get(field, [])[:2]:
                sp = it.get("span") or [0, 0]
                lines.append(f"- {field}：{it.get('quote')} `[{it.get('id')} @{sp[0]}-{sp[1]}]`"
                             f"（规则 {it.get('why', {}).get('rule')}）")
        lines.append("")
    lines.append("## 知识点清单（可检索，kind + key + 锚点）")
    lines.append("")
    for k in know:
        sp = k.get("span") or [0, 0]
        lines.append(f"- **{k.get('id')}** [{k.get('kind')}] {k.get('text')}"
                     f" · `[{k.get('anchor')} @{sp[0]}-{sp[1]}]` p.{k.get('page')} · {k.get('section')}")
        if k.get("expansion"):
            lines.append(f"  - 展开：{k.get('expansion')}")
    lines.append("")
    lines.append("## 冲突与待人工确认（机器判不了的部分）")
    lines.append("")
    if not conf:
        lines.append("- 本次抽取未发现多值点或极性并存点；不等于原文无冲突，见下节未报告项。")
    for c in conf:
        lines.append(f"- **{c.get('field')}** · {c.get('kind')} · {c.get('machine_verdict')}")
        for it in c.get("items") or []:
            lines.append(f"  - `{it.get('anchor')}` p.{it.get('page')}"
                         f"（{it.get('polarity') or ','.join(it.get('numbers') or [])}）："
                         f"{it.get('quote')}")
        lines.append(f"  - 待办：{c.get('todo')}")
    lines.append("")
    lines.append("## 未报告与无法判断")
    lines.append("")
    for field in CARD_ORDER:
        obj = card.get(field) or {}
        if obj.get("items"):
            continue
        for h in obj.get("handoff") or []:
            lines.append(f"- **{field}**：{obj.get('status')}｜{h.get('why')}｜"
                         f"{h.get('action')}｜检索词 {', '.join(h.get('search_terms') or [])}")
    lines.append("")
    lines.append("## 覆盖与可检索摘要")
    lines.append("")
    lines.append(f"- 标题 {cov.get('headings')} 个，成节 {cov.get('sections')} 个，"
                 f"命中 {cov.get('matched_titles')} 个，覆盖 {cov.get('ratio', 0):.0%}，"
                 f"顺序一致：{cov.get('order_ok')}")
    lines.append(f"- 正文块 {cov.get('blocks_kept')} 个，归节 {cov.get('blocks_assigned')} 个；"
                 f"按角色排除 {cov.get('excluded_by_role')}")
    lines.append(f"- 知识点 {len(know)} 条；索引词条见 `index.json`（`scripts/query.py` 可查）")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="结构化笔记")
    ap.add_argument("--parse", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--trace", default=None)
    args = ap.parse_args(argv)
    res, trace = build(load(Path(args.parse)))
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.trace:
        Path(args.trace).write_text(json.dumps(trace, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    print(json.dumps({"ok": True, "sections": len(res["sections"]),
                      "knowledge": len(res["knowledge"]),
                      "filled": sum(1 for v in res["card"].values() if v["items"]),
                      "conflicts": len(res["conflicts"]),
                      "coverage": res["coverage"]["ratio"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
