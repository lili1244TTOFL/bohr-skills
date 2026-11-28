#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compose.py — 据原稿与 bib 产出改好的稿 revised.md。

真做三件事，每件都在正文里留痕、在文末表里给依据：

1 分组：相关工作若逐篇罗列，按**原文出现过的维度词**重组为维度小节
        （原文写「基于对齐」「基于正则化」，维度就叫这个名字）。
        原文没给维度词时**不编维度名**：按原文动词（提出/验证/对比…）归类，
        并在 §A 与人工确认项里写明"原文缺维度词"。
2 补引：没有引用标记的论断句按三级判据补 bib 里的条目——
        ① 与本句词面重合的条目（题名/作者/刊名/关键词的二元组与词）
        ② 本稿别处已引的文献（只用于「研究空白」这类由已引工作推出的论断）
        ③ 都不命中则不补，记进人工确认项，不硬塞。
        补进去的每一处都在句内用〔补引 …〕标出，依据见 §B。
3 证据指向：贡献条目补 Fig./Table/Section 指向，只从**原稿真实出现的标记**里挑
        （标记所在原句与条目有词面重合才挂），挑不到就写"原文无对应标记，待作者补"，
        绝不编编号。依据见 §C。

不调用模型；不改写原句文字；不新增材料外事实。

用法：
  python compose.py --draft draft.md --bib refs.bib --out revised.md [--json facts.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SECTIONS = ["研究背景", "相关工作", "研究空白", "贡献"]
# 维度词：原文写成"基于X/采用X/面向X"时，维度就叫"基于X"（基于优先，避免"使用基于X"取到"使用"）
CUE_ORDER = ("基于", "采用", "使用", "利用", "面向", "针对")
CUE_OBJ_STOP = re.compile(r"的|方法|策略|框架|模型|机制|算法|技术|方案|思路|[，,。；;：:（(]")
CUE_COMPARE = re.compile(r"前者|后者|相比之下|相较之下")
CUE_VERB = [("提出", "方法提出"), ("设计", "方案设计"), ("构建", "模型构建"),
            ("训练", "训练策略"), ("验证", "实验验证"), ("评测", "评测方式"),
            ("评估", "评测方式"), ("对比", "对比分析"), ("比较", "对比分析"),
            ("扩展", "方法扩展")]
CITE = re.compile(r"\[([^\]]+)\]|\\cite\{([^}]*)\}")
EVID = re.compile(r"\b(Fig(?:ure)?\.?\s*\d+|Table\s*\d+|Section\s*\d+(?:\.\d+)*)\b", re.I)
CLAUSE_SEP = "，,：:；;"
PUNCT = "。！？；!?;"
EN_STOP = set("""a an the of in on for to with and or by from at as is are was were be been this
that these those using based via under over between among into its their our we study research
method methods approach results effect new novel review case cases toward""".split())


# ------------------------------------------------------------------ 基础解析
def read_text(p) -> str:
    return Path(p).read_text(encoding="utf-8-sig", errors="replace")


def parse_bib(text: str) -> dict:
    """BibTeX → {key: {字段}}。花括号配平，值里的逗号不会切坏。"""
    out: dict[str, dict] = {}
    for m in re.finditer(r"@(\w+)\s*\{([^,\s]*)\s*,", text):
        i, depth = m.end(), 1
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[m.end():i - 1]
        fields = {k.lower(): v.strip() for k, v in re.findall(r"(\w+)\s*=\s*\{(.*?)\}", body, re.S)}
        fields["type"] = m.group(1)
        out[m.group(2).strip()] = fields
    return out


def split_sections(draft: str) -> dict:
    """按标题关键词切四段；找不到的段留空串。

    所有章节都用二级标题时按 `##` 切，否则退回 1–3 级——避免把
    "# 引言与相关工作（改写稿）" 这类总标题误当成「相关工作」段。
    """
    got = {}
    lvl2 = any(re.search(rf"^##\s*.*{n}.*$", draft, re.M) for n in SECTIONS)
    head = r"^##\s" if lvl2 else r"^#{1,3}\s"
    for name in SECTIONS:
        pat = rf"^##\s*.*{name}.*$" if lvl2 else rf"^#{{1,3}}\s*.*{name}.*$"
        m = re.search(pat, draft, re.M)
        if not m:
            got[name] = ""
            continue
        start = m.end()
        nxt = re.search(head, draft[start:], re.M)
        got[name] = draft[start:start + nxt.start()] if nxt else draft[start:]
    return got


def split_keep(text: str, seps: str, hard: bool = False) -> list[str]:
    """按 seps 断句/断分句，但方括号与花括号里的分隔符不算（`[@a; @b]` 不会被切开）。

    hard=True 时换行也断（用于整篇扫描）。
    """
    out, buf, sq, br = [], [], 0, 0
    for ch in text:
        if ch == "[":
            sq += 1
        elif ch == "]":
            sq = max(0, sq - 1)
        elif ch == "{":
            br += 1
        elif ch == "}":
            br = max(0, br - 1)
        if not sq and not br and (ch in seps or (hard and ch == "\n")):
            if ch != "\n":
                buf.append(ch)
            s = "".join(buf).strip()
            if s:
                out.append(s)
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def sentences(text: str) -> list[str]:
    return split_keep(text, PUNCT, hard=True)


def clauses(text: str) -> list[str]:
    return split_keep(text, CLAUSE_SEP)


def cite_keys(text: str) -> list[str]:
    keys = []
    for a, b in CITE.findall(text):
        for k in re.split(r"[,;\s]+", (a or b) or ""):
            k = k.strip().lstrip("@")
            if k:
                keys.append(k)
    return keys


def fmt_keys(keys) -> str:
    return "[" + "; ".join("@" + k for k in keys) + "]"


def zh_bigrams(text: str) -> set:
    out = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


def en_tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z][a-z\-]{3,}", text.lower()) if t not in EN_STOP}


