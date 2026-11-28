#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py — 从论文的 JATS 段落材料构建可查询、可增量更新的知识图谱。

设计口径（对应 SKILL.md 第 5 节与 rules/criteria.md）：
  * 输入是段落 JSON，不是 PDF：位置一律用 source_id + paragraph_id，**不用页码**。
  * 抽取是规则式的（词表 + 关系词窗口 + 同句共现），不猜语义。
  * 每条证据带 stance（作者自述 / 他人转述 / 未标明），每条边带 attribution、
    direction_basis 与 applicability：转述、共现、推测不得标 high。
  * 方法/任务标签跨篇合并并留依据；数据集/指标/材料/研究者/结论保留分离并记冲突。
  * 数值结果按篇分离记 measurements，不做跨篇汇总。
  * 查询真跑，结果带子图与证据；不是"文档里说有查询"。
  * 支持增量更新（--append）：既有节点与编号原样保留。
  * 零模型请求，无第三方依赖。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

VERSION = "2.0.0"
SKILL_NAME = "graph-jats"
PKG = Path(__file__).resolve().parent

# ---------------------------------------------------------------- 判定口径

CODES: dict[str, tuple[str, str]] = {
    "G01": ("error", "节点 id 唯一且非空"),
    "G02": ("error", "边端点必须存在"),
    "G03": ("error", "节点与边至少一条证据，且三字段齐全"),
    "G04": ("error", "类型在允许集合内"),
    "G05": ("error", "标签非空且 ≤80 字"),
    "G06": ("error", "关系类型与端点类型签名在允许集合内（方向约束）"),
    "G07": ("error", "quote 必须是该 paragraph_id 原文的子串（证据可回指）"),
    "G08": ("error", "转述/共现/推测不得标 high 置信"),
    "G09": ("error", "增量模式：既有节点 id 与类型、标签全部保留"),
}

# 实体类型：题面点名研究者、材料、方法、任务、指标、结论
ENTITY_TYPES = ("paper", "researcher", "material", "method",
                "dataset", "metric", "task", "finding")

# 关系类型：题面点名使用、比较、优于、支持、矛盾，另加来源绑定类关系
REL_ALLOWED = ("uses_method", "evaluates_on", "reports_metric", "about",
               "mentions_material", "cites_researcher", "authored_by",
               "reports_finding", "compared_with", "outperforms",
               "supports", "contradicts")

# 端点类型签名：G06 用它拦方向错、类型错的关系
SIG: dict[str, set[tuple[str, str]]] = {
    "uses_method": {("paper", "method"), ("researcher", "method")},
    "evaluates_on": {("paper", "dataset"), ("researcher", "dataset")},
    "reports_metric": {("paper", "metric"), ("researcher", "metric"),
                       ("method", "metric")},
    "about": {("paper", "task"), ("researcher", "task")},
    "mentions_material": {("paper", "material"), ("researcher", "material")},
    "cites_researcher": {("paper", "researcher")},
    "authored_by": {("researcher", "paper")},
    "reports_finding": {("paper", "finding")},
    "compared_with": {("method", "method"), ("dataset", "dataset"),
                      ("metric", "metric")},
    "outperforms": {("method", "method")},
    "supports": {("method", "method"), ("method", "finding"),
                 ("finding", "method")},
    "contradicts": {("method", "metric"), ("method", "method"),
                    ("finding", "method")},
}

FIXED_REL = {"method": "uses_method", "dataset": "evaluates_on",
             "metric": "reports_metric", "task": "about",
             "material": "mentions_material"}

CONF = ("high", "medium", "low")
STANCES = ("self", "reported", "unspecified")

ID_KEYS = ("paragraph_id", "paragraphId", "para_id", "pid", "id")
TEXT_KEYS = ("text", "content", "body", "paragraph", "value")

# 提取词表：只认显式表述，宁可少抽也不错抽
LEX = {
    "method": ["bert", "roberta", "electra", "t5", "gpt", "lstm", "gru", "cnn",
               "rnn", "transformer", "attention", "gan", "svm", "crf",
               "random forest", "xgboost", "graph neural network", "gnn",
               "encoder-decoder", "diffusion model", "contrastive learning",
               "resnet"],
    "dataset": ["sst-2", "sst-5", "imdb", "mnist", "cifar-10", "cifar-100",
                "squad", "wmt", "conll", "conll-2003", "ms marco", "wikitext",
                "librispeech", "chexpert", "glue", "snli", "mnli", "qqp",
                "stsb", "paws", "atis", "reuters"],
    "metric": ["accuracy", "f1", "f1-score", "auc", "precision", "recall",
               "bleu", "rouge", "mse", "mae", "rmse", "perplexity", "kappa",
               "mean average precision", "mrr", "ndcg"],
    "task": ["classification", "regression", "translation", "summarization",
             "question answering", "segmentation", "detection", "retrieval",
             "named entity recognition", "parsing", "ranking"],
    "material": ["corpus", "annotated corpus", "text corpus", "participants",
                 "patients", "specimen", "specimens", "tissue", "cohort"],
}
# 缩写/写法归一：同一对象不因缩写、连字符、大小写裂成两个节点
ALIAS = {"gnn": "graph neural network", "sst2": "sst-2", "sst 2": "sst-2",
         "f1-score": "f1", "f 1": "f1", "f-score": "f1", "ms-marco": "ms marco",
         "conll2003": "conll-2003", "bi-lstm": "lstm", "cnns": "cnn",
         "rnns": "rnn", "qa": "question answering"}
# ALIAS 的键必须同时也是可匹配的词，否则设了也不生效。这里按"归一后的写法"
# 反查它属于哪一类，自动登记进词表，杜绝"别名表里有一条永远不触发的死规则"。
for _alias, _canon in ALIAS.items():
    for _kind, _terms in LEX.items():
        if _canon in _terms and _alias not in _terms:
            _terms.append(_alias)
            break
# 跨篇合并策略：只有方法/任务按标签合并；其余保留分离并记冲突
MERGE_KINDS = {"method", "task"}
KEEP_CONFLICT = {
    "dataset": "两篇共用数据集名，但未报告划分/版本/许可，不得视为同一数据集实例",
    "metric": "同名指标的计算口径与所用数据集可能不同，不得合并",
    "material": "通用语料/样本名称不代表两篇用了同一物理样品、同一批受试或同一采集条件",
    "researcher": "同名姓氏可能对应不同研究者，未报告机构与全名时不得合并",
    "finding": "两篇的结论各自绑定自己的样本与数值，合并等于把不同论文的结果合成一个结论",
}
KEEP_BASIS = "同名只说明写法相同，不构成同一实例的证据"

# 自述 / 他人转述 线索
SELF_CUES = (" we ", " our ", " us ", "we ", "our ", "this paper", "this study",
             "本研究", "我们", "本文")
REPORTED_CUES = ("et al", "previous work", "prior work", "earlier stud",
                 "reported by", "taken from", "baseline from", "已有研究", "前人")
# 结论（finding）线索：必须带 that，避免把"we report accuracy"当结论
FINDING_CUES = ("we find that", "we show that", "we demonstrate that",
                "we observe that", "results show that", "findings suggest that",
                "this suggests that", "we conclude that")
# 显式用法线索：有它才把"提及方法"抬到 high
USE_CUES = ("use", "uses", "using", "used", "fine-tune", "finetune", "train",
            "trained", "apply", "applied", "employ", "adopt", "leverage",
            "built on", "based on", "evaluat", "采用", "使用")
EVAL_CUES = ("evaluat", "experiment", "benchmark", "test", "measure", "train",
             "评估", "实验")
# 关系词窗口
CUE_REL = {
    "compared_with": ("compare", "compared", "comparing", "comparison with",
                      "versus", " vs ", "相较", "对比"),
    "outperforms": ("outperform", "outperforms", "outperformed", "better than",
                    "higher than", "surpass", "exceed", "优于", "超过"),
    "supports": ("confirm", "confirms", "confirmed", "support", "supports",
                 "consistent with", "支持", "印证"),
    "contradicts": ("however", "in contrast", "does not", "do not", " fail",
                    "failed", "inconsistent", "contradict", "contradicts",
                    "相反", "矛盾", "未能"),
}

TAG = re.compile(r"<[^>]{1,80}>")
SENT_END = re.compile(r"[。！？!?]|\.(?=\s|$)")
NUM = re.compile(r"(?<![0-9A-Za-z.\-])([0-9]+(?:[.,][0-9]+)?)")
ACRONYM = re.compile(r"\b([A-Z][A-Z0-9\-]{1,9}[A-Z0-9])\b")
ETAL = re.compile(r"\b([A-Z][A-Za-z\-']{1,20})\s+et\s+al\b\.?")
# 句号紧跟这些缩写时不切句：否则 "Devlin et al." 会被切成独立一句，
# 后面那半句就丢了"这是转述"的线索，转述会被误标成作者自述。
ABBREV = frozenset("""al e.g i.e vs fig figs eq eqs tab sec no cf approx dr prof
inc ltd ref refs etc resp min max st jr sr vol pp ed eds""".split())


def _is_abbrev_period(text: str, i: int) -> bool:
    """句号在 i 位，前面那个词是不是缩写。"""
    j = i
    while j > 0 and not text[j - 1].isspace():
        j -= 1
    word = text[j:i].strip(".,;:()[]").lower()
    return word in ABBREV


def split_sentences(text: str) -> list[str]:
    out, start = [], 0
    for m in SENT_END.finditer(text):
        if text[m.start()] == "." and _is_abbrev_period(text, m.start()):
            continue
        seg = text[start:m.end()].strip()
        if seg:
            out.append(seg)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


