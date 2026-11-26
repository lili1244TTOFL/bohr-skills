#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pass1_prep.py — 为「第一次模型调用」准备输入：分节、分段、分句、编号、规模估算。

不使用任何模型。产出三样东西：
  prep.json        结构化底稿（段落/句子/偏移/所属章节），后续 renumber/insert 全靠它定位
  pass1_input.txt  紧凑的模型输入（P###:S###<TAB>句子）
  stdout 一行 JSON  统计与耗时（给 run.py 记录）

用法：
  python pass1_prep.py <paper.txt|paper.md> --out work/prep.json [--emit work/pass1_input.txt]
校验（规则层第 5 节，不花模型请求）：
  python pass1_prep.py --verify work/pass1_out.txt --prep work/prep.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- 编码兜底
def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


# ---------------------------------------------------------------- 章节识别
# 命中即归类；顺序敏感（先具体后笼统）。
SECTION_PATTERNS: list[tuple[str, str]] = [
    ("decl", r"(?i)\b(acknowledg(e)?ments?|funding|author contributions?|competing interests?|"
             r"conflicts? of interest|data availability|ethics (approval|statement)|"
             r"supplementary (material|information)|附录|致谢|基金|资助|作者贡献|利益冲突|"
             r"数据可得性|伦理|补充材料)"),
    ("abstract", r"(?i)^(abstract|summary|摘\s*要)\b"),
    ("keywords", r"(?i)\b(key\s*words?|关键词|关键字)\b"),
    ("refs", r"(?i)^(references?|bibliography|参考文献|引用文献)\b"),
    ("intro", r"(?i)\b(introduction|background|引\s*言|前\s*言|绪\s*论)\b"),
    ("related", r"(?i)\b(related work|literature review|state of the art|研究现状|相关工作|"
                r"文献综述|国内外研究)"),
    ("method", r"(?i)\b(methods?|materials and methods|methodology|experimental (setup|design)|"
               r"materials?|方\s*法|材料与方法|实验设计|研究设计|数据来源)"),
    ("results", r"(?i)\b(results?|findings|experiments?|evaluation|结\s*果|实验结果|实验与分析)"),
    ("discussion", r"(?i)\b(discussion|讨\s*论|分\s*析与讨论)"),
    ("conclusion", r"(?i)\b(conclusions?|summary and outlook|concluding remarks|结\s*论|结\s*语|"
                   r"总结与展望)"),
    ("limitations", r"(?i)\b(limitations?|future work|局\s*限|不\s*足|展\s*望)"),
    ("appendix", r"(?i)^(appendix|supporting information|附\s*录)"),
    ("title", r"(?i)^(title|题\s*目)"),
]

_HEAD_NUM = r"^(?:[0-9]{1,2}(?:\.[0-9]{1,2}){0,3}|[一二三四五六七八九十]{1,3}[、.．]?)\s*"
_HEAD_MD = re.compile(r"^#{1,4}\s+(?P<t>.+?)\s*#*\s*$")
_HEAD_PLAIN = re.compile(r"^" + _HEAD_NUM + r"(?P<t>[^\n]{2,40})$")


def classify_heading(text: str) -> str | None:
    """把一行标题文本映射到章节类型；不是标题返回 None。"""
    t = text.strip().strip("：: ")
    if not t:
        return None
    for kind, pat in SECTION_PATTERNS:
        if re.search(pat, t):
            return kind
    return None


# ---------------------------------------------------------------- 分句
# 中文句末标点 + 英文句末标点；英文需防缩写/小数/参考文献编号。
_ABBR = (
    "al|e\\.g|i\\.e|etc|vs|cf|fig|figs|tab|eq|eqs|no|nos|vol|pp|p|sec|ref|refs|"
    "approx|min|max|dr|prof|st|jr|sr|inc|ltd|co|resp|suppl|ca|ed|eds"
)
_SENT_ZH = "。！？…"
_SPLIT_RE = re.compile(
    r"(?<=[。！？])(?![”’」』）)]*[。！？])"            # 中文句末（分号不切：多用于并列项）
    r"|(?<=[.!?])\s+(?=[A-Z0-9\"“(（\[])"           # 英文句末：点后空白 + 大写/数字/引号
)


def _is_abbrev(tail: str) -> bool:
    m = re.search(r"(?i)([A-Za-z.]+)\.$", tail)
    if not m:
        return False
    tok = m.group(1).strip(".")
    if re.fullmatch(r"(?i)(" + _ABBR + r")", tok):
        return True
    return bool(re.fullmatch(r"[A-Z]", tok))  # 人名首字母缩写 A.