def overlap(a: str, b: str) -> tuple[int, list[str]]:
    """两段文本的词面重合：中文二元组 + 英文词，返回命中数与命中词。"""
    hit = sorted((zh_bigrams(a) & zh_bigrams(b)) | (en_tokens(a) & en_tokens(b)))
    return len(hit), hit[:6]


def entry_profile(e: dict) -> str:
    return " ".join(str(e.get(k, "")) for k in ("title", "author", "journal", "keywords") if e.get(k))


# ------------------------------------------------------------------ 0 去套话
# 题面把"空转铺垫"点名为失分项；原稿里这类分句要真删掉，不能只在 result.json 里报。
CLICHE = [
    (r"近年来.{0,20}(受到|引起|备受|获得).{0,6}关注", "套话：近年来…受到关注"),
    (r"随着.{0,12}的(快速)?发展", "套话：随着…的发展"),
    (r"具有(重要|深远)意义", "套话：具有…意义"),
    (r"引起了?(广泛|极大)关注", "套话：引起关注"),
    (r"备受关注", "套话：备受关注"),
]


def despin(text: str) -> tuple[str, list[dict]]:
    """删套话分句：套话所在分句整句删掉；该分句若带引用，把引用抬成中性主语「相关研究」。

    只删空转铺垫，不动有内容的断言；每处改法逐条记进 records，改写稿 §F 里有表。
    """
    records: list[dict] = []
    out_lines = []
    for line in text.splitlines():
        if not line.strip():
            out_lines.append(line)
            continue
        rebuilt = []
        for sent in sentences(line):
            # 套话可能跨分句（"近年来，该方向受到广泛关注"），所以先在整句上定位命中区间，
            # 再把落在区间里的分句整块删掉，未命中的分句原样保留。
            spans = [(m.start(), m.end(), why) for pat, why in CLICHE
                     for m in re.finditer(pat, sent)]
            if not spans:
                rebuilt.append(sent)
                continue
            keep, dropped, keys = [], [], []
            pos = 0
            for cl in clauses(sent):
                idx = sent.find(cl, pos)
                if idx < 0:
                    idx = pos
                pos = idx + len(cl)
                if any(a < pos and idx < b for a, b, _ in spans):
                    why = next(w for a, b, w in spans if a < pos and idx < b)
                    dropped.append({"clause": cl.strip(), "rule": why})
                    keys.extend(cite_keys(cl))
                else:
                    keep.append(cl)
            if not dropped:
                rebuilt.append(sent)
                continue
            heads = [d for d in dropped if cite_keys(d["clause"])]
            pieces = []
            if keys:
                pieces.append("相关研究 " + fmt_keys(list(dict.fromkeys(keys))) + "，")
            tail = "".join(k.strip() for k in keep).strip()
            tail = re.sub(r"^[，,、；;：:]+", "", tail)
            if tail:
                pieces.append(tail)
            new = "".join(pieces).strip()
            new = re.sub(r"[，,、；;：:]\s*$", "", new)
            if not new:
                records.append({"原句": sent.strip(), "删除": [d["clause"] for d in dropped],
                                "规则": "、".join(d["rule"] for d in dropped),
                                "改后": "（整句删除：删完只剩套话，没有可留的断言）"})
                continue
            if not new.endswith(tuple(PUNCT)):
                new += "。"
            rules = "、".join(dict.fromkeys(d["rule"] for d in dropped))
            records.append({"原句": sent.strip(), "删除": [d["clause"] for d in dropped],
                            "规则": rules,
                            "改后": new,
                            "保留引用": list(dict.fromkeys(keys)),
                            "注": "套话分句带引用，引用抬成中性主语「相关研究」保留" if heads else ""})
            rebuilt.append(new)
        out_lines.append("".join(rebuilt) if rebuilt else line)
    return "\n".join(out_lines), records


