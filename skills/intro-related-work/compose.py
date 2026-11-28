#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compose.py — 依原稿与 bib 产出改好的稿 revised.md，以及逐条可回指原句的依据表。

做六件事，每件都在正文或附录里留痕：

1 去套话：删除"近年来…受到广泛关注"式空转分句，套话分句带的引用抬成「相关研究 [@…]」保留，
  逐条记进 §F（带原稿行号）。
2 分组：相关工作按**原文出现过的维度词**重组（原文写「基于对齐」维度就叫这个）；原文没有维度词
  就不编维度名，退回原文动词归组，并写进 §D。逐句依据进 §A（带原稿行号）。
3 补引：没有引用标记的论断句按三级判据补 bib 里已有的条目——① 与本句词面重合 **≥2 处**的条目
  （阈值收紧，避免一词巧合就挂错引用）② 本稿别处已引文献（只用于「研究空白」）③ 都不命中不补，
  记进 §D。每处句内标〔补引 Bn〕，依据见 §B。
4 证据指向：贡献条目补 Fig./Table/Section 指向，只从**原稿真实出现过的标记**里挑，且标记原句与
  条目词面重合 ≥2 才挂；挑不到写"待作者补"，不编编号。依据见 §C。
5 转述对照：相关工作逐条抽出「结论 / 适用条件 / 局限」（全部照抄原文分句），同一组内逐句可比，
  见 §A2；缺哪一项标"原文未写"，不补写。
6 空白推导：研究空白段的每条论断 → 依据的已引工作 → 该工作在本稿记录过的局限 → 对应贡献条目，
  见 §G；追不到局限的标"未推导"并进 §D。

不调用模型；不改写有内容的原句；不新增材料外事实；不编编号、不编条目字段。

用法：
  python compose.py --draft draft.md --bib refs.bib --out revised.md [--json facts.json]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

VERSION = "2.0.0"
SECTIONS = ["研究背景", "相关工作", "研究空白", "贡献"]

# 章节别名：中文/英文混排都认（题面异常处理点名「语言切换」）
SECTION_ALIASES = {
    "研究背景": ("研究背景", "研究背景与问题", "背景与问题", "背景", "引言", "introduction", "background"),
    "相关工作": ("相关工作", "相关研究", "related work", "related works", "literature review"),
    "研究空白": ("研究空白", "研究缺口", "空白与缺口", "空白", "研究动机", "research gap",
              "gap", "problem statement"),
    "贡献": ("本文贡献", "贡献", "contributions", "contribution"),
}
# 长别名优先：`## 引言与相关工作` 归「相关工作」而不是「研究背景」
ALIAS_ORDER = sorted(((a.lower(), n) for n, al in SECTION_ALIASES.items() for a in al),
                     key=lambda x: -len(x[0]))

CITE = re.compile(r"\[([^\]]+)\]|\\cite\{([^}]*)\}")
EVID = re.compile(r"(Fig(?:ure)?\.?\s*\d+[a-z]?|Table\s*\d+[a-z]?|Section\s*\d+(?:\.\d+)*|式\s*\(?\d+\)?)", re.I)
NUM = re.compile(r"(?<![A-Za-z0-9])(\d+(?:\.\d+)?\s*[%％]?)(?![A-Za-z0-9])")
# 中文数量词也算数字（「在三个数据集上验证」这种写法题面同样要来源）
NUM_ZH = re.compile(r"([一二三四五六七八九十百千两]{1,3})\s*(个|类|种|条|组|项|阶段|次|轮|倍|层|维|套)"
                    r"(?:数据集|基线|指标|场景|任务|模型)?")
TERM_GLOSS = re.compile(r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9\-]{1,24})\s*[（(]\s*([^）)]{2,60})[）)]")
ABBR = re.compile(r"(?<![A-Za-z])([A-Z]{2,6})(?![A-Za-z])")
ABBR_STOP = {"FIG", "TABLE", "SECTION", "DOI", "URL", "PDF", "ET", "AL", "EQ", "REF"}
CLAUSE_SEP = "，,：:；;、"
PUNCT = "。！？；!?;"
EN_SENT = re.compile(r"(?<=[A-Za-z0-9\)\]\"'])\.\s+(?=[A-Z(\"'])")
EN_STOP = set("""a an the of in on for to with and or by from at as is are was were be been this
that these those using based via under over between among into its their our we study research
method methods approach approaches results effect effects new novel review case cases toward
however but only also such can may show shows propose proposes present presents find finds""".split())

# 套话：题面点名「近年来受到广泛关注」式空转铺垫
CLICHE = [
    (r"近年来.{0,20}(受到|引起|备受|获得).{0,6}关注", "套话：近年来…受到关注"),
    (r"随着.{0,12}的(快速)?发展", "套话：随着…的发展"),
    (r"具有(重要|深远)意义", "套话：具有…意义"),
    (r"引起了?(广泛|极大)关注", "套话：引起关注"),
    (r"备受关注", "套话：备受关注"),
    (r"in recent years.{0,40}(attracted|drawn).{0,20}attention", "套话：in recent years…attention"),
]

# 转述对照三要素的判别词（只用来定位原文分句，判断结果逐句照抄原文）
LIMIT_CUE = re.compile(
    r"但|然而|不过|仅|只|依赖|退化|失效|未能|无法|缺失|不足|受限|难以|局限|受制|代价|开销|"
    r"but\b|however|only|rel(?:y|ies|iant)|degrad|fail|cannot|limitation|limited|scarce|costly", re.I)
COND_CUE = re.compile(
    r"在.{0,14}(?:下|时|场景|条件|情形|设置)|适用于|面向|针对|用于|当.{0,10}时|"
    r"\bunder\b|\bwhen\b|applies to|\bfor low|\bin low|\bon [a-z]", re.I)
CLAIM_CUE = re.compile(
    r"表明|显示|证明|发现|提出|给出|报告|验证|show|demonstrat|find|report|propos|present|suggest", re.I)
# 作者判断标注（题面：「断言处是否有证据或明确标注为作者判断」）
AUTHOR_CUE = re.compile(r"本文|本工作|本研究|我们|作者|据笔者|this paper|this work|we\b|our\b|in our", re.I)
# 夸大表述（题面：「不夸大适用范围」）
OVERCLAIM = re.compile(r"首次|全部|所有|任何|完全|彻底|最优|唯一|最优越|first ever|all existing|any existing|"
                       r"state-of-the-art|universal|always", re.I)

