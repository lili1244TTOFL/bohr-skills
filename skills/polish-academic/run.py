#!/usr/bin/env python3
"""run.py —— 学术语言润色：检测、逐句留痕、守恒核对、对照评测（v4）。

分工：脚本检测硬问题、核对改写有没有改坏、把每一处改动落成可复核记录；
改写由调用方模型完成；未给改后稿时脚本只做最小词面修复，不动结论强度。

题面四关注点 → 机制：
    改得对   D 组：语法、被动、冗余、口语、长句、名词化、时态、中式、绝对化
    说得像   T 组（术语表译名）+ S 组（目标文体越界）+ A 组（缩写与符号首现）
    没改坏   K 组：数字/单位/引用/图表指向/公式/否定/限定强度，两侧同口径计数 + 集合比对
    讲得清   changes.md 逐句「原文/改文/类型/理由」；M 组：最小编辑与无留痕改动

所有结论可回原稿：码 + 原文片段 + 段号 + 句号 + 句文本。
"""
from __future__ import annotations

import argparse
import bisect
import difflib
import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path

VERSION = "4.0.0"
ROOT = Path(__file__).resolve().parent
PROFILE_FILE = ROOT / "rules" / "style_profiles.json"

# ---------------- 问题检测：中英各一套，避免只覆盖一种语言 ----------------
DETECT = [
    ("D01", "主谓一致", r"\b(data|results?)\s+was\b", 0.9, "data/results 为复数"),
    ("D02", "被动堆叠", r"\b(was|were)\s+\w+ed\b[^.]{0,40}\b(was|were)\s+\w+ed\b", 0.7,
     "一句内两处被动"),
    ("D03", "模糊加强", r"\b(clearly|obviously|extremely|very)\b|非常|极其|显然", 0.8,
     "加强词可删或换具体量"),
    ("D04", "名词化堆叠", r"\bthe\s+\w+tion\s+of\s+the\s+\w+\s+of\b", 0.7,
     "多层 of 结构改动词"),
    ("D05", "冗余短语", r"\b(in order to|due to the fact that|it is worth noting that)\b",
     0.9, "可省为更短表达"),
    ("D06", "口语词", r"\b(a lot of|big|huge|stuff|get)\b", 0.8, "学术文体应换书面词"),
    ("D07", "长句", None, 0.6, "超过阈值应切分"),
    ("D08", "时态混用", r"\b(will be|is)\s+\w+ed\b[^.]{0,50}\b(was|were)\b", 0.7,
     "同句时态需统一"),
    ("D09", "中式表达", r"进行(了)?[^。，]{0,8}(研究|分析|探讨)|具有[^。，]{0,8}(意义|作用)"
     r"|众所周知|值得一提的是", 0.8, "空转铺垫或翻译腔"),
    ("D10", "绝对化", r"\b(always|never|all|none|proves?)\b|必然|完全|一定", 0.6,
     "绝对表述需限定"),
]

CITE = re.compile(r"\[\d+(?:[-,]\d+)*\]")
FIGREF = re.compile(r"\b(?:Fig(?:ure)?\.?\s*\d+|Table\s*\d+|Eq\.?\s*\(?\d+)\b", re.I)
UNIT = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|g|kg|mL|ml|L|mm|cm|m|s|ms|h|min|Hz|kHz|GHz"
                  r"|°C|K|mol|mM|μM|nM)\b")
NUM = re.compile(r"\d+(?:[.,]\d+)?%?")
BOOST = re.compile(r"\b(significantly|substantially|markedly|considerably|strongly|highly"
                   r"|clearly|obviously|very|extremely)\b|显著|明显|大幅|非常|极其")
HEDGE = re.compile(r"\b(may|might|could|possibly|likely|suggest|appears?|tends? to)\b"
                   r"|可能|或许|倾向于")
NEG = re.compile(r"\b(not|no|never|none|cannot|without)\b|不|无|未|没有")
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

LONG_WORDS = 40      # 拉丁文长句阈值（词）
LONG_CHARS = 80      # 中文长句阈值（字）
REWRITE_RATIO = 0.60  # 低于此相似度视为疑似整句重写
CHANGE_RATIO = 0.995  # 相似度低于此值视为该句有改动

MATH_PATTERNS = (
    r"\$\$.+?\$\$",
    r"(?<![\$\\])\$(?!\$)[^$\n]+?\$",
    r"\\\(.+?\\\)",
    r"\\\[.+?\\\]",
    r"\\begin\{[A-Za-z*]+\}.+?\\end\{[A-Za-z*]+\}",
    r"`[^`\n]+`",
)
ABBR_WORDS = {"fig", "figs", "eq", "eqs", "tab", "table", "ref", "refs", "sec", "sect",
              "no", "vol", "pp", "p", "vs", "viz", "cf", "approx", "al", "dr", "prof",
              "ed", "eds", "st", "resp", "i.e", "e.g", "et al", "al."}
SPLIT_PAT = re.compile(r"[。！？；!?;]|\.(?=\s|$)")
TOK_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+|[0-9]+(?:[.,][0-9]+)?%?"
                    r"|[^\sA-Za-z0-9\u4e00-\u9fff]")

# 术语表写法锁定：同一概念的单位/符号变体（A04 用）
SYMBOL_CLASSES = (("mL", "ml"), ("μL", "μl", "uL", "ul"), ("μM", "uM", "μmol", "umol"),
                  ("°C", "℃"), ("kg", "Kg", "KG"), ("mg", "Mg", "MG"),
                  ("×", "x"), ("μ", "u"))


# ============================ 基础 I/O ============================
def read_text(p: Path) -> tuple[str, str]:
    """读文本并回报编码；编码混乱时降级而不是崩。返回（正文, 编码标签）。"""
    raw = p.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return _norm(raw[3:].decode("utf-8", "replace")), "utf-8-bom"
    for enc in ("utf-8", "gb18030"):
        try:
            return _norm(raw.decode(enc)), enc
        except UnicodeDecodeError:
            continue
    return _norm(raw.decode("utf-8", "replace")), "utf-8-replace"


def _norm(t: str) -> str:
    return t.replace("\r\n", "\n").replace("\r", "\n")


def sha16(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]


def cell(s: str, n: int = 160) -> str:
    """Markdown 表格单元格：截断、去竖线、去换行。"""
    s = re.sub(r"\s+", " ", str(s)).strip().replace("|", "\\|")
    return s if len(s) <= n else s[: n - 1] + "…"


# ============================ 结构切分 ============================
def math_spans(text: str) -> list:
    """公式 / 行内代码区间：不参与改写，也不作为句子切分点。"""
    spans = []
    for pat in MATH_PATTERNS:
        for m in re.finditer(pat, text, re.S):
            spans.append((m.start(), m.end()))
    spans.sort()
    merged: list = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def in_spans(pos: int, spans: list) -> bool:
    if not spans:
        return False
    starts = [s for s, _ in spans]
    i = bisect.bisect_right(starts, pos) - 1
    if i < 0:
        return False
    return spans[i][0] <= pos < spans[i][1]


def paragraphs(text: str) -> list:
    out, prev = [], 0
    for m in re.finditer(r"\n[ \t]*\n", text):
        out.append((prev, m.start()))
        prev = m.end()
    out.append((prev, len(text)))
    return [(s, e) for s, e in out if text[s:e].strip()]


def para_kind(s: str) -> str:
    t = s.strip()
    if t.startswith("#"):
        return "标题"
    if t.startswith("|") or t.startswith("```"):
        return "表/代码"
    if t.startswith(("- ", "* ", "> ")):
        return "条目"
    return "正文"


def _is_abbr(seg: str, dot: int) -> bool:
    j = dot
    while j > 0 and (seg[j - 1].isalpha() or seg[j - 1] == "."):
        j -= 1
    word = seg[j:dot].strip(".").lower()
    if word in ABBR_WORDS:
        return True
    return len(word) == 1 and seg[j:dot].isupper()