def split_sentences(par: str) -> list[str]:
    par = par.strip()
    if not par:
        return []
    parts, buf = [], ""
    pieces = _SPLIT_RE.split(par)
    for piece in pieces:
        if not piece:
            continue
        buf += piece
        if re.search(r"[。！？…]$", buf) or re.search(r"[.!?]$", buf.rstrip()):
            if _is_abbrev(buf.rstrip()) or re.search(r"[0-9]\.[0-9]$", buf.rstrip()):
                continue  # 缩写/小数，不切
            parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())
    # 去掉纯标点碎片
    return [p for p in parts if re.search(r"[\w\u4e00-\u9fff]", p)]


# ---------------------------------------------------------------- 长句按语义切分
DEP_START = (
    "其中", "该研究", "该方法", "该结果", "这与", "这一", "这种", "这些", "上述",
    "前者", "后者", "因此", "从而", "进而", "同时", "但也", "但", "而", "也", "则",
    "均", "都", "且", "即",
)
DEP_START_EN = (
    "which", "that", "this", "these", "those", "its", "their", "thereby", "thus",
    "hence", "while", "whereas", "but", "and", "or", "i.e.", "e.g.",
)
DEP_START_EN = (
    "which", "that", "this", "these", "those", "its", "their", "thereby", "thus",
    "hence", "while", "whereas", "but", "and", "or", "i.e.", "e.g.",
)
# 语义边界：句内并列项用分号；逗号后接以下话语标记时视为新语义单元
DISCOURSE = (
    "首先", "其次", "再次", "此外", "另外", "同时", "随后", "最后", "然而", "但是",
    "不过", "因此", "所以", "总之", "综上", "具体而言", "换言之", "相比之下",
    "与此相对", "一方面", "另一方面", "再者", "进而", "第一", "第二", "第三",
    "其一", "其二", "其三",
)
DISCOURSE_EN = (
    "In addition", "Moreover", "Furthermore", "However", "Therefore", "Thus",
    "Meanwhile", "Finally", "First", "Second", "Third", "Nevertheless",
    "Consequently", "Additionally", "In contrast",
)
MIN_PART = 8           # 切出片段最短长度


def _starts_dependent(seg: str) -> bool:
    s = seg.lstrip()
    low = s.lower()
    for w in DEP_START_EN:
        if low.startswith(w + " "):
            return True
    for w in DEP_START:
        if s.startswith(w):
            return True
    return False


def _split_points(text: str) -> list[str]:
    """按语义边界切：分号之后、话语标记前的逗号。"""
    cuts: list[tuple[int, int]] = []
    for m in re.finditer(r"[；;]", text):
        cuts.append((m.end(), m.end()))
    zh = "|".join(DISCOURSE)
    en = "|".join(re.escape(x) for x in DISCOURSE_EN)
    for m in re.finditer(rf"[，,]\s*(?=(?:{zh}))", text):
        if m.start() > 0:
            cuts.append((m.start(), m.start() + 1))
    for m in re.finditer(rf"[，,]\s*(?=(?:{en})\b)", text):
        if m.start() > 0:
            cuts.append((m.start(), m.start() + 1))
    if not cuts:
        return [text]
    cuts.sort()
    parts, last = [], 0
    for end, nxt in cuts:
        if end <= last:
            continue
        parts.append(text[last:end])
        last = nxt
    parts.append(text[last:])
    return [p for p in parts if p.strip()]


def split_long(text: str) -> list[str]:
    """按自然语言边界切分。依赖上文的片段并回前段（前后一致不切）。"""
    chunks = _split_points(text)
    if len(chunks) < 2:
        return [text]
    chunks = [c.strip().lstrip("，,、 ") for c in chunks if c.strip()]
    merged: list[str] = []
    for c in chunks:
        if merged and (_starts_dependent(c) or len(merged[-1]) < MIN_PART):
            merged[-1] = merged[-1] + c
        elif merged and merged[-1].rstrip().endswith(("：", ":")):
            merged[-1] = merged[-1] + c
        else:
            merged.append(c)
    merged = [m.strip() for m in merged if m.strip()]
    merged = [m.rstrip("；; ") for m in merged if m.rstrip("；; ")]
    return merged if len(merged) > 1 else [text]