VENUE_FIELDS = ("journal", "booktitle", "publisher", "school", "institution",
                "howpublished", "doi", "arxiv", "series")


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
        fields["type"] = m.group(1).lower()
        venue = ""
        for f in VENUE_FIELDS:
            if fields.get(f):
                venue = f
                break
        fields["venue"] = fields.get(venue, "") if venue else ""
        fields["venue_field"] = venue
        out[m.group(2).strip()] = fields
    return out


def entry_profile(e: dict) -> str:
    return " ".join(str(e.get(k, "")) for k in ("title", "author", "journal", "booktitle",
                                               "keywords", "venue") if e.get(k))


def entry_title(e: dict) -> str:
    return str(e.get("title", "") or "")


def bib_field_report(e: dict) -> dict:
    """题面「必要字段（作者、年份、来源）」逐项判定。"""
    got = {"author": bool(e.get("author")), "year": bool(e.get("year")),
           "title": bool(e.get("title")), "来源": bool(e.get("venue"))}
    return {"got": got, "missing": [k for k, v in got.items() if not v]}


# ------------------------------------------------------------------ 切章
HEAD = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.M)


def _headings(draft: str) -> list[dict]:
    out = []
    for m in HEAD.finditer(draft):
        line = draft[:m.start()].count("\n") + 1
        out.append({"level": len(m.group(1)), "text": m.group(2).strip(),
                    "line": line, "start": m.start(), "end": m.end()})
    return out


def _assign(draft: str, heads: list[dict], level: int) -> dict:
    """在指定标题级别上给每个标题找最长别名 → 该级别的章节归属。"""
    got: dict[str, dict] = {}
    for i, h in enumerate(heads):
        if h["level"] != level:
            continue
        low = h["text"].lower()
        name = next((n for a, n in ALIAS_ORDER if a in low), None)
        if not name or name in got:
            continue
        nxt_end = len(draft)
        for h2 in heads[i + 1:]:
            if h2["level"] <= level:
                nxt_end = h2["start"]
                break
        body = draft[h["end"]:nxt_end]
        base = h["line"]
        # body 以换行起头，第 k 个 splitlines 元素就是原稿第 base+k 行（不是 base+1+k）
        lines = [(base + k, t) for k, t in enumerate(body.splitlines())]
        got[name] = {"body": body, "heading": h["text"], "heading_line": h["line"],
                     "level": level, "lines": reflow(lines)}
    return got


def parse_sections(draft: str) -> dict:
    """四级标题（研究背景/相关工作/研究空白/贡献）定位，带原稿行号。

    先试二级标题，再退一级、三级：取能认出的部分最多的一层，避免
    `# 引言与相关工作（改写稿）` 这类总标题把整篇吞成一段。
    """
    heads = _headings(draft)
    best, best_level = {}, None
    for level in (2, 1, 3, 4):
        got = _assign(draft, heads, level)
        if len(got) > len(best):
            best, best_level = got, level
    order = sorted(best, key=lambda n: best[n]["heading_line"])
    return {"sections": best, "order": order, "level": best_level,
            "headings_seen": [(h["level"], h["text"], h["line"]) for h in heads]}


def split_sections(draft: str) -> dict:
    """兼容旧接口：{章节名: 正文}，缺段为空串。"""
    got = parse_sections(draft)["sections"]
    return {n: (got[n]["body"] if n in got else "") for n in SECTIONS}


# ------------------------------------------------------------------ 断句
def split_keep(text: str, seps: str, hard: bool = False) -> list[str]:
    """按 seps 断句/断分句，但方括号与花括号里的分隔符不算（`[@a; @b]` 不会被切开）。"""
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
    """中英混排断句：中文标点 + 英文 `句点+空格+大写`（`Fig. 2`／小数不会被切开）。

    英文分隔符是零宽 lookbehind + 句点 + 空白，切完要把句点带回去，否则 "two lines." 会丢点。
    """
    out = []
    for seg in split_keep(text, PUNCT, hard=True):
        last = 0
        pieces = []
        for m in EN_SENT.finditer(seg):
            pieces.append(seg[last:m.start() + 1])   # 含句点
            last = m.end()
        pieces.append(seg[last:])
        out.extend(p.strip() for p in pieces if p.strip())
    return out


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


def cell(s) -> str:
    return str(s).replace("|", "/").replace("\n", " ")


# 工具自己的脚手架行（分组小标题、"依据：…"、编号说明）不是稿子的正文，判据不该拿它们当论断
SCAFFOLD = re.compile(r"^(?:相关工作按原文维度|按原文维度分为|\*依据：|维度\s*\d+\s*：|逐组列出|"
                      r"本组\s*\d+\s*句)")


def is_prose(line: str) -> bool:
    s = line.lstrip()
    if not s or s.startswith(("#", ">", "|", "```", "*")):
        return False
    return not SCAFFOLD.search(s)


def join_parts(parts: list[str]) -> str:
    """把断句后的片段拼回一行：中文直接连，英文两句之间补一个空格（别拼成 `lines.The`）。"""
    out = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and out[-1].isascii() and out[-1] not in "，。；：、 " and p[:1].isascii():
            out += " "
        out += p
    return out


BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
TERMINAL = re.compile(r"[。！？；!?;.…]\s*$")


def reflow(lines: list[tuple[int, str]]) -> list[tuple[int | str, str]]:
    """把硬换行折起来的段落合回一行（Markdown 里换行不等于断句）。

    行号取首末两行（`L10–11`）。列表项、标题、引用块、表格行不参与合并。
    """
    out: list[tuple[int | str, str]] = []
    for no, raw in lines:
        if (out and is_prose(raw) and not BULLET.match(raw)
                and is_prose(out[-1][1]) and not BULLET.match(out[-1][1])
                and not TERMINAL.search(out[-1][1])):
            pno, ptext = out[-1]
            head = str(pno).split("–")[0]
            sep = " " if (ptext.rstrip()[-1:].isascii() and raw.lstrip()[:1].isascii()) else ""
            out[-1] = (f"{head}–{no}", ptext.rstrip() + sep + raw.lstrip())
            continue
        out.append((no, raw))
    return out