def sentences(text: str) -> list:
    """句子表：段号、句号、起止、句文本。缩写点与公式内的点不切。"""
    mspans = math_spans(text)
    out = []
    for pi, (ps, pe) in enumerate(paragraphs(text), 1):
        seg = text[ps:pe]
        cuts = []
        for m in SPLIT_PAT.finditer(seg):
            if m.group(0) == "." and _is_abbr(seg, m.start()):
                continue
            if in_spans(ps + m.end(), mspans):
                continue
            cuts.append(m.end())
        if not cuts or cuts[-1] < len(seg):
            cuts.append(len(seg))
        prev, k = 0, 0
        for c in cuts:
            piece = seg[prev:c]
            lead = len(piece) - len(piece.lstrip())
            if piece.strip():
                k += 1
                out.append({"para": pi, "sent": k, "start": ps + prev + lead,
                            "end": ps + c, "text": piece.strip()})
            prev = c
    return out


def locate(sents: list, pos: int) -> dict:
    """把字符位置换成（段, 句）；供回原稿检索，不提供字符偏移。"""
    if not sents:
        return {"para": 1, "sent": 1, "text": ""}
    starts = [s["start"] for s in sents]
    i = max(0, bisect.bisect_right(starts, pos) - 1)
    s = sents[i]
    if pos > s["end"] and i + 1 < len(sents):
        s = sents[i + 1]
    return s


def cjk_ratio(t: str) -> float:
    body = re.sub(r"\s", "", t)
    if not body:
        return 0.0
    return len(CJK.findall(body)) / len(body)


def long_sentence(s: str) -> bool:
    """长句判据：中文按字，拉丁按词；不混用同一阈值。"""
    if cjk_ratio(s) > 0.3:
        return len(re.sub(r"\s", "", s)) > LONG_CHARS
    return len(s.split()) > LONG_WORDS


# ============================ 术语表 ============================
def load_glossary(p: Path | None) -> dict:
    """Markdown 两列/三列表。冲突不静默覆盖：同一原文对应多个译名时全部记账。"""
    rows, mapping, conflicts = [], {}, []
    if not p or not p.exists():
        return {"path": None, "rows": rows, "map": mapping, "conflicts": conflicts}
    text, _enc = read_text(p)
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set("".join(cells)) <= set("-: "):
            continue
        if cells[0].lower() in ("原文", "source", "en", "术语", "原词"):
            continue
        src, tgt = cells[0], cells[1]
        note = cells[2] if len(cells) > 2 else ""
        rows.append({"原文": src, "译名": tgt, "说明": note})
        if src in mapping and mapping[src] != tgt:
            conflicts.append({"原文": src, "已用": mapping[src], "另一写法": tgt})
        else:
            mapping.setdefault(src, tgt)
    return {"path": str(p), "rows": rows, "map": mapping, "conflicts": conflicts}


def term_rows(glossary: dict, src: str, pol: str, sents_src: list, lang: str) -> list:
    """逐条术语：两侧出现次数与所在段，供人工判断术语表是否建全。"""
    out = []
    for r in glossary["rows"]:
        s_term, t_term = r["原文"], r["译名"]
        s_n = src.count(s_term)
        t_n = pol.count(t_term)
        paras = sorted({s["para"] for s in sents_src if s_term in s["text"]})
        if s_n == 0:
            verdict = "原文未出现"
        elif lang == "same":
            verdict = "同语：术语表只锁写法"
        elif t_n >= s_n:
            verdict = "已对齐"
        elif t_n == 0:
            verdict = "未使用译名"
        else:
            verdict = "译名不足"
        out.append({"原文": s_term, "译名": t_term, "原文出现": s_n,
                    "改后译名出现": t_n, "所在段": paras, "说明": r["说明"],
                    "判定": verdict, "语言方向": lang})
    return out


# ============================ 计数与守恒 ============================
def strip_citations(t: str) -> str:
    """数数字前先去掉引用与图表指向，避免编号被重复计成正文数字。"""
    return CITE.sub(" ", FIGREF.sub(" ", t))


def _nfig(s: str) -> str:
    s = re.sub(r"\s+", "", s).lower()
    return s.replace("figure", "fig").replace("fig.", "fig").replace("eq.", "eq")