# ------------------------------------------------------------------ 1 分组
def based_cue(clause: str) -> tuple[str, str] | None:
    """取原文的维度词短语：cue 优先取"基于"，宾语在「的/方法/策略…」处截断，不硬截字。"""
    for cue in CUE_ORDER:
        m = re.search(re.escape(cue) + r"([\u4e00-\u9fffA-Za-z0-9\-]{1,12})", clause)
        if not m:
            continue
        tail = m.group(1)
        cut = len(tail)
        for sm in CUE_OBJ_STOP.finditer(tail):
            if sm.start() >= 1:
                cut = min(cut, sm.start())
                break
        obj = tail[:cut]
        if len(obj) >= 2:
            return cue, obj
    return None


def dimension_of(clause: str) -> tuple[str, str]:
    """一句/一个分句 →（维度名，依据）。维度名一律取自原文。"""
    cue = based_cue(clause)
    if cue:
        return cue[0] + cue[1], f"原文维度词「{cue[0] + cue[1]}」"
    if CUE_COMPARE.search(clause):
        return "跨维度比较（前者/后者）", "原文用「前者/后者」承接比较，本身不是新维度"
    for verb, label in CUE_VERB:
        if verb in clause:
            return label, f"原文动词「{verb}」，原文未给「基于/采用 X」式维度词"
    return "未标维度", "原文无维度词也无归类动词"


def regroup(section_text: str) -> dict:
    """相关工作 → 维度分组。返回 {groups, statements, rows, note}。"""
    groups: dict[str, dict] = {}
    statements, rows = [], []
    for sent in sentences(section_text):
        cues = [c for c in (based_cue(x) for x in clauses(sent)) if c]
        cls = [c.strip() for c in clauses(sent) if c.strip()]
        if len(cues) >= 2 and len(cls) >= 2:
            statements.append(sent)
            prev = None
            for cl in cls:
                label, basis = dimension_of(cl)
                body = cl.rstrip(CLAUSE_SEP + " ").strip()
                keep = bool(cite_keys(cl)) or label != "未标维度"
                if not keep:
                    continue            # 纯引导语（如"按方法路线分为两类："），不是条目
                if label == "未标维度" and prev:
                    label, basis = prev, basis + "（承接上一个分句的维度）"
                prev = label
                g = groups.setdefault(label, {"label": label, "basis": basis, "items": []})
                g["items"].append(body)
                rows.append({"clause": body, "dim": label, "basis": basis,
                             "why": "原文一句并列多个维度词，按分句拆开"})
            continue
        label, basis = dimension_of(sent)
        g = groups.setdefault(label, {"label": label, "basis": basis, "items": []})
        g["items"].append(sent)
        rows.append({"clause": sent, "dim": label, "basis": basis, "why": "整句归入"})
    return {"groups": list(groups.values()), "statements": statements, "rows": rows}


# ------------------------------------------------------------------ 2 补引
def best_lexical(sent: str, bib: dict) -> tuple[str | None, str, list[str]]:
    best, best_key, best_terms = 0, None, []
    for key, e in bib.items():
        n, terms = overlap(sent, entry_profile(e))
        if n > best:
            best, best_key, best_terms = n, key, terms
    return best_key, (f"与 bib 条目 {best_key} 词面重合 {best} 处：{ '/'.join(best_terms) }"
                      if best_key else ""), best_terms