# ------------------------------------------------------------------ 1 去套话
def despin_lines(lines: list[tuple[int, str]]) -> tuple[list[tuple[int, str]], list[dict]]:
    """删套话分句（逐行），返回新行与记录；记录带原稿行号。"""
    records: list[dict] = []
    out: list[tuple[int, str]] = []
    for no, line in lines:
        if not is_prose(line):
            out.append((no, line))
            continue
        rebuilt = []
        for sent in sentences(line):
            # 套话可能跨分句（"近年来，该方向受到广泛关注"），先在整句上定位命中区间，
            # 再把落在区间里的分句整块删掉，未命中的分句原样保留。
            spans = [(m.start(), m.end(), why) for pat, why in CLICHE
                     for m in re.finditer(pat, sent, re.I)]
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
                records.append({"line": no, "原句": sent.strip(),
                                "删除": [d["clause"] for d in dropped],
                                "规则": "、".join(d["rule"] for d in dropped),
                                "改后": "（整句删除：删完只剩套话，没有可留的断言）"})
                continue
            if not new.endswith(tuple(PUNCT)):
                new += "。"
            rules = "、".join(dict.fromkeys(d["rule"] for d in dropped))
            records.append({"line": no, "原句": sent.strip(),
                            "删除": [d["clause"] for d in dropped], "规则": rules, "改后": new,
                            "保留引用": list(dict.fromkeys(keys)),
                            "注": "套话分句带引用，引用抬成中性主语「相关研究」保留" if heads else ""})
            rebuilt.append(new)
        out.append((no, join_parts(rebuilt) if rebuilt else line))
    return out, records


# ------------------------------------------------------------------ 2 分组
CUE_ORDER = ("基于", "采用", "使用", "利用", "面向", "针对")
# 只认这四个当"路线维度词"：「面向/针对」多半在说适用条件，不是技术路线
ROUTE_CUE = ("基于", "采用", "使用", "利用")
# 一句里显式枚举时才按分句拆组（「分为两类：一类…另一类…」），否则整句归一句的维度
ENUM = re.compile(r"分为|一类|另一类|前者|后者|第[一二三四五六七八九]条|第[一二三四五六七八九]类|"
                  r"two (lines|families|groups)|three (lines|families|groups)", re.I)
CUE_OBJ_STOP = re.compile(r"的|方法|策略|框架|模型|机制|算法|技术|方案|思路|[，,。；;：:（(]")
CUE_COMPARE = re.compile(r"前者|后者|相比之下|相较之下|the former|the latter")
CUE_VERB = [("提出", "方法提出"), ("设计", "方案设计"), ("构建", "模型构建"),
            ("训练", "训练策略"), ("验证", "实验验证"), ("评测", "评测方式"),
            ("评估", "评测方式"), ("对比", "对比分析"), ("比较", "对比分析"),
            ("扩展", "方法扩展"), ("propose", "方法提出"), ("present", "方法提出"),
            ("train", "训练策略"), ("evaluat", "评测方式"), ("compar", "对比分析"),
            ("extend", "方法扩展"), ("design", "方案设计")]


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
    low = clause.lower()
    for verb, label in CUE_VERB:
        if verb in clause or verb in low:
            return label, f"原文动词「{verb}」，原文未给「基于/采用 X」式维度词"
    return "未标维度", "原文无维度词也无归类动词"


def regroup(lines: list[tuple[int, str]]) -> dict:
    """相关工作 → 维度分组（带行号）。

    分组单位是**句子**：一句一个维度。只有句子显式枚举路线（「分为两类：一类…另一类…」
    「前者/后者」）时才按分句拆开。这样「第一条基于平行语料对齐：…」不会因为后半句出现
    「面向…」而被拆成两个维度。
    """
    groups: dict[str, dict] = {}
    statements, rows = [], []
    prev_label: str | None = None
    for no, raw in lines:
        if not is_prose(raw):
            continue
        for sent in sentences(raw):
            cls = [c.strip() for c in clauses(sent) if c.strip()]
            cues = [c for c in (based_cue(x) for x in cls) if c and c[0] in ROUTE_CUE]
            if ENUM.search(sent) and len(cues) >= 2 and len(cls) >= 2:
                statements.append({"line": no, "sentence": sent})
                local = None
                for cl in cls:
                    label, basis = dimension_of(cl)
                    body = cl.rstrip(CLAUSE_SEP + " ").strip()
                    if not (cite_keys(cl) or label != "未标维度"):
                        rows.append({"line": no, "clause": body, "dim": "（引导语）",
                                     "basis": "无引用、无维度词", "why": "引导语，不单独成组"})
                        continue        # 纯引导语（如"按方法路线分为两类："）
                    if label == "未标维度" and local:
                        label, basis = local, basis + "（承接上一个分句的维度）"
                    local = label
                    g = groups.setdefault(label, {"label": label, "basis": basis, "items": []})
                    g["items"].append(body)
                    rows.append({"line": no, "clause": body, "dim": label, "basis": basis,
                                 "why": "原文一句显式枚举多个维度，按分句拆开"})
                prev_label = local or prev_label
                continue
            label, basis = dimension_of(sent)
            if label == "未标维度" and not cite_keys(sent):
                rows.append({"line": no, "clause": sent.strip(), "dim": "（引导语）",
                             "basis": "无引用、无维度词",
                             "why": "引导语，不单独成组"})
                continue
            if label == "未标维度" and prev_label:
                label, basis = prev_label, (f"原文未另给维度词，沿用上一句维度「{prev_label}」")
            g = groups.setdefault(label, {"label": label, "basis": basis, "items": []})
            g["items"].append(sent)
            rows.append({"line": no, "clause": sent, "dim": label, "basis": basis,
                         "why": "整句归入"})
            prev_label = label
    return {"groups": list(groups.values()), "statements": statements, "rows": rows}


# ------------------------------------------------------------------ 5 转述对照
def _first_clause(cue, sent: str) -> str:
    for cl in clauses(sent):
        if cue.search(cl):
            return cl.strip().rstrip(CLAUSE_SEP + " ").strip()
    if cue is not LIMIT_CUE and cue is not COND_CUE and cue.search(sent):
        return sent.strip()
    return "原文未写"