def _nunit(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _tokens(pat, text: str) -> Counter:
    """用整条匹配（group 0）计数：分组存在时 findall 会返回空串，不能用来计数。"""
    return Counter(m.group(0).lower() for m in pat.finditer(text))


def profile(text: str) -> dict:
    """两侧同一口径：引用/图号先剥再数数字；单位去空格；图号归一。"""
    body = strip_citations(text)
    return {
        "num": Counter(NUM.findall(body)),
        "unit": Counter(_nunit(u) for u in UNIT.findall(text)),
        "cite": Counter(CITE.findall(text)),
        "fig": Counter(_nfig(f) for f in FIGREF.findall(text)),
        "neg": _tokens(NEG, text),
        "boost": _tokens(BOOST, text),
        "hedge": _tokens(HEDGE, text),
        "formula": len(math_spans(text)),
    }


def counts(p: dict) -> dict:
    return {k: (v if isinstance(v, int) else len(v)) for k, v in p.items()}


def conservation(ps: dict, pp: dict, translated: bool = False) -> tuple:
    """K 组：先比数量，再比集合。数量相同但内容变了也要报。"""
    probs, drift, rows = [], {}, []
    labels = (("num", "数字"), ("unit", "单位"), ("cite", "引用"),
              ("fig", "图表指向"), ("formula", "公式"), ("neg", "否定"))
    same_set_code = {"num": "K03", "unit": "K03", "cite": "K04", "fig": "K05", "neg": "K06"}
    for key, label in labels:
        a, b = ps[key], pp[key]
        na = a if isinstance(a, int) else len(a)
        nb = b if isinstance(b, int) else len(b)
        verdict = "一致"
        if na != nb:
            verdict = f"数量 {na}→{nb}"
            drift[key] = {"source": na, "polished": nb}
            probs.append({"code": "K01", "detail": f"{label}数量变化：原文 {na} → 改后 {nb}"})
        elif isinstance(a, Counter) and a != b:
            if translated and key == "neg":
                continue  # 否定词跨语言不同形（不/not），集合不可比，数量已在上一支比过
            lost = [f"{k}×{a[k] - b.get(k, 0)}" for k in a if a[k] > b.get(k, 0)]
            add = [f"{k}×{b[k] - a.get(k, 0)}" for k in b if b[k] > a.get(k, 0)]
            code = same_set_code.get(key, "K03")
            verdict = "集合变更"
            probs.append({"code": code, "detail":
                          f"{label}被换掉但数量相同：原文多 {lost[:6]}，改后多 {add[:6]}"})
        rows.append({"项": label, "原文": na, "改后": nb, "判定": verdict})
    # 限定强度：同语按词比对，跨语言只比数量（词表不可跨语言对齐）
    lost_boost = ps["boost"] - pp["boost"]
    lost_hedge = ps["hedge"] - pp["hedge"]
    add_boost = pp["boost"] - ps["boost"]
    add_hedge = pp["hedge"] - ps["hedge"]
    if translated:
        nb, nh = len(ps["boost"]), len(pp["boost"])
        hb, hh = len(ps["hedge"]), len(pp["hedge"])
        if nh < nb:
            probs.append({"code": "K02", "detail":
                          f"加强词数量减少：{nb} → {nh}（跨语言，只比数量，词形需人工核对）"})
        elif nh > nb:
            probs.append({"code": "K07", "detail":
                          f"加强词数量增加：{nb} → {nh}（跨语言，只比数量，需人工核对）"})
        if hh < hb:
            probs.append({"code": "K02", "detail":
                          f"留有余地词数量减少：{hb} → {hh}（跨语言，只比数量）"})
        elif hh > hb:
            probs.append({"code": "K07", "detail":
                          f"留有余地词数量增加：{hb} → {hh}（跨语言，只比数量）"})
        lost_boost = add_boost = {}
        lost_hedge = add_hedge = {}
    else:
        if lost_boost or lost_hedge:
            probs.append({"code": "K02", "detail":
                          f"限定强度被削弱：加强词减少 {dict(lost_boost)}，"
                          f"留有余地词减少 {dict(lost_hedge)}"})
        if add_boost:
            probs.append({"code": "K07", "detail":
                          f"结论被悄悄加强：新增加强词 {dict(add_boost)}（原文没有）"})
    rows.append({"项": "加强词", "原文": len(ps["boost"]), "改后": len(pp["boost"]),
                 "判定": "一致" if len(ps["boost"]) == len(pp["boost"]) else
                 ("削弱" if len(ps["boost"]) > len(pp["boost"]) else "加强")})
    rows.append({"项": "留有余地词", "原文": len(ps["hedge"]), "改后": len(pp["hedge"]),
                 "判定": "一致" if len(ps["hedge"]) == len(pp["hedge"]) else
                 ("削弱" if len(ps["hedge"]) > len(pp["hedge"]) else "加强")})
    return probs, drift, rows, {"boost_lost": dict(lost_boost),
                                "hedge_lost": dict(lost_hedge),
                                "boost_added": dict(add_boost),
                                "hedge_added": dict(add_hedge)}


# ============================ 检测 ============================
def detect(text: str, sents: list) -> list:
    out = []
    for code, name, pat, conf, why in DETECT:
        if code == "D07":
            for s in sents:
                if long_sentence(s["text"]):
                    out.append({"code": code, "name": name, "span": s["text"][:70],
                                "para": s["para"], "sent": s["sent"],
                                "sentence": s["text"][:200],
                                "confidence": conf, "reason": why})
            continue
        for m in re.finditer(pat, text, re.I):
            s = locate(sents, m.start())
            out.append({"code": code, "name": name, "span": m.group(0)[:70],
                        "para": s["para"], "sent": s["sent"],
                        "sentence": s["text"][:200],
                        "confidence": conf, "reason": why})
    out.sort(key=lambda x: (x["para"], x["sent"], x["code"], x["span"]))
    return out


# ============================ 词面修复（无外部改后稿时） ============================
REWRITE = [
    (r"\bin order to\b", "to"),
    (r"\ba lot of\b", "many"),
    (r"\bbig\b", "large"),
    (r"\bhuge\b", "large"),
    (r"\bthe data was\b", "the data were"),
    (r"\bthe results was\b", "the results were"),
    (r"\bdue to the fact that\b", "because"),
    (r"\bit is worth noting that\b", ""),
    (r"值得一提的是[，,]", ""),
    (r"众所周知[，,]", ""),
]


def _sub_keep_case(seg: str, pat: str, rep: str) -> str:
    def one(m):
        s = m.group(0)
        r = re.sub(pat, rep, s, flags=re.I)
        if r and s[:1].isupper() and r[:1].islower():
            r = r[:1].upper() + r[1:]
        return r
    return re.sub(pat, one, seg, flags=re.I)


def auto_rewrite(text: str) -> tuple:
    """只做词面修复：不删加强词、不合并句子，公式与行内代码原样保留。"""
    mspans = math_spans(text)
    segs, prev = [], 0
    for s, e in mspans:
        segs.append((prev, s, False))
        segs.append((s, e, True))
        prev = e
    segs.append((prev, len(text), False))
    applied, out = [], []
    for s, e, protected in segs:
        seg = text[s:e]
        if protected or not seg:
            out.append(seg)
            continue
        for pat, rep in REWRITE:
            hits = re.findall(pat, seg, re.I)
            if not hits:
                continue
            seg = _sub_keep_case(seg, pat, rep)
            m = re.search(pat, text, re.I)
            loc = locate(sentences(text), m.start()) if m else {"para": 0, "sent": 0}
            applied.append({"pattern": pat, "replacement": rep, "count": len(hits),
                            "段": loc.get("para"), "句": loc.get("sent")})
        out.append(seg)
    res = "".join(out)
    res = re.sub(r"[ \t]{2,}", " ", res)
    res = re.sub(r"[ \t]+([,.;:!?，。；：！？])", r"\1", res)
    res = re.sub(r"[ \t]+\n", "\n", res)
    return res, applied


# ============================ 句级对照与 diff ============================
def toks(s: str) -> list:
    return TOK_RE.findall(s)


def ratio(a: str, b: str) -> float:
    if a.strip() == b.strip():
        return 1.0
    return difflib.SequenceMatcher(None, toks(a), toks(b), autojunk=False).ratio()


def align(A: list, B: list) -> list:
    """按顺序对齐两侧句子，允许单侧跳一句（新增/删除）。"""
    pairs, i, j = [], 0, 0
    while i < len(A) and j < len(B):
        r = ratio(A[i]["text"], B[j]["text"])
        if r >= 0.45:
            pairs.append({"src": A[i], "pol": B[j], "ratio": r})
            i += 1
            j += 1
            continue
        r_b = ratio(A[i]["text"], B[j + 1]["text"]) if j + 1 < len(B) else 0.0
        r_a = ratio(A[i + 1]["text"], B[j]["text"]) if i + 1 < len(A) else 0.0
        if r_b > r_a and r_b >= 0.45:
            pairs.append({"src": None, "pol": B[j], "ratio": 0.0})
            j += 1
        elif r_a >= 0.45:
            pairs.append({"src": A[i], "pol": None, "ratio": 0.0})
            i += 1
        else:
            pairs.append({"src": A[i], "pol": B[j], "ratio": r})
            i += 1
            j += 1
    pairs += [{"src": s, "pol": None, "ratio": 0.0} for s in A[i:]]
    pairs += [{"src": None, "pol": s, "ratio": 0.0} for s in B[j:]]
    return pairs


def word_diff(a: str, b: str, cap: int = 8) -> list:
    def join(ts: list) -> str:
        out = ""
        for t in ts:
            if out and (re.match(r"[A-Za-z0-9]", t) and re.match(r"[A-Za-z0-9]", out[-1])):
                out += " "
            out += t
        return out

    ta, tb = toks(a), toks(b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    pairs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        pairs.append({"操作": {"replace": "替换", "delete": "删除", "insert": "新增"}[tag],
                      "原文": join(ta[i1:i2]) or "—",
                      "改文": join(tb[j1:j2]) or "—"})
        if len(pairs) >= cap:
            break
    return pairs


def edit_records(sents_src: list, pol_text: str, issues: list,
                 translated: bool = False) -> tuple:
    """逐句修改说明：原文、改文、类型、理由、置信度、词级变更。"""
    sents_pol = sentences(pol_text)
    code_of = {}
    for it in issues:
        code_of.setdefault((it["para"], it["sent"]), []).append(it)
    rows, leftover = [], []
    for p in align(sents_src, sents_pol):
        a, b, r = p["src"], p["pol"], p["ratio"]
        if a is None:
            leftover.append({"类型": "新增句", "改文": b["text"][:200],
                             "位置": f"段{b['para']}·句{b['sent']}"})
            continue
        hits = code_of.get((a["para"], a["sent"]), [])
        changed = b is None or r < CHANGE_RATIO
        if not changed:
            continue
        seen, codes, why_parts = set(), [], []
        for h in hits:
            if h["code"] in seen:
                continue
            seen.add(h["code"])
            codes.append({"码": h["code"], "类型": h["name"]})
            why_parts.append(f"{h['code']} {h['reason']}")
        why = "；".join(why_parts)
        if b is None:
            codes.append({"码": "M03", "类型": "删句"})
        if not codes and translated:
            codes = [{"码": "M04", "类型": "译句"}]
            why = "跨语言改写：该句是原文对应译文，逐句可对照"
        if not codes:
            codes = [{"码": "M02", "类型": "无检测码的改动"}]
        if not why:
            why = "该句被改动但无检测码命中，需人工确认改动理由"
        rows.append({
            "序号": len(rows) + 1, "para": a["para"], "sent": a["sent"],
            "位置": f"段{a['para']}·句{a['sent']}",
            "改后位置": f"段{b['para']}·句{b['sent']}" if b else "（已删除）",
            "修改类型": codes,
            "原文": a["text"], "改文": b["text"] if b else "—",
            "相似度": round(r, 3) if b else 0.0,
            "修改理由": why,
            "置信度": min([h["confidence"] for h in hits], default=None),
            "词级变更": word_diff(a["text"], b["text"]) if b else [],
        })
    return rows, leftover


# ============================ A 组：缩写与符号 ============================
DEF_RE = re.compile(r"([A-Za-z][A-Za-z\-]+(?:\s+[A-Za-z\-]+){0,5}|[\u4e00-\u9fff]{2,20})"
                    r"\s*[\(（]\s*([A-Z][A-Z0-9\-]{1,7})\s*[\)）]")
STOPWORDS = ("the", "a", "an", "of", "in", "on", "and", "with", "for", "to", "by",
             "与", "的", "和", "及", "由", "在")
CJK_BREAK = re.compile(r"[的了与和及为在用到对将从把被是由本其该此中者，、；：]")


def _cjk_tail(s: str) -> str:
    """中文全称：取最后一个助词/顿号之后的片段，避免把「本研究进行了」当全称。"""
    parts = [p for p in CJK_BREAK.split(s) if p]
    return parts[-1] if parts else s


def abbrevs(text: str) -> list:
    out = []
    for m in DEF_RE.finditer(text):
        full, abbr = m.group(1).strip(), m.group(2)
        for w in STOPWORDS:
            if full.lower().startswith(w + " "):
                full = full[len(w) + 1:]
        if CJK.search(full) and not re.search(r"[A-Za-z]", full):
            full = _cjk_tail(full)
        if len(abbr) < 2 or (abbr.isupper() and len(set(abbr)) == 1):
            continue
        out.append({"缩写": abbr, "全称": full, "pos": m.start()})
    return out


def abbrev_checks(src: str, pol: str, sents_src: list, sents_pol: list) -> tuple:
    """A 组：首现定义是否保留、是否有未定义缩写、一缩多义、单位写法是否统一。"""
    probs, rows = [], []
    src_defs = abbrevs(src)
    pol_defs = abbrevs(pol)
    src_by, pol_by = {}, {}
    for d in src_defs:
        src_by.setdefault(d["缩写"], []).append(d)
    for d in pol_defs:
        pol_by.setdefault(d["缩写"], []).append(d)
    for abbr, defs in src_by.items():
        fulls = []
        for d in defs:
            if d["全称"].lower() not in [x.lower() for x in fulls]:
                fulls.append(d["全称"])
        loc_s = locate(sents_src, defs[0]["pos"])
        pol_hits = [m.start() for m in re.finditer(rf"\b{re.escape(abbr)}\b", pol)]
        pol_def = pol_by.get(abbr, [])
        status = "一致"
        if len(fulls) > 1:
            status = "一缩多义"
            probs.append({"code": "A03", "detail":
                          f"缩写 {abbr} 在原文对应多个全称：{fulls[:3]}"})
        if not pol_hits:
            status = "改后未使用"
        elif not pol_def:
            status = "改后首现缺全称"
            probs.append({"code": "A01", "detail":
                          f"缩写 {abbr} 下次出现未给全称（原文首现给了「{fulls[0]}」）"})
        elif pol_hits[0] < pol_def[0]["pos"]:
            status = "改后先裸用后定义"
            probs.append({"code": "A02", "detail":
                          f"缩写 {abbr} 在改后稿第 {pol_hits[0]} 字符处先用，"
                          f"定义在第 {pol_def[0]['pos']} 字符处"})
        rows.append({"缩写": abbr, "全称": fulls[0] if fulls else "",
                     "原文首现": f"段{loc_s['para']}·句{loc_s['sent']}",
                     "改后首现": (f"段{locate(sents_pol, pol_hits[0])['para']}·"
                                 f"句{locate(sents_pol, pol_hits[0])['sent']}")
                                 if pol_hits else "未出现",
                     "判定": status})
    defined = set(src_by)
    for abbr in sorted(set(re.findall(r"\b[A-Z][A-Z0-9]{1,6}\b", pol)) - defined):
        n = len(re.findall(rf"\b{re.escape(abbr)}\b", pol))
        if n >= 2 and not pol_by.get(abbr) and abbr not in ("DNA", "RNA", "ANOVA", "SD", "SE"):
            probs.append({"code": "A02", "detail":
                          f"改后稿中 {abbr} 出现 {n} 次但从未给出全称（原文也没有定义）"})
    for group in SYMBOL_CLASSES:
        used = [v for v in group if v in pol]
        if len(used) > 1:
            probs.append({"code": "A04", "detail":
                          f"同一符号存在多种写法：{'、'.join(used)}（应统一一种）"})
    return probs, rows


# ============================ T 组：术语 ============================
def term_checks(glossary: dict, src: str, pol: str, sents_src: list) -> tuple:
    probs = []
    if not glossary["rows"]:
        return probs, []
    rs, rp = cjk_ratio(src), cjk_ratio(pol)
    if rs < 0.15 and rp > 0.4:
        lang = "en→zh"
    elif rs > 0.4 and rp < 0.15:
        lang = "zh→en"
    else:
        lang = "same"
    rows = term_rows(glossary, src, pol, sents_src, lang)
    if glossary["conflicts"]:
        c = glossary["conflicts"][0]
        probs.append({"code": "T02", "detail":
                      f"术语表内部冲突：{c['原文']} 同时映射到 {c['已用']} 与 {c['另一写法']}"})
    for r in rows:
        if r["原文出现"] == 0:
            continue
        if lang == "same":
            continue  # 同语润色：术语表只锁写法，译名一致性不适用
        if r["改后译名出现"] == 0:
            probs.append({"code": "T01", "detail":
                          f"术语「{r['原文']}」应按术语表写作「{r['译名']}」，改后稿未出现"})
        elif r["判定"] == "译名不足":
            probs.append({"code": "T01", "detail":
                          f"术语「{r['原文']}」原文出现 {r['原文出现']} 次，"
                          f"改后仅 {r['改后译名出现']} 次使用译名"})
        if r["原文"] in pol:
            probs.append({"code": "T03", "detail":
                          f"术语「{r['原文']}」在改后稿中原样残留 {pol.count(r['原文'])} 处，"
                          f"与术语表写法「{r['译名']}」混用"})
    return probs, rows


# ============================ S 组：文体越界 ============================
def load_profiles() -> dict:
    if not PROFILE_FILE.exists():
        return {}
    try:
        return json.loads(PROFILE_FILE.read_text(encoding="utf-8")).get("profiles", {})
    except Exception:
        return {}


def pick_style(given: str | None, case: dict, src_path: Path, src: str) -> tuple:
    """文体：参数 > case.json > 关键词推断；推断的都记一笔，不假装确定。"""
    profiles = load_profiles()
    if given:
        if given in profiles:
            return given, "调用方指定"
        return None, f"未知文体「{given}」"
    for k in ("style", "genre", "register", "体裁", "文体"):
        v = case.get(k)
        if isinstance(v, str) and v in profiles:
            return v, "case.json"
    hay = (src_path.name + " " + src[:400]).lower()
    for key, hints in (("review-response", ("审稿", "回复", "response to reviewer")),
                       ("grant", ("基金", "申请书", "grant", "proposal")),
                       ("dissertation", ("学位论文", "thesis", "dissertation")),
                       ("abstract", ("摘要", "abstract")),
                       ("journal", ("论文", "manuscript", "paper"))):
        if any(h in hay for h in hints):
            return key, "按文件名/开头推断"
    return None, "未指定"


def style_checks(pol: str, style: str | None) -> tuple:
    probs, rows = [], []
    if not style:
        return probs, rows
    prof = load_profiles().get(style, {})
    for rule in prof.get("avoid", []):
        for m in re.finditer(rule["pattern"], pol, re.I):
            probs.append({"code": "S01", "detail":
                          f"{prof.get('label', style)} 文体不宜出现「{m.group(0)}」："
                          f"{rule['why']}"})
            if len(probs) >= 6:
                break
    rows.append({"文体": prof.get("label", style), "禁用表达命中": len(probs),
                 "规则条数": len(prof.get("avoid", []))})
    return probs, rows


# ============================ M 组：最小编辑 ============================
def minimal_edit(rows: list) -> tuple:
    """M01 整句重写、M02 无留痕改动；返回问题与统计。"""
    probs, rewrites, unrecorded = [], [], []
    for r in rows:
        if r["改文"] == "—" or r["相似度"] >= CHANGE_RATIO:
            continue
        codes = [c["码"] for c in r["修改类型"]]
        if r["相似度"] < REWRITE_RATIO:
            rewrites.append(r)
            probs.append({"code": "M01", "detail":
                          f"{r['位置']} 相似度 {r['相似度']}，疑似整句重写；"
                          f"最小编辑原则下应逐处改"})
        if "M02" in codes:
            unrecorded.append(r)
    if unrecorded:
        probs.append({"code": "M02", "detail":
                      f"{len(unrecorded)} 处改动无检测码支撑理由："
                      + "；".join(x["位置"] for x in unrecorded[:5])})
    return probs, rewrites, unrecorded


# ============================ 评测（同一函数用于改后稿与基线） ============================
def lang_direction(src: str, pol: str) -> str:
    """判断两侧是否跨语言：同语润色与翻译任务的判据不同。"""
    rs, rp = cjk_ratio(src), cjk_ratio(pol)
    if rs < 0.15 and rp > 0.4:
        return "en→zh"
    if rs > 0.4 and rp < 0.15:
        return "zh→en"
    return "same"


def evaluate(src: str, sents_src: list, out_text: str, glossary: dict,
             issues: list, style: str | None) -> dict:
    ps, pp = profile(src), profile(out_text)
    sents_out = sentences(out_text)
    lang = lang_direction(src, out_text)
    translated = lang != "same"
    k_probs, drift, k_rows, strength = conservation(ps, pp, translated)
    t_probs, t_rows = term_checks(glossary, src, out_text, sents_src)
    a_probs, a_rows = abbrev_checks(src, out_text, sents_src, sents_out)
    s_probs, s_rows = style_checks(out_text, style)
    edits, extra = edit_records(sents_src, out_text, issues, translated)
    if translated:
        # 跨语言改写：逐句相似度不是最小编辑的判据，M01/M02 不判
        m_probs, rewrites, unrecorded = [], [], []
    else:
        m_probs, rewrites, unrecorded = minimal_edit(edits)
    # 检出了但改后稿里该片段仍在：具体问题没被改掉（不是"这一句动过"就算处理）
    npol = re.sub(r"\s+", " ", out_text).lower()
    unhandled = []
    for i in issues:
        span = re.sub(r"\s+", " ", i["span"]).strip().lower()
        if len(span) >= 2 and span in npol:
            unhandled.append(dict(i, 未处理原因="该片段在改后稿中原样出现"))
    probs = k_probs + t_probs + a_probs + s_probs + m_probs
    return {
        "problems": probs,
        "problem_codes": Counter(p["code"] for p in probs),
        "drift": drift,
        "k_rows": k_rows,
        "strength": strength,
        "term_rows": t_rows,
        "abbrev_rows": a_rows,
        "style_rows": s_rows,
        "edits": edits,
        "extra": extra,
        "rewrites": rewrites,
        "unrecorded": unrecorded,
        "unhandled": unhandled,
        "profile_src": ps,
        "profile_out": pp,
        "sents_out": sents_out,
        "lang": lang,
        "translated": translated,
        "changed_sentences": len([e for e in edits if e["改文"] != "—"]),
        "mean_ratio": round(sum(e["相似度"] for e in edits) / len(edits), 3) if edits else None,
    }


# ============================ 段落覆盖 ============================
def paragraph_coverage(text: str, sents_src: list, issues: list, edits: list) -> list:
    rows = []
    for pi, (s, e) in enumerate(paragraphs(text), 1):
        seg = text[s:e]
        ss = [x for x in sents_src if x["para"] == pi]
        codes = sorted({i["code"] for i in issues if i["para"] == pi})
        touched = [x for x in edits if x["位置"].startswith(f"段{pi}·")]
        sims = [x["相似度"] for x in touched]
        rows.append({
            "段": pi, "类型": para_kind(seg), "字符数": len(seg),
            "句数": len(ss), "码": codes,
            "已改动句": len([x for x in touched if x["改文"] != "—"]),
            "相似度": round(sum(sims) / len(sims), 3) if sims else None,
            "处理状态": ("已改动" if any(x["改文"] != "—" for x in touched)
                        else ("有检出未改" if codes else "无需改动")),
        })
    return rows


# ============================ 覆盖清单（题面处理项逐条） ============================
def coverage_table(ctx: dict) -> list:
    n_para = ctx["n_para"]
    touched = ctx["n_para_touched"]
    terms = ctx["term_rows"]
    zero_hit = [t for t in terms if t["原文出现"] == 0]
    edits = ctx["edits"]
    unrec = ctx["unrecorded"]
    rows = []

    def add(item, basis, status, evidence):
        rows.append({"处理项": item, "题面依据": basis, "状态": status, "证据": evidence})

    add("指定章节逐段处理", "指定章节是否逐段处理",
        "covered" if n_para and touched else "missing",
        f"{n_para} 段，其中 {touched} 段有改动或检出；逐段见 `bilingual.md`")
    if not ctx["glossary_rows"]:
        add("术语表建全", "术语表是否建全", "missing",
            "未提供术语表（`--glossary`）；术语一致性未核验，记入 gaps")
    elif zero_hit:
        add("术语表建全", "术语表是否建全", "partial",
            f"术语表 {ctx['glossary_rows']} 条，其中 {len(zero_hit)} 条在原文零命中"
            f"：{'、'.join(t['原文'] for t in zero_hit[:5])}")
    else:
        add("术语表建全", "术语表是否建全", "covered",
            f"术语表 {ctx['glossary_rows']} 条全部命中原文；逐条出现次数见 `bilingual.md`")
    add("缩写与符号首次出现统一", "缩写与符号的首次出现是否统一",
        "covered", f"缩写清单 {len(ctx['abbrev_rows'])} 条；不一致 "
        f"{ctx['n_abbrev_problems']} 条，见 `bilingual.md`「缩写首现清单」")
    add("逐段双语对照", "中英双语对照与修改清单是否齐全",
        "covered" if ctx["n_para"] else "missing",
        f"`bilingual.md` 逐段给原文/改后/相似度/术语对照，共 {n_para} 段")
    add("逐句修改清单", "中英双语对照与修改清单是否齐全",
        "covered" if edits else "partial",
        f"`changes.md` 逐句 {len(edits)} 条，每条含原文、改文、类型、理由")
    add("原文—改文对照", "原文—改文对照、修改类型标签与修改理由",
        "covered" if edits and all(e["原文"] and e["改文"] for e in edits) else "partial",
        f"{len(edits)} 条记录中原文与改文成对给出，并附段落/句号定位")
    add("修改类型与理由", "每一处修改是否有原文、改文、修改类型与理由",
        "partial" if unrec else ("covered" if edits else "partial"),
        f"无检测码支撑的改动 {len(unrec)} 处"
        + (f"（{'; '.join(x['位置'] for x in unrec[:3])}）" if unrec else "；全部改动均有码与理由"))
    add("数据与单位守恒", "数据与单位有没有被顺手改掉",
        "covered", f"`changes.md`「守恒核对」{len(ctx['k_rows'])} 项：数字/单位/引用/图表指向/公式/否定")
    add("限定强度守恒", "限定强度有没有被加强或削弱",
        "covered", "加强词与留有余地词两侧同口径计数，削弱报 K02、凭空加强报 K07")
    add("引用编号与图表指向", "引用编号与图表指向是否对得上",
        "covered", "引用与图号做集合比对（K04/K05），不只是比数量")
    add("最小编辑", "语义保持与最小编辑",
        "covered" if not ctx["translated"] else "partial",
        (f"改动句 {ctx['changed']} 句，平均相似度 {ctx['mean_ratio']}；"
         f"整句重写 {ctx['n_rewrites']} 处（M01）") if not ctx["translated"] else
        f"跨语言改写（{ctx['lang']}）：逐句相似度不作为最小编辑判据；"
        f"逐句对应关系见 `changes.md`")
    add("目标文体贴合", "改写后的文本是否符合目标文体与语域",
        "covered" if ctx["style_src"] == "调用方指定" or ctx["style_src"] == "case.json"
        else ("partial" if ctx["style_src"] else "missing"),
        f"文体判定：{ctx['style'] or '未定'}（{ctx['style_src']}）")
    add("对照条件与增益", "对照条件是否一致：同一份原文、同一术语表、同一验收标准",
        "covered" if ctx.get("baseline") else "missing",
        "同一 `evaluate()` 函数、同一术语表、同一阈值跑基线与改后稿，见 `compare.md`")
    return rows


# ============================ 主流程 ============================
def write_blocked(out: Path, case_id: str, why: str) -> int:
    out.mkdir(parents=True, exist_ok=True)
    res = {"case_id": case_id, "skill_version": VERSION, "status": "blocked",
           "目标产物": None,
           "事实与来源映射": {"条数": 0, "逐条": []},
           "版本与边界": {"gaps": [why], "problems": [], "人工确认项": []},
           "gaps": [why], "uncertainty": ["无输入：未做任何检测"],
           "deliverables": ["result.json"]}
    (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": False, "status": "blocked", "why": why}, ensure_ascii=False))
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="学术语言润色：检测、逐句留痕、守恒核对、对照评测")
    ap.add_argument("--source", required=True, help="原稿文件（.md/.txt/.tex）")
    ap.add_argument("--polished", default=None, help="改后稿；不给则做最小词面修复")
    ap.add_argument("--glossary", default=None, help="术语表（Markdown 两列/三列表）")
    ap.add_argument("--baseline", default=None, help="基线改后稿：用同一规则跑对照")
    ap.add_argument("--style", default=None,
                    help="目标文体：journal|dissertation|grant|review-response|abstract")
    ap.add_argument("--out", default="output", help="产物目录")
    ap.add_argument("--work", default=None, help="可选：额外写一份 issues.json 的目录")
    ap.add_argument("--case-id", default=None)
    a = ap.parse_args(argv)

    t0 = time.perf_counter()
    src_p = Path(a.source)
    if not src_p.exists():
        return write_blocked(Path(a.out), a.case_id or src_p.stem, f"原文不存在：{src_p}")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    src, src_enc = read_text(src_p)

    # 平台形态：case.json 与原稿同目录，带 case_id / output_contract / 体裁
    case, case_path, contract = {}, None, []
    cj = src_p.parent / "case.json"
    if cj.exists():
        try:
            case = json.loads(read_text(cj)[0])
            case_path = str(cj)
        except Exception:
            case = {}
    case_id = a.case_id or str(case.get("case_id") or case.get("id") or src_p.stem)
    oc = case.get("output_contract")
    contract = list(oc) if isinstance(oc, (dict, list)) else []

    glossary = load_glossary(Path(a.glossary) if a.glossary else None)
    sents_src = sentences(src)
    issues = detect(src, sents_src)
    style, style_src = pick_style(a.style, case, src_p, src)
    gaps, applied = [], []

    pol_path = Path(a.polished) if a.polished else None
    if pol_path and pol_path.exists():
        pol, pol_enc = read_text(pol_path)
        pol_from = "外部提供"
    else:
        if pol_path:
            gaps.append(f"改后稿不存在：{pol_path}，改用内置最小词面修复")
        pol, applied = auto_rewrite(src)
        pol_enc = src_enc
        pol_from = "内置最小词面规则" if applied else "未改写"
        if applied:
            gaps.append("改后稿由内置词面规则生成（逐条见 事实与来源映射.规则命中），"
                        "仅做词面替换，未动结论强度")
        else:
            gaps.append("未提供改后稿，且原文未命中可机械修复的模式："
                        "polished.md 为原文回填，待模型或人工改写")

    if not glossary["rows"]:
        gaps.append("未提供术语表：术语一致性与译名对齐未核验")
    if glossary["conflicts"]:
        gaps.append(f"术语表内部冲突 {len(glossary['conflicts'])} 处，取首条译名继续核验，"
                    "需人工裁决")
    if not style:
        gaps.append(f"目标文体未定（{style_src}）：文体越界检查跳过")
    elif style_src in ("按文件名/开头推断",):
        gaps.append(f"目标文体由推断得来（{style}），需人工确认")
    if src_enc not in ("utf-8", "utf-8-bom"):
        gaps.append(f"原稿编码为 {src_enc}，已按该编码解码；如有乱码需人工核对")
    if pol_enc not in ("utf-8", "utf-8-bom") and pol_from == "外部提供":
        gaps.append(f"改后稿编码为 {pol_enc}，已按该编码解码")

    ev = evaluate(src, sents_src, pol, glossary, issues, style)
    problems = ev["problems"]
    status = ("needs_polish" if pol_from == "未改写"
              else ("success" if not problems else "partial"))

    cov = paragraph_coverage(src, sents_src, issues, ev["edits"])
    n_para = len(cov)
    n_touched = len([c for c in cov if c["处理状态"] in ("已改动", "有检出未改")])
    n_abbrev_problems = len([p for p in problems if p["code"] in ("A01", "A02", "A03", "A04")])
    ctx = {
        "n_para": n_para, "n_para_touched": n_touched,
        "glossary_rows": len(glossary["rows"]), "term_rows": ev["term_rows"],
        "abbrev_rows": ev["abbrev_rows"], "n_abbrev_problems": n_abbrev_problems,
        "edits": ev["edits"], "unrecorded": ev["unrecorded"],
        "k_rows": ev["k_rows"], "changed": ev["changed_sentences"],
        "mean_ratio": ev["mean_ratio"], "n_rewrites": len(ev["rewrites"]),
        "translated": ev["translated"], "lang": ev["lang"],
        "style": style, "style_src": style_src, "baseline": a.baseline,
    }
    coverage = coverage_table(ctx)

    # ---------------- 基线对照：同一 evaluate、同一术语表、同一阈值 ----------------
    cmp_rows, cmp_verdict = [], None
    if a.baseline:
        bp = Path(a.baseline)
        if not bp.exists():
            gaps.append(f"基线文件不存在：{bp}，未做对照")
        else:
            base, base_enc = read_text(bp)
            be = evaluate(src, sents_src, base, glossary, issues, style)
            def brief(e):
                return {"问题": len(e["problems"]),
                        "K": sum(1 for p in e["problems"] if p["code"].startswith("K")),
                        "T": sum(1 for p in e["problems"] if p["code"].startswith("T")),
                        "A": sum(1 for p in e["problems"] if p["code"].startswith("A")),
                        "S": sum(1 for p in e["problems"] if p["code"].startswith("S")),
                        "M": sum(1 for p in e["problems"] if p["code"].startswith("M")),
                        "未处理检出": len(e["unhandled"]),
                        "改动句": e["changed_sentences"],
                        "平均相似度": e["mean_ratio"]}
            mb, mp = brief(be), brief(ev)
            for k in mb:
                b, s = mb[k], mp[k]
                better = "持平"
                if isinstance(b, (int, float)) and isinstance(s, (int, float)):
                    if k == "平均相似度":
                        # 相似度越接近 1 越接近最小编辑，方向与其他指标相反
                        better = "更好" if s > b else ("更差" if s < b else "持平")
                    else:
                        better = "更好" if s < b else ("更差" if s > b else "持平")
                cmp_rows.append({"指标": k, "基线": b, "本 Skill": s, "判定": better})
            better = sum(1 for r in cmp_rows if r["判定"] == "更好")
            worse = sum(1 for r in cmp_rows if r["判定"] == "更差")
            cmp_verdict = {"更好项": better, "更差项": worse,
                           "结论": ("本 Skill 优于基线" if better > worse else
                                    ("与基线持平" if better == worse else "本 Skill 劣于基线"))}
            (out / "compare.md").write_text(render_compare(cmp_rows, cmp_verdict,
                                                           src_p, bp, glossary, style,
                                                           be["problems"]),
                                            encoding="utf-8")

    # ---------------- 写产物 ----------------
    (out / "polished.md").write_text(pol, encoding="utf-8")
    (out / "changes.md").write_text(render_changes(ev, status, pol_from, glossary),
                                    encoding="utf-8")
    (out / "bilingual.md").write_text(
        render_bilingual(src, pol, sents_src, ev, cov, glossary, issues), encoding="utf-8")
    (out / "coverage.md").write_text(render_coverage(coverage, ev, status, cov),
                                     encoding="utf-8")

    issue_dump = {"case_id": case_id, "source": str(src_p), "编码": src_enc,
                  "段落数": n_para, "句数": len(sents_src), "issues": issues,
                  "counts": {c: sum(1 for i in issues if i["code"] == c)
                             for c, *_ in DETECT}}
    issue_json = json.dumps(issue_dump, ensure_ascii=False, indent=1)
    (out / "issues.json").write_text(issue_json, encoding="utf-8")
    if a.work:
        work = Path(a.work)
        work.mkdir(parents=True, exist_ok=True)
        (work / "issues.json").write_text(issue_json, encoding="utf-8")

    must_confirm = []
    for p in problems:
        must_confirm.append({"类型": p["code"], "项": p["detail"]})
    for r in ev["unrecorded"]:
        must_confirm.append({"类型": "M02", "项": f"{r['位置']} 改动无检测码"})
    for i in ev["unhandled"][:20]:
        must_confirm.append({"类型": i["code"],
                             "项": f"{i['code']} 检出未改：段{i['para']}·句{i['sent']} "
                                    f"「{i['span']}」"})

    deliverables = ["polished.md", "changes.md", "bilingual.md", "coverage.md",
                    "issues.json", "result.json"]
    if a.baseline and (out / "compare.md").exists():
        deliverables.append("compare.md")

    result = {
        "case_id": case_id,
        "skill_version": VERSION,
        "status": status,
        "输入标识": {
            "原文": {"path": str(src_p), "sha256_16": sha16(src), "编码": src_enc,
                     "字符数": len(src), "段落数": n_para, "句数": len(sents_src)},
            "改后稿": {"path": str(pol_path) if (pol_path and pol_path.exists()) else None,
                       "来源": pol_from, "sha256_16": sha16(pol), "编码": pol_enc},
            "术语表": {"path": glossary["path"], "条数": len(glossary["rows"]),
                       "冲突": glossary["conflicts"]},
            "基线": ({"path": a.baseline, "sha256_16": sha16(read_text(Path(a.baseline))[0])}
                     if a.baseline and Path(a.baseline).exists() else None),
            "目标文体": {"值": style, "来源": style_src},
            "case_json": case_path,
        },
        "目标产物": {
            "path": "polished.md",
            "字符数": len(pol), "句数": len(ev["sents_out"]), "段落数": len(paragraphs(pol)),
            "改后稿已给": pol_from == "外部提供",
            "改动句数": ev["changed_sentences"],
            "内容": ("改后全文" if pol_from == "外部提供" else
                     ("改后全文（内置词面规则）" if pol_from == "内置最小词面规则"
                      else "未改写，回填原文")),
        },
        "事实与来源映射": {
            "证据单位": "code + span（问题码 + 原文片段）+ 段号/句号 + 句文本",
            "语言方向": ev["lang"],
            "条数": len(issues),
            "逐条": [{"码": i["code"], "类型": i["name"], "原文片段": i["span"],
                      "段": i["para"], "句": i["sent"], "句文本": i["sentence"],
                      "理由": i["reason"], "置信度": i["confidence"]} for i in issues],
            "规则命中": applied,
            "逐句修改": ev["edits"],
            "未处理的检出": [{"码": i["code"], "段": i["para"], "句": i["sent"],
                              "片段": i["span"]} for i in ev["unhandled"]],
            "术语表条数": len(glossary["rows"]),
            "术语对照": ev["term_rows"],
            "缩写首现": ev["abbrev_rows"],
            "守恒核对": ev["k_rows"],
            "强度变化": ev["strength"],
            "改后稿计数": counts(ev["profile_out"]),
            "原稿计数": counts(ev["profile_src"]),
        },
        "版本与边界": {
            "gaps": gaps,
            "problems": problems,
            "人工确认项": must_confirm,
            "drift": ev["drift"],
            "改动摘要": {"改动句": ev["changed_sentences"],
                         "删除句": len([e for e in ev["extra"] if e["类型"] == "新增句"]),
                         "新增句": len(ev["extra"]),
                         "平均相似度": ev["mean_ratio"],
                         "整句重写": len(ev["rewrites"]),
                         "无留痕改动": len(ev["unrecorded"]),
                         "未处理检出": len(ev["unhandled"])},
        },
        "段落覆盖": cov,
        "覆盖清单": coverage,
        "检查码统计": dict(ev["problem_codes"]),
        "基线对照": ({"指标": cmp_rows, "结论": cmp_verdict,
                      "基线问题": be["problems"],
                      "基线检查码统计": dict(be["problem_codes"]),
                      "条件": {"同一原稿": sha16(src), "同一术语表": glossary["path"],
                               "同一规则版本": VERSION, "同一函数": "evaluate()"}}
                     if cmp_rows else None),
        "stats": {
            "issues": len(issues), "problems": len(problems),
            "polished": pol_from != "未改写", "glossary_terms": len(glossary["rows"]),
            "paragraphs": n_para, "sentences": len(sents_src),
            "profile_source": counts(ev["profile_src"]),
            "profile_polished": counts(ev["profile_out"]),
            "elapsed_s": round(time.perf_counter() - t0, 3),
        },
        "checks": problems,
        "gaps": gaps,
        "uncertainty": gaps + [m["项"] for m in must_confirm[:10]],
        "deliverables": deliverables,
        "output_contract": contract,
    }
    for k in contract:
        if str(k) in result:
            continue
        result[str(k)] = None
        gaps.append(f"output_contract 字段「{k}」无对应产出，置 null")

    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "issues": len(issues),
                      "edits": len(ev["edits"]), "problems": len(problems),
                      "confirm": len(must_confirm),
                      "deliverables": len(deliverables)}, ensure_ascii=False))
    return 0