def add_cites(sections: dict, bib: dict, draft_keys: list[str]) -> dict:
    """给没有引用标记的论断句补引；返回 {by_section: {name: text}, records, pending}。"""
    records, pending = [], []
    out = {}
    for name in SECTIONS:
        body = sections.get(name, "")
        if not body.strip():
            out[name] = body
            continue
        if name == "贡献":
            out[name] = body          # 贡献是本文自己的工作，不补引他人文献
            continue
        new_lines = []
        for line in body.splitlines():
            if not line.strip():
                new_lines.append(line)
                continue
            sent_list = sentences(line)
            rebuilt = []
            for sent in sent_list:
                if cite_keys(sent):
                    rebuilt.append(sent)
                    continue
                key, basis, _ = best_lexical(sent, bib)
                rule = ""
                if key:
                    rule = "①词面相关"
                    keys = [key]
                elif name == "研究空白" and draft_keys:
                    rule = "②本稿已引文献（空白论断由已引工作推出）"
                    keys = draft_keys[:3]
                    basis = f"空白论断由本稿已引的 {len(keys)} 条文献推出：{', '.join(keys)}"
                else:
                    pending.append({"section": name, "sentence": sent,
                                    "why": "与库内任一条目词面重合 0，且不属可由已引工作推出的空白论断"})
                    rebuilt.append(sent)
                    continue
                core = sent.rstrip(PUNCT)
                tail = sent[len(core):]
                tag = fmt_keys(keys)
                rebuilt.append(f"{core} {tag}〔补引 B{len(records) + 1}〕{tail}")
                records.append({"section": name, "sentence": sent, "added": keys,
                                "rule": rule, "basis": basis,
                                "index": len(records) + 1})
            new_lines.append("".join(rebuilt) if rebuilt else line)
        out[name] = "\n".join(new_lines)
    return {"by_section": out, "records": records, "pending": pending}


# ------------------------------------------------------------------ 3 证据指向
def marker_contexts(draft: str) -> list[dict]:
    """全稿真实出现的 Fig./Table/Section 标记 + 所在句。"""
    out = []
    for sent in sentences(draft):
        for m in EVID.finditer(sent):
            out.append({"marker": re.sub(r"\s+", " ", m.group(1)), "sentence": sent.strip()})
    return out


def point_evidence(sections: dict, draft: str) -> dict:
    contrib = sections.get("贡献", "")
    items = [l for l in contrib.splitlines() if re.match(r"\s*(?:[-*]|\d+[.)])\s+", l)]
    ctx = marker_contexts(draft)
    records, pending, new_items = [], [], []
    for it in items:
        body = re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", it).strip()
        have = [m.group(1) for m in EVID.finditer(body)]
        if have:
            records.append({"item": body, "marker": "、".join(have), "basis": "原文自带，未改",
                            "index": len(records) + 1})
            new_items.append(it)
            continue
        best, best_c, best_terms = 0, None, []
        for c in ctx:
            if c["sentence"] == body:
                continue
            n, terms = overlap(body, c["sentence"])
            if n > best:
                best, best_c, best_terms = n, c, terms
        if best_c and best >= 2:
            new_items.append(f"{it.rstrip().rstrip(PUNCT)}，见 {best_c['marker']}"
                             f"〔补指向 C{len(records) + 1}〕。")
            records.append({"item": body, "marker": best_c["marker"],
                            "basis": f"沿用原文标记「{best_c['marker']}」，所在原句与本条词面重合 "
                                     f"{best} 处：{'/'.join(best_terms)}",
                            "index": len(records) + 1})
        else:
            new_items.append(f"{it.rstrip()}（原文无对应 Fig./Table/Section 标记，待作者补，"
                             f"不编编号）")
            pending.append({"section": "贡献", "sentence": body,
                            "why": "原文没有任何可挂的 Fig./Table/Section 标记"
                                   if not ctx else
                                   f"原文标记（{'、'.join(sorted({c['marker'] for c in ctx}))}）"
                                   "与本条词面重合不足，未挂"})
    return {"items": new_items, "records": records, "pending": pending}


# ------------------------------------------------------------------ 组装
INDEX_CH = "ABCDEFGH"