def critique(lines: list[tuple[int, str]], groups: dict | None = None) -> list[dict]:
    """相关工作逐句 →（结论 / 适用条件 / 局限）。三要素全部照抄原文分句，不补写。

    一句里并列多个工作时按分句拆行：每个带引用键的分句单独一行（同一维度内逐工作可比），
    一句只有一个引用时按整句一行。
    """
    items = [(g["label"], it) for g in (groups or {}).get("groups", []) for it in g["items"]]
    rows = []
    for no, raw in lines:
        if not is_prose(raw):
            continue
        for sent in sentences(raw):
            keys_all = list(dict.fromkeys(cite_keys(sent)))
            if not keys_all:
                continue
            pieces = [c.strip() for c in clauses(sent) if c.strip()]
            per = [(c, list(dict.fromkeys(cite_keys(c)))) for c in pieces]
            per = [(c, ks) for c, ks in per if ks]
            if len(keys_all) < 2 or not per:
                per = [(sent, keys_all)]
            for unit, keys in per:
                cond = _first_clause(COND_CUE, unit)
                lim = _first_clause(LIMIT_CUE, unit)
                if lim == cond and lim != "原文未写":
                    lim = "原文未写"      # 同一分句既是条件又是局限时不重复记
                claim = _first_clause(CLAIM_CUE, unit)
                if claim == "原文未写":
                    claim = unit.strip().rstrip(PUNCT + CLAUSE_SEP) + "（照抄分句）"
                best_dim, best_n = "未在分组内", 0
                for label, it in items:
                    n, _ = overlap(unit, it)
                    if n > best_n:
                        best_dim, best_n = label, n
                lack = [n for n, v in (("结论", claim), ("适用条件", cond), ("局限", lim))
                        if v.startswith("原文未写")]
                rows.append({"line": no, "keys": keys, "dim": best_dim, "sentence": sent.strip(),
                             "unit": unit.strip(), "claim": claim, "cond": cond, "limit": lim,
                             "missing": lack,
                             "verdict": "三要素齐" if not lack else "缺" + "、".join(lack)})
    return rows


# ------------------------------------------------------------------ 3 补引
def best_lexical(sent: str, bib: dict) -> tuple[str | None, str, list[str], int]:
    best, best_key, best_terms = 0, None, []
    for key, e in bib.items():
        n, terms = overlap(sent, entry_profile(e))
        if n > best:
            best, best_key, best_terms = n, key, terms
    if best_key:
        basis = f"与 bib 条目 {best_key} 词面重合 {best} 处：{'/'.join(best_terms)}"
    else:
        basis = ""
    return best_key, basis, best_terms, best


MIN_OVERLAP = 2      # 收紧：1 处词面重合不足以挂引用


def add_cites(lines_by_section: dict, bib: dict, draft_keys: list[str]) -> dict:
    """给没有引用标记的论断句补引；返回 {by_section(行表), records, pending}。"""
    records, pending = [], []
    out = {}
    for name in SECTIONS:
        lines = lines_by_section.get(name, [])
        if name == "贡献":
            out[name] = list(lines)      # 贡献是本文自己的工作，不补引他人文献
            continue
        new_lines = []
        for no, line in lines:
            if not is_prose(line):
                new_lines.append((no, line))
                continue
            rebuilt = []
            for sent in sentences(line):
                if cite_keys(sent) or EVID.search(sent):
                    rebuilt.append(sent)
                    continue
                key, basis, _, n_hit = best_lexical(sent, bib)
                rule = ""
                if key and n_hit >= MIN_OVERLAP:
                    rule = f"①词面相关（重合≥{MIN_OVERLAP}）"
                    keys = [key]
                elif name == "研究空白" and draft_keys:
                    rule = "②本稿已引文献（空白论断由已引工作推出）"
                    keys = draft_keys[:3]
                    basis = f"空白论断由本稿已引的 {len(keys)} 条文献推出：{', '.join(keys)}"
                else:
                    pending.append({"section": name, "line": no, "sentence": sent,
                                    "why": (f"与库内任一条目词面重合最高 {n_hit} 处，低于阈值 "
                                            f"{MIN_OVERLAP}，不硬挂")
                                    if n_hit else
                                    "与库内任一条目词面重合 0，且不属可由已引工作推出的空白论断"})
                    rebuilt.append(sent)
                    continue
                core = sent.rstrip(PUNCT)
                tail = sent[len(core):]
                tag = fmt_keys(keys)
                rebuilt.append(f"{core} {tag}〔补引 B{len(records) + 1}〕{tail}")
                records.append({"index": len(records) + 1, "section": name, "line": no,
                                "sentence": sent, "added": keys, "rule": rule, "basis": basis})
            new_lines.append((no, join_parts(rebuilt) if rebuilt else line))
        out[name] = new_lines
    return {"by_section": out, "records": records, "pending": pending}


# ------------------------------------------------------------------ 4 证据指向
def marker_contexts(draft: str) -> list[dict]:
    """全稿真实出现的 Fig./Table/Section 标记 + 所在句 + 行号。"""
    out = []
    for no, raw in reflow(list(enumerate(draft.splitlines(), 1))):
        for sent in sentences(raw):
            for m in EVID.finditer(sent):
                out.append({"marker": re.sub(r"\s+", " ", m.group(1)), "sentence": sent.strip(),
                            "line": no})
    return out


def contribution_items(lines: list[tuple[int, str]]) -> list[dict]:
    out = []
    for no, raw in lines:
        m = re.match(r"\s*(?:[-*]|\d+[.)])\s+(.*)", raw)
        if m:
            out.append({"line": no, "item": m.group(1).strip()})
    return out


def point_evidence(lines: list[tuple[int, str]], draft: str) -> dict:
    items = contribution_items(lines)
    ctx = marker_contexts(draft)
    records, pending, new_items = [], [], []
    for it in items:
        body = it["item"]
        have = [m.group(1) for m in EVID.finditer(body)]
        if have:
            records.append({"index": len(records) + 1, "line": it["line"], "item": body,
                            "marker": "、".join(have), "basis": "原文自带，未改"})
            new_items.append({"line": it["line"], "text": "- " + body})
            continue
        best, best_c, best_terms = 0, None, []
        for c in ctx:
            if c["sentence"] == body:
                continue
            n, terms = overlap(body, c["sentence"])
            if n > best:
                best, best_c, best_terms = n, c, terms
        if best_c and best >= MIN_OVERLAP:
            new_items.append({"line": it["line"],
                              "text": f"- {body.rstrip().rstrip(PUNCT)}，见 {best_c['marker']}"
                                      f"〔补指向 C{len(records) + 1}〕。"})
            records.append({"index": len(records) + 1, "line": it["line"], "item": body,
                            "marker": best_c["marker"],
                            "basis": f"沿用原文标记「{best_c['marker']}」（原稿 L{best_c['line']}），"
                                     f"所在原句与本条词面重合 {best} 处：{'/'.join(best_terms)}"})
        else:
            new_items.append({"line": it["line"],
                              "text": f"- {body.rstrip()}（原文无对应 Fig./Table/Section 标记，"
                                      f"待作者补，不编编号）"})
            pending.append({"section": "贡献", "line": it["line"], "sentence": body,
                            "why": "原文没有任何可挂的 Fig./Table/Section 标记"
                                   if not ctx else
                                   f"原文标记（{'、'.join(sorted({c['marker'] for c in ctx}))}）"
                                   "与本条词面重合不足，未挂"})
    return {"items": new_items, "records": records, "pending": pending}