# ============================ 渲染 ============================
def render_changes(ev: dict, status: str, pol_from: str, glossary: dict) -> str:
    L = ["# 修改记录（逐句：原文 / 改文 / 修改类型 / 理由）", "",
         f"- 状态：`{status}`；改后稿来源：{pol_from}",
         f"- 逐句记录 {len(ev['edits'])} 条；改动句 {ev['changed_sentences']} 句；"
         f"平均相似度 {ev['mean_ratio']}",
         f"- 未处理检出 {len(ev['unhandled'])} 条（片段仍在改后稿中）；"
         f"需人工确认 {len(ev['problems']) + len(ev['unrecorded']) + len(ev['unhandled'])} 条",
         f"- 术语表 {len(glossary['rows'])} 条", ""]
    n = 0
    if ev["edits"]:
        n += 1
        L += [f"## {n} 逐句修改说明", "",
              "| # | 位置 | 改后位置 | 修改类型 | 原文 | 改文 | 修改理由 | 置信度 |",
              "|-|-|-|-|-|-|-|-|"]
        for e in ev["edits"]:
            kinds = "、".join(f"{c['码']} {c['类型']}" for c in e["修改类型"])
            L.append(f"| {e['序号']} | {e['位置']} | {e['改后位置']} | {kinds} | "
                     f"{cell(e['原文'])} | {cell(e['改文'])} | {cell(e['修改理由'])} | "
                     f"{e['置信度'] if e['置信度'] is not None else '—'} |")
        n += 1
        L += ["", f"## {n} 词级变更（每处替换的原文与改文）", "",
              "| # | 位置 | 操作 | 原文 | 改文 |", "|-|-|-|-|-|"]
        for e in ev["edits"]:
            for w in e["词级变更"]:
                L.append(f"| {e['序号']} | {e['位置']} | {w['操作']} | "
                         f"{cell(w['原文'], 60)} | {cell(w['改文'], 60)} |")
    if ev["extra"]:
        n += 1
        L += ["", f"## {n} 新增句", "", "| # | 改后位置 | 改文 |", "|-|-|-|"]
        for k, x in enumerate(ev["extra"], 1):
            L.append(f"| {k} | {x['位置']} | {cell(x['改文'])} |")
    n += 1
    L += ["", f"## {n} 检出但改后稿未处理（该片段仍在改后稿中）", "",
          "| # | 码 | 片段 | 位置 | 理由 | 建议 |", "|-|-|-|-|-|-|"]
    if ev["unhandled"]:
        for k, i in enumerate(ev["unhandled"], 1):
            L.append(f"| {k} | {i['code']} | `{cell(i['span'], 60)}` | "
                     f"段{i['para']}·句{i['sent']} | {cell(i['reason'])} | "
                     f"按该码判据改这一处后重跑核验 |")
    else:
        L.append("| — | — | 无 | — | 检出片段的写法在改后稿中都不再出现 | — |")
    n += 1
    L += ["", f"## {n} 守恒核对（原文与改后同口径）", "",
          "| 项 | 原文 | 改后 | 判定 |", "|-|-|-|-|"]
    for r in ev["k_rows"]:
        L.append(f"| {r['项']} | {r['原文']} | {r['改后']} | {r['判定']} |")
    n += 1
    L += ["", f"## {n} 核验结论", "", "| 码 | 说明 |", "|-|-|"]
    if ev["problems"]:
        for p in ev["problems"]:
            L.append(f"| {p['code']} | {cell(p['detail'])} |")
    else:
        L.append("| — | 无守恒/术语/文体/最小编辑问题 |")
    return "\n".join(L) + "\n"