def sections_out(sections: dict, grp: dict, cites: dict, evid: dict) -> dict:
    """改稿里四段正文的实际文本（不含附录）。核验改稿时按这四段跑判据。"""
    rel = []
    if grp["statements"]:
        rel += [f"> 原文分组陈述（未改字）：{s}" for s in grp["statements"]] + [""]
    if grp["groups"]:
        rel += [f"相关工作按方法路线分为 {len(grp['groups'])} 组，逐组列出：", ""]
    for i, g in enumerate(grp["groups"], 1):
        rel.append(f"### 维度 {i}：{g['label']}")
        rel.append(f"*依据：{g['basis']}；本组 {len(g['items'])} 句、"
                   f"{len({k for it in g['items'] for k in cite_keys(it)})} 条引用*")
        rel.append("")
        rel += [f"- {it}" for it in g["items"]]
        rel.append("")
    return {
        "研究背景": cites["by_section"].get("研究背景", "").strip(),
        "相关工作": "\n".join(rel).strip(),
        "研究空白": cites["by_section"].get("研究空白", "").strip(),
        "贡献": "\n".join(evid["items"]).strip(),
    }


def render(sections: dict, grp: dict, cites: dict, evid: dict, bib: dict,
           draft: str, extra_pending: list[str] | None = None,
           spin_rows: list[dict] | None = None) -> str:
    L = ["# 引言与相关工作（改写稿）", "",
         "> 由 `compose.py` 依原稿与 bib 生成：做去套话、分组、补引、证据指向四件事，不改写有内容的原句。",
         "> 句中〔补引 Bn〕〔补指向 Cn〕是工具插入的标注，编号对应文末 §B/§C 的表；定稿前逐条处理。", ""]
    out_secs = sections_out(sections, grp, cites, evid)

    # 研究背景（原句保留，可能已去套话并补引）
    L += ["## 研究背景", "", out_secs["研究背景"] or "> 待补", ""]

    # 相关工作（分组）
    L += ["## 相关工作", ""]
    L += out_secs["相关工作"].splitlines() if out_secs["相关工作"] else ["> 待补：相关工作段为空"]
    L.append("")

    # 研究空白 / 贡献
    L += ["## 研究空白", "", out_secs["研究空白"] or "> 待补", ""]
    L += ["## 贡献", ""]
    L += out_secs["贡献"].splitlines() if out_secs["贡献"] else ["> 待补：贡献段没有分条"]
    L.append("")

    # 附录：三段依据
    L += ["---", "",
          "## A 分组依据（相关工作逐句 → 维度）", "",
          "| # | 原句/分句 | 维度 | 依据 | 处理 |", "|-|-|-|-|-|"]
    for i, r in enumerate(grp["rows"], 1):
        L.append(f"| {i} | {r['clause'].replace('|', '/')} | {r['dim']} | {r['basis']} | {r['why']} |")
    L += ["", "## B 补引记录（文中〔补引 n〕对应此处编号）", ""]
    if cites["records"]:
        L += ["| # | 段落 | 补入条目 | 判据 | 依据 | 原句 |", "|-|-|-|-|-|-|"]
        for r in cites["records"]:
            L.append(f"| B{r['index']} | {r['section']} | "
                     f"{', '.join('@' + k for k in r['added'])} | {r['rule']} | "
                     f"{r['basis'].replace('|', '/')} | {r['sentence'].replace('|', '/')} |")
    else:
        L.append("（本次没有可补的论断：原文论断要么已带引用，要么词面无法与库内条目对上。）")
    L += ["", "## C 贡献证据指向", ""]
    if evid["records"]:
        L += ["| # | 贡献条目 | 指向 | 依据 |", "|-|-|-|-|"]
        for r in evid["records"]:
            L.append(f"| C{r['index']} | {r['item'].replace('|', '/')} | {r['marker']} | "
                     f"{r['basis'].replace('|', '/')} |")
    else:
        L.append("（贡献段没有分条，无条目可挂。）")
    L += ["", "## D 人工确认项（工具不替你决定）", ""]
    pend = list(extra_pending or [])
    pend += [f"{p['section']}：{p['sentence']} —— {p['why']}"
             for p in cites["pending"] + evid["pending"]]
    if pend:
        for i, p in enumerate(pend, 1):
            L.append(f"{i}. {p}")
    else:
        L.append("（无。）")
    L += ["", "## E 本次用到的 bib 条目", "",
          "| 键 | 题名 | 作者 | 年 | 字段是否齐 |", "|-|-|-|-|-|"]
    for k, e in bib.items():
        ok = all(e.get(f) for f in ("author", "year", "title"))
        L.append(f"| {k} | {e.get('title', '—')} | {e.get('author', '—')} | "
                 f"{e.get('year', '—')} | {'齐' if ok else '缺字段'} |")
    if not bib:
        L.append("| — | — | — | — | bib 为空 |")
    L += ["", "## F 去套话记录", ""]
    if spin_rows:
        L += ["| # | 段 | 删掉的分句 | 命中规则 | 改后 |", "|-|-|-|-|-|"]
        for i, r in enumerate(spin_rows, 1):
            L.append(f"| F{i} | {r.get('section', '')} | "
                     f"{'；'.join(r.get('删除') or []).replace('|', '/')} | {r.get('规则', '')} | "
                     f"{str(r.get('改后', '')).replace('|', '/')} |")
    else:
        L.append("（原文没有命中套话表的分句，未删改。）")
    L.append("")
    return "\n".join(L)