# ------------------------------------------------------------------ 6 空白推导
def gap_chain(gap_lines: list[tuple[int, str]], crit: list[dict],
              contrib: list[dict]) -> list[dict]:
    """研究空白逐句 → 已引工作 → 该工作在本稿记录的局限 → 对应贡献条目。"""
    limit_by_key: dict[str, list[dict]] = {}
    for r in crit:
        if r["limit"] != "原文未写":
            for k in r["keys"]:
                limit_by_key.setdefault(k, []).append(r)
    rows = []
    for no, raw in gap_lines:
        if not is_prose(raw):
            continue
        for sent in sentences(raw):
            keys = list(dict.fromkeys(cite_keys(sent)))
            if not keys:
                rows.append({"line": no, "sentence": sent.strip(), "keys": [],
                             "evidence": [], "contrib": None,
                             "verdict": "未推导：本条空白没有引用任何已引工作（C05）"})
                continue
            ev = []
            for k in keys:
                for r in limit_by_key.get(k, []):
                    ev.append({"key": k, "limit": r["limit"], "line": r["line"],
                               "sentence": r["sentence"]})
            best, best_item, best_n = 0, None, 0
            for c in contrib:
                n, _ = overlap(sent, c["item"])
                if n > best:
                    best, best_item, best_n = n, c, n
            if ev:
                verdict = (f"成立：{len({e['key'] for e in ev})}/{len(keys)} 条已引工作在本稿记录了局限")
            else:
                verdict = (f"未推导：引用了 {len(keys)} 条文献，但本稿没有记录它们的局限"
                           f"（C09）")
            rows.append({"line": no, "sentence": sent.strip(), "keys": keys, "evidence": ev,
                         "other_limits": ([{"key": k, "limit": r["limit"], "line": r["line"]}
                                           for k, rs in limit_by_key.items() if k not in keys
                                           for r in rs][:3] if not ev else []),
                         "contrib": (f"L{best_item['line']}：{best_item['item']}"
                                     if best_item and best_n >= MIN_OVERLAP else None),
                         "contrib_overlap": best_n, "verdict": verdict})
    return rows


# ------------------------------------------------------------------ 引用覆盖
def route_of_entry(key: str, e: dict, groups: list[dict]) -> tuple[str, str]:
    """bib 条目 → 方法路线（只在被引位置查不到时兜底）：用题名词与路线名的最长词面重合。"""
    for g in groups:
        lz, lt = zh_bigrams(g["label"]), en_tokens(g["label"])
        n = len((zh_bigrams(entry_profile(e)) & lz) | (en_tokens(entry_profile(e)) & lt))
        if n:
            return g["label"], f"未被引于相关工作，按题名与路线名「{g['label']}」词面重合 {n} 处归入"
    if not str(e.get("title", "")).strip():
        return "未归路线（题名为空）", "bib 条目没有 title，也无法从引用位置判断路线"
    return "未归路线", "题名与已有路线名无词面重合，不强行归类"


def coverage(lines_by_section: dict, bib: dict, groups: list[dict]) -> dict:
    """引用覆盖：按方法路线 + 按段 + 关键工作（被引 ≥2 处或在空白段被引）。

    路线归属优先看**引用位置**（哪条相关工作句子引了它、该句归哪个维度），
    只有查不到引用位置时才退回题名词簇，避免凭题名猜路线。
    """
    section_keys = {n: [] for n in SECTIONS}
    key_sites: dict[str, list[dict]] = {}
    sent_at: dict[int, list[str]] = {}
    for name in SECTIONS:
        for no, raw in lines_by_section.get(name, []):
            if not is_prose(raw):
                continue
            for sent in sentences(raw):
                if name == "相关工作":
                    sent_at.setdefault(no, []).append(sent)
                for k in cite_keys(sent):
                    section_keys[name].append(k)
                    key_sites.setdefault(k, []).append({"section": name, "line": no})
    for n in section_keys:
        section_keys[n] = sorted(set(section_keys[n]))

    item_owner = [(g["label"], it) for g in groups for it in g["items"]]

    def route_by_site(key: str) -> tuple[str | None, str]:
        for site in key_sites.get(key, []):
            if site["section"] != "相关工作":
                continue
            for sent in sent_at.get(site["line"], []):
                if key not in cite_keys(sent):
                    continue
                best, bl = 0, None
                for lab, it in item_owner:
                    n, _ = overlap(sent, it)
                    if n > best:
                        best, bl = n, lab
                if bl:
                    return bl, f"被引于相关工作 L{site['line']}，该句归「{bl}」"
        return None, ""

    route_rows: dict[str, dict] = {}
    order = [g["label"] for g in groups]
    for key, e in bib.items():
        label, why = route_by_site(key)
        if not label:
            label, why = route_of_entry(key, e, groups)
        r = route_rows.setdefault(label, {"label": label, "entries": [], "cited": [],
                                          "basis_by_entry": {}})
        r["entries"].append(key)
        r["basis_by_entry"][key] = why
        if key in key_sites:
            r["cited"].append(key)

    routes = []
    for label in order + [k for k in route_rows if k not in order]:
        r = route_rows.get(label)
        if r is None:
            routes.append({"label": label, "entries": [], "cited": [], "uncited": [],
                           "basis_by_entry": {},
                           "verdict": "该维度在本稿只出现维度词，bib 内没有对应条目"})
            continue
        uncited = [k for k in r["entries"] if k not in r["cited"]]
        if r["entries"] and not r["cited"]:
            verdict = f"空覆盖：该路线 {len(r['entries'])} 条条目，0 条被引用"
        elif uncited:
            verdict = f"{len(r['cited'])}/{len(r['entries'])} 条被引用，未引：" + "、".join(uncited)
        else:
            verdict = f"{len(r['cited'])}/{len(r['entries'])} 条被引用"
        routes.append({**r, "uncited": uncited, "verdict": verdict})

    key_works = []
    for k, sites in sorted(key_sites.items(), key=lambda x: (-len(x[1]), x[0])):
        secs = sorted({s["section"] for s in sites}, key=SECTIONS.index)
        key_works.append({"key": k, "n_sites": len(sites), "n_sections": len(secs),
                          "sections": secs, "in_gap": "研究空白" in secs,
                          "draft_lines": [s["line"] for s in sites]})
    # 「未归路线」是兜底桶，不是稿子里真有的方法路线：无引用归 C04，不冒充覆盖缺口
    real = [r for r in routes if not r["label"].startswith("未归路线")]
    return {
        "routes": routes,
        "uncovered_routes": [r["label"] for r in real if r["entries"] and not r["cited"]],
        "unrouted_entries": sorted(k for r in routes if r["label"].startswith("未归路线")
                                   for k in r["entries"]),
        "section_cites": section_keys,
        "sections_without_cites": [n for n in SECTIONS[:3] if not section_keys.get(n)],
        "key_works": key_works,
        "off_bib": sorted(set(key_sites) - set(bib)),
        "bib_not_cited": sorted(set(bib) - set(key_sites)),
    }