def render_bilingual(src: str, pol: str, sents_src: list, ev: dict, cov: list,
                     glossary: dict, issues: list) -> str:
    L = ["# 逐段双语对照（原文 ↔ 改后 ↔ 术语）", "",
         f"- 原文 {len(cov)} 段 / {len(sents_src)} 句；改后 {len(paragraphs(pol))} 段",
         f"- 术语表 {len(glossary['rows'])} 条；缩写 {len(ev['abbrev_rows'])} 条", "",
         "## 1 段落对照", "",
         "| 段 | 类型 | 原文 | 改后 | 相似度 | 改动句 | 命中码 | 术语（原文→译名，改后译名命中） |",
         "|-|-|-|-|-|-|-|-|"]
    pol_paras = [pol[s:e].strip() for s, e in paragraphs(pol)]
    for c in cov:
        pi = c["段"]
        src_seg = src[paragraphs(src)[pi - 1][0]:paragraphs(src)[pi - 1][1]].strip()
        pol_seg = pol_paras[pi - 1] if pi - 1 < len(pol_paras) else ""
        terms = []
        for t in ev["term_rows"]:
            if pi in t["所在段"]:
                terms.append(f"{t['原文']}→{t['译名']}（{t['判定']}）")
        L.append(f"| {pi} | {c['类型']} | {cell(src_seg, 400)} | {cell(pol_seg, 400)} | "
                 f"{c['相似度'] if c['相似度'] is not None else '—'} | {c['已改动句']} | "
                 f"{'、'.join(c['码']) or '—'} | {'；'.join(terms) if terms else '—'} |")
    L += ["", "## 2 术语对照汇总", "",
          "| 原文 | 术语表译名 | 原文出现 | 改后译名出现 | 所在段 | 判定 |",
          "|-|-|-|-|-|-|"]
    if ev["term_rows"]:
        for t in ev["term_rows"]:
            L.append(f"| {cell(t['原文'], 40)} | {cell(t['译名'], 40)} | {t['原文出现']} | "
                     f"{t['改后译名出现']} | {'、'.join(str(x) for x in t['所在段']) or '—'} | "
                     f"{t['判定']} |")
    else:
        L.append("| — | — | — | — | — | 未提供术语表 |")
    L += ["", "## 3 缩写首现清单", "",
          "| 缩写 | 全称 | 原文首现 | 改后首现 | 判定 |", "|-|-|-|-|-|"]
    if ev["abbrev_rows"]:
        for x in ev["abbrev_rows"]:
            L.append(f"| {x['缩写']} | {cell(x['全称'], 60)} | {x['原文首现']} | "
                     f"{x['改后首现']} | {x['判定']} |")
    else:
        L.append("| — | 原文未出现「全称（缩写）」形式的定义 | — | — | 不适用 |")
    L += ["", "## 4 句级定位索引", "",
          "| 段 | 句 | 码 | 原文片段 |", "|-|-|-|-|"]
    if issues:
        for i in issues:
            L.append(f"| {i['para']} | {i['sent']} | {i['code']} | `{cell(i['span'], 60)}` |")
    else:
        L.append("| — | — | — | 未检出问题 |")
    return "\n".join(L) + "\n"


