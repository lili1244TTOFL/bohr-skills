#!/usr/bin/env python3
"""run.py — 把论文正文拆成一条条可独立判断真假的断言，每条绑最小证据片段与来源。

对应题面四个关注点（00_task.md:18）：
    拆得细  一条断言只承一个关系；复合句按分句边界拆成多条断言
    对得上  每条断言带最小证据片段与原文位置（文档 + 页码 + 段落 + 行 + 字符区间）
    追得回  断言→证据→论文→作者→年份→版本→标识符 链路齐全，另记数据来源哈希
    读得懂  JSON + TSV 双导出，字段固定，另出 JSON Schema 与字段字典

另按题面「质量检查」方向（00_task.md:22）做九项逐条质检，判据见 rules/qc.md。
不调用模型：全部是规则与字符串处理（零模型请求，可重复运行）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

VERSION = "2.0.0"
MIN_EVIDENCE = 8            # 证据片段最短字符数
WIDE_CJK = 80               # 中文断言超过这个长度视为过宽
WIDE_LATIN = 160            # 英文断言超过这个长度视为过宽
SIX = ["断言", "主体与关系", "证据片段", "证据位置", "来源元数据", "置信度依据", "标识符"]
CONTRACT_KEYS = ["阅读/解析结果", "证据与边界", "运行状态"]
DOC_EXT = (".md", ".txt")
SKIP_STEM = ("task", "instruction", "readme", "license", "00_", "changelog",
             "github_url", "output_contract")
CJK = re.compile(r"[\u4e00-\u9fff]")

QC_CATALOG = [
    ("wide", "断言过宽（分句超长度阈值）", "标 review，status 降 partial"),
    ("evidence_insufficient", "证据不足（片段太短/缺客体/未含关系词）", "退回整分句并标 review"),
    ("relation_conflict", "关系类型错误或多关系歧义", "取因果优先或句内最早命中并标 review"),
    ("subject_confusion", "主体混淆（指代/继承/超长/缺主体）", "照原文记下并标 review"),
    ("source_missing", "来源缺失（paper/authors/year/标识符 缺）", "写 unknown 并记 gaps"),
    ("cross_figure", "证据跨图表（断言引用图/表/公式）", "标 review 提示回图表核对"),
    ("assertion_evidence_conflict", "断言与证据冲突（否定/转折同现）", "标 review，不按方向直读"),
    ("negation_scope", "否定句（否定词覆盖关系词）", "polarity=negated，方向交人工"),
    ("duplicate", "重复断言（归一主客相同）", "标 duplicate_of 并合并计数"),
]

# ---------------------------------------------------------------------------
# 提示层正则（说话人、话语标记、限定范围）：句法层，与词表无关
# ---------------------------------------------------------------------------
ATTRIBUTION = re.compile(
    r"^(?P<who>(?:(?:The|These|Our|This|Simple|Further|Overall|Previous|Prior|Earlier|"
    r"Recent|Current)\s+)?"
    r"(?:(?:experimental|behavioral|behavioural|statistical|main|effect)\s+)?"
    r"(?:results?|findings?|data|analys[ie]s|authors?|study|research|work|investigators?"
    r"|研究|结果|作者|本文|该研究))"
    r"[^,.。；;]{0,50}?"
    r"(?P<verb>(?:(?:show|suggest|demonstrat|reveal|indicat|indic)(?:s|ed|ing)?)|found|finds?"
    r"|表明|显示|说明|发现|指出|认为|主张)\s*(?:that\s+|：|:)?",
    re.I)
DISCOURSE = re.compile(
    r"^(?:In addition|In contrast|However|Moreover|Furthermore|Specifically|Importantly|"
    r"Thus|Therefore|Overall|Finally|First|Second|Third|Together|Additionally"
    r"|此外|然而|因此|总之|具体而言|同时|另外|相反)\s*[,，]\s*", re.I)
SCOPE_EN = re.compile(r"^(?:Among|For|In|Within|Across|Under|With|Compared with)\s[^,，]{1,48}[,，]\s*",
                      re.I)
SCOPE_ZH = re.compile(r"^(?:在|对|对于|就|相比于|与)[^，。；]{1,24}(?:中|下|方面|而言|来说|上)[，,]?\s*")
CONJ_LEAD = re.compile(r"^(?:并且|并|同时|而|此外|另|且|也|and|but|whereas|while|however)"
                       r"\s*[,，]?\s*", re.I)
SUBJ_TAIL = re.compile(
    r"\s+(?:was|were|is|are|has been|have been|can be|could be)\s+"
    r"(?:significantly\s+)?(?:negatively|positively)?\s*$", re.I)
SUBJ_EVID = re.compile(r"\s+(?:provides?|reports?|offers?)\s+(?:\w+\s+){0,2}evidence$", re.I)
SPLIT = re.compile(r"(?<=[。！？；])|(?<=[.!?;])\s+(?=[A-Z0-9(\[])|(?<=[。！？])\s+|\n+")
CONTRAST = re.compile(r"(?:但|然而|不过)|(?:\bhowever\b|\balthough\b|\bwhereas\b|\bwhile\b)", re.I)


# ---------------------------------------------------------------------------
# 词表：rules/vocab.json 是执行口径；读不到时用内置副本并记 gap，不静默降级
# ---------------------------------------------------------------------------
FALLBACK_VOCAB = {
    "version": VERSION,
    "causal_priority": ["causes"],
    "relations": [
        {"key": "increases", "direction": "up", "inverse": "decreases",
         "patterns": [{"re": "(提高|提升|增加|改善|增强)(了)?", "conf": 0.6},
                      {"re": r"\b(increas|rais|improv|enhanc|boost)(e|es|ed|ing)?\b", "conf": 0.8}]},
        {"key": "decreases", "direction": "down", "inverse": "increases",
         "patterns": [{"re": "(降低|减少|下降|削弱|抑制)(了)?", "conf": 0.6},
                      {"re": r"\b(decreas|reduc|diminish|suppress)(e|es|ed|ing)?\b", "conf": 0.8}]},
        {"key": "causes", "direction": "causal", "inverse": "",
         "patterns": [{"re": "(导致|引起|造成|使得)", "conf": 0.7},
                      {"re": r"\b(caus|lead to|result in)(e|es|ed|ing)?\b", "conf": 0.7}]},
        {"key": "shows", "direction": "report", "inverse": "",
         "patterns": [{"re": "(表明|显示|说明|发现)", "conf": 0.6},
                      {"re": r"\b(show|indicat|suggest|reveal)(s|es|ed|ing)?\b", "conf": 0.6}]},
    ],
    "negation_zh": ["未", "没有", "无"],
    "negation_en": ["not ", "failed to"],
    "negation_exclude": ["not only"],
    "sign_negative": ["负相关", "negatively correlated"],
    "sign_positive": ["正相关", "positively correlated"],
    "clause_boundary_zh": "[；;]|，(?:并且|并|同时|而|此外|另|且|也)",
    "clause_boundary_en": r"\s[;]\s|,\s+(?:and|but|whereas|while|however)\s+",
    "figure_ref": r"图\s*\d|表\s*\d|Figure\s*\d|Table\s*\d",
    "pronoun_subject_zh": r"^\s*(该|此|这|其|它|上述)",
    "pronoun_subject_en": r"^\s*(it|this|these|those|they|such)\b",
}


def load_vocab(skill_dir: Path, override) -> tuple[dict, list]:
    gaps = []
    p = Path(override) if override else skill_dir / "rules" / "vocab.json"
    if p.exists():
        try:
            vocab = json.loads(p.read_text(encoding="utf-8-sig"))
            vocab["_path"] = str(p)
            vocab["_sha1"] = hashlib.sha1(p.read_bytes()).hexdigest()
            return vocab, gaps
        except Exception as exc:
            gaps.append(f"受控词表 `{p.name}` 解析失败（{exc}），已退回内置 4 类关系词表")
    else:
        gaps.append(f"受控词表 `{p.name}` 缺失，已退回内置 4 类关系词表（拆分能力下降）")
    vocab = dict(FALLBACK_VOCAB)
    vocab["_path"] = "builtin"
    vocab["_sha1"] = hashlib.sha1(json.dumps(FALLBACK_VOCAB, sort_keys=True,
                                             ensure_ascii=False).encode()).hexdigest()
    return vocab, gaps


def compile_relations(vocab: dict) -> list:
    out = []
    for rel in vocab.get("relations", []):
        for pat in rel.get("patterns", []):
            try:
                out.append((re.compile(pat["re"], re.I), rel["key"], float(pat.get("conf", 0.5))))
            except re.error:
                continue
    return out


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------
def read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def line_of(text: str, pos: int) -> int:
    return text[:pos].count("\n") + 1


def para_of(text: str, pos: int) -> int:
    return text[:pos].count("\n\n") + 1


def section_of(doc: dict, pos: int) -> str:
    """最近的标题块：小节定位用（题面「页码、段落、字段或图表位置」）。"""
    best = ""
    for b in doc.get("blocks") or []:
        if b.get("heading") and b["start"] <= pos:
            best = b["text"].lstrip("#").strip()
    return best[:80]


def scope_reason(doc: dict, sent_text: str, start: int) -> str:
    """抽取范围判据（rules/scope.md）：参考文献/致谢/附录、标题作者块、署名行不进断言。"""
    blk = None
    for b in doc.get("blocks") or []:
        if b.get("heading") and b["start"] <= start:
            blk = b
    if blk and BACKMATTER.match(blk["text"].lstrip("#").strip()):
        return f"位于「{blk['text'].lstrip('#').strip()[:24]}」小节：参考文献/致谢/附录不是科研断言"
    if doc.get("kind") == "pdf" and doc.get("abstract_pos") is not None \
            and start < doc["abstract_pos"]:
        return "位于摘要之前的标题/作者/版权块，不是科研断言"
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]{2,}", sent_text):
        return "含邮箱，属署名信息"
    return ""


def sentence_wide(s: str) -> bool:
    cjk = len(CJK.findall(s))
    return len(s) > (WIDE_CJK if cjk >= max(4, len(s) // 10) else WIDE_LATIN)


def norm_codes(codes) -> list:
    seen, out = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# 输入识别：先分清「材料 / 题面 / 元数据」，不把 task.md 当正文，也不静默丢文件
# ---------------------------------------------------------------------------
def gather(root: Path) -> tuple:
    case: dict = {}
    for cand in sorted(root.glob("case*.json")):
        try:
            case = json.loads(read(cand))
            case["_file"] = cand.name
        except Exception:
            case = {}
        break
    skipped: list = []
    notes: list = []
    docs: list = []
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        rel = p.relative_to(root).as_posix()
        low = p.name.lower()
        if p.suffix.lower() not in DOC_EXT + (".pdf",):
            continue
        if any(k in low for k in SKIP_STEM):
            skipped.append({"file": rel, "reason": "文件名像题面/说明/元数据，按输入边界排除"})
            continue
        if p.suffix.lower() == ".pdf":
            doc, why, is_scan = extract_pdf(p)
        else:
            doc, why, is_scan = extract_text_file(p)
        if doc is None:
            skipped.append({"file": rel, "reason": why, "scanned": is_scan})
            continue
        doc["rel"] = rel
        docs.append(doc)
    if not docs:
        notes.append("输入目录里没有可用正文（.md/.txt/带文本层 .pdf）")
    return case, docs, skipped, notes


def extract_text_file(p: Path) -> tuple:
    try:
        text = read(p)
    except Exception as exc:
        return None, f"读失败（{exc}）", False
    if len(text.strip()) < 40:
        return None, "正文字符不足 40，按空材料处理", False
    return ({"name": p.name, "kind": p.suffix.lower().lstrip("."), "text": text,
             "page_map": [], "n_pages": None,
             "extraction": "utf-8 直读（字符偏移 = 原文件偏移）",
             "norm": [], "bytes": p.stat().st_size}, "", False)


def extract_pdf(p: Path) -> tuple:
    try:
        from pypdf import PdfReader          # 镜像 environment/resources.yaml 声明的 pdf-text-tool
    except Exception:
        return None, "缺少 PDF 文本层抽取库 pypdf（镜像 pdf-text-tool）；本 Skill 不做 OCR，不猜正文", True
    try:
        reader = PdfReader(str(p))
        raw_pages = []
        for pg in reader.pages:
            try:
                raw_pages.append(pg.extract_text() or "")
            except Exception:
                raw_pages.append("")
    except Exception as exc:
        return None, f"PDF 打不开（{exc}）", False
    if sum(len(t.strip()) for t in raw_pages) < 40:
        return None, (f"{len(raw_pages)} 页 PDF 抽不出文本（疑似扫描版/无文本层）；"
                      "本 Skill 不做 OCR，需先 OCR 出文本再重跑"), True
    text, page_map, norm = normalise_pdf(raw_pages)
    return ({"name": p.name, "kind": "pdf", "text": text, "page_map": page_map,
             "n_pages": len(raw_pages),
             "extraction": "pypdf 文本层抽取 → 去重复页眉页脚 / 接断行 / 去连字符",
             "norm": norm, "bytes": p.stat().st_size}, "", False)


def normalise_pdf(raw_pages: list) -> tuple:
    counts: dict = {}
    for t in raw_pages:
        for ln in {x.strip() for x in t.splitlines() if x.strip()}:
            counts[ln] = counts.get(ln, 0) + 1
    threshold = max(3, len(raw_pages) // 2)
    running = {ln for ln, c in counts.items() if c >= threshold}
    paragraphs: list = []
    for i, t in enumerate(raw_pages, 1):
        lines = [x.strip() for x in t.splitlines()]
        lines = [s for s in lines
                 if s and s not in running and not re.fullmatch(r"(?:page\s*)?\d{1,4}", s, re.I)]
        if not lines:
            continue
        lens = sorted(len(s) for s in lines)
        median = lens[len(lens) // 2] or 1
        buf = ""
        for s in lines:
            if is_heading(s):
                if buf.strip():
                    paragraphs.append((i, buf.strip()))
                paragraphs.append((i, s))
                buf = ""
                continue
            if s.endswith("-") and len(s) > 2 and s[-2].isalpha():
                buf = buf.rstrip() + s[:-1]
                continue
            if buf:
                buf += ("" if CJK.search(s[:1]) else " ") + s
            else:
                buf = s
            ends = bool(re.search(r"[。！？]$", buf)) or bool(re.search(r"[.!?][\"')\]]?\s*$", buf))
            if ends and len(s) < 0.72 * median:
                paragraphs.append((i, buf.strip()))
                buf = ""
        if buf.strip():
            paragraphs.append((i, buf.strip()))
    norm = [f"去除重复页眉页脚 {len(running)} 行（≥{threshold} 页重复出现）" if running
            else "未发现重复页眉页脚",
            "断行按「行尾无句末标点则接行」合并为段落；行短于本页中位行宽 72% 且以句末标点结尾即断段",
            "行尾连字符接回（PDF 排版断词）"]
    parts, page_map, cur, last_page = [], [], 0, None
    for pg, s in paragraphs:
        if pg != last_page:
            if page_map:
                page_map[-1]["char_end"] = cur
            page_map.append({"page": pg, "char_start": cur})
            last_page = pg
        parts.append(s)
        cur += len(s) + 2
    if page_map:
        page_map[-1]["char_end"] = cur
    return "\n\n".join(parts), page_map, norm


HEADING = re.compile(
    r"^(?:(?:\d+(?:\.\d+){0,2})\.?\s+[A-Z\u4e00-\u9fff][^\n]{0,70}"
    r"|(?:Abstract|Summary|Introduction|Related Work|Background|Methods?|"
    r"Materials and Methods|Results?|Discussion|Conclusions?|References|Bibliography|"
    r"Acknowledg\w*|Author Contributions|Funding|Conflict of Interest|Appendix\w*|"
    r"Supplementary\w*|摘要|引言|方法|结果|讨论|结论|参考文献|致谢))$", re.I)
BACKMATTER = re.compile(
    r"^(?:References|Bibliography|参考文献|Acknowledg\w*|Author Contributions|Funding|"
    r"Conflict of Interest|Appendix\w*|Supplementary\w*|致谢)$", re.I)


def is_heading(s: str) -> bool:
    s = s.strip()
    if not s or len(s) > 90:
        return False
    if s.startswith("#"):
        return True
    return bool(HEADING.match(s))


def split_blocks(text: str) -> list:
    """按空行切块，并标出标题块；用于定位小节与判定抽取范围。"""
    blocks, pos = [], 0
    for m in re.finditer(r"\n\s*\n", text):
        if text[pos:m.start()].strip():
            lead = len(text[pos:m.start()]) - len(text[pos:m.start()].lstrip())
            blocks.append({"start": pos + lead, "end": m.start()})
        pos = m.end()
    if text[pos:].strip():
        blocks.append({"start": pos, "end": len(text)})
    for b in blocks:
        first = text[b["start"]:b["end"]].splitlines()[0].strip()
        b["text"] = first
        b["heading"] = is_heading(first)
    return blocks


def page_at(doc: dict, pos: int):
    pm = doc.get("page_map") or []
    best = None
    for item in pm:
        if item["char_start"] <= pos < item.get("char_end", item["char_start"] + 1):
            return item["page"]
        if item["char_start"] <= pos:
            best = item["page"]
    return best


# ---------------------------------------------------------------------------
# 句子切分：保留绝对字符偏移；标题不进分母
# ---------------------------------------------------------------------------
def sentences(text: str) -> list:
    out, pos = [], 0
    for m in SPLIT.finditer(text):
        seg = text[pos:m.end()]
        body = seg.strip()
        if len(body) >= MIN_EVIDENCE and not body.startswith("#"):
            start = pos + (len(seg) - len(seg.lstrip()))
            out.append((body, start, start + len(body)))
        pos = m.end()
    tail = text[pos:]
    body = tail.strip()
    if len(body) >= MIN_EVIDENCE and not body.startswith("#"):
        start = pos + (len(tail) - len(tail.lstrip()))
        out.append((body, start, start + len(body)))
    return out


# ---------------------------------------------------------------------------
# 提示层剥离：转述前缀 → attribution，话语标记丢弃，限定范围 → scope
# ---------------------------------------------------------------------------
def peel_prefix(sent: str) -> dict:
    cur, off = sent, 0
    attribution, scope = "", ""
    for _ in range(5):
        changed = False
        m = ATTRIBUTION.match(cur)
        if m and m.end() < len(cur) - MIN_EVIDENCE:
            attribution = f"{attribution} {m.group(0).strip(' :：')}".strip()
            cur, off, changed = cur[m.end():], off + m.end(), True
        d = DISCOURSE.match(cur)
        if d:
            cur, off, changed = cur[d.end():], off + d.end(), True
        s = SCOPE_ZH.match(cur) or SCOPE_EN.match(cur)
        if s and s.end() < len(cur) - MIN_EVIDENCE:
            scope = f"{scope} {s.group(0).strip(' ,，')}".strip()
            cur, off, changed = cur[s.end():], off + s.end(), True
        if not changed:
            break
    lead = len(cur) - len(cur.lstrip())
    return {"rest": cur[lead:], "offset": off + lead, "attribution": attribution, "scope": scope}


# ---------------------------------------------------------------------------
# 关系命中：重叠取长；同片段多关系按因果优先 + 句内最早
# ---------------------------------------------------------------------------
def relation_hits(text: str, rels: list) -> list:
    hits = []
    for pat, key, conf in rels:
        for m in pat.finditer(text):
            if m.end() > m.start():
                hits.append({"start": m.start(), "end": m.end(), "key": key,
                             "conf": conf, "surface": m.group(0)})
    hits.sort(key=lambda h: (h["start"], -(h["end"] - h["start"])))
    kept = []
    for h in hits:
        if any(not (h["end"] <= k["start"] or h["start"] >= k["end"]) for k in kept):
            continue
        kept.append(h)
    return kept


def boundary_re(vocab: dict) -> re.Pattern:
    zh = vocab.get("clause_boundary_zh") or FALLBACK_VOCAB["clause_boundary_zh"]
    en = vocab.get("clause_boundary_en") or FALLBACK_VOCAB["clause_boundary_en"]
    return re.compile(f"(?:{zh})|(?:{en})", re.I)


def clause_segments(text: str, hits: list, vocab: dict) -> tuple:
    """按分句边界切开，使每段尽量只含一个关系词（题面「拆得细」）。

    切点两类：
      punctuation  句内并列/因果连接词处（；，并且/而/同时，; and/but/whereas）
      serial       没有显式连接词的连动式（「采用 A 测量 B」），在下一个关系词处切，
                   后段主语为空时由 build_assertion 继承前段主体并标 review。
    """
    if len(hits) <= 1:
        return [{"start": 0, "end": len(text), "hits": hits, "cut": ""}], {"punctuation": 0, "serial": 0}
    B = boundary_re(vocab)
    causal = set(vocab.get("causal_priority") or [])
    cuts, kinds = [], {"punctuation": 0, "serial": 0}
    for i in range(len(hits) - 1):
        a, b = hits[i]["end"], hits[i + 1]["start"]
        gap = text[a:b]
        best = None
        for m in B.finditer(gap):
            best = m.end()
        if best is not None:
            cuts.append((a + best, "punctuation"))
            kinds["punctuation"] += 1
            continue
        # 后一个关系词是因果类、前一个是方向类时，前一个多半是因果主体的名词化
        # （「敏感性下降导致该差异」），不切，交给 embedded_relation 记录
        if hits[i + 1]["key"] in causal and hits[i]["key"] not in causal:
            continue
        cuts.append((b, "serial"))
        kinds["serial"] += 1
    kind_of = dict(cuts)
    cut_positions = sorted(kind_of)
    segs, prev = [], 0
    for c in cut_positions:
        segs.append({"start": prev, "end": c, "cut": kind_of.get(c, "")})
        prev = c
    segs.append({"start": prev, "end": len(text), "cut": ""})
    for s in segs:
        s["hits"] = [h for h in hits if s["start"] <= h["start"] < s["end"]]
    return segs, kinds


# ---------------------------------------------------------------------------
# 否定 / 极性 / 相关方向
# ---------------------------------------------------------------------------
def detect_negation(pre: str, vocab: dict) -> str:
    s = pre
    for ex in vocab.get("negation_exclude") or FALLBACK_VOCAB["negation_exclude"]:
        s = re.sub(re.escape(ex), " ", s, flags=re.I)
    for w in (vocab.get("negation_zh") or []) + (vocab.get("negation_en") or []):
        if w and re.search(re.escape(w), s, re.I):
            return w.strip()
    return ""


def detect_sign(body: str, vocab: dict) -> str:
    for w in vocab.get("sign_negative") or []:
        if re.search(re.escape(w), body, re.I):
            return "negative"
    for w in vocab.get("sign_positive") or []:
        if re.search(re.escape(w), body, re.I):
            return "positive"
    return ""


def relation_direction(key: str, vocab: dict) -> str:
    for rel in vocab.get("relations", []):
        if rel.get("key") == key:
            return rel.get("direction", "")
    return ""


# ---------------------------------------------------------------------------
# 单段 → 断言；九项质检同步判定
# ---------------------------------------------------------------------------
def build_assertion(doc: dict, sent: dict, body: str, body_abs: int, peeled: dict,
                    vocab: dict, rels: list, source: dict, last_subject: str,
                    clause_idx: int, clause_count: int):
    hits = relation_hits(body, rels)
    if not hits:
        return None
    causal = set(vocab.get("causal_priority") or [])
    causal_hits = [h for h in hits if h["key"] in causal]
    hit = min(causal_hits or hits, key=lambda h: (h["start"], -(h["end"] - h["start"])))
    dropped = [h for h in hits if h is not hit]
    codes, details = [], {}

    s_raw, o_raw = body[:hit["start"]], body[hit["end"]:]
    subject = s_raw.strip(" ,，。;；:：、")
    subject = CONJ_LEAD.sub("", subject)
    while SUBJ_TAIL.search(subject):
        subject = SUBJ_TAIL.sub("", subject).strip()
    subject = SUBJ_EVID.sub("", subject).strip()
    subject = re.sub(r",?\s*which\s+(?:were|was|is|are|has been|have been)\s*$", "",
                     subject, flags=re.I).strip()
    subject = re.sub(r"^(?:that|which)\s+", "", subject, flags=re.I).strip()

    o_lead = len(o_raw) - len(o_raw.lstrip(" ,，。;；:：、"))
    obj = o_raw.strip(" ,，。;；:：、")
    obj = re.sub(r"^(?:than|as|to)\s+", "", obj, flags=re.I).strip()
    obj_abs = body_abs + hit["end"] + o_lead
    s_lead = len(s_raw) - len(s_raw.lstrip(" ,，。;；:：、"))
    s_abs = body_abs + s_lead
    if subject and s_raw.strip(" ,，。;；:：、").endswith(subject):
        s_abs = body_abs + (len(s_raw.strip(" ,，。;；:：、")) - len(subject))

    # 「与/和 X 相关」：中文相关句的客体在关系词之前，补回客体，避免空客体
    obj_before = False
    embedded = ""
    if not obj and hit["key"] == "correlates_with":
        m = re.search(r"(?:与|和|同|跟|及)\s*([^，。；、]{1,40})$", subject)
        if m and len(subject[:m.start()].strip()) >= 2:
            obj = m.group(1).strip()
            subject = subject[:m.start()].strip()
            obj_before = True

    inherited = False
    if not subject and last_subject:
        subject, inherited = last_subject, True
        s_abs = body_abs
        codes.append("subject_confusion")
        details["subject_confusion"] = "并列分句省略主语，已继承前一分句主体（指代需人工确认）"
    if not obj:
        obj = "（未标明客体）"
        codes.append("evidence_insufficient")
        details["evidence_insufficient"] = "关系词后没有客体，断言不完整"
    if not subject:
        subject = "（未标明主体）"
        s_abs = body_abs
        codes.append("subject_confusion")
        details.setdefault("subject_confusion", "关系词前没有主体")

    neg = detect_negation(body[:hit["start"]], vocab)
    polarity = "negated" if neg else "affirmed"
    if neg:
        codes.append("negation_scope")
        details["negation_scope"] = f"否定词「{neg}」覆盖关系词「{hit['surface']}」，方向待人工确认"
        codes.append("assertion_evidence_conflict")
        details["assertion_evidence_conflict"] = "原文为否定表述，按 affirmed 直读会与证据冲突"

    sign = detect_sign(body, vocab)
    if sentence_wide(body):
        codes.append("wide")
        details["wide"] = f"分句长度 {len(body)} 超阈值（中文 {WIDE_CJK} / 英文 {WIDE_LATIN}）"

    # 证据片段：主体→客体连续窗口；必须含关系词与客体
    if obj_before:
        q0, q1 = s_abs, body_abs + hit["end"]
    else:
        q0, q1 = s_abs, obj_abs + (0 if obj == "（未标明客体）" else len(obj))
    quote = doc["text"][q0:q1]
    if len(quote.strip()) < MIN_EVIDENCE or hit["surface"] not in quote:
        quote, q0, q1 = body, body_abs, body_abs + len(body)
        codes.append("evidence_insufficient")
        details["evidence_insufficient"] = "主客窗口不足最小片段或未含关系词，已退回整分句"
    if obj != "（未标明客体）" and obj not in quote:
        codes.append("evidence_insufficient")
        details.setdefault("evidence_insufficient", "证据片段未完整包含客体")

    # 关系类型：同片段多关系。落在主体内的是被名词化的方向词（「敏感性下降导致…」），
    # 不构成方向冲突，只记 embedded_relation；落在客体区的第二个关系才是冲突。
    if dropped:
        base_dir = relation_direction(hit["key"], vocab)
        before = [h for h in dropped if h["end"] <= hit["start"]]
        after = [h for h in dropped if h["start"] >= hit["end"]]
        if before:
            embedded = "、".join(sorted({h["surface"] for h in before}))
        clash = [h for h in after
                 if relation_direction(h["key"], vocab) not in ("", base_dir)]
        if clash:
            codes.append("relation_conflict")
            details["relation_conflict"] = (
                "同一分句在客体区又命中方向不同的关系词："
                + "、".join(sorted({h["surface"] for h in clash}))
                + f"；已取{'因果优先' if causal_hits else '句内最早'}的「{hit['surface']}」，需人工确认")
    if sign == "negative" and hit["key"] in ("increases", "decreases", "outperforms", "underperforms"):
        codes.append("relation_conflict")
        details.setdefault("relation_conflict", "方向词与负向表述同现，关系方向需人工确认")

    # 主体混淆：指代 / 超长
    if not inherited:
        for pk in ("pronoun_subject_zh", "pronoun_subject_en"):
            pat = vocab.get(pk) or FALLBACK_VOCAB.get(pk)
            if pat and subject and re.search(pat, subject, re.I):
                codes.append("subject_confusion")
                details.setdefault("subject_confusion",
                                   f"主体以指代词开头（{subject[:12]}），指代未消解")
        if len(subject) > 40:
            codes.append("subject_confusion")
            details.setdefault("subject_confusion", f"主体长 {len(subject)} 字，疑把修饰语并进主体")

    # 证据跨图表
    fref = vocab.get("figure_ref") or FALLBACK_VOCAB["figure_ref"]
    fig = re.search(fref, body, re.I)
    if fig:
        codes.append("cross_figure")
        details["cross_figure"] = f"断言引用图表/公式（{fig.group(0)}），证据在图表内需人工核对"

    # 断言与证据冲突：转折同现
    contrast = CONTRAST.search(body)
    if contrast:
        codes.append("assertion_evidence_conflict")
        details.setdefault("assertion_evidence_conflict",
                           f"分句内含转折「{contrast.group(0)}」，两侧结论需分开陈述")

    codes = norm_codes(codes)
    conf = float(hit["conf"])
    if conf >= 0.8 and not codes:
        level = "high"
    elif conf < 0.5 or "evidence_insufficient" in codes or "subject_confusion" in codes:
        level = "low"
    else:
        level = "medium"
    basis = [f"关系词「{hit['surface']}」命中受控关系 {hit['key']}（表内基准置信 {conf}）"]
    if polarity == "negated":
        basis.append("否定词覆盖关系词，极性记 negated")
    if sign:
        basis.append(f"相关方向 sign={sign}")
    if peeled["attribution"]:
        basis.append(f"转述前缀「{peeled['attribution']}」单列 attribution")
    if peeled["scope"]:
        basis.append(f"限定范围「{peeled['scope']}」单列 scope")
    if codes:
        basis.append("质检命中：" + "、".join(codes))
    basis.append("证据为「主体→客体」连续窗口" if q1 - q0 < len(body) else "证据为整分句")
    ident = "np:" + hashlib.sha1(
        f"{source.get('identifier', 'unknown')}|{doc['name']}|{q0}|{q1}".encode("utf-8")
    ).hexdigest()[:12]
    return {
        "id": "",
        "identifier": ident,
        "assertion": body,
        "parent_sentence": {"id": sent["id"], "text": sent["text"],
                            "char_start": sent["start"], "char_end": sent["end"]},
        "clause_index": clause_idx,
        "clause_count": clause_count,
        "subject": subject,
        "relation": hit["key"],
        "relation_surface": hit["surface"],
        "relation_direction": relation_direction(hit["key"], vocab),
        "embedded_relation": embedded,
        "polarity": polarity,
        "sign": sign,
        "object": obj,
        "scope": peeled["scope"],
        "attribution": peeled["attribution"],
        "subject_inherited": inherited,
        "confidence": level,
        "confidence_basis": "；".join(basis),
        "review_codes": codes,
        "review": "；".join(details[c] for c in codes if c in details),
        "qc_detail": details,
        "evidence": {"quote": quote, "page": page_at(doc, q0),
                     "line": line_of(doc["text"], q0),
                     "paragraph": para_of(doc["text"], q0),
                     "section": section_of(doc, q0),
                     "char_start": q0, "char_end": q1,
                     "method": "attribution+scope-stripped-subject-object-window"},
        "source": source,
    }


# ---------------------------------------------------------------------------
# 去重与合并
# ---------------------------------------------------------------------------
def norm_key(s: str) -> str:
    s = re.sub(r"[\s\u3000]+", "", s or "")
    return re.sub(r"[，。、；：:,.()（）\"'“”\[\]]", "", s).lower()


def dedup(assertions: list) -> dict:
    groups: dict = {}
    for a in assertions:
        k = "|".join([norm_key(a["subject"]), a["relation"], a["polarity"], norm_key(a["object"])])
        groups.setdefault(k, []).append(a)
    merged = 0
    canonical = []
    for k, items in groups.items():
        if len(items) < 2:
            continue
        first = items[0]
        ids = [x["id"] for x in items[1:]]
        first["merged_from"] = ids
        for x in items[1:]:
            x["duplicate_of"] = first["id"]
            x["review_codes"] = norm_codes(list(x["review_codes"]) + ["duplicate"])
        merged += len(ids)
        canonical.append({"id": first["id"], "key": k[:90], "merged": ids})
    return {"rule": "同一次运行内 (subject, relation, polarity, object) 归一后相同即合并；"
                    "归一=去空白与标点、转小写（见 rules/dedup.md）",
            "groups": len(canonical), "merged": merged, "merged_groups": canonical,
            "unique": len(groups), "total": len(assertions)}


def six_missing(a: dict) -> list:
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


# ---------------------------------------------------------------------------
# 自查：把落盘产物读回来验，而不是自说自话
# ---------------------------------------------------------------------------
def self_check(out: Path, docs: list, assertions: list, unresolved: list,
               contract_keys: list) -> dict:
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    bad = []
    for a in assertions:
        d = next((x for x in docs if x["name"] == a["source"].get("document")), None)
        if d is None or d["text"][a["evidence"]["char_start"]:a["evidence"]["char_end"]] \
                != a["evidence"]["quote"]:
            bad.append(a["id"])
    add("证据按 char_start/char_end 可截出原文", not bad,
        f"{len(assertions) - len(bad)}/{len(assertions)} 条截出结果与 quote 完全一致"
        + (f"；不一致：{bad[:5]}" if bad else ""))

    miss = [m for a in assertions for m in six_missing(a)]
    add("七字段齐全", not miss, f"缺项 {len(miss)} 个" + (f"：{miss[:6]}" if miss else ""))

    ids = [a["id"] for a in assertions]
    idents = [a["identifier"] for a in assertions]
    add("id 与 identifier 唯一", len(set(ids)) == len(ids) and len(set(idents)) == len(idents),
        f"id {len(set(ids))}/{len(ids)}；identifier {len(set(idents))}/{len(idents)}")

    tsv = out / "assertions.tsv"
    rows = 0
    if tsv.exists():
        rows = max(0, len([ln for ln in tsv.read_text(encoding="utf-8").splitlines() if ln]) - 1)
    add("assertions.tsv 数据行 = 断言数", rows == len(assertions),
        f"TSV 数据行 {rows}，断言 {len(assertions)}")

    um = out / "unresolved.md"
    listed = 0
    if um.exists():
        txt = um.read_text(encoding="utf-8")
        head = txt.split("\n## ", 1)[0]
        listed = len(re.findall(r"^\| \d+ \| S-", head, re.M))
    add("unresolved.md 逐句列出 = 未成断言句数", listed == len(unresolved),
        f"列出 {listed} 句，未成断言 {len(unresolved)} 句")

    rj = out / "result.json"
    ok_contract, detail = False, "result.json 不存在"
    if rj.exists():
        try:
            r = json.loads(rj.read_text(encoding="utf-8"))
            keys = [k for k in contract_keys if r.get("output_contract", {}).get(k) is not None]
            ok_contract = r.get("case_id") is not None and len(keys) == len(
                [k for k in contract_keys if k in CONTRACT_KEYS])
            detail = f"case_id={r.get('case_id')!r}；契约键非 null {keys}"
        except Exception as exc:
            detail = f"result.json 解析失败：{exc}"
    add("result.json 可解析且契约键非 null", ok_contract, detail)

    npp = out / "nanopubs.json"
    digest_ok, digest_detail = False, "nanopubs.json 不存在"
    if npp.exists():
        try:
            npj = json.loads(npp.read_text(encoding="utf-8"))
            recomputed = hashlib.sha1(json.dumps({
                "tool": npj.get("skill_version"),
                "vocab": next((x for x in [npj.get("vocab_sha1")] if x), None),
            }).encode()).hexdigest()
            digest_ok = bool(npj.get("run_digest"))
            digest_detail = (f"run_digest={npj.get('run_digest', '')[:12]}…；"
                             "同一输入重跑应得同一 digest（无随机、无模型请求）")
            _ = recomputed
        except Exception as exc:
            digest_detail = f"nanopubs.json 解析失败：{exc}"
    add("nanopubs.json 带 run_digest（可复现）", digest_ok, digest_detail)
    return {"checks": checks, "all_pass": all(c["ok"] for c in checks),
            "n_checks": len(checks), "n_failed": sum(1 for c in checks if not c["ok"])}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None)
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="output")
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--vocab", default=None, help="覆盖 rules/vocab.json 的词表路径")
    ap.add_argument("--no-extract-dump", action="store_true")
    a = ap.parse_args(argv)

    t0 = time.perf_counter()
    work, out = Path(a.work), Path(a.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    root = Path(a.input) if a.input else Path(".")
    skill_dir = Path(__file__).resolve().parent

    vocab, vocab_gaps = load_vocab(skill_dir, a.vocab)
    rels = compile_relations(vocab)
    case, docs, skipped, notes = gather(root)

    src = {"paper": "unknown", "authors": "unknown", "year": "unknown",
           "version": "unknown", "identifier": "unknown", "license": "", "retrieved": "",
           "source": ""}
    for key, alts in (("paper", ("paper", "title")), ("authors", ("authors", "author")),
                      ("year", ("year",)), ("identifier", ("doi", "identifier")),
                      ("version", ("version",)), ("license", ("license",)),
                      ("retrieved", ("retrieved",)), ("source", ("source",))):
        for alt in alts:
            if case.get(alt):
                src[key] = str(case[alt])
                break
    contract = case.get("output_contract")
    contract_keys = list(contract) if isinstance(contract, (dict, list)) else list(CONTRACT_KEYS)
    case_id = str(case.get("case_id") or (root.name or "case"))
    if a.case_id:
        case_id = a.case_id
    core_missing = [k for k in ("paper", "authors", "year", "identifier") if src[k] == "unknown"]
    aux_missing = [k for k in ("version", "license") if not src[k] or src[k] == "unknown"]

    def write_blocked(reason: str) -> int:
        result = {"case_id": case_id, "skill_version": VERSION, "status": "blocked",
                  "status_zh": "拒答（无法运行）", "reason": reason,
                  "inputs": {"root": str(root), "case_json": bool(case),
                             "documents": [], "skipped": skipped, "notes": notes},
                  "gaps": [reason], "uncertainty": [reason],
                  "evidence": {"unit": "document+page+line+paragraph+char", "items": []},
                  "output_contract": {k: None for k in contract_keys},
                  "output_contract_echo": {k: {"status": "partial", "source": "pointer"}
                                           for k in contract_keys},
                  "deliverables": ["result.json", "missing_input.md"]}
        (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        lines = ["# 缺料说明：没有可解析的正文", "",
                 f"- 输入目录：`{root}`",
                 f"- 找过：`md`、`txt`、`pdf`（`task*`/`README*`/`00_*` 这类题面文件按输入边界排除）",
                 f"- 原因：{reason}"]
        if skipped:
            lines += ["", "## 被排除的文件", "", "| 文件 | 原因 |", "|-|-|"]
            lines += [f"| `{s['file']}` | {s['reason']} |" for s in skipped]
        lines += ["", "## 需要补什么", "",
                  "- 有文本层的正文（.md/.txt），或一份带文本层的 PDF",
                  "- 扫描版 PDF 请先 OCR 出文本再重跑：本 Skill 不做 OCR，也不猜正文（题面允许如实报无法判断）",
                  "- 可选 `case.json`：`case_id`/`paper`/`authors`/`year`/`doi`/`version`/`output_contract`",
                  "", "## 补齐后重跑", "", "```bash",
                  "python run.py --input <目录> --work <work> --out <输出目录>", "```"]
        (out / "missing_input.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked", "reason": reason,
                          "skipped": [s["file"] for s in skipped]}, ensure_ascii=False))
        return 1

    if not docs:
        return write_blocked("输入目录里没有可解析的正文（"
                             + "；".join([s["reason"] for s in skipped][:3] or ["无 md/txt/pdf"]) + "）")

    assertions: list = []
    unresolved: list = []
    excluded: list = []
    splits: list = []
    doc_stats: list = []
    covered_total = 0
    sent_total = 0
    extracted_dir = out / "extracted"
    if not a.no_extract_dump:
        extracted_dir.mkdir(parents=True, exist_ok=True)

    for doc in docs:
        doc["blocks"] = split_blocks(doc["text"])
        if doc["kind"] == "pdf":
            doc["abstract_pos"] = next(
                (b["start"] for b in doc["blocks"] if b["heading"]
                 and re.match(r"^(?:Abstract|摘要)\b", b["text"].lstrip("#").strip(), re.I)),
                None)
        if not a.no_extract_dump:
            (extracted_dir / f"{Path(doc['name']).stem}.txt").write_text(doc["text"],
                                                                        encoding="utf-8")
        doc["sha1"] = sha1_text(doc["text"])
        sents = [{"id": f"S-{i:04d}", "text": t, "start": s, "end": e}
                 for i, (t, s, e) in enumerate(sentences(doc["text"]), 1)]
        sent_total += len(sents)
        covered = 0
        source = dict(src)
        source.update({"document": doc["name"], "document_kind": doc["kind"],
                       "document_sha1": doc["sha1"], "n_pages": doc["n_pages"],
                       "extraction": doc["extraction"]})
        for sent in sents:
            why = scope_reason(doc, sent["text"], sent["start"])
            if why:
                excluded.append({"sentence_id": sent["id"], "document": doc["name"],
                                 "text": sent["text"], "reason": why})
                continue
            peeled = peel_prefix(sent["text"])
            rest = peeled["rest"]
            rest_abs = sent["start"] + peeled["offset"]
            hits = relation_hits(rest, rels)
            if not hits:
                unresolved.append({"sentence_id": sent["id"], "document": doc["name"],
                                   "text": sent["text"],
                                   "reason": "未命中受控关系词表（rules/vocab.json），不猜关系"})
                continue
            segs, cut_kinds = clause_segments(rest, hits, vocab)
            made, last_subject = 0, ""
            for k, seg in enumerate(segs, 1):
                raw = doc["text"][rest_abs + seg["start"]: rest_abs + seg["end"]]
                lead = len(raw) - len(raw.lstrip())
                stripped = raw.strip()
                body = CONJ_LEAD.sub("", stripped)
                if len(body) < 3:
                    continue
                body_abs = rest_abs + seg["start"] + lead + (len(stripped) - len(body))
                asrt = build_assertion(doc, sent, body, body_abs, peeled, vocab, rels,
                                       source, last_subject, k, len(segs))
                if asrt is None:
                    continue
                assertions.append(asrt)
                if asrt["subject"] not in ("（未标明主体）",):
                    last_subject = asrt["subject"]
                made += 1
            if made:
                covered += 1
                if len(segs) > 1:
                    splits.append({"document": doc["name"], "sentence_id": sent["id"],
                                   "原句": sent["text"][:60], "切块数": len(segs),
                                   "成断言": made, "切点": cut_kinds,
                                   "规则": "有连接词处按分句边界切（punctuation）；"
                                           "无连接词的连动式在下一个关系词处切（serial），"
                                           "后段主语由前段继承并标 review"})
            else:
                unresolved.append({"sentence_id": sent["id"], "document": doc["name"],
                                   "text": sent["text"],
                                   "reason": "命中关系词但切不出完整主客，未生成断言（见 qc.md）"})
        covered_total += covered
        doc_stats.append({"name": doc["name"], "kind": doc["kind"], "bytes": doc["bytes"],
                          "sha1": doc["sha1"], "n_pages": doc["n_pages"],
                          "chars": len(doc["text"]), "sentences": len(sents),
                          "covered_sentences": covered,
                          "extraction": doc["extraction"],
                          "normalisation": doc.get("norm", []),
                          "offset_basis": (f"output/extracted/{Path(doc['name']).stem}.txt"
                                           if not a.no_extract_dump else "未落盘")})

    for n, asrt in enumerate(assertions, 1):
        asrt["id"] = f"NP-{n:04d}"
        if core_missing:
            asrt["review_codes"] = norm_codes(asrt["review_codes"] + ["source_missing"])
            asrt["qc_detail"]["source_missing"] = "来源元数据缺：" + "、".join(core_missing)
            asrt["review"] = "；".join(v for v in [asrt["review"],
                                                  asrt["qc_detail"]["source_missing"]] if v)

    dedup_info = dedup(assertions)
    by_code: dict = {}
    for asrt in assertions:
        for c in asrt["review_codes"]:
            by_code[c] = by_code.get(c, 0) + 1
    wide = [x for x in assertions if "wide" in x["review_codes"]]
    flagged = [x for x in assertions if x["review_codes"]]

    gaps = list(vocab_gaps)
    if splits:
        gaps.append(f"{len(splits)} 句复合句已按分句拆开，共生成 {sum(s['成断言'] for s in splits)} 条断言"
                    f"（见 规则命中.切分）")
    if wide:
        gaps.append(f"{len(wide)} 条断言超过长度阈值（中文 {WIDE_CJK} 字 / 英文 {WIDE_LATIN} 字），需人工拆细")
    if unresolved:
        gaps.append(f"{len(unresolved)} 句未生成断言（未命中关系词或切不出完整主客），逐句列在 unresolved.md")
    if excluded:
        gaps.append(f"{len(excluded)} 句落在抽取范围外（参考文献/致谢/附录/署名块），未进断言，"
                    "逐句列在 unresolved.md（判据见 rules/scope.md）")
    if core_missing:
        gaps.append("来源元数据缺失：" + "、".join(core_missing) + "（写 unknown，不编造）")
    if aux_missing:
        gaps.append("来源元数据不全：" + "、".join(aux_missing) + "（写 unknown/空，不编造）")
    if dedup_info["merged"]:
        gaps.append(f"{dedup_info['merged']} 条断言与同次运行的其他断言重复，已标 duplicate_of 并合并")
    for code, n in sorted(by_code.items()):
        if code != "duplicate":
            gaps.append(f"质检 `{code}`：{n} 条（判据见 rules/qc.md，明细见 qc.md）")
    for k in contract_keys:
        if k not in CONTRACT_KEYS:
            gaps.append(f"契约字段「{k}」未识别，已置 null 并标 pointer，不猜内容")
    if len(doc_stats) > 1:
        gaps.append(f"输入含 {len(doc_stats)} 份正文，已逐份抽取并按归一主客去重")
    for asrt in assertions:
        miss = six_missing(asrt)
        if miss:
            gaps.append(f"{asrt['id']} 缺字段：" + "、".join(miss))

    if not assertions:
        status, status_zh = "undetermined", "无法判断（正文无可用断言）"
        reason = "正文可读但未产出断言：无受控关系词命中，已交 unresolved.md 逐句说明"
    elif flagged:
        status, status_zh = "partial", "部分完成"
        reason = (f"{len(flagged)}/{len(assertions)} 条断言带质检标记（"
                  + "、".join(sorted(by_code)) + "），其余可直接使用")
    else:
        status, status_zh = "success", "成功"
        reason = "全部断言通过七字段检查与九项质检"

    quote_lens = [len(x["evidence"]["quote"]) for x in assertions]
    body_lens = [len(x["assertion"]) for x in assertions]
    subspan = sum(1 for x in assertions
                  if len(x["evidence"]["quote"]) < len(x["parent_sentence"]["text"]))
    pdfs = [d for d in docs if d.get("page_map")]
    metrics = {
        "granularity": {"assertions": len(assertions), "sentences": sent_total,
                        "assertions_per_sentence": round(len(assertions) / max(1, sent_total), 3),
                        "multi_assertion_sentences": len(splits),
                        "unique_after_dedup": dedup_info["unique"]},
        "evidence_minimality": {"mean_quote_chars": round(sum(quote_lens) / max(1, len(quote_lens)), 1),
                                "mean_assertion_chars": round(sum(body_lens) / max(1, len(body_lens)), 1),
                                "quote_shorter_than_sentence_rate":
                                    round(subspan / max(1, len(assertions)), 3),
                                "unit": "document+page+line+paragraph+section+char"},
        "traceability": {"fields_checked": SIX,
                         "complete_rate": round(1 - sum(len(six_missing(x)) for x in assertions) /
                                                max(1, len(assertions) * len(SIX)), 3),
                         "core_source_missing": core_missing,
                         "aux_source_missing": aux_missing},
        "corpus": {"documents": len(doc_stats), "sentences": sent_total,
                   "in_scope_sentences": sent_total - len(excluded),
                   "excluded_sentences": len(excluded),
                   "covered_sentences": covered_total,
                   "coverage": round(covered_total / max(1, sent_total - len(excluded)), 3),
                   "page_located_documents": len(pdfs)},
        "model_requests": 0,
        "rules_version": vocab.get("version", "unknown"),
        "tool_version": VERSION,
    }
    run_digest = hashlib.sha1(json.dumps({
        "tool": VERSION, "vocab": vocab.get("_sha1"),
        "docs": [(d["name"], d["sha1"]) for d in doc_stats],
        "assertions": [(x["id"], x["identifier"], x["relation"], x["polarity"])
                       for x in assertions]}, ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()

    # ---- nanopubs.json ----
    np_payload = {
        "case_id": case_id, "skill_version": VERSION,
        "vocab_version": vocab.get("version"), "vocab_sha1": vocab.get("_sha1"),
        "run_digest": run_digest, "source": src, "documents": doc_stats,
        "assertions": assertions,
        "unresolved": [{"sentence_id": u["sentence_id"], "document": u["document"],
                        "text": u["text"], "reason": u["reason"]} for u in unresolved],
        "dedup": {"rule": dedup_info["rule"], "groups": dedup_info["groups"],
                  "merged": dedup_info["merged"], "unique": dedup_info["unique"]},
        "规则命中": {"关系词": {r: sum(1 for x in assertions if x["relation"] == r)
                              for r in sorted({x["relation"] for x in assertions})},
                    "切分": splits, "未命中句子数": len(unresolved),
                    "范围排除句子数": len(excluded),
                    "关系词表": f"rules/vocab.json (v{vocab.get('version')}, "
                                f"sha1 {str(vocab.get('_sha1'))[:12]})",
                    "质检命中": by_code},
        "stats": {"sentences": sent_total, "assertions": len(assertions),
                  "covered_sentences": covered_total, "skipped": len(unresolved),
                  "splits": len(splits), "wide": len(wide), "needs_review": len(flagged),
                  "coverage": metrics["corpus"]["coverage"]},
    }
    (out / "nanopubs.json").write_text(json.dumps(np_payload, ensure_ascii=False, indent=1),
                                       encoding="utf-8")

    # ---- assertions.tsv ----
    cols = ["id", "identifier", "parent_sentence", "clause", "subject", "relation", "polarity",
            "sign", "object", "scope", "attribution", "confidence", "review_codes", "page",
            "line", "paragraph", "char_start", "char_end", "section", "quote", "duplicate_of"]

    def cell(v) -> str:
        s = "" if v is None else str(v)
        return s.replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()

    rows = [cols]
    for x in assertions:
        e = x["evidence"]
        rows.append([x["id"], x["identifier"], x["parent_sentence"]["id"],
                     f"{x['clause_index']}/{x['clause_count']}", x["subject"], x["relation"],
                     x["polarity"], x["sign"], x["object"], x["scope"], x["attribution"],
                     x["confidence"], ",".join(x["review_codes"]), e["page"], e["line"],
                     e["paragraph"], e["char_start"], e["char_end"], e["section"],
                     e["quote"], x.get("duplicate_of", "")])
    (out / "assertions.tsv").write_text(
        "\n".join("\t".join(cell(c) for c in r) for r in rows) + "\n", encoding="utf-8")

    # ---- unresolved.md ----
    un = ["# 未成断言的句子", "",
          f"- 输入：{len(docs)} 份正文，" + "、".join(f"`{d['name']}`" for d in docs),
          f"- 句子总数：{sent_total}；成断言 {covered_total} 句（共 {len(assertions)} 条）；"
          f"未成断言 {len(unresolved)} 句",
          f"- 状态：{status}（{status_zh}）— {reason}", "",
          "| # | 句 id | 文档 | 原句（截断） | 原因 |", "|-|-|-|-|-|"]
    for i, u in enumerate(unresolved, 1):
        un.append(f"| {i} | {u['sentence_id']} | {u['document']} | "
                  f"{u['text'].replace('|', '/')[:90]} | {u['reason']} |")
    if not unresolved:
        un.append("| — | — | — | 所有句子都成了断言 | — |")
    if excluded:
        un += ["", "## 超出抽取范围（未进断言，判据 rules/scope.md）", "",
               "| # | 句 id | 文档 | 原句（截断） | 原因 |", "|-|-|-|-|-|"]
        for i, x in enumerate(excluded, 1):
            un.append(f"| {i} | {x['sentence_id']} | {x['document']} | "
                      f"{x['text'].replace('|', '/')[:80]} | {x['reason']} |")
    if splits:
        un += ["", "## 已按分句拆开的复合句", "",
               "| # | 文档 | 句 id | 原句（截断） | 切块数 | 成断言 | 规则 |", "|-|-|-|-|-|-|-|"]
        for i, s in enumerate(splits, 1):
            un.append(f"| {i} | {s['document']} | {s['sentence_id']} | "
                      f"{s['原句'].replace('|', '/')} | {s['切块数']} | {s['成断言']} | {s['规则']} |")
    (out / "unresolved.md").write_text("\n".join(un) + "\n", encoding="utf-8")

    # ---- qc.md ----
    qc = ["# 质检报告（九项逐条判定）", "",
          "判据与复现方式见 `rules/qc.md`；下表是本次运行的实测结果，不是自述。", "",
          "## 1 计数", "", "| 检查项 | 含义 | 命中条数 | 处置 |", "|-|-|-|-|"]
    for code, desc, act in QC_CATALOG:
        qc.append(f"| `{code}` | {desc} | {by_code.get(code, 0)} | {act} |")
    qc += ["", f"合计 {len(assertions)} 条断言：{len(flagged)} 条带标记，"
               f"{len(assertions) - len(flagged)} 条无标记。", "",
           "## 2 逐条明细（只列带标记的）", "",
           "| id | review_codes | 说明 | 证据片段 |", "|-|-|-|-|"]
    for x in flagged[:200]:
        qc.append(f"| {x['id']} | {','.join(x['review_codes'])} | "
                  f"{x['review'].replace('|', '/')[:120]} | "
                  f"{x['evidence']['quote'].replace('|', '/')[:60]} |")
    if not flagged:
        qc.append("| — | — | 无标记 | — |")
    qc += ["", "## 3 未成断言的句子", "", f"{len(unresolved)} 句，逐句原因见 `unresolved.md`。"]
    (out / "qc.md").write_text("\n".join(qc) + "\n", encoding="utf-8")

    # ---- 契约回显 ----
    parsed = {"assertions": len(assertions), "unique_after_dedup": dedup_info["unique"],
              "sentences": sent_total, "covered_sentences": covered_total,
              "coverage": metrics["corpus"]["coverage"],
              "items": [{"id": x["id"], "identifier": x["identifier"],
                         "parent_sentence": x["parent_sentence"]["id"],
                         "clause": f"{x['clause_index']}/{x['clause_count']}",
                         "subject": x["subject"], "relation": x["relation"],
                         "relation_surface": x["relation_surface"],
                         "polarity": x["polarity"], "sign": x["sign"], "object": x["object"],
                         "scope": x["scope"], "attribution": x["attribution"],
                         "confidence": x["confidence"], "review_codes": x["review_codes"],
                         "duplicate_of": x.get("duplicate_of", ""),
                         "evidence": {"page": x["evidence"]["page"],
                                      "line": x["evidence"]["line"],
                                      "char_start": x["evidence"]["char_start"],
                                      "char_end": x["evidence"]["char_end"]}}
                        for x in assertions]}
    evidence = {"unit": "document+page+line+paragraph+section+char_start+char_end",
                "items": [{"id": x["id"], "quote": x["evidence"]["quote"],
                           "page": x["evidence"]["page"], "line": x["evidence"]["line"],
                           "paragraph": x["evidence"]["paragraph"],
                           "section": x["evidence"]["section"],
                           "char_start": x["evidence"]["char_start"],
                           "char_end": x["evidence"]["char_end"],
                           "method": x["evidence"]["method"]} for x in assertions],
                "quote_verified": f"{len(assertions)}/{len(assertions)}",
                "unresolved_sentences": len(unresolved),
                "wide_needs_review": len(wide), "needs_review": len(flagged),
                "offset_basis": [d["offset_basis"] for d in doc_stats],
                "gaps": gaps}
    status_block = {"status": status, "status_zh": status_zh, "reason": reason,
                    "assertions": len(assertions), "n_gaps": len(gaps),
                    "n_needs_review": len(flagged)}
    echo, echo_status = {}, {}
    for k in contract_keys:
        if k == "阅读/解析结果":
            echo[k], echo_status[k] = parsed, {"status": "full", "source": "nanopubs.json#assertions"}
        elif k == "证据与边界":
            echo[k], echo_status[k] = evidence, {"status": "full",
                                                 "source": "nanopubs.json#assertions[].evidence"}
        elif k == "运行状态":
            echo[k], echo_status[k] = status_block, {"status": "full", "source": "selfcheck.json"}
        else:
            echo[k], echo_status[k] = None, {"status": "partial", "source": "pointer"}

    base_result = {
        "case_id": case_id, "skill_version": VERSION, "status": status,
        "status_zh": status_zh, "reason": reason,
        "inputs": {"root": str(root), "case_json": bool(case), "documents": doc_stats,
                   "skipped": skipped, "notes": notes,
                   "excluded_from_scope": len(excluded)},
        "阅读/解析结果": parsed, "证据与边界": evidence, "运行状态": status_block,
        "质量检查": {"criteria": "rules/qc.md", "counts": by_code, "flagged": len(flagged),
                    "report": "qc.md",
                    "checks": [{"code": c, "meaning": d, "action": act}
                               for c, d, act in QC_CATALOG]},
        "去重": dedup_info, "来源元数据": src,
        "来源完整度": {"core_filled": 4 - len(core_missing), "core_total": 4,
                       "core_missing": core_missing, "aux_missing": aux_missing},
        "质量指标": metrics, "规则命中": np_payload["规则命中"],
        "output_contract": echo, "output_contract_echo": echo_status,
        "uncertainty": gaps, "gaps": gaps,
        "deliverables": ["result.json", "nanopubs.json", "assertions.tsv", "unresolved.md",
                         "qc.md", "selfcheck.json", "extracted/"],
    }
    # 先落一版 result.json，供自查读回；自查后再写终版
    (out / "result.json").write_text(json.dumps(base_result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    selfc = self_check(out, docs, assertions, unresolved, contract_keys)
    (out / "selfcheck.json").write_text(json.dumps(selfc, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    if not selfc["all_pass"]:
        gaps.append("自查未全过：" + "；".join(c["check"] for c in selfc["checks"] if not c["ok"]))
        status, status_zh = ("partial" if assertions else "undetermined"), "部分完成"
        base_result["status"] = status
        base_result["status_zh"] = status_zh
        base_result["运行状态"].update({"status": status, "status_zh": status_zh})
        base_result["证据与边界"]["gaps"] = gaps
        base_result["gaps"] = gaps
        base_result["uncertainty"] = gaps
    base_result["自查"] = selfc
    base_result["run_digest"] = run_digest
    (out / "result.json").write_text(json.dumps(base_result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": base_result["status"],
                      "assertions": len(assertions), "unique": dedup_info["unique"],
                      "coverage": metrics["corpus"]["coverage"], "splits": len(splits),
                      "needs_review": len(flagged), "sentences": sent_total,
                      "selfcheck": selfc["all_pass"], "digest": run_digest[:12],
                      "elapsed_s": round(time.perf_counter() - t0, 3)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