# ------------------------------------------------------------------ 事实/术语/数字/论断
def number_rows(lines_by_section: dict) -> dict:
    rows, dropped = [], 0
    for name in SECTIONS:
        for no, raw in lines_by_section.get(name, []):
            if not is_prose(raw):
                continue
            for sent in sentences(raw):
                ev_spans = [(m.start(), m.end()) for m in EVID.finditer(sent)]
                vals = [m.group(1).strip() for m in NUM.finditer(sent)
                        if not any(a <= m.start() < b for a, b in ev_spans)]
                vals += [m.group(0).strip() for m in NUM_ZH.finditer(sent)]
                for v in vals:
                    if len(rows) >= 80:
                        dropped += 1
                        continue
                    cited = bool(cite_keys(sent))
                    evid = bool(EVID.search(sent))
                    rows.append({"kind": "数字", "section": name, "line": no,
                                 "value": v, "sentence": sent.strip(),
                                 "verdict": ("同句带引用" if cited else
                                             "同句带图表/章节指向" if evid else "无来源")})
    return {"rows": rows, "dropped": dropped}


def term_rows(lines_by_section: dict) -> dict:
    rows, seen, dropped = [], set(), 0
    for name in SECTIONS:
        for no, raw in lines_by_section.get(name, []):
            for sent in sentences(raw):
                for m in TERM_GLOSS.finditer(sent):
                    term = m.group(1).strip()
                    gloss = m.group(2).strip()
                    if re.search(r"\[@|\\cite|^\W*$", gloss):
                        continue          # 括号里是引用标记，不是术语释义
                    if term.lower() in seen or len(term) < 2:
                        continue
                    if len(rows) >= 60:
                        dropped += 1
                        continue
                    seen.add(term.lower())
                    rows.append({"kind": "术语", "section": name, "line": no, "term": term,
                                 "gloss": gloss, "sentence": sent.strip(),
                                 "verdict": "有原文释义（括号内）"})
                for m in ABBR.finditer(sent):
                    ab = m.group(1)
                    if ab in ABBR_STOP or ab.lower() in seen:
                        continue
                    if len(rows) >= 60:
                        dropped += 1
                        continue
                    seen.add(ab.lower())
                    gl = (re.search(re.escape(ab) + r"\s*[（(]\s*([^）)]{2,60})", sent)
                          or re.search(r"([\u4e00-\u9fffA-Za-z\-]{2,40})\s*[（(]\s*"
                                       + re.escape(ab) + r"\s*[）)]", sent))
                    rows.append({"kind": "术语", "section": name, "line": no, "term": ab,
                                 "gloss": gl.group(1).strip() if gl else "",
                                 "sentence": sent.strip(),
                                 "verdict": "有原文释义" if gl else "首次出现未见全称，需作者补或删缩写"})
    return {"rows": rows, "dropped": dropped}


def claim_rows(lines_by_section: dict) -> dict:
    """关键论断：逐句记录「有引用／有图表指向／已标注为作者判断／无来源」。"""
    rows = []
    for name in SECTIONS:
        for no, raw in lines_by_section.get(name, []):
            for sent in sentences(raw):
                s = sent.strip()
                if len(s) < 12 and len(s.split()) < 5:
                    continue
                keys = list(dict.fromkeys(cite_keys(s)))
                ev = [m.group(1) for m in EVID.finditer(s)]
                author = bool(AUTHOR_CUE.search(s))
                if keys:
                    verdict = "有引用"
                elif ev:
                    verdict = "有图表/章节指向"
                elif author:
                    verdict = "已标注为作者判断"
                else:
                    verdict = "无来源"
                rows.append({"kind": "关键论断", "section": name, "line": no,
                             "sentence": s, "keys": keys, "markers": ev,
                             "author_claim": author, "verdict": verdict})
    return {"rows": rows}


# ------------------------------------------------------------------ 组装
def sections_out(secs, grp, cites, evid) -> dict:
    """改稿四段正文（不含附录）。核验改稿时按这四段跑判据。"""
    rel = []
    if grp["statements"]:
        rel += [f"> 原文分组陈述（未改字，L{s['line']}）：{s['sentence']}"
                for s in grp["statements"]] + [""]
    if grp["groups"]:
        rel += [f"相关工作按原文维度分为 {len(grp['groups'])} 组，逐组列出：", ""]
    for i, g in enumerate(grp["groups"], 1):
        rel.append(f"### 维度 {i}：{g['label']}")
        rel.append(f"*依据：{g['basis']}；本组 {len(g['items'])} 句、"
                   f"{len({k for it in g['items'] for k in cite_keys(it)})} 条引用*")
        rel.append("")
        rel += [f"- {it}" for it in g["items"]]
        rel.append("")
    bg = "".join(t for _, t in cites["by_section"].get("研究背景", []))
    gap = "".join(t for _, t in cites["by_section"].get("研究空白", []))
    return {
        "研究背景": bg.strip(),
        "相关工作": "\n".join(rel).strip(),
        "研究空白": gap.strip(),
        "贡献": "\n".join(it["text"] for it in evid["items"]).strip(),
    }


def handoff_text(out_secs: dict, grp: dict, gap_rows: list[dict], evid: dict) -> list[str]:
    """四部分承接链：每段由上一段的哪条依据承接而来（骨架用，规则生成，不含新事实）。"""
    L = []
    if out_secs["研究背景"]:
        keys = list(dict.fromkeys(cite_keys(out_secs["研究背景"])))
        L.append(f"研究背景：交代具体未解决问题，引 {len(keys)} 条（{'、'.join(keys) or '无'}）")
    else:
        L.append("研究背景：缺，待作者补")
    if out_secs["相关工作"]:
        L.append(f"相关工作：承接背景的问题，按原文维度组织为 {len(grp['groups'])} 组"
                 f"（{'、'.join(g['label'] for g in grp['groups'])}），逐组给结论/适用条件/局限")
    else:
        L.append("相关工作：缺，待作者补")
    ok = [r for r in gap_rows if str(r["verdict"]).startswith("成立")]
    L.append(f"研究空白：由相关工作里 {len(ok)}/{len(gap_rows)} 条缺口依据推出（见 §G）"
             if gap_rows else "研究空白：缺，待作者补")
    n_ev = len([r for r in evid["records"]])
    L.append(f"贡献：{len(evid['items'])} 条，逐条挂图表/章节指向 {n_ev} 条（见 §C），"
             f"对应缺口 {len([r for r in gap_rows if r['contrib']])} 条（见 §G）"
             if evid["items"] else "贡献：缺分条，待作者补")
    return L