def render_coverage(cov: list, ev: dict, status: str, para_rows: list) -> str:
    L = ["# 处理项覆盖清单（题面处理项逐条）", "",
         f"- 状态：`{status}`；覆盖项 {len(cov)} 条", "",
         "| 处理项 | 题面依据 | 状态 | 证据 |", "|-|-|-|-|"]
    for c in cov:
        L.append(f"| {c['处理项']} | {cell(c['题面依据'])} | {c['状态']} | {cell(c['证据'], 200)} |")
    L += ["", "## 段落覆盖明细", "",
          "| 段 | 类型 | 字符数 | 句数 | 码 | 已改动句 | 处理状态 |",
          "|-|-|-|-|-|-|-|"]
    for c in para_rows:
        L.append(f"| {c['段']} | {c['类型']} | {c['字符数']} | {c['句数']} | "
                 f"{'、'.join(c['码']) or '—'} | {c['已改动句']} | {c['处理状态']} |")
    return "\n".join(L) + "\n"


def render_compare(rows: list, verdict: dict | None, src_p: Path, base_p: Path,
                   glossary: dict, style: str | None, base_problems: list) -> str:
    L = ["# 基线对照（同一原稿 / 同一术语表 / 同一规则与阈值）", "",
         f"- 原稿：`{src_p.name}`；基线：`{base_p.name}`",
         f"- 术语表条数：{len(glossary['rows'])}；目标文体：{style or '未定'}",
         f"- 对照方法：同一 `evaluate()` 函数、同一阈值、同一检查码，"
         f"仅替换被评文本", "",
         "| 指标 | 基线 | 本 Skill | 判定 |", "|-|-|-|-|"]
    for r in rows:
        L.append(f"| {r['指标']} | {r['基线']} | {r['本 Skill']} | {r['判定']} |")
    if verdict:
        L += ["", f"**结论**：{verdict['结论']}（更好 {verdict['更好项']} 项 / "
                  f"更差 {verdict['更差项']} 项）"]
    L += ["", "## 基线逐条命中（同一 `evaluate()` 的实际输出）", "",
          "| 码 | 说明 |", "|-|-|"]
    if base_problems:
        for p in base_problems:
            L.append(f"| {p['code']} | {cell(p['detail'], 200)} |")
    else:
        L.append("| — | 基线在 K/T/A/S/M 组均无命中 |")
    L += ["", "指标含义：「问题」为 K/T/A/S/M 组问题总数（越少越好）；"
              "「未处理检出」为检出了但片段仍留在改后稿里的条数（越少越好，"
              "但删掉原句也会把它压低——这类改写会改由 K02 强度削弱报出来）；"
              "「平均相似度」为逐句相似度均值（越接近 1 越接近最小编辑）。"]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