# ---------------------------------------------------------------- 主流程
def build_blocks_from_text(raw: str) -> list[dict]:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")

    blocks: list[dict] = []          # {text, section, para_index}
    section = "unknown"
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        text = "\n".join(buf).strip()
        if text:
            blocks.append({"text": text, "section": section, "para_index": None})
        buf = []

    for line in lines:
        s = line.strip()
        if not s:
            flush()
            continue
        md = _HEAD_MD.match(s)
        plain = _HEAD_PLAIN.match(s)
        head_text = None
        if md:
            head_text = md.group("t")
        elif plain and len(s) <= 48 and not re.search(r"[。，；！？,;]", s):
            # 正文句子也常以"3 结果显示…"开头，用标点排除误判
            head_text = plain.group("t")

        if head_text is not None:
            kind = classify_heading(head_text)
            if kind:
                flush()
                section = kind
                blocks.append({"text": s, "section": "heading", "heading_kind": kind,
                               "para_index": None})
                continue
        buf.append(s)
    flush()
    return blocks


CAPTION_RE = re.compile(r"^\s*(图|表|Figure|Fig\.?|Table)\s*([0-9]+(?:[.\-][0-9]+)*)")
TABLE_MAX_ROWS = 40


def _table_blocks(doc) -> list[dict]:
    """把 docx 表格读成 block（只读上下文，不插标记）。"""
    out = []
    for ti, t in enumerate(getattr(doc, "tables", []), 1):
        rows = []
        for r in t.rows[:TABLE_MAX_ROWS]:
            cells = [c.text.strip().replace("\n", " ") for c in r.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if not rows:
            continue
        out.append({"text": f"表 {ti}：" + "；".join(rows), "section": "table",
                    "para_index": None, "no_anchor": True,
                    "table_index": ti, "n_rows": len(rows)})
    return out


def build_blocks_from_docx(path: str) -> list[dict]:
    """docx 每段一个 block，para_index 指向 docx 段落序号（插入时用）。"""
    from docx import Document

    doc = Document(path)
    blocks: list[dict] = []
    section = "unknown"
    for i, p in enumerate(doc.paragraphs):
        text = (p.text or "").strip()
        if not text:
            continue
        style = (p.style.name or "") if p.style is not None else ""
        is_kw = bool(re.match(r"(?i)^(关\s*键\s*词|关键字|key\s*words?|索引词)\s*[:：]", text))
        is_head = (not is_kw) and (style.lower().startswith("heading") or bool(
            len(text) <= 48 and classify_heading(text)
            and not re.search(r"[。！？]", text)))
        kind = classify_heading(text) if is_head else None
        if is_head and kind:
            section = kind
            blocks.append({"text": text, "section": "heading", "heading_kind": kind,
                           "para_index": i})
            continue
        if CAPTION_RE.match(text):
            blocks.append({"text": text, "section": "caption", "para_index": i})
            continue
        blocks.append({"text": text, "section": section, "para_index": i})
    blocks.extend(_table_blocks(doc))
    return blocks


def parse_paper(raw: str) -> dict:
    return assemble(build_blocks_from_text(raw))


def assemble(blocks: list[dict]) -> dict:
    paragraphs, sentences = [], []
    pid = sid_global = gid = 0
    for b in blocks:
        if b["section"] == "heading":
            paragraphs.append({"pid": None, "section": "heading", "kind": b["heading_kind"],
                               "text": b["text"], "sentences": [],
                               "para_index": b.get("para_index")})
            continue
        # 段内若含多个物理段（含空行已被拆开），这里只处理单段
        pid += 1
        p_uid = f"P{pid:03d}"
        psec = b["section"]
        if re.match(r"(?i)^(关\s*键\s*词|关键字|key\s*words?|索引词)\s*[:：]", b["text"]):
            psec = "keywords"          # 关键词行不分句，整行一条
            sid_global += 1
            sents = [{"sid": "S001", "uid": f"{p_uid}:S001", "idx": sid_global,
                      "text": b["text"].strip(), "section": psec,
                      "para_index": b.get("para_index")}]
            paragraphs.append({"pid": p_uid, "section": psec, "text": b["text"],
                               "para_index": b.get("para_index"), "sentences": sents})
            sentences.extend(sents)
            continue
        sents = []
        k = 0
        cursor = 0
        for stext in split_sentences(b["text"]):
            gid += 1
            parts = split_long(stext)
            for pi, pt in enumerate(parts, 1):
                k += 1
                sid_global += 1
                off = b["text"].find(pt, cursor)
                if off < 0:
                    off = cursor
                cursor = off + len(pt)
                sents.append({"sid": f"S{k:03d}", "uid": f"{p_uid}:S{k:03d}",
                              "idx": sid_global, "text": pt, "section": psec,
                              "group": f"G{gid:03d}", "part": pi, "n_parts": len(parts),
                              "offset": off, "para_index": b.get("para_index"),
                              "no_anchor": bool(b.get("no_anchor")),
                              "table_index": b.get("table_index")})
        paragraphs.append({"pid": p_uid, "section": psec, "text": b["text"],
                           "para_index": b.get("para_index"), "sentences": sents})
        sentences.extend(sents)

    sections: list[dict] = []
    for p in paragraphs:
        if p["pid"] is None:
            continue
        if not sections or sections[-1]["section"] != p["section"]:
            sections.append({"section": p["section"], "from": p["pid"], "to": p["pid"],
                             "n_sent": 0})
        sections[-1]["to"] = p["pid"]
        sections[-1]["n_sent"] += len(p["sentences"])

    # 题注上下文：前一段最后一句 + 后一段第一句，供模型判断是否需要来源
    body = [p for p in paragraphs if p["pid"] and p["section"] not in ("heading",)]
    for idx, p in enumerate(body):
        if p["section"] != "caption":
            continue
        prev = body[idx - 1]["sentences"][-1]["text"][:60] if idx > 0 and body[idx - 1]["sentences"] else ""
        nxt = body[idx + 1]["sentences"][0]["text"][:60] if idx + 1 < len(body) and body[idx + 1]["sentences"] else ""
        for sn in p["sentences"]:
            sn["ctx"] = (prev + " / " + nxt).strip(" /")

    # 首个无标题块通常是题名：短、无句末标点、不是正文句式
    first = next((p for p in paragraphs if p["pid"]), None)
    if first and first["section"] == "unknown" and len(first["text"]) <= 120 \
            and not re.search(r"[。！？](?!$)", first["text"]):
        first["section"] = "title"
        for s in first["sentences"]:
            s["section"] = "title"
        for sec in sections:
            if sec["from"] == first["pid"]:
                sec["section"] = "title"

    return {"paragraphs": paragraphs, "sentences": sentences, "sections": sections}


def estimate_tokens(text: str) -> dict:
    zh = len(re.findall(r"[\u4e00-\u9fff]", text))
    en_words = len(re.findall(r"[A-Za-z][A-Za-z'-]*", text))
    chars = len(text)
    # 粗估：中文 0.7 token/字，英文 1.35 token/词；系数偏保守（宁高不低）
    est = round(zh * 0.7 + en_words * 1.35)
    return {"chars": chars, "zh_chars": zh, "en_words": en_words,
            "est_tokens": est, "est_tokens_hi": round(est * 1.3)}


def build(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    src = Path(args.input)
    if src.suffix.lower() == ".docx":
        blocks = build_blocks_from_docx(str(src))
        raw = "\n\n".join(b["text"] for b in blocks if b["section"] != "heading")
        doc = assemble(blocks)
    else:
        raw = src.read_text(encoding="utf-8-sig", errors="replace")
        doc = parse_paper(raw)
    n_sent = len(doc["sentences"])

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 不送模型的分区：题名、参考文献表（条目本身不是句子）、以及保留但需人看的
    skip = set(args.skip_sections.split(",")) if args.skip_sections else {"title", "refs"}
    model_sents = [s for s in doc["sentences"] if s.get("section") not in skip]
    payload = {
        "source": str(src),
        "generated_by": "pass1_prep.py",
        "n_paragraphs": sum(1 for p in doc["paragraphs"] if p["pid"]),
        "n_sentences": n_sent,
        "n_sentences_model": len(model_sents),
        "skipped_sections": sorted(skip),
        "sections": doc["sections"],
        "paragraphs": doc["paragraphs"],
        "sentences": doc["sentences"],
        "stats": estimate_tokens(raw),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    emit = Path(args.emit) if args.emit else out_path.with_name("pass1_input.txt")
    lines = []
    for s in model_sents:
        row = f"{s['uid']}\t{s.get('section', 'unknown')}\t{s['text']}"
        if s.get("ctx"):
            row += "\t" + s["ctx"]
        elif s.get("no_anchor"):
            row += "\t[表格内容，只读]"
        lines.append(row)
    emit.write_text("\n".join(lines) + "\n", encoding="utf-8")

    elapsed = round((time.perf_counter() - t0) * 1000, 1)
    model_text = "\n".join(s["text"] for s in model_sents)
    summary = {
        "ok": True, "source": str(src), "prep": str(out_path), "emit": str(emit),
        "n_paragraphs": payload["n_paragraphs"], "n_sentences": n_sent,
        "n_sentences_model": len(model_sents),
        "sections": [f"{s['section']}:{s['from']}-{s['to']}({s['n_sent']})" for s in doc["sections"]],
        "input_tokens": estimate_tokens(model_text),
        "elapsed_ms": elapsed,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- 自检
RC_CITE = {"RC1", "RC2", "RC3", "RC4"}
RC_OWN = {"RC5", "RC6", "RC7"}


def verify(args: argparse.Namespace) -> int:
    prep = json.loads(Path(args.prep).read_text(encoding="utf-8"))
    skip = set(prep.get("skipped_sections") or ["title", "refs"])
    expect = [s["uid"] for s in prep["sentences"] if s.get("section") not in skip]
    expect_set = set(expect)
    text_by_uid = {s["uid"]: s["text"] for s in prep["sentences"]}

    rows, problems, batch, declared = [], [], None, {}
    for ln in Path(args.verify).read_text(encoding="utf-8-sig", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("#PASS1"):
            for k, v in re.findall(r"(CITE|OWN|STRUCT|AMBIG):(\d+)", ln):
                declared[k] = int(v)
            continue
        if ln.startswith("#BATCH"):
            batch = ln
            continue
        if ln.startswith("#"):
            problems.append({"kind": "note", "line": ln})
            continue
        f = [x.strip() for x in ln.split("|")]
        if len(f) < 4:
            problems.append({"kind": "bad_line", "line": ln})
            continue
        uid, cat, rc, head = f[0], f[1], f[2], f[3]
        rows.append({"uid": uid, "cat": cat, "rc": rc, "head": head})

    seen = [r["uid"] for r in rows]
    got = set(seen)
    dup = sorted({u for u in seen if seen.count(u) > 1})
    missing = sorted(expect_set - got)
    extra = sorted(got - expect_set)

    for r in rows:
        if r["cat"] not in {"CITE", "OWN", "STRUCT", "AMBIG"}:
            problems.append({"kind": "bad_category", "uid": r["uid"], "value": r["cat"]})
        if r["cat"] == "CITE" and r["rc"] not in RC_CITE:
            problems.append({"kind": "rc_mismatch", "uid": r["uid"], "cat": r["cat"], "rc": r["rc"]})
        if r["cat"] == "OWN" and r["rc"] not in RC_OWN:
            problems.append({"kind": "rc_mismatch", "uid": r["uid"], "cat": r["cat"], "rc": r["rc"]})
        body = text_by_uid.get(r["uid"], "")
        if body and r["head"] and r["head"] not in body:
            problems.append({"kind": "not_found", "uid": r["uid"], "head": r["head"][:30]})

    counts = {k: sum(1 for r in rows if r["cat"] == k) for k in ("CITE", "OWN", "STRUCT", "AMBIG")}
    summary_ok = True
    if declared:
        for k, v in declared.items():
            if counts[k] != v:
                summary_ok = False
                problems.append({"kind": "summary_mismatch", "cat": k,
                                 "declared": v, "actual": counts[k]})
    else:
        summary_ok = False
        problems.append({"kind": "summary_missing"})
    total = max(1, len(rows))
    warnings = []
    if counts["CITE"] / total < 0.05:
        warnings.append("LOW_COVERAGE_CITE")
    if counts["OWN"] == 0:
        warnings.append("OWN_ZERO")

    result = {
        "ok": not (missing or dup or extra) and summary_ok,
        "n_expected": len(expect), "n_rows": len(rows), "counts": counts,
        "summary_ok": summary_ok,
        "share": {k: round(v / total, 3) for k, v in counts.items()},
        "missing": missing[:20], "n_missing": len(missing),
        "duplicate": dup[:20], "extra": extra[:20],
        "batch": batch, "problems": problems[:40], "n_problems": len(problems),
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser(description="pass1 输入准备与结果自检")
    ap.add_argument("input", nargs="?", help="论文文本 (.txt/.md)")
    ap.add_argument("--out", default="work/prep.json")
    ap.add_argument("--emit", default=None)
    ap.add_argument("--verify", default=None, help="pass1 输出文件，做覆盖/格式自检")
    ap.add_argument("--prep", default="work/prep.json")
    ap.add_argument("--skip-sections", default="title,abstract,keywords,refs",
                    help="不送模型的分区（逗号分隔），默认 title,abstract,keywords,refs")
    args = ap.parse_args(argv)
    if args.verify:
        return verify(args)
    if not args.input:
        ap.error("缺少输入文件，或使用 --verify")
    return build(args)


if __name__ == "__main__":
    raise SystemExit(main())