def render(secs, grp, cites, evid, bib, draft, gap_rows, crit_rows,
           spin_rows=None, extra_pending=None) -> str:
    L = ["# 引言与相关工作（改写稿）", "",
         "> 由 `compose.py` 依原稿与 bib 生成：去套话、分组、补引、证据指向、转述对照、空白推导六件事；"
         "不改写有内容的原句，不编编号、不编条目字段。",
         "> 句中〔补引 Bn〕〔补指向 Cn〕〔空白出处待补〕是工具插入的标注，编号对应文末 §B/§C/§G；"
         "定稿前逐条处理。", ""]
    out = sections_out(secs, grp, cites, evid)

    L += ["## 研究背景", "", out["研究背景"] or "> 待补：原稿未见研究背景段", ""]
    L += ["## 相关工作", ""]
    L += out["相关工作"].splitlines() if out["相关工作"] else ["> 待补：相关工作段为空"]
    L.append("")
    L += ["## 研究空白", "", out["研究空白"] or "> 待补：原稿未见研究空白段", ""]
    L += ["## 贡献", ""]
    L += out["贡献"].splitlines() if out["贡献"] else ["> 待补：贡献段没有分条"]
    L.append("")

    L += ["---", "", "## A 分组依据（相关工作逐句 → 维度）", "",
          "| # | 原稿行 | 原句/分句 | 维度 | 依据 | 处理 |", "|-|-|-|-|-|-|"]
    for i, r in enumerate(grp["rows"], 1):
        L.append(f"| {i} | L{r['line']} | {cell(r['clause'])} | {cell(r['dim'])} | "
                 f"{cell(r['basis'])} | {cell(r['why'])} |")

    L += ["", "## A2 同维度转述对照（结论 / 适用条件 / 局限，全部照抄原文）", "",
          "| # | 原稿行 | 工作 | 所在维度 | 结论 | 适用条件 | 局限 | 缺项 |",
          "|-|-|-|-|-|-|-|-|"]
    for i, r in enumerate(crit_rows, 1):
        L.append(f"| {i} | L{r['line']} | {'、'.join('@' + k for k in r['keys'])} | {cell(r['dim'])} | "
                 f"{cell(r['claim'])} | {cell(r['cond'])} | {cell(r['limit'])} | "
                 f"{cell('、'.join(r['missing']) or '无')} |")

    L += ["", "## B 补引记录（文中〔补引 n〕对应此处编号）", ""]
    if cites["records"]:
        L += ["| # | 段 | 原稿行 | 补入条目 | 判据 | 依据 | 原句 |", "|-|-|-|-|-|-|-|"]
        for r in cites["records"]:
            L.append(f"| B{r['index']} | {r['section']} | L{r['line']} | "
                     f"{', '.join('@' + k for k in r['added'])} | {cell(r['rule'])} | "
                     f"{cell(r['basis'])} | {cell(r['sentence'])} |")
    else:
        L.append("（本次没有可补的论断：原文论断要么已带引用，要么词面无法与库内条目对上。）")

    L += ["", "## C 贡献证据指向", ""]
    if evid["records"]:
        L += ["| # | 原稿行 | 贡献条目 | 指向 | 依据 |", "|-|-|-|-|-|"]
        for r in evid["records"]:
            L.append(f"| C{r['index']} | L{r['line']} | {cell(r['item'])} | "
                     f"{cell(r['marker'])} | {cell(r['basis'])} |")
    else:
        L.append("（贡献段没有分条，无条目可挂。）")

    L += ["", "## D 人工确认项（工具不替你决定）", ""]
    pend = list(extra_pending or [])
    pend += [f"{p['section']} L{p.get('line', '?')}：{p['sentence']} —— {p['why']}"
             for p in cites["pending"] + evid["pending"]]
    pend += [f"研究空白 L{r['line']}：{r['sentence']} —— {r['verdict']}"
             for r in gap_rows if not str(r["verdict"]).startswith("成立")]
    for r in gap_rows:
        if r.get("other_limits") and not str(r["verdict"]).startswith("成立"):
            pend.append(f"研究空白 L{r['line']}：本稿在别处记录了 "
                        + "、".join(f"@{o['key']}(L{o['line']})" for o in r["other_limits"])
                        + " 的局限；若那就是这条空白的依据，请把引用挂到该局限句或本句上")
    ask: dict[str, list[int]] = {}
    for r in crit_rows:
        if "局限" in r["missing"]:
            for k in r["keys"]:
                ask.setdefault(k, []).append(r["line"])
    for k, lines_ in ask.items():
        pend.append(f"相关工作 {'、'.join('L' + str(x) for x in sorted(set(lines_)))}："
                    f"@{k} 未写局限 —— 转述对照缺一项，空白推导要用它，请作者补一句适用范围或失效条件")
    if pend:
        for i, p in enumerate(pend, 1):
            L.append(f"{i}. {p}")
    else:
        L.append("（无。）")

    L += ["", "## E 本次用到的 bib 条目", "",
          "| 键 | 题名 | 作者 | 年 | 来源 | 必要字段 |", "|-|-|-|-|-|-|"]
    for k, e in bib.items():
        rep = bib_field_report(e)
        L.append(f"| {k} | {cell(e.get('title', '—') or '—')} | {cell(e.get('author', '—') or '—')} | "
                 f"{cell(e.get('year', '—') or '—')} | {cell(e.get('venue', '') or '—')} | "
                 f"{'齐' if not rep['missing'] else '缺' + '、'.join(rep['missing'])} |")
    if not bib:
        L.append("| — | — | — | — | — | bib 为空 |")

    L += ["", "## F 去套话记录", ""]
    if spin_rows:
        L += ["| # | 段 | 原稿行 | 删掉的分句 | 命中规则 | 改后 |", "|-|-|-|-|-|-|"]
        for i, r in enumerate(spin_rows, 1):
            L.append(f"| F{i} | {r.get('section', '')} | L{r.get('line', '?')} | "
                     f"{cell('；'.join(r.get('删除') or []))} | {cell(r.get('规则', ''))} | "
                     f"{cell(str(r.get('改后', '')))} |")
    else:
        L.append("（原文没有命中套话表的分句，未删改。）")

    L += ["", "## G 空白推导链（空白 ← 已引工作的局限 → 贡献条目）", ""]
    if gap_rows:
        L += ["| # | 原稿行 | 空白论断 | 依据已引工作 | 该工作的局限（原文） | 对应贡献 | 判定 |",
              "|-|-|-|-|-|-|-|"]
        for i, r in enumerate(gap_rows, 1):
            ev = "；".join(f"@{e['key']}（L{e['line']}）：{e['limit']}" for e in r["evidence"])
            verdict = r["verdict"]
            if r.get("other_limits"):
                verdict += ("；本稿其它位置记录的局限：" +
                            "；".join(f"@{o['key']}（L{o['line']}）：{o['limit']}"
                                     for o in r["other_limits"]))
            L.append(f"| G{i} | L{r['line']} | {cell(r['sentence'])} | "
                     f"{cell('、'.join('@' + k for k in r['keys']) or '无')} | "
                     f"{cell(ev or '本稿未记录')} | {cell(r['contrib'] or '未对上贡献条目')} | "
                     f"{cell(verdict)} |")
    else:
        L.append("（研究空白段为空，无空白可推导。）")
    L.append("")
    return "\n".join(L)