def norm_text(s: str) -> str:
    s = TAG.sub(" ", s)
    s = s.replace("\u3000", " ").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def norm_key(label: str) -> str:
    """规范化键：大小写、连字符/下划线/空格、末尾复数一律归一。

    只用于"是不是同一个写法"，不用于判断"是不是同一个实例"。
    """
    s = label.lower().strip()
    s = re.sub(r"[\u2010-\u2015_\-]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    toks = s.split(" ")
    if toks:
        last = toks[-1]
        if len(last) > 4 and last.endswith("s") and not last.endswith("ss"):
            toks[-1] = last[:-1]
    return " ".join(toks)


def sha16(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return "unknown"


def clip(quote: str, limit: int = 160) -> str:
    """短引不超过 160 字；只在词边界截断，保证仍是原文子串。"""
    if len(quote) <= limit:
        return quote
    cut = quote.rfind(" ", 0, limit)
    return quote[:cut if cut > 40 else limit].strip()


def stance_of(sent: str) -> str:
    low = sent.lower()
    padded = " " + low + " "
    if any(c in padded for c in REPORTED_CUES):
        return "reported"
    if any(c in padded for c in SELF_CUES):
        return "self"
    return "unspecified"


def attribution_of(stance: str) -> str:
    """归属两分：作者自述（含无主语的被动陈述）vs 他人转述。

    两档是题面「区分作者自述与他人转述」的最小划分；证据上的 stance 再细分三档。
    """
    return "reported_elsewhere" if stance == "reported" else "self"


# ---------------------------------------------------------------- 输入解析

def walk_paragraphs(obj, out: list[tuple[str, str]]) -> None:
    """容忍多种段落 JSON 形状：{paragraphs:[...]}、[...]、嵌套 {sections:{body:[]}}。"""
    if isinstance(obj, dict):
        pid = next((str(obj[k]) for k in ID_KEYS
                    if k in obj and isinstance(obj[k], (str, int))), None)
        txt = next((obj[k] for k in TEXT_KEYS
                    if k in obj and isinstance(obj[k], str)), None)
        if txt and len(norm_text(txt)) >= 20:
            out.append((pid or f"p{len(out) + 1:04d}", norm_text(txt)))
        for v in obj.values():
            walk_paragraphs(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk_paragraphs(v, out)


def load_manifest(root: Path | None) -> tuple[dict, list]:
    """读 corpus-manifest.json：每篇的 DOI/标题/作者/许可/哈希；根元数据只归第一篇。"""
    meta: dict[str, dict] = {}
    gaps: list[str] = []
    if root is None:
        return meta, gaps
    mp = root / "corpus-manifest.json"
    if not mp.exists():
        return meta, ["缺 corpus-manifest.json：DOI/作者/许可/哈希未核，未补造"]
    try:
        obj = json.loads(read(mp))
    except Exception as exc:
        return meta, [f"corpus-manifest.json 不是合法 JSON：{exc}"]
    papers = obj.get("papers") if isinstance(obj, dict) else None
    papers = papers if isinstance(papers, list) else []
    for p in papers:
        if isinstance(p, dict) and p.get("source_id"):
            meta[str(p["source_id"])] = {
                k: str(p.get(k) or "unknown")
                for k in ("doi", "title", "authors", "license", "sha256")}
    root_meta = obj.get("root") if isinstance(obj, dict) else None
    if isinstance(root_meta, dict) and papers:
        first = str((papers[0] or {}).get("source_id"))
        meta.setdefault(first, {})
        for k in ("doi", "title", "authors"):
            if root_meta.get(k):
                meta[first].setdefault(k, str(root_meta[k]))
    if len(papers) < 2:
        gaps.append(f"corpus-manifest.json 只登记 {len(papers)} 篇，题目要求两篇")
    return meta, gaps


COMMON_WORDS = frozenset("""
This These Those Our Their Its The They There Then Than That What When Where Which
Who Why How Here Both Each Such With From While However Finally First Second Third
Thus Therefore Because Although After Before Since Given Recent Many Most Some Other
Another More Less Also Only Even Over Under Between During Without Using Based Results
Table Figure Section Appendix Study Paper Method Methods Model Models Data Training
Test Error Score Text Corpus Sentence Word Words Label Labels Example Examples
""".split())


def author_surnames(meta: dict, source_id: str) -> list[str]:
    """从 manifest 的作者串取姓氏；只用来判断"这个名字是否真的出现在正文里"。

    取每个作者片段的最后一个词，长度 ≥4 且不在常见词表里。它只用于**认出**正文
    已经点到的名字，不用来凭 manifest 补造正文没出现的研究者。
    """
    raw = (meta.get(source_id) or {}).get("authors", "")
    if not raw or raw == "unknown":
        return []
    out = []
    for chunk in re.split(r"[;,、/]| and ", raw):
        toks = re.findall(r"[A-Z][A-Za-z\-']{2,20}", chunk)
        if not toks:
            continue
        name = toks[-1]
        if len(name) >= 4 and name not in COMMON_WORDS and name not in out:
            out.append(name)
    return out


# ---------------------------------------------------------------- 抽取

def find_entities(sent: str, surnames: list[str]) -> list[tuple[str, str, str, int]]:
    """返回 [(类型, 归一标签, 原文写法, 句内位置)]。

    同句里取最长匹配：`annotated corpus` 命中后不再另建 `corpus` 节点，
    `conll-2003` 不再另建 `conll`，避免同一段文字裂成两个节点。
    """
    low = sent.lower()
    hits: list[tuple[str, str, str, int, int]] = []
    for kind, terms in LEX.items():
        for t in terms:
            for m in re.finditer(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low):
                canon = ALIAS.get(t, t)
                hits.append((kind, canon, sent[m.start():m.end()],
                             m.start(), m.end()))
    for m in ETAL.finditer(sent):
        hits.append(("researcher", m.group(1).lower(), m.group(1),
                     m.start(), m.end()))
    for name in surnames:
        for m in re.finditer(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", sent):
            hits.append(("researcher", name.lower(), name, m.start(), m.end()))
    kept = []
    for h in hits:
        covered = any(o is not h and o[0] == h[0] and o[1] != h[1]
                      and o[3] <= h[3] and o[4] >= h[4]
                      and (o[4] - o[3]) > (h[4] - h[3]) for o in hits)
        if not covered:
            kept.append(h)
    seen: dict[tuple[str, str], tuple[str, str, str, int]] = {}
    for kind, canon, surface, start, _end in kept:
        key = (kind, norm_key(canon))
        if key not in seen or start < seen[key][3]:
            seen[key] = (kind, canon, surface, start)
    return sorted(seen.values(), key=lambda x: x[3])


def extract_measurements(sent: str, ents, source_id: str, pid: str,
                         stance: str) -> list[dict]:
    """数值结果：每条只在它被报告的那句里出现，按篇分离，不跨篇汇总。"""
    out = []
    metrics = [e for e in ents if e[0] == "metric"]
    if not metrics:
        return out
    for _kind, canon, _surface, pos in metrics:
        win_start, win_end = max(0, pos - 40), min(len(sent), pos + 60)
        window = sent[win_start:win_end]
        best = None
        for m in NUM.finditer(window):
            abs_start = win_start + m.start()
            abs_end = win_start + m.end()
            try:
                f = float(m.group(1).replace(",", "."))
            except ValueError:
                continue
            gap = abs(abs_start - pos)
            if best is None or gap < best[0]:
                best = (gap, f, abs_start, abs_end)
        if best is None:
            continue
        _gap, value, abs_start, abs_end = best
        head = sent[max(0, abs_start - 28):abs_start].lower()
        tail = sent[abs_end:abs_end + 28].lower()
        unit = "%" if "%" in tail[:6] else ("points" if "point" in tail else "")
        delta_cue = re.search(r"\b(by|gain|improve|increase|higher|reduce|drop|"
                              r"boost|outperform)\b", head)
        kind = "delta" if (delta_cue and unit) else "absolute"
        out.append({"metric": canon, "value": value, "unit": unit,
                    "kind": kind, "source_id": source_id,
                    "paragraph_id": pid, "stance": stance,
                    "quote": clip(sent)})
    uniq, seen = [], set()
    for r in out:
        k = (r["metric"], r["value"], r["unit"], r["kind"], r["paragraph_id"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def _edge(src: str, tgt: str, rel: str, confidence: str, attribution: str,
          basis: str, direction_basis: str, applicability: dict,
          quote: str, source_id: str, pid: str, stance: str,
          reported_by: str | None = None) -> dict:
    return {
        "source": src, "target": tgt, "type": rel,
        "confidence": confidence, "attribution": attribution, "basis": basis,
        "direction_basis": direction_basis,
        "applicability": applicability,
        "reported_by": reported_by,
        "evidence": [{"source_id": source_id, "paragraph_id": pid,
                      "quote": quote, "stance": stance}],
    }


def _node(nid: str, kind: str, label: str, surface: str,
          confidence: str = "high") -> dict:
    return {"id": nid, "type": kind, "label": label,
            "canonical": norm_key(label), "surface_forms": [surface],
            "norm_rule": "小写+连字符/下划线/空格归一+末尾复数归一",
            "confidence": confidence, "evidence": []}


def _add_ev(target: dict, evidence: dict) -> None:
    if evidence not in target["evidence"]:
        target["evidence"].append(evidence)


def _applicability(ents) -> dict:
    """适用条件：同一句里出现的限定对象。跨篇不得相互套用。"""
    app = {"datasets": [], "tasks": [], "metrics": [], "from_sentence": True}
    for kind, canon, _s, _p in ents:
        slot = {"dataset": "datasets", "task": "tasks", "metric": "metrics"}.get(kind)
        if slot and canon not in app[slot]:
            app[slot].append(canon)
    return app


def extract(paras: list[tuple[str, str]], source_id: str,
            surnames: list[str], opts: dict) -> tuple[list, list, list, list]:
    """按段→句抽取节点、边与数值；返回 (nodes, edges, measurements, rejected)。"""
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}
    measures: list[dict] = []
    rejected: list[dict] = []

    paper = f"paper:{source_id}"
    nodes[paper] = _node(paper, "paper", f"论文 {source_id}", source_id)
    if paras:
        pid0, txt0 = paras[0]
        s0 = (split_sentences(txt0) or [txt0])[0]
        _add_ev(nodes[paper], {"source_id": source_id, "paragraph_id": pid0,
                               "quote": clip(s0), "stance": stance_of(s0)})

    def touch(kind: str, canon: str, surface: str, sid_scope: str) -> dict:
        nid = f"{kind}:{slug(canon)}@{sid_scope}"
        if nid not in nodes:
            nodes[nid] = _node(nid, kind, canon, surface)
        elif surface not in nodes[nid]["surface_forms"]:
            nodes[nid]["surface_forms"].append(surface)
        return nodes[nid]

    for pid, text in paras:
        for sent in split_sentences(text):
            ents = find_entities(sent, surnames)
            if not ents:
                continue
            stance = stance_of(sent)
            quote = clip(sent)
            app = _applicability(ents)
            etal = ETAL.search(sent)
            cited = etal.group(1).lower() if etal else None
            researcher_src = (f"researcher:{slug(cited)}@{source_id}"
                              if (cited and stance == "reported") else None)

            for kind, canon, surface, _pos in ents:
                node = touch(kind, canon, surface, source_id)
                node["confidence"] = "high" if stance != "reported" else "medium"
                _add_ev(node, {"source_id": source_id, "paragraph_id": pid,
                               "quote": quote, "stance": stance})
                if kind == "researcher":
                    same = norm_key(cited or "") == norm_key(canon)
                    if same and stance == "reported":
                        # 本文引用他人：论文 → 研究者
                        key = (paper, node["id"], "cites_researcher", "self")
                        e = edges.get(key)
                        if e is None:
                            edges[key] = _edge(paper, node["id"], "cites_researcher",
                                               "high", "self", "citation",
                                               "本文（引用者）→ 被引用研究者",
                                               app, quote, source_id, pid, stance)
                        else:
                            _add_ev(edges[key], {"source_id": source_id,
                                                 "paragraph_id": pid,
                                                 "quote": quote, "stance": stance})
                    else:
                        key = (node["id"], paper, "authored_by", "self")
                        if key not in edges:
                            edges[key] = _edge(node["id"], paper, "authored_by",
                                               "high", "self", "author_metadata",
                                               "研究者 → 该研究者的论文",
                                               app, quote, source_id, pid, stance)
                    continue
                rel = FIXED_REL.get(kind)
                if not rel:
                    continue
                src = researcher_src if (stance == "reported" and researcher_src) else paper
                conf, basis = fixed_strength(rel, sent, ents, stance)
                key = (src, node["id"], rel, attribution_of(stance))
                if key not in edges:
                    edges[key] = _edge(src, node["id"], rel, conf,
                                       key[3], basis,
                                       "陈述主体 → 被陈述对象", app, quote,
                                       source_id, pid, stance,
                                       f"researcher:{slug(cited)}@{source_id}"
                                       if stance == "reported" and cited else None)
                else:
                    _add_ev(edges[key], {"source_id": source_id,
                                         "paragraph_id": pid,
                                         "quote": quote, "stance": stance})

            # 结论节点：显式结论句才建，标签就用原文
            if any(c in sent.lower() for c in FINDING_CUES):
                label = clip(sent, 80)
                nid = f"finding:{slug(label[:28])}"
                if nid not in nodes:
                    nodes[nid] = _node(nid, "finding", label, label,
                                       "high" if stance != "reported" else "medium")
                _add_ev(nodes[nid], {"source_id": source_id, "paragraph_id": pid,
                                     "quote": quote, "stance": stance})
                key = (paper, nid, "reports_finding", attribution_of(stance))
                if key not in edges:
                    edges[key] = _edge(paper, nid, "reports_finding",
                                       "high" if stance == "self" else "medium",
                                       key[3], "claim_cue",
                                       "论文 → 本文结论",
                                       app, quote, source_id, pid, stance)
                else:
                    _add_ev(edges[key], {"source_id": source_id,
                                         "paragraph_id": pid,
                                         "quote": quote, "stance": stance})

            if opts.get("direction", True):
                build_cue_relations(sent, ents, paper, source_id, pid, stance,
                                    quote, app, edges, rejected)
                build_cooccurrence(ents, source_id, pid, stance, quote,
                                   app, edges)

            if opts.get("numbers", True):
                measures.extend(extract_measurements(sent, ents, source_id, pid, stance))
    return list(nodes.values()), list(edges.values()), measures, rejected


def fixed_strength(rel: str, sent: str, ents, stance: str) -> tuple[str, str]:
    """证据强度：显式关系词 + 自述 = high；仅提及或转述 = medium。"""
    low = sent.lower()
    if stance == "reported":
        return "medium", "mention"
    if rel == "uses_method" and any(c in low for c in USE_CUES):
        return "high", "explicit"
    if rel == "evaluates_on" and any(c in low for c in EVAL_CUES):
        return "high", "explicit"
    if rel == "reports_metric":
        has_number = any(e[0] == "metric" for e in ents) and bool(NUM.search(sent))
        return ("high", "explicit") if has_number else ("medium", "mention")
    return "medium", "mention"


def build_cue_relations(sent: str, ents, paper: str, source_id: str, pid: str,
                        stance: str, quote: str, app: dict,
                        edges: dict, rejected: list) -> None:
    """比较 / 优于 / 支持 / 矛盾：只在同一句里有可配对实体时建边。"""
    low = sent.lower()
    for rel, cues in CUE_REL.items():
        hit = next((c for c in cues if c in low), None)
        if hit is None:
            continue
        cue_pos = low.find(hit)
        cand = None
        if rel == "outperforms":
            before = [e for e in ents if e[3] < cue_pos]
            after = [e for e in ents if e[3] > cue_pos]
            if before and after:
                cand = (before[-1], after[0])
        elif rel == "compared_with":
            by_kind: dict[str, list] = {}
            for e in ents:
                by_kind.setdefault(e[0], []).append(e)
            for kind in ("method", "dataset", "metric"):
                if len(by_kind.get(kind, [])) >= 2:
                    cand = (by_kind[kind][0], by_kind[kind][1])
                    break
        else:
            if len(ents) >= 2:
                cand = (ents[0], ents[1])
        if cand is None:
            rejected.append({"relation": rel, "cue": hit, "paragraph_id": pid,
                             "source_id": source_id,
                             "reason": "同句内没有可配对的两个实体，未建边"})
            continue
        a, b = cand
        pair = (a[0], b[0])
        if pair not in SIG.get(rel, set()):
            rejected.append({"relation": rel, "cue": hit, "paragraph_id": pid,
                             "source_id": source_id,
                             "reason": f"端点类型 {pair} 不在该关系的允许签名内，未建边"})
            continue
        sa = f"{a[0]}:{slug(a[1])}@{source_id}"
        sb = f"{b[0]}:{slug(b[1])}@{source_id}"
        conf = "medium" if stance == "reported" else "high"
        attr = attribution_of(stance)
        if rel == "compared_with":
            dbasis = "原文语序：先出现的实体 → 后出现的实体（比较本身无方向）"
        elif rel == "outperforms":
            dbasis = "比较词前实体 → 比较词后实体（有方向）"
        else:
            dbasis = "原文语序：先出现的实体 → 后出现的实体"
        key = (sa, sb, rel, attr)
        if key not in edges:
            edges[key] = _edge(sa, sb, rel, conf, attr, f"cue:{hit}",
                               dbasis, app, quote, source_id, pid, stance)
        else:
            _add_ev(edges[key], {"source_id": source_id, "paragraph_id": pid,
                                 "quote": quote, "stance": stance})


def build_cooccurrence(ents, source_id: str, pid: str, stance: str, quote: str,
                       app: dict, edges: dict) -> None:
    """同句方法×指标共现：只能说明"这句同时提到"，固定 low。"""
    methods = [e for e in ents if e[0] == "method"]
    metrics = [e for e in ents if e[0] == "metric"]
    attr = attribution_of(stance)
    for m in methods:
        for mt in metrics:
            key = (f"method:{slug(m[1])}@{source_id}",
                   f"metric:{slug(mt[1])}@{source_id}", "reports_metric", attr)
            if key not in edges:
                edges[key] = _edge(key[0], key[1], "reports_metric", "low", attr,
                                   "cooccurrence",
                                   "同句共现，无方向语义",
                                   app, quote, source_id, pid, stance)


# ---------------------------------------------------------------- 对齐与合并

def merge_candidates(nodes: list) -> dict[tuple[str, str], list]:
    groups: dict[tuple[str, str], list] = {}
    for n in nodes:
        if n["type"] == "paper":
            continue
        groups.setdefault((n["type"], n.get("canonical") or norm_key(n["label"])),
                          []).append(n)
    return groups


def sources_of(node: dict) -> list[str]:
    return sorted({e["source_id"] for e in node.get("evidence") or []})


def build_merges(nodes: list) -> list[dict]:
    """跨篇候选按类型分流：方法/任务合并，其余保留分离并写冲突。"""
    merges = []
    for (kind, key), ns in sorted(merge_candidates(nodes).items()):
        srcs = sorted({s for n in ns for s in sources_of(n)})
        if len(srcs) < 2:
            continue
        cand = sorted(n["id"] for n in ns)
        forms = sorted({f for n in ns for f in n.get("surface_forms") or []})
        if kind in MERGE_KINDS:
            merges.append({
                "kind": kind, "label": ns[0]["label"], "candidate_ids": cand,
                "sources": srcs, "decision": "merge",
                "basis": [f"类型 {kind}，规范化键相同：{key}",
                          f"两篇都显式出现：{srcs}",
                          f"原文字面写法：{forms}",
                          "只合并方法/任务名称，不断言同一模型权重、超参或实验条件"],
                "conflict": "",
            })
        else:
            merges.append({
                "kind": kind, "label": ns[0]["label"], "candidate_ids": cand,
                "sources": srcs, "decision": "keep_separate",
                "basis": [f"类型 {kind}，规范化键相同：{key}",
                          f"两篇都显式出现：{srcs}",
                          f"原文字面写法：{forms}", KEEP_BASIS],
                "conflict": KEEP_CONFLICT.get(kind, "同名不同实例，不得合并"),
            })
    return merges


def resolve(nodes: list, edges: list, merges: list) -> tuple[list, list]:
    """按合并记录改写 id：合并的收成一个节点，保留分离的维持 类型:标签@source。"""
    merge_map: dict[str, str] = {}
    for m in merges:
        if m["decision"] != "merge":
            continue
        canon = f"{m['kind']}:{slug(m['label'])}"
        for i in m["candidate_ids"]:
            merge_map[i] = canon
    out_nodes: dict[str, dict] = {}
    for n in nodes:
        if n["type"] == "paper":
            out_nodes[n["id"]] = n
            continue
        nid = merge_map.get(n["id"], n["id"])
        if nid in out_nodes:
            tgt = out_nodes[nid]
            for e in n.get("evidence") or []:
                _add_ev(tgt, e)
            for f in n.get("surface_forms") or []:
                if f not in tgt["surface_forms"]:
                    tgt["surface_forms"].append(f)
            if n.get("confidence") == "high":
                tgt["confidence"] = "high"
        else:
            out_nodes[nid] = {**n, "id": nid}
    out_edges: dict[tuple, dict] = {}
    for e in edges:
        s = merge_map.get(e["source"], e["source"])
        t = merge_map.get(e["target"], e["target"])
        k = (s, t, e["type"], e.get("attribution", ""))
        if k in out_edges:
            for evd in e.get("evidence") or []:
                _add_ev(out_edges[k], evd)
        else:
            out_edges[k] = {**e, "source": s, "target": t}
    return list(out_nodes.values()), list(out_edges.values())


def detect_homonyms(nodes: list) -> list[dict]:
    """同名不同类：同一规范化键挂在两个实体类型下，记一条，不合并。"""
    by_key: dict[str, set] = {}
    for n in nodes:
        if n["type"] == "paper":
            continue
        by_key.setdefault(n.get("canonical") or norm_key(n["label"]), set()).add(
            (n["type"], n["id"]))
    out = []
    for key, hits in sorted(by_key.items()):
        if len({t for t, _ in hits}) > 1:
            out.append({"label_key": key,
                        "nodes": sorted(i for _, i in hits),
                        "action": "keep_separate_by_type",
                        "reason": "同名但实体类型不同，按类型分节点，不做跨类型合并"})
    return out


def unmatched_candidates(paras: list[tuple[str, str]], source_id: str) -> list[dict]:
    """边界证据：正文里的大写缩写没进词表，逐条列出，不猜它的含义。"""
    known = {t for ts in LEX.values() for t in ts}
    out, seen = [], set()
    for pid, text in paras:
        low = text.lower()
        spans = []
        for t in known:
            for m in re.finditer(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low):
                spans.append((m.start(), m.end()))
        for m in ACRONYM.finditer(text):
            tok = m.group(1)
            if tok.lower() in known or tok in seen:
                continue
            # 词表长词命中的那一段不再当成"没抽到"，例如 MS MARCO 里的 MARCO
            if any(s <= m.start() and m.end() <= e for s, e in spans):
                continue
            seen.add(tok)
            out.append({"source_id": source_id, "paragraph_id": pid,
                        "term": tok, "handling": "未进词表，未建节点"})
    return out


# ---------------------------------------------------------------- 校验

def verify(nodes: list, edges: list, para_index: dict, prev_ids: set | None,
           append_mode: bool) -> tuple[dict, list]:
    probs: list[tuple[str, str]] = []
    ids = [n["id"] for n in nodes]
    by_id = {n["id"]: n for n in nodes}

    for n in nodes:
        if not n.get("id"):
            probs.append(("G01", f"节点缺 id：{n.get('label')}"))
        if len(n.get("label") or "") > 80:
            probs.append(("G05", f"标签过长：{n['id']}"))
        if n["type"] not in ENTITY_TYPES:
            probs.append(("G04", f"类型非法：{n['type']}"))
        if not n.get("evidence"):
            probs.append(("G03", f"节点无证据：{n['id']}"))
    if len(ids) != len(set(ids)):
        probs.append(("G01", "节点 id 重复"))

    for e in edges:
        if e["source"] not in by_id or e["target"] not in by_id:
            probs.append(("G02", f"悬空边：{e['source']} -> {e['target']}"))
            continue
        if not e.get("evidence"):
            probs.append(("G03", f"边无证据：{e['source']}->{e['target']}"))
        for evd in e.get("evidence") or []:
            if not all(evd.get(k) for k in ("source_id", "paragraph_id", "quote")):
                probs.append(("G03", f"证据缺字段：{e['source']}->{e['target']}"))
                break
        st, tt = by_id[e["source"]]["type"], by_id[e["target"]]["type"]
        rel = e.get("type")
        if rel not in REL_ALLOWED:
            probs.append(("G06", f"关系类型非法：{rel}"))
        elif (st, tt) not in SIG.get(rel, set()):
            probs.append(("G06", f"关系方向/签名不合法：{st} -{rel}-> {tt}"))
        if not e.get("direction_basis"):
            probs.append(("G06", f"边缺方向依据：{e['source']}->{e['target']}"))
        if e.get("confidence") not in CONF:
            probs.append(("G08", f"置信度取值非法：{e.get('confidence')}"))
        elif e.get("confidence") == "high":
            if e.get("attribution") not in (None, "self"):
                probs.append(("G08", "转述边标了 high："
                                     f"{e['source']}->{e['target']}"))
            if e.get("basis") in ("cooccurrence", "mention"):
                probs.append(("G08", "共现/仅提及的边标了 high："
                                     f"{e['source']}->{e['target']}"))
        if e.get("attribution") not in (None, "self", "reported_elsewhere"):
            probs.append(("G08", f"attribution 取值非法：{e.get('attribution')}"))

    for n in nodes:
        for evd in n.get("evidence") or []:
            if not all(evd.get(k) for k in ("source_id", "paragraph_id", "quote")):
                probs.append(("G03", f"节点证据缺字段：{n['id']}"))
                break
            src = para_index.get((evd["source_id"], evd["paragraph_id"]), "")
            if src and evd["quote"] not in src:
                probs.append(("G07", f"节点的引文不在原段里：{n['id']}"))
                break
    for e in edges:
        for evd in e.get("evidence") or []:
            if not all(evd.get(k) for k in ("source_id", "paragraph_id", "quote")):
                break
            src = para_index.get((evd["source_id"], evd["paragraph_id"]), "")
            if src and evd["quote"] not in src:
                probs.append(("G07", f"边的引文不在原段里："
                                     f"{e['source']}->{e['target']}"))
                break

    if append_mode and prev_ids is not None:
        missing = sorted(prev_ids - set(ids))
        if missing:
            probs.append(("G09", f"增量后丢了既有节点 id：{missing[:5]}"))
    counts = {c: sum(1 for p in probs if p[0] == c) for c in CODES}
    checks = {c: {"ok": counts[c] == 0,
                  "detail": f"{CODES[c][1]}：{counts[c]} 处"} for c in CODES}
    if not append_mode:
        checks["G09"]["detail"] = "未启用增量模式（--append）：不适用"
    return checks, probs


def validate_schema(graph: dict) -> tuple[bool, list[str]]:
    """用包内 rules/graph.schema.json 做最小结构校验；schema 缺失时记一条。"""
    sp = PKG / "rules" / "graph.schema.json"
    if not sp.exists():
        return True, ["缺 rules/graph.schema.json：跳过 schema 校验"]
    try:
        schema = json.loads(read(sp))
    except Exception as exc:
        return True, [f"rules/graph.schema.json 不是合法 JSON：{exc}"]
    problems = []
    for key in schema.get("required") or []:
        if key not in graph:
            problems.append(f"schema 要求顶层字段 {key} 缺失")
    for key, spec in (schema.get("properties") or {}).items():
        if key not in graph:
            continue
        typ = spec.get("type")
        want = {"array": list, "object": dict, "string": str,
                "integer": int, "number": (int, float)}.get(typ)
        if want and not isinstance(graph[key], want):
            problems.append(f"schema 要求 {key} 为 {typ}")
        if typ == "array" and isinstance(graph[key], list) and spec.get("minItems"):
            if len(graph[key]) < spec["minItems"]:
                problems.append(f"schema 要求 {key} 至少 {spec['minItems']} 项")
        if typ == "array" and isinstance(graph[key], list):
            req = spec.get("itemRequired") or []
            miss = sorted({k for k in req
                           for it in graph[key]
                           if isinstance(it, dict) and k not in it})
            if miss:
                problems.append(f"schema 要求 {key} 每项含 {miss}")
    return not problems, problems


# ---------------------------------------------------------------- 查询

DEFAULT_QUERIES = [
    {"id": "node", "label": "按标签找节点", "params": ["label"], "kind": "node"},
    {"id": "neighbors", "label": "邻居与边", "params": ["node_id"], "kind": "neighbors"},
    {"id": "by-source", "label": "按来源列实体", "params": ["source_id"], "kind": "by_source"},
    {"id": "cross-paper", "label": "跨篇对齐与合并依据", "params": ["label"], "kind": "cross_paper"},
    {"id": "results", "label": "逐篇数值结果（不跨篇合并）", "params": [], "kind": "results"},
    {"id": "unresolved", "label": "无法确定的关系", "params": [], "kind": "unresolved"},
]


def load_query_templates() -> tuple[list, list]:
    qp = PKG / "rules" / "queries.json"
    if not qp.exists():
        return DEFAULT_QUERIES, ["缺 rules/queries.json：用内置 6 条查询定义"]
    try:
        obj = json.loads(read(qp))
    except Exception as exc:
        return DEFAULT_QUERIES, [f"rules/queries.json 不是合法 JSON：{exc}"]
    items = obj.get("queries") if isinstance(obj, dict) else obj
    out = []
    for it in items or []:
        if isinstance(it, dict) and it.get("id") and it.get("kind"):
            out.append(it)
    if len(out) < 3:
        return DEFAULT_QUERIES, ["rules/queries.json 可用查询不足 3 条：用内置定义"]
    return out, []


def degree_map(edges: list) -> dict[str, int]:
    deg: dict[str, int] = {}
    for e in edges:
        deg[e["source"]] = deg.get(e["source"], 0) + 1
        deg[e["target"]] = deg.get(e["target"], 0) + 1
    return deg


def subgraph(edges: list, node_ids: set, anchor: set | None = None) -> dict:
    """结果子图：anchor 给定时只留与 anchor 直接相连的边（深度 1）。"""
    keep = [e for e in edges
            if (anchor and (e["source"] in anchor or e["target"] in anchor))
            or (not anchor and (e["source"] in node_ids
                                or e["target"] in node_ids))]
    ids = set(node_ids)
    for e in keep:
        ids.add(e["source"])
        ids.add(e["target"])
    return {"nodes": sorted(ids),
            "edges": [f"{e['source']} -{e['type']}-> {e['target']}"
                      for e in keep]}


def run_queries(templates: list, nodes: list, edges: list, merges: list,
                measures: list, sources: list) -> list[dict]:
    by_id = {n["id"]: n for n in nodes}
    deg = degree_map(edges)
    labelled = [n for n in nodes if n["type"] != "paper"]
    top = max(labelled, key=lambda n: (deg.get(n["id"], 0), n["id"]), default=None)
    out = []
    for tpl in templates:
        kind = tpl.get("kind")
        rows: list[dict] = []
        ids: set[str] = set()
        if kind in ("node", "neighbors"):
            if top is None:
                out.append({**tpl, "args": [], "n": 0, "rows": [],
                            "subgraph": {"nodes": [], "edges": []}})
                continue
            ids = {top["id"]}
            nb = [e for e in edges
                  if top["id"] in (e["source"], e["target"])]
            if kind == "node":
                rows = [{"node": top["id"], "label": top["label"],
                         "degree": deg.get(top["id"], 0),
                         "evidence": top["evidence"][:2]}]
            else:
                rows = [{"edge": f"{e['source']} -{e['type']}-> {e['target']}",
                         "confidence": e.get("confidence"),
                         "attribution": e.get("attribution"),
                         "applicability": e.get("applicability"),
                         "evidence": e["evidence"][:2]} for e in nb]
                for e in nb:
                    ids.add(e["source"])
                    ids.add(e["target"])
            out.append({**tpl, "args": [top["label"] if kind == "node"
                                        else top["id"]],
                        "n": len(rows), "rows": rows,
                        "subgraph": subgraph(edges, ids,
                                             {top["id"]} if kind == "neighbors"
                                             else None)})
            continue
        if kind == "by_source":
            target = next((s for s in sources if s != sources[0]), None) or (
                sources[0] if sources else "")
            sel = [n for n in labelled if n.get("evidence")
                   and all(e["source_id"] == target for e in n["evidence"])]
            rows = [{"node": n["id"], "label": n["label"],
                     "confidence": n.get("confidence"),
                     "paragraph_id": n["evidence"][0]["paragraph_id"],
                     "quote": n["evidence"][0]["quote"],
                     "stance": n["evidence"][0].get("stance")} for n in sel]
            ids = {n["id"] for n in sel}
            out.append({**tpl, "args": [target], "n": len(rows), "rows": rows,
                        "subgraph": subgraph(edges, ids)})
            continue
        if kind == "cross_paper":
            for m in merges:
                evid = []
                for nid in m["candidate_ids"]:
                    n = by_id.get(nid)
                    if n and n.get("evidence"):
                        evid.append({"node": nid,
                                     "paragraph_id": n["evidence"][0]["paragraph_id"],
                                     "quote": n["evidence"][0]["quote"]})
                rows.append({"label": m["label"], "kind": m["kind"],
                             "decision": m["decision"], "sources": m["sources"],
                             "candidate_ids": m["candidate_ids"],
                             "basis": m["basis"], "conflict": m["conflict"],
                             "evidence": evid})
                ids.update(m["candidate_ids"])
            out.append({**tpl, "args": [], "n": len(rows), "rows": rows,
                        "subgraph": subgraph(edges, ids)})
            continue
        if kind == "results":
            by_metric: dict[str, list] = {}
            for r in measures:
                by_metric.setdefault(r["metric"], []).append(r)
            for metric, rs in sorted(by_metric.items()):
                rows.append({"metric": metric,
                             "per_source": rs,
                             "note": "逐篇分列，不做跨篇汇总或平均（不同样本、不同划分不可比）"})
            ids = {n["id"] for n in nodes if n["type"] == "metric"}
            out.append({**tpl, "args": [], "n": len(rows), "rows": rows,
                        "subgraph": subgraph(edges, ids)})
            continue
        # unresolved：保留分离的候选 + 非 high 的边
        for m in merges:
            if m["decision"] == "keep_separate":
                rows.append({"kind": "keep_separate",
                             "candidate_ids": m["candidate_ids"],
                             "conflict": m["conflict"]})
                ids.update(m["candidate_ids"])
        weak = [e for e in edges if e.get("confidence") in ("medium", "low")
                or e.get("attribution") == "reported_elsewhere"]
        for e in weak:
            rows.append({"kind": "weak_edge",
                         "edge": f"{e['source']} -{e['type']}-> {e['target']}",
                         "confidence": e.get("confidence"),
                         "attribution": e.get("attribution"),
                         "evidence": e["evidence"][:1]})
        ids.update({e["source"] for e in weak})
        ids.update({e["target"] for e in weak})
        out.append({**tpl, "args": [], "n": len(rows), "rows": rows,
                    "subgraph": subgraph(edges, ids)})
    return out


# ---------------------------------------------------------------- 统计

def coverage_block(nodes: list, edges: list, measures: list,
                   queries: list) -> dict:
    req_ent = ["researcher", "material", "method", "task", "metric", "finding"]
    have_ent = sorted({n["type"] for n in nodes} - {"paper"})
    req_rel = ["uses_method", "compared_with", "outperforms", "supports",
               "contradicts"]
    have_rel = sorted({e["type"] for e in edges})
    with_sub = sum(1 for q in queries
                   if q.get("n", 0) > 0 and (q.get("subgraph") or {}).get("edges"))
    return {
        "entity_types": {
            "required": req_ent,
            "covered": [t for t in req_ent if t in have_ent],
            "missing": [t for t in req_ent if t not in have_ent],
            "n_required": len(req_ent),
            "n_covered": len([t for t in req_ent if t in have_ent]),
        },
        "relation_types": {
            "required": req_rel,
            "covered": [t for t in req_rel if t in have_rel],
            "missing": [t for t in req_rel if t not in have_rel],
            "n_required": len(req_rel),
            "n_covered": len([t for t in req_rel if t in have_rel]),
            "all_covered": have_rel,
        },
        "evidence_fields": {"required": ["source_id", "paragraph_id", "quote"],
                            "unit": "source_id+paragraph_id+quote",
                            "strength_levels": "high/medium/low",
                            "stance_levels": list(STANCES)},
        "queries": {"n_query": len(queries),
                    "n_with_results": sum(1 for q in queries if q.get("n", 0) > 0),
                    "n_with_subgraph": with_sub,
                    "required_min_with_results": 3},
        "measurements": {"n": len(measures),
                         "cross_paper_aggregation": "none（逐篇分列，不汇总）"},
    }


def quality_metrics(nodes: list, edges: list, merges: list, measures: list,
                    queries: list, checks: dict, para_index: dict) -> dict:
    items = []
    for n in nodes:
        items.extend(n.get("evidence") or [])
    for e in edges:
        items.extend(e.get("evidence") or [])
    total = len(items) or 1
    complete = sum(1 for i in items
                   if all(i.get(k) for k in ("source_id", "paragraph_id", "quote")))
    verbatim = sum(1 for i in items
                   if all(i.get(k) for k in ("source_id", "paragraph_id", "quote"))
                   and i["quote"] in para_index.get((i["source_id"],
                                                     i["paragraph_id"]), ""))
    e_total = len(edges) or 1
    return {
        "n_evidence": len(items),
        "evidence_complete_rate": round(complete / total, 4),
        "evidence_verbatim_rate": round(verbatim / total, 4),
        "n_edges": len(edges),
        "edge_direction_rate": round(
            sum(1 for e in edges if e.get("direction_basis")) / e_total, 4),
        "edge_condition_rate": round(
            sum(1 for e in edges if (e.get("applicability") or {}).get("from_sentence"))
            / e_total, 4),
        "edge_strength_levels": sorted({e.get("confidence") for e in edges}),
        "n_entity_entity_edges": sum(
            1 for e in edges if not e["source"].startswith("paper:")
            and not e["target"].startswith("paper:")),
        "n_cross_paper_records": len(merges),
        "n_merge_basis": sum(1 for m in merges if m.get("basis")),
        "n_measurements": len(measures),
        "queries_with_results": sum(1 for q in queries if q.get("n", 0) > 0),
        "queries_with_subgraph": sum(
            1 for q in queries if (q.get("subgraph") or {}).get("edges")),
        "gates_ok": sum(1 for c in CODES if checks.get(c, {}).get("ok")),
        "gates_total": len(CODES),
    }


# ---------------------------------------------------------------- 落盘

def write_graph_md(path: Path, graph: dict) -> None:
    """可读图谱：人不用跑代码也能核对节点、边、证据与冲突。"""
    L = ["# 知识图谱（可读版）", "",
         f"- case_id：`{graph['case_id']}`",
         f"- 来源：{', '.join(graph['sources']) or '无'}",
         f"- 节点 {len(graph['nodes'])} / 边 {len(graph['edges'])} / "
         f"数值 {len(graph.get('measurements') or [])} 条",
         "- 证据单位：`source_id + paragraph_id + quote`；位置是组织方 JATS 段落 ID，"
         "不是 PDF 页码。", "",
         "## 1 论文", "", "| source_id | DOI | 标题 | 作者 | 许可 |", "|-|-|-|-|-|"]
    for p in graph["papers"]:
        L.append(f"| {p['source_id']} | {p.get('doi')} | {p.get('title')} | "
                 f"{p.get('authors')} | {p.get('license')} |")
    L += ["", "## 2 节点", "",
          "| id | 类型 | 标签 | 原文写法 | 置信度 | 证据数 |", "|-|-|-|-|-|-|"]
    for n in graph["nodes"]:
        L.append(f"| `{n['id']}` | {n['type']} | {n['label']} | "
                 f"{'/'.join(n.get('surface_forms') or [])} | {n.get('confidence')} | "
                 f"{len(n.get('evidence') or [])} |")
    L += ["", "## 3 边（含方向、适用条件与证据）", "",
          "| 边 | 置信度 | 归属 | 方向依据 | 适用条件 | 证据 |", "|-|-|-|-|-|-|"]
    for e in graph["edges"]:
        app = e.get("applicability") or {}
        cond = "; ".join(f"{k}={v}" for k, v in app.items()
                         if k != "from_sentence" and v) or "—"
        ev = (e.get("evidence") or [{}])[0]
        L.append(f"| `{e['source']} -{e['type']}-> {e['target']}` | "
                 f"{e.get('confidence')} | {e.get('attribution')} | "
                 f"{e.get('direction_basis')} | {cond} | "
                 f"{ev.get('paragraph_id')}：{ev.get('quote')} |")
    L += ["", "## 4 跨篇对齐：合并依据与不得合并的冲突", ""]
    if not graph["merges"]:
        L.append("（本期材料没有跨篇同名候选）")
    for m in graph["merges"]:
        L += [f"### {m['label']}（{m['kind']}）：`{m['decision']}`", ""]
        L += [f"- 候选：{m['candidate_ids']}", f"- 来源：{m['sources']}"]
        L += [f"- 依据：{b}" for b in m.get("basis") or []]
        if m.get("conflict"):
            L.append(f"- 冲突/不得合并：{m['conflict']}")
        L.append("")
    L += ["## 5 数值结果（逐篇分离，不跨篇汇总）", ""]
    ms = graph.get("measurements") or []
    if not ms:
        L.append("（本期材料没有带数值的指标句）")
    else:
        L += ["| 指标 | 值 | 类型 | 单位 | 来源 | 段落 |", "|-|-|-|-|-|-|"]
        for r in ms:
            L.append(f"| {r['metric']} | {r['value']} | {r['kind']} | "
                     f"{r['unit'] or '—'} | {r['source_id']} | {r['paragraph_id']} |")
    L += ["", "## 6 未抽到的候选（边界证据，不猜含义）", ""]
    um = graph.get("boundary", {}).get("unmatched_candidates") or []
    L.append(f"- 正文大写缩写未进词表：{len(um)} 个"
             + (f"（{', '.join(x['term'] for x in um[:20])}）" if um else ""))
    rj = graph.get("boundary", {}).get("rejected_relation_candidates") or []
    L.append(f"- 关系词命中但没有合法配对而未建边：{len(rj)} 处")
    for r in rj[:10]:
        L.append(f"  - `{r['relation']}`（cue `{r['cue']}`，{r['source_id']} "
                 f"{r['paragraph_id']}）：{r['reason']}")
    L += ["", "## 7 覆盖与限制", "",
          f"- 实体类型覆盖 {graph['coverage']['entity_types']['n_covered']}"
          f"/{graph['coverage']['entity_types']['n_required']}，"
          f"缺 {graph['coverage']['entity_types']['missing'] or '无'}",
          f"- 关系类型覆盖 {graph['coverage']['relation_types']['n_covered']}"
          f"/{graph['coverage']['relation_types']['n_required']}，"
          f"缺 {graph['coverage']['relation_types']['missing'] or '无'}",
          f"- 查询 {graph['coverage']['queries']['n_query']} 条，"
          f"有结果 {graph['coverage']['queries']['n_with_results']} 条，"
          f"带子图 {graph['coverage']['queries']['n_with_subgraph']} 条"]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 主流程

def pipeline(*, input_dir=None, primary=None, secondary=None, work="work",
             out="output", case_id=None, append=None, disable=(),
             argv=None) -> dict:
    t0 = time.perf_counter()
    opts = {"evidence": True, "align": True, "direction": True, "numbers": True}
    for d in disable:
        if d in opts:
            opts[d] = False
    work_d, out_d = Path(work), Path(out)
    work_d.mkdir(parents=True, exist_ok=True)
    out_d.mkdir(parents=True, exist_ok=True)
    gaps: list[str] = []

    case_id = case_id
    contract: list = []
    srcs: list[tuple[str, Path]] = []
    root: Path | None = None
    if primary:
        srcs.append(("primary", Path(primary)))
    if secondary:
        srcs.append(("secondary", Path(secondary)))
    if input_dir:
        d = Path(input_dir)
        root = d
        cj = d / "case.json"
        if cj.exists():
            try:
                case = json.loads(read(cj))
                case_id = case_id or case.get("case_id")
                oc = case.get("output_contract")
                contract = list(oc) if isinstance(oc, (dict, list)) else []
            except Exception:
                pass
        if not srcs:
            idx = d / "material.json"
            if idx.exists():
                try:
                    obj = json.loads(read(idx))
                    papers = obj.get("papers") if isinstance(obj, dict) else []
                    for p in papers or []:
                        sid, fn = p.get("source_id"), p.get("file")
                        if sid and fn and (d / fn).exists():
                            srcs.append((str(sid), d / fn))
                except Exception:
                    pass
            if not srcs:
                for sid, pat in (("primary", "*primary*material*.json"),
                                 ("secondary", "*secondary*material*.json")):
                    hit = sorted(d.rglob(pat))
                    if hit:
                        srcs.append((sid, hit[0]))
            if not srcs:
                any_json = [p for p in sorted(d.rglob("*.json"))
                            if p.name not in ("case.json", "material.json")
                            and "manifest" not in p.name]
                if any_json:
                    srcs.append(("primary", any_json[0]))
        case_id = case_id or d.name
    case_id = case_id or "case"

    prev: dict | None = None
    if append:
        ap = Path(append)
        try:
            prev = json.loads(read(ap))
        except Exception as exc:
            gaps.append(f"增量基图读不出（{ap}）：{exc}；退化为单次全量构建")
            prev = None

    if not srcs:
        msg = "没有找到段落材料 JSON"
        r = {"case_id": case_id, "status": "blocked", "error": msg,
             "checks": {}, "gaps": [msg], "deliverables": ["missing_input.md"],
             "evidence": {"unit": "source_id+paragraph_id+quote", "items": []},
             "output_contract": {k: None for k in contract},
             "output_contract_echo": {k: {"status": "partial", "source": "pointer"}
                                      for k in contract}}
        (out_d / "result.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        (out_d / "missing_input.md").write_text("\n".join([
            "# 缺料说明：没有找到段落材料", "",
            f"- 输入目录：`{root}`" if root else "- 未给 `--input`，也没有 `--primary/--secondary`",
            "- 找过：`material.json` 索引 → `*primary*material*.json` / "
            "`*secondary*material*.json` → 目录下任意 `.json`（排除 case.json / manifest）",
            "- 需要补：至少一份带段落的材料 JSON（两篇更好，能算跨篇对齐记录）",
            "- 缺什么才能出产物：没有段落就没有实体与关系可抽，"
            "`graph.json` / `graph.md` / `triples.tsv` 无从生成",
            "- 补齐后重跑：`python run.py --input <目录> --work <w> --out <o>`",
        ]) + "\n", encoding="utf-8")
        return {"status": "blocked", "case_id": case_id, "gaps": [msg],
                "deliverables": ["missing_input.md"]}

    meta, mgaps = load_manifest(root)
    gaps.extend(mgaps)

    nodes: list[dict] = []
    edges: list[dict] = []
    measures: list[dict] = []
    rejected: list[dict] = []
    unmatched: list[dict] = []
    per_source: dict[str, int] = {}
    para_index: dict[tuple[str, str], str] = {}
    bad_sources: list[dict] = []
    pdf_inputs: list[str] = []
    if root is not None:
        pdf_inputs = sorted(p.name for p in root.rglob("*.pdf"))
    prev_sources = set((prev or {}).get("sources") or [])

    for sid, path in srcs:
        if sid in prev_sources:
            gaps.append(f"{sid} 已在增量基图里：跳过，不重复抽取")
            continue
        if not path.exists():
            gaps.append(f"{sid} 材料不存在：{path}")
            bad_sources.append({"source_id": sid, "path": str(path),
                                "reason": "文件不存在"})
            continue
        try:
            obj = json.loads(read(path))
        except Exception as exc:
            gaps.append(f"{sid} 材料不是合法 JSON：{exc}")
            bad_sources.append({"source_id": sid, "path": str(path),
                                "reason": f"不是合法 JSON：{exc}"})
            continue
        paras: list[tuple[str, str]] = []
        walk_paragraphs(obj, paras)
        seen, uniq = set(), []
        for pid, txt in paras:
            k = (pid, txt)
            if k not in seen:
                seen.add(k)
                uniq.append((pid, txt))
        per_source[sid] = len(uniq)
        if not uniq:
            gaps.append(f"{sid} 未解析出段落（≥20 字）")
            bad_sources.append({"source_id": sid, "path": str(path),
                                "reason": "JSON 里没有 ≥20 字的段落"
                                          "（字段名不认识或正文为空）"})
            continue
        for pid, txt in uniq:
            para_index[(sid, pid)] = txt
        n, e, m, rj = extract(uniq, sid, author_surnames(meta, sid), opts)
        if not opts["evidence"]:
            for thing in list(n) + list(e):
                thing["evidence"] = [{"source_id": sid, "paragraph_id": "",
                                      "quote": "", "stance": "unspecified"}]
        nodes.extend(n)
        edges.extend(e)
        measures.extend(m)
        rejected.extend(rj)
        unmatched.extend(unmatched_candidates(uniq, sid))

    if not opts["direction"]:
        for e in edges:
            e.pop("direction_basis", None)
            e.pop("applicability", None)
        gaps.append("direction 已关闭：未记录方向依据与适用条件（消融用）")
    if not opts["numbers"]:
        gaps.append("numbers 已关闭：未抽数值结果（消融用）")

    if len(set(per_source) | prev_sources) < 2:
        gaps.append("只解析到一篇材料；题目要求两篇进同一图谱")
    if pdf_inputs:
        gaps.append(f"输入目录含 {len(pdf_inputs)} 个 PDF：本 Skill 不解析 PDF"
                    "（位置用 JATS paragraph_id），未从 PDF 取任何数据")

    for n in nodes:
        if n["type"] == "paper":
            sid = n["id"].split(":", 1)[1]
            info = meta.get(sid, {})
            n["meta"] = {"doi": info.get("doi", "unknown"),
                         "title": info.get("title", "unknown"),
                         "authors": info.get("authors", "unknown"),
                         "license": info.get("license", "unknown"),
                         "sha256": info.get("sha256", "unknown")}
            if info.get("doi") in (None, "", "unknown"):
                gaps.append(f"{sid} 缺 DOI（manifest 未登记，未借用另一篇的）")

    # 去重节点/边（同 id 并证据）
    def dedupe(nodes: list, edges: list) -> tuple[list, list]:
        nm: dict[str, dict] = {}
        for n in nodes:
            if n["id"] in nm:
                for evd in n.get("evidence") or []:
                    _add_ev(nm[n["id"]], evd)
                for f in n.get("surface_forms") or []:
                    if f not in nm[n["id"]]["surface_forms"]:
                        nm[n["id"]]["surface_forms"].append(f)
            else:
                nm[n["id"]] = n
        em: dict[tuple, dict] = {}
        for e in edges:
            k = (e["source"], e["target"], e["type"], e.get("attribution", ""))
            if k in em:
                for evd in e.get("evidence") or []:
                    _add_ev(em[k], evd)
            else:
                em[k] = e
        return list(nm.values()), list(em.values())

    nodes, edges = dedupe(nodes, edges)

    prev_ids = {n["id"] for n in (prev or {}).get("nodes") or []}
    prev_nodes = {n["id"]: n for n in (prev or {}).get("nodes") or []}
    incremental = None
    if prev is not None:
        nodes, edges, merges, incremental = align_append(
            prev, nodes, edges, opts)
    else:
        merges = build_merges(nodes) if opts["align"] else []
        if opts["align"]:
            nodes, edges = resolve(nodes, edges, merges)
        else:
            gaps.append("align 已关闭：未做跨篇对齐判定（消融用）")

    homonyms = detect_homonyms(nodes)
    checks, probs = verify(nodes, edges, para_index,
                           prev_ids if append else None, bool(append))

    covered = coverage_block(nodes, edges, measures, [])
    templates, qgaps = load_query_templates()
    gaps.extend(qgaps)
    all_sources = sorted(set(per_source) | prev_sources)
    queries = run_queries(templates, nodes, edges, merges, measures, all_sources)
    covered = coverage_block(nodes, edges, measures, queries)
    q_ok = covered["queries"]["n_with_results"]
    if q_ok < 3:
        gaps.append(f"只有 {q_ok}/3 条查询有结果")
    for p in probs:
        gaps.append(f"{p[0]}：{p[1]}")
    for kind in covered["entity_types"]["missing"]:
        gaps.append(f"实体类型 {kind} 未在本批材料中出现（未补造）")
    for kind in covered["relation_types"]["missing"]:
        gaps.append(f"关系类型 {kind} 未在本批材料中出现（未补造）")

    nodes.sort(key=lambda n: n["id"])
    edges.sort(key=lambda e: (e["source"], e["target"], e["type"],
                              e.get("attribution") or ""))
    measures.sort(key=lambda r: (r["source_id"], r["paragraph_id"], r["metric"]))
    merges = sorted(merges, key=lambda m: (m["kind"], m["label"]))

    status = "success" if nodes and edges and not probs else "partial"
    if not opts["evidence"]:
        status = "partial"

    graph = {
        "case_id": case_id,
        "skill": SKILL_NAME,
        "skill_version": VERSION,
        "sources": all_sources,
        "paragraphs_per_source": {**(prev or {}).get("paragraphs_per_source", {}),
                                  **per_source},
        "papers": [{"source_id": n["id"].split(":", 1)[1], **n.get("meta", {})}
                   for n in nodes if n["type"] == "paper"],
        "nodes": nodes, "edges": edges, "merges": merges,
        "measurements": measures,
        "conflicts": {
            "keep_separate": [m for m in merges
                              if m["decision"] == "keep_separate"],
            "homonyms": homonyms,
            "unresolved_edges": [
                {"edge": f"{e['source']} -{e['type']}-> {e['target']}",
                 "confidence": e.get("confidence"),
                 "attribution": e.get("attribution"),
                 "reason": "共现或转述，不是本文的直接陈述"}
                for e in edges if e.get("confidence") == "low"
                or e.get("attribution") == "reported_elsewhere"],
        },
        "coverage": covered,
        "boundary": {"unmatched_candidates": unmatched,
                     "rejected_relation_candidates": rejected,
                     "pdf_inputs": pdf_inputs,
                     "no_page_numbers": "证据不含页码；位置是 JATS paragraph_id"},
    }
    if incremental:
        graph["incremental"] = incremental

    schema_ok, schema_probs = validate_schema(graph)
    graph["schema_check"] = {"ok": schema_ok, "schema": "rules/graph.schema.json",
                             "problems": schema_probs}
    if not schema_ok:
        gaps.append(f"schema 校验失败：{schema_probs[:3]}")
        status = "partial"

    metrics = quality_metrics(nodes, edges, merges, measures, queries,
                              checks, para_index)

    gp = out_d / "graph.json"
    gp.write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
    gmp = out_d / "graph.md"
    write_graph_md(gmp, graph)

    tp = out_d / "triples.tsv"
    rows = []
    for e in edges:
        for evd in e.get("evidence") or []:
            rows.append("\t".join([
                e["source"], e["type"], e["target"], e.get("confidence") or "",
                e.get("attribution") or "", str(evd["source_id"]),
                str(evd["paragraph_id"]), evd.get("stance") or "",
                (evd.get("quote") or "").replace("\t", " ").replace("\n", " ")]))
    tp.write_text("source\trelation\ttarget\tconfidence\tattribution\t"
                  "source_id\tparagraph_id\tstance\tquote\n"
                  + "".join(r + "\n" for r in rows), encoding="utf-8")

    qp = out_d / "queries.json"
    qp.write_text(json.dumps({"case_id": case_id, "queries": queries},
                             ensure_ascii=False, indent=1), encoding="utf-8")
    vp = out_d / "verify.json"
    vp.write_text(json.dumps(
        {"counts": {c: (0 if checks[c]["ok"] else 1) for c in CODES},
         "problems": probs, "schema_check": graph["schema_check"],
         "metrics": metrics}, ensure_ascii=False, indent=1), encoding="utf-8")
    if incremental:
        (out_d / "incremental.json").write_text(
            json.dumps(incremental, ensure_ascii=False, indent=1), encoding="utf-8")

    if bad_sources or not nodes or not edges:
        lines = ["# 读不出或读不全的材料", "",
                 f"- 输入：`{root}`" if root else "- 输入：命令行参数",
                 f"- 材料 {len(srcs)} 份，读出段落 {sum(per_source.values())} 段，"
                 f"节点 {len(nodes)}、边 {len(edges)}", "",
                 "| source_id | 材料 | 读不出的原因 |", "|-|-|-|"]
        for b in bad_sources:
            lines.append(f"| {b['source_id']} | `{Path(b['path']).name}` | "
                         f"{b['reason']} |")
        if not bad_sources:
            lines.append("| — | — | 材料都读出了段落，但抽取后无节点或无边 |")
        if pdf_inputs:
            lines.append(f"| — | `{', '.join(pdf_inputs[:5])}` | 输入含 PDF："
                         "本 Skill 不解析 PDF，未从 PDF 取任何数据 |")
        lines += ["", "图谱只包含读得出的部分；缺失部分是材料本身的问题，"
                      "未补造实体或关系。"]
        (out_d / "unreadable.md").write_text("\n".join(lines) + "\n",
                                             encoding="utf-8")

    rules = {
        "实体抽取": {"按类型": {k: sum(1 for n in nodes if n["type"] == k)
                            for k in sorted({n["type"] for n in nodes})},
                 "规则": "LEX 词表按句匹配（method/dataset/metric/task/material）、"
                         "et al. 与 manifest 作者姓氏建 researcher、"
                         "显式结论句建 finding"},
        "关系抽取": {"按类型": {k: sum(1 for e in edges if e["type"] == k)
                            for k in sorted({e["type"] for e in edges})},
                 "按强度": {k: sum(1 for e in edges if e.get("confidence") == k)
                          for k in CONF},
                 "规则": "固定关系（uses_method/evaluates_on/reports_metric/"
                         "about/mentions_material）＋关系词窗口（compared_with/"
                         "outperforms/supports/contradicts）＋同句方法×指标共现"},
        "合并": {"merge": sum(1 for m in merges if m["decision"] == "merge"),
               "keep_separate": sum(1 for m in merges
                                    if m["decision"] == "keep_separate"),
               "规则": "方法/任务按规范化标签跨篇合并；数据集/指标/材料/研究者/"
                       "结论保留分离并写冲突"},
        "查询": {"有结果的查询": q_ok, "查询总数": len(queries),
               "带子图的查询": covered["queries"]["n_with_subgraph"]},
        "数值": {"条目": len(measures), "跨篇汇总": "none"},
        "读不出材料": bad_sources,
    }

    arts = {"graph": str(gp), "graph_md": str(gmp), "triples": str(tp),
            "queries": str(qp), "verify": str(vp)}
    payload = {
        "parsed": {"papers": graph["papers"], "nodes": len(nodes),
                   "edges": len(edges),
                   "node_types": rules["实体抽取"]["按类型"],
                   "relation_types": rules["关系抽取"]["按类型"],
                   "merges": rules["合并"], "measurements": len(measures),
                   "coverage": covered},
        "evidence": {"unit": "source_id+paragraph_id+quote",
                     "strength_levels": ["high", "medium", "low"],
                     "stance_levels": list(STANCES),
                     "checks": checks, "gaps": gaps,
                     "metrics": metrics,
                     "items": [{"node": n["id"],
                                "source_id": n["evidence"][0]["source_id"],
                                "paragraph_id": n["evidence"][0]["paragraph_id"],
                                "quote": n["evidence"][0]["quote"],
                                "stance": n["evidence"][0].get("stance")}
                               for n in nodes if n.get("evidence")][:50],
                     "edge_items": [
                         {"edge": f"{e['source']} -{e['type']}-> {e['target']}",
                          "confidence": e.get("confidence"),
                          "attribution": e.get("attribution"),
                          "source_id": (e.get("evidence") or [{}])[0].get("source_id"),
                          "paragraph_id": (e.get("evidence") or [{}])[0].get("paragraph_id"),
                          "quote": (e.get("evidence") or [{}])[0].get("quote")}
                         for e in edges][:50],
                     "schema_check": graph["schema_check"]},
        "status": {"status": status,
                   "reason": gaps[0] if gaps else
                   "两篇材料、G01–G09、查询与 schema 全部通过",
                   "queries_with_results": q_ok, "n_queries": len(queries),
                   "manual_takeover": "gaps 里逐条给出人工接管点；"
                                      "未报告项未写成事实"},
    }
    echo, echo_status = {}, {}
    for key in contract:
        if key == "阅读/解析结果":
            echo[key], echo_status[key] = payload["parsed"], {
                "status": "full", "source": "graph.json"}
        elif key == "证据与边界":
            echo[key], echo_status[key] = payload["evidence"], {
                "status": "full", "source": "evidence"}
        elif key == "运行状态":
            echo[key], echo_status[key] = payload["status"], {
                "status": "full", "source": "status"}
        else:
            echo[key], echo_status[key] = None, {"status": "partial",
                                                 "source": "pointer"}
            gaps.append(f"契约字段「{key}」未识别，已置 null")

    def file_info(p: Path) -> dict:
        return {"name": p.name, "bytes": p.stat().st_size if p.exists() else 0,
                "sha256": sha16(p)}

    manifest = {
        "skill": SKILL_NAME, "skill_version": VERSION, "case_id": case_id,
        "python": sys.version.split()[0],
        "command": " ".join(argv) if argv else "pipeline(...)",
        "inputs": [{"source_id": sid, "path": Path(p).name,
                    "bytes": Path(p).stat().st_size if Path(p).exists() else 0,
                    "sha256": sha16(Path(p)), "paragraphs": per_source.get(sid, 0)}
                   for sid, p in srcs],
        "outputs": [file_info(x) for x in (gp, gmp, tp, qp, vp)],
        "mode": "append" if append else "fresh",
        "disable": sorted(k for k, v in opts.items() if not v),
        "determinism": "graph.json / graph.md / triples.tsv 不含时间戳与随机量，"
                       "同输入两次运行字节一致；时间只出现在 result.json 的 elapsed_s",
        "reproduce": f"python run.py --input {root or '<dir>'} "
                     f"--work <w> --out <o>",
    }
    (out_d / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    result = {
        "output_contract_echo": echo_status,
        "case_id": case_id,
        "skill": SKILL_NAME,
        "skill_version": VERSION,
        "status": status,
        "sources": graph["sources"],
        "papers": graph["papers"],
        "stats": {"paragraphs": per_source, "nodes": len(nodes),
                  "edges": len(edges), "merges": len(merges),
                  "merge": rules["合并"]["merge"],
                  "keep_separate": rules["合并"]["keep_separate"],
                  "measurements": len(measures),
                  "queries_with_results": q_ok,
                  "evidence_items": metrics["n_evidence"],
                  "elapsed_s": round(time.perf_counter() - t0, 3)},
        "checks": checks,
        "coverage": covered,
        "metrics": metrics,
        "boundary": graph["boundary"],
        "evidence": payload["evidence"],
        "queries": queries,
        "merges": merges,
        "measurements": measures,
        "conflicts": graph["conflicts"],
        "incremental": incremental,
        "规则命中": rules,
        "deliverables": [{"kind": "graph", "path": arts["graph"]},
                         {"kind": "graph_readable", "path": arts["graph_md"]},
                         {"kind": "triples", "path": arts["triples"]},
                         {"kind": "queries", "path": arts["queries"]},
                         {"kind": "verify", "path": arts["verify"]},
                         {"kind": "run_manifest",
                          "path": str(out_d / "run_manifest.json")}],
        "gaps": gaps,
        "uncertainty": list(gaps),
        "output_contract": echo,
    }
    result["阅读/解析结果"] = payload["parsed"]
    result["证据与边界"] = payload["evidence"]
    result["运行状态"] = payload["status"]
    (out_d / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def align_append(prev: dict, nodes: list, edges: list,
                 opts: dict) -> tuple[list, list, list, dict]:
    """增量更新：既有节点 id 与编号原样保留，新论文只做新增/合并/冲突判定。"""
    prev_nodes = {n["id"]: n for n in prev.get("nodes") or []}
    prev_edges = list(prev.get("edges") or [])
    prev_merges = list(prev.get("merges") or [])
    by_key: dict[tuple[str, str], list[dict]] = {}
    for n in sorted(prev_nodes.values(), key=lambda x: x["id"]):
        if n["type"] == "paper":
            continue
        by_key.setdefault((n["type"], n.get("canonical") or norm_key(n["label"])),
                          []).append(n)

    mapping: dict[str, str] = {}
    added, merged, separated, conflicts = [], [], [], []
    for n in nodes:
        if n["type"] == "paper":
            continue
        key = (n["type"], n.get("canonical") or norm_key(n["label"]))
        hits = by_key.get(key) or []
        if not hits:
            added.append({"id": n["id"], "type": n["type"], "label": n["label"]})
            continue
        hit = hits[0]
        existing_ids = sorted(h["id"] for h in hits)
        srcs = sorted(set(sources_of(n)) | {s for h in hits for s in sources_of(h)})
        forms = sorted(set(n.get("surface_forms") or [])
                       | {f for h in hits for f in h.get("surface_forms") or []})
        if n["type"] in MERGE_KINDS:
            mapping[n["id"]] = hit["id"]
            for evd in n.get("evidence") or []:
                _add_ev(prev_nodes[hit["id"]], evd)
            for f in n.get("surface_forms") or []:
                if f not in prev_nodes[hit["id"]]["surface_forms"]:
                    prev_nodes[hit["id"]]["surface_forms"].append(f)
            rec = {"kind": n["type"], "label": hit["label"],
                   "candidate_ids": sorted(set(existing_ids) | {n["id"]}),
                   "sources": srcs, "decision": "merge",
                   "action": "merge_into_existing", "kept_id": hit["id"],
                   "basis": [f"类型 {n['type']}，规范化键相同：{key[1]}",
                             f"跨篇显式出现：{srcs}",
                             f"原文字面写法：{forms}",
                             "增量沿用首次出现的 id，不重命名既有节点",
                             "id 里的 @来源 后缀记录首次出现来源，不代表唯一来源",
                             "只合并名称，不断言同一模型权重、超参或实验条件"],
                   "conflict": ""}
            if rec not in prev_merges and rec not in merged:
                merged.append(rec)
        else:
            clause = KEEP_CONFLICT.get(n["type"], "同名不同实例，不得合并")
            separated.append({"kind": n["type"], "label": hit["label"],
                              "candidate_ids": sorted(set(existing_ids) | {n["id"]}),
                              "sources": srcs, "decision": "keep_separate",
                              "action": "keep_separate", "kept_id": hit["id"],
                              "new_id": n["id"], "existing_ids": existing_ids,
                              "basis": [f"类型 {n['type']}，规范化键相同：{key[1]}",
                                        f"跨篇显式出现：{srcs}",
                                        f"原文字面写法：{forms}", KEEP_BASIS],
                              "conflict": clause})
            conflicts.append({"kind": n["type"], "label": n["label"],
                              "existing_ids": existing_ids, "new_id": n["id"],
                              "conflict": clause})

    new_nodes = [n for n in nodes if n["id"] not in mapping]
    for n in new_nodes:
        prev_nodes[n["id"]] = n
    out_edges: dict[tuple, dict] = {}
    for e in prev_edges:
        out_edges[(e["source"], e["target"], e["type"],
                   e.get("attribution", ""))] = e
    for e in edges:
        s = mapping.get(e["source"], e["source"])
        t = mapping.get(e["target"], e["target"])
        k = (s, t, e["type"], e.get("attribution", ""))
        if k in out_edges:
            for evd in e.get("evidence") or []:
                _add_ev(out_edges[k], evd)
        else:
            out_edges[k] = {**e, "source": s, "target": t}
    kept_ids = sorted(prev_nodes)
    endpoints = {e["source"] for e in prev_edges} | {e["target"] for e in prev_edges}
    incremental = {
        "mode": "append",
        "previous": {"case_id": prev.get("case_id"),
                     "nodes": len(prev.get("nodes") or []),
                     "edges": len(prev_edges),
                     "sources": prev.get("sources") or []},
        "kept_ids": kept_ids,
        "id_preservation": {"kept": len(kept_ids),
                            "previous_n": len(prev.get("nodes") or []),
                            "missing": sorted(set(prev_nodes) - set(kept_ids)),
                            "edge_endpoints_missing": sorted(endpoints - set(kept_ids)),
                            "rule": "增量模式只新增与合并，不重命名既有 id；"
                                    "合并时沿用首次出现的 id"},
        "added_nodes": added,
        "merged_into_existing": merged,
        "kept_separate": separated,
        "conflicts": conflicts,
        "policy": {"merge": sorted(MERGE_KINDS),
                   "keep_separate": [k for k in ENTITY_TYPES
                                     if k not in MERGE_KINDS | {"paper"}]},
    }
    merges = prev_merges + merged + separated
    return list(prev_nodes.values()), list(out_edges.values()), merges, incremental


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="从 JATS 段落材料构建可查询知识图谱（支持增量更新）")
    ap.add_argument("--input", default=None)
    ap.add_argument("--primary", default=None)
    ap.add_argument("--secondary", default=None)
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="output")
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--append", default=None,
                    help="既有 graph.json：只处理新论文，保留既有节点与编号")
    ap.add_argument("--disable", default="",
                    help="消融开关，逗号分隔：evidence,align,direction,numbers")
    a = ap.parse_args(argv)
    disable = tuple(x.strip() for x in (a.disable or "").split(",") if x.strip())
    started = ["run.py"] + list(argv if argv is not None else sys.argv[1:])
    res = pipeline(input_dir=a.input, primary=a.primary, secondary=a.secondary,
                   work=a.work, out=a.out, case_id=a.case_id,
                   append=a.append, disable=disable, argv=started)
    ok = res.get("status") in ("success", "partial")
    print(json.dumps({"ok": ok, "status": res.get("status"),
                      "case_id": res.get("case_id"),
                      "sources": res.get("sources"),
                      "nodes": (res.get("stats") or {}).get("nodes"),
                      "edges": (res.get("stats") or {}).get("edges"),
                      "queries_ok": (res.get("stats") or {}).get(
                          "queries_with_results"),
                      "mode": "append" if a.append else "fresh",
                      "gaps": len(res.get("gaps") or [])},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