def build(draft_text: str, bib_text: str) -> dict:
    """原稿 + bib → 改写稿与逐条依据（供 run.py 与 CLI 共用）。"""
    bib = parse_bib(bib_text)
    raw = split_sections(draft_text)
    # 先删套话分句，再做分组/补引/证据指向：套话留在句中会把分组与补引判据带偏
    secs, spin_rows = {}, []
    for name in SECTIONS:
        clean, rows = despin(raw.get(name, ""))
        secs[name] = clean
        for r in rows:
            spin_rows.append({"section": name, **r})
    draft_keys = list(dict.fromkeys(cite_keys("\n".join(secs.values()))))
    grp = regroup(secs.get("相关工作", ""))
    cites = add_cites(secs, bib, draft_keys)
    evid = point_evidence(secs, draft_text)

    # 分组维度不足时的提醒要写进改写稿 §D，不能只留在 result.json 里
    dim_note = (f"相关工作只落进 1 个维度（{grp['groups'][0]['label']}）：原文没有可用的维度词，"
                "本工具不编维度名，需作者补「基于/采用 X」式表述"
                if len(grp["groups"]) == 1 else None)
    md = render(secs, grp, cites, evid, bib, draft_text,
                extra_pending=[dim_note] if dim_note else [],
                spin_rows=spin_rows)

    sites = []
    for name in SECTIONS:
        for sent in sentences(secs.get(name, "")):
            for k in cite_keys(sent):
                sites.append({"key": k, "section": name, "sentence": sent.strip()[:80]})

    one_dim = len(grp["groups"]) == 1
    unresolved = []
    if one_dim:
        unresolved.append(f"相关工作只落进 1 个维度（{grp['groups'][0]['label']}）："
                          "原文没有可用的维度词，本工具不编维度名，需作者补「基于/采用 X」式表述")
    unresolved += [f"{p['section']}：{p['sentence'][:40]} —— {p['why']}"
                   for p in cites["pending"] + evid["pending"]]
    return {
        "revised_md": md,
        "audit_sections": sections_out(secs, grp, cites, evid),
        "groups": [{"label": g["label"], "n_items": len(g["items"]), "basis": g["basis"]}
                   for g in grp["groups"]],
        "statements": grp["statements"],
        "group_rows": grp["rows"],
        "despin_rows": spin_rows,
        "cites_added": cites["records"],
        "cites_pending": cites["pending"],
        "evidence_rows": evid["records"],
        "evidence_pending": evid["pending"],
        "bib": bib,
        "bib_keys": sorted(bib),
        "cite_sites": sites,
        "unresolved": unresolved,
        "stats": {"n_groups": len(grp["groups"]), "n_group_sentences": len(grp["rows"]),
                  "n_despin": len(spin_rows),
                  "n_cites_added": len(cites["records"]),
                  "n_evidence_added": sum(1 for r in evid["records"]
                                          if "原文自带" not in r["basis"]),
                  "n_pending": len(unresolved)},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="原稿 + bib → 改写稿 revised.md")
    ap.add_argument("--draft", required=True)
    ap.add_argument("--bib", required=True)
    ap.add_argument("--out", default="revised.md")
    ap.add_argument("--json", default=None, help="把逐条依据写成 JSON")
    a = ap.parse_args(argv)
    facts = build(read_text(a.draft), read_text(a.bib))
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(facts["revised_md"], encoding="utf-8")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(
            json.dumps({k: v for k, v in facts.items()
                        if k not in ("revised_md", "bib")},
                       ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(p), "bytes": p.stat().st_size,
                      **facts["stats"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