def build(draft_text: str, bib_text: str) -> dict:
    """原稿 + bib → 改写稿与逐条依据（供 run.py 与 CLI 共用）。"""
    bib = parse_bib(bib_text)
    parsed = parse_sections(draft_text)
    raw = {n: (parsed["sections"][n]["lines"] if n in parsed["sections"] else []) for n in SECTIONS}

    # 先删套话分句，再做分组/补引/证据指向：套话留在句中会把分组与补引判据带偏
    secs, spin_rows = {}, []
    for name in SECTIONS:
        clean, rows = despin_lines(raw.get(name, []))
        secs[name] = clean
        for r in rows:
            spin_rows.append({"section": name, **r})

    draft_keys = list(dict.fromkeys(cite_keys("\n".join(t for n in SECTIONS for _, t in secs[n]))))
    cites = add_cites(secs, bib, draft_keys)
    # 补引之后的分组/推导/覆盖一律以**交付文本**为底：否则 §B 补进去的引用不会出现在正文里
    secs = {n: cites["by_section"][n] for n in SECTIONS}
    grp = regroup(secs["相关工作"])
    evid = point_evidence(secs["贡献"], draft_text)
    crit_rows = critique(secs["相关工作"], {"groups": grp["groups"]})
    # 空白推导的"局限证据"连研究背景一起看：背景里写下的限制同样是空白的出处
    crit_all = critique(secs["相关工作"] + secs["研究背景"], {"groups": grp["groups"]})
    gap_rows = gap_chain(secs["研究空白"], crit_all, contribution_items(secs["贡献"]))
    cov = coverage(secs, bib, grp["groups"])

    # 分组维度不足时的提醒要写进改写稿 §D，不能只留在 result.json 里
    dim_note = (f"相关工作只落进 1 个维度（{grp['groups'][0]['label']}）：原文没有可用的维度词，"
                "本工具不编维度名，需作者补「基于/采用 X」式表述"
                if len(grp["groups"]) == 1 else None)
    md = render(secs, grp, cites, evid, bib, draft_text, gap_rows, crit_rows,
                spin_rows=spin_rows,
                extra_pending=[dim_note] if dim_note else [])
    out_secs = sections_out(secs, grp, cites, evid)

    sites = []
    for name in SECTIONS:
        for no, rawline in secs.get(name, []):
            for sent in sentences(rawline):
                for k in cite_keys(sent):
                    sites.append({"key": k, "section": name, "sentence": sent.strip()[:80],
                                  "line": no})

    one_dim = len(grp["groups"]) == 1
    unresolved = []
    if one_dim:
        unresolved.append(f"相关工作只落进 1 个维度（{grp['groups'][0]['label']}）："
                          "原文没有可用的维度词，本工具不编维度名，需作者补「基于/采用 X」式表述")
    unresolved += [f"{p['section']} L{p.get('line', '?')}：{p['sentence'][:40]} —— {p['why']}"
                   for p in cites["pending"] + evid["pending"]]
    unresolved += [f"研究空白 L{r['line']}：{r['sentence'][:40]} —— {r['verdict']}"
                   for r in gap_rows if not str(r["verdict"]).startswith("成立")]

    nums = number_rows(secs)
    terms = term_rows(secs)
    claims = claim_rows(secs)
    return {
        "revised_md": md,
        "audit_sections": out_secs,
        "handoff": handoff_text(out_secs, grp, gap_rows, evid),
        "groups": [{"label": g["label"], "n_items": len(g["items"]), "basis": g["basis"]}
                   for g in grp["groups"]],
        "group_rows": grp["rows"],
        "statements": grp["statements"],
        "compare_rows": crit_rows,
        "gap_rows": gap_rows,
        "coverage": cov,
        "number_rows": nums,
        "term_rows": terms,
        "claim_rows": claims,
        "despin_rows": spin_rows,
        "cites_added": cites["records"],
        "cites_pending": cites["pending"],
        "evidence_rows": evid["records"],
        "evidence_items": evid["items"],
        "evidence_pending": evid["pending"],
        "bib": bib,
        "bib_keys": sorted(bib),
        "cite_sites": sites,
        "sections_found": parsed["order"],
        "sections_missing": [n for n in SECTIONS if not secs.get(n)],
        "heading_level": parsed["level"],
        "unresolved": unresolved,
        "stats": {
            "n_groups": len(grp["groups"]), "n_group_sentences": len(grp["rows"]),
            "n_despin": len(spin_rows),
            "n_cites_added": len(cites["records"]),
            "n_evidence_added": sum(1 for r in evid["records"] if "原文自带" not in r["basis"]),
            "n_compare": len(crit_rows), "n_gap": len(gap_rows),
            "n_gap_derived": sum(1 for r in gap_rows if str(r["verdict"]).startswith("成立")),
            "n_routes": len(cov["routes"]),
            "n_uncovered_routes": len(cov["uncovered_routes"]),
            "n_numbers": len(nums["rows"]), "n_terms": len(terms["rows"]),
            "n_claims": len(claims["rows"]),
            "n_claims_unsourced": sum(1 for r in claims["rows"] if r["verdict"] == "无来源"),
            "n_pending": len(unresolved),
        },
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
