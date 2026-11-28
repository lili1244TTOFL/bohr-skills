#!/usr/bin/env python3
"""run.py —— 文献翻译：段/句双层拆分、术语锁定、上下文携带、标记保护、译后核验。

分工（两阶段交接，脚本不调用模型）：
  阶段一  脚本读原文 → 切段切句 → 抽角色/页码/章节 → 抽术语与保护标记 →
          写 translation_brief.md（每段：上下文、必须使用的译名、必须原样保留的
          数字/单位/引用/公式/图表编号、输出格式与段号标记）。
  阶段二  调用方模型按 brief 逐段翻译，写成 targets.txt（推荐每段前加 `### S001`）。
  阶段三  再跑一次脚本：回填译文、段内逐句对照、跑 Q01–Q06，写
          bilingual.md / segments.json / verify.json / result.json。

没有外部译文时脚本只交「术语锁定草案」，并在 labels/status 里标成未译，不写成成功。

题面四个关注点 → 代码位置：
    术语稳   load_glossary + term_scan（全篇回扫、禁用译名）      → Q01 / Q05
    段落全   split_blocks + continuity（覆盖、重复、错位、跨页）   → Q02
    关系准   tokens_of 逐段多重集比对（数字/单位/引用/图表/公式）  → Q03 / Q04 / Q06
    可核验   char 区间 + 句区间 + 逐段 basis + labels + manual_check
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

VERSION = "3.0.0"
RULES_VERSION = "rules/conventions.md v3.0.0"

# ---------------------------------------------------------------- 正则与词典
NUM_RE = re.compile(r"(?<![A-Za-z0-9_.])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9_]|\.[0-9])")
CITE_RE = re.compile(
    r"\[\d+(?:\s*[-,–]\s*\d+)*\]"                                  # [1] [1,3] [2-5]
    r"|\([A-Z][A-Za-z\-]+(?: et al\.)?,? \d{4}[a-z]?\)"            # (Smith et al., 2019)
)
MATH_RE = re.compile(r"\$[^$\n]{2,80}\$|\\\([^)\n]{2,80}\\\)")
EQ_MENTION_RE = re.compile(r"\bEq(?:uation)?s?\.?\s*\(?(\d{1,2}[a-z]?)\)?")
EQ_TAIL_RE = re.compile(r"\((\d{1,2}[a-z]?)\)\s*$")
EQ_TAG_RE = re.compile(r"\\tag\{(\d{1,2}[a-z]?)\}")
FIGTAB_RE = re.compile(
    r"\b(?:Fig(?:ure)?s?\.?|Tab(?:le)?s?\.?)\s*\d+[A-Za-z]?|[图表]\s*\d+[A-Za-z]?")
NEG_EN_RE = re.compile(
    r"\b(?:not|no|never|none|neither|nor|cannot|without|failed to|lack(?:s|ed|ing)?|"
    r"unclear|unknown|uncertain|unable|absence of|absent)\b",
    re.I)
NEG_ZH_WORDS = ("没有", "并未", "未能", "未见", "未曾", "无法", "不能", "不存在",
                "未", "无", "不")
NEG_ZH_EXCEPT = ("不同", "不断", "不仅", "不但", "不论", "无论", "不管", "不确",
                 "不宜", "不必", "不妨", "不少", "不久", "不已", "不一", "无不",
                 "未来")
HEDGE_RE = re.compile(
    r"\b(?:may|might|could|possibly|likely|probably|suggests?|suggested|appears?|"
    r"appeared|seems?|seemed|tends? to)\b|可能|或许|似乎|大概", re.I)
PRONOUN_RE = re.compile(r"\b(?:it|its|they|them|their|this|these|those)\b", re.I)

# 单位别名：原文/译文两侧都归一到同一 canonical，中文单位与英文单位等价
UNIT_ALIAS = {
    "毫升": "ml", "ml": "ml", "millilitre": "ml", "milliliter": "ml", "millilitres": "ml",
    "milliliters": "ml", "l": "l", "升": "l", "litre": "l", "liter": "l", "litres": "l",
    "liters": "l", "dl": "dl", "μl": "ul", "µl": "ul", "ul": "ul",
    "毫克": "mg", "mg": "mg", "milligram": "mg", "milligrams": "mg",
    "微克": "ug", "μg": "ug", "µg": "ug", "ug": "ug", "microgram": "ug",
    "微克数": "ug", "纳克": "ng", "ng": "ng",
    "克": "g", "g": "g", "gram": "g", "grams": "g",
    "千克": "kg", "kg": "kg", "公斤": "kg", "kilogram": "kg", "kilograms": "kg",
    "秒": "s", "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s",
    "毫秒": "ms", "ms": "ms", "millisecond": "ms", "milliseconds": "ms",
    "分钟": "min", "min": "min", "mins": "min", "minute": "min", "minutes": "min",
    "小时": "h", "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
    "天": "d", "d": "d", "day": "d", "days": "d",
    "周": "wk", "wk": "wk", "week": "wk", "weeks": "wk",
    "月": "mo", "mo": "mo", "month": "mo", "months": "mo",
    "年": "yr", "yr": "yr", "year": "yr", "years": "yr", "岁": "yr",
    "%": "%", "％": "%", "百分比": "%", "‰": "‰",
    "°c": "c", "℃": "c", "摄氏度": "c", "°f": "f",
    "米": "m", "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "厘米": "cm", "cm": "cm", "毫米": "mm", "mm": "mm", "纳米": "nm", "nm": "nm",
    "公里": "km", "km": "km", "微米": "um", "μm": "um", "µm": "um", "um": "um",
    "hz": "hz", "khz": "khz", "mhz": "mhz", "ghz": "ghz", "m": "m",
    "mol": "mol", "mmol": "mmol", "μmol": "umol", "nmol": "nmol",
    "m": "m", "mmhg": "mmhg", "pa": "pa", "kpa": "kpa", "mpa": "mpa",
    "bar": "bar", "atm": "atm", "gy": "gy", "mgy": "mgy", "rpm": "rpm",
    "kda": "kda", "da": "da", "bp": "bp", "kb": "kb", "ppm": "ppm", "ppb": "ppb",
    "倍": "x", "×": "x", "times": "x", "fold": "x",
    "mj": "mj", "kj": "kj", "j": "j", "kcal": "kcal", "cal": "cal", "w": "w",
    "kw": "kw", "v": "v", "mv": "mv", "a": "a", "ma": "ma", "na": "na",
    "hz": "hz", "ppm": "ppm", "g/l": "g/l", "mg/l": "mg/l", "mg/kg": "mg/kg",
    "g/dl": "g/dl", "mg/dl": "mg/dl", "mmol/l": "mmol/l", "μmol/l": "umol/l",
    "ml/min": "ml/min", "ml/kg": "ml/kg", "per": "per",
}
_UNIT_KEYS = sorted(UNIT_ALIAS, key=len, reverse=True)
_STOPISH = {"and", "or", "of", "the", "in", "on", "at", "to", "for", "with", "was",
            "were", "is", "are", "by", "as", "we", "it", "this", "that", "from",
            "study", "studies", "group", "groups", "subject", "subjects", "patient",
            "patients", "trial", "trials", "case", "cases", "sample", "samples",
            "participant", "participants", "week", "weeks", "year", "years"}

SECTIONS_KNOWN = {
    "abstract": "Abstract", "摘要": "Abstract",
    "introduction": "Introduction", "引言": "Introduction", "前言": "Introduction",
    "background": "Background", "背景": "Background",
    "related work": "Related work", "相关工作": "Related work",
    "methods": "Methods", "method": "Methods", "materials and methods": "Methods",
    "材料与方法": "Methods", "方法": "Methods",
    "results": "Results", "结果": "Results",
    "discussion": "Discussion", "讨论": "Discussion",
    "conclusion": "Conclusion", "conclusions": "Conclusion", "结论": "Conclusion",
    "references": "References", "reference": "References", "参考文献": "References",
    "acknowledgements": "Acknowledgements", "acknowledgments": "Acknowledgements",
    "致谢": "Acknowledgements",
    "appendix": "Appendix", "supplementary": "Supplementary", "附录": "Appendix",
}
ROLE_ZH = {"title": "标题", "heading": "小标题", "body": "正文", "abstract": "摘要",
           "caption_fig": "图注", "caption_tab": "表题", "table": "表格",
           "equation": "公式", "reference": "参考文献条目"}
KIND_ZH = {"num": "数字", "unit": "数字-单位", "cite": "文献引用",
           "figtab": "图表编号", "eqno": "公式编号", "math": "行内公式"}


# ---------------------------------------------------------------- 基础工具
def read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def flat(text: str) -> str:
    """把段内换行压成空格，只用于显示；char 区间始终指向原始文本。"""
    return re.sub(r"\s*\n\s*", " ", text).strip()


def cell(text: str, limit: int = 0) -> str:
    """Markdown 表格单元格转义。"""
    s = flat(text).replace("|", "\\|")
    if limit and len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


def multiset(seq) -> dict:
    out: dict[str, int] = {}
    for x in seq:
        out[str(x)] = out.get(str(x), 0) + 1
    return out


# ---------------------------------------------------------------- 输入识别
CASE_KEYS = ("material", "materials", "paper", "source", "source_file", "document",
             "document_file", "file", "files", "input_files", "manuscript")
INLINE_TEXT_KEYS = ("text", "source_text", "content", "material_text", "paper_text")
SRC_EXTS = (".txt", ".md", ".pdf", ".tex", ".docx")


def load_case(root: Path) -> dict:
    p = root / "case.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(read(p))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _pick_material(root: Path, case: dict) -> tuple[Path | None, str]:
    """按 case.json 指认 > 目录内后缀优先级 的顺序选原文，绝不含 task.md。"""
    for key in CASE_KEYS:
        v = case.get(key)
        for item in (v if isinstance(v, list) else [v]):
            if not isinstance(item, str) or len(item) > 300:
                continue
            cand = (root / item) if not Path(item).is_absolute() else Path(item)
            if cand.is_file() and cand.suffix.lower() in SRC_EXTS:
                return cand, f"case.json 字段 {key}"
            for hit in root.rglob(Path(item).name):
                if hit.is_file() and hit.suffix.lower() in SRC_EXTS:
                    return hit, f"case.json 字段 {key}"
    cands = [p for p in sorted(root.rglob("*")) if p.is_file()
             and p.suffix.lower() in SRC_EXTS]
    banned = ("glossary", "target", "task", "case", "skill", "readme", "acknowledg",
              "missing_input")
    cands = [p for p in cands if not any(b in p.name.lower() for b in banned)]
    if not cands:
        return None, ""
    for ext in (".pdf", ".tex", ".docx", ".md", ".txt"):
        hit = [p for p in cands if p.suffix.lower() == ext]
        if hit:
            hit.sort(key=lambda p: (-p.stat().st_size, p.name))
            return hit[0], f"目录内 {ext} 最大文件"
    return None, ""


def extract_pdf(path: Path, work: Path) -> tuple[str, list[str], list[str]]:
    """PDF → 文本。优先 pdftotext（保留分页 \\f），其次 pypdf / pdfminer。拿不到就说明缺什么。"""
    notes, gaps = [], []
    exe = shutil.which("pdftotext")
    if exe:
        dst = work / "_source.txt"
        try:
            subprocess.run([exe, "-layout", "-enc", "UTF-8", str(path), str(dst)],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=120)
            txt = read(dst)
            if txt.strip():
                notes.append("PDF 文本由 pdftotext -layout 抽取（分页符 \\f 保留为页码依据）")
                return txt, notes, gaps
            gaps.append("pdftotext 抽出的文本为空：疑似扫描件 / 无文字层")
        except Exception as e:  # noqa: BLE001
            gaps.append(f"pdftotext 调用失败：{e}")
    for mod, fn in (("pypdf", "_pdf_pypdf"), ("pdfminer.high_level", "_pdf_pdfminer")):
        try:
            __import__(mod.split(".")[0])
        except Exception:
            continue
        try:
            txt = globals()[fn](path)
            if txt.strip():
                notes.append(f"PDF 文本由 {mod} 抽取（按页插入 \\f）")
                return txt, notes, gaps
            gaps.append(f"{mod} 抽出的文本为空：疑似扫描件 / 无文字层")
        except Exception as e:  # noqa: BLE001
            gaps.append(f"{mod} 抽取失败：{e}")
    gaps.append("未能在本机抽到 PDF 文字层：扫描件需先 OCR（平台若提供 ocr-tool 先跑 OCR，"
                "或另给 .txt/.md 文本层），否则本 Skill 拒绝把图像当原文编造译文")
    return "", notes, gaps


def _pdf_pypdf(path: Path) -> str:
    from pypdf import PdfReader  # type: ignore
    return "\f".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages)


def _pdf_pdfminer(path: Path) -> str:
    from pdfminer.high_level import extract_pages  # type: ignore
    from pdfminer.layout import LTTextContainer  # type: ignore
    pages = []
    for layout in extract_pages(str(path)):
        pages.append("\n".join(el.get_text() for el in layout
                                if isinstance(el, LTTextContainer)))
    return "\f".join(pages)


def read_source(root: Path, case: dict, work: Path) -> dict:
    """返回 {text, path, notes, gaps}；取不到时 text 为空，由 main 走 blocked。"""
    notes, gaps = [], []
    for key in INLINE_TEXT_KEYS:
        v = case.get(key)
        if isinstance(v, str) and len(v) > 200:
            notes.append(f"原文取自 case.json 字段 {key}（内联文本）")
            return {"text": v, "path": None, "notes": notes, "gaps": gaps}
    path, how = _pick_material(root, case)
    if path is None:
        return {"text": "", "path": None, "notes": notes, "gaps": gaps}
    notes.append(f"原文选自 {path.relative_to(root).as_posix() if path.is_relative_to(root) else path}（{how}）")
    if path.suffix.lower() == ".pdf":
        text, n2, g2 = extract_pdf(path, work)
        notes += n2
        gaps += g2
        return {"text": text, "path": path, "notes": notes, "gaps": gaps}
    if path.suffix.lower() in (".tex", ".docx"):
        gaps.append(f"{path.suffix} 不是脚本可直接切分的纯文本：需先转成 .txt/.md"
                    "（LaTeX 用 detex，Word 用 pandoc/LibreOffice），否则无法给出段号与句号")
        return {"text": "", "path": path, "notes": notes, "gaps": gaps}
    return {"text": read(path), "path": path, "notes": notes, "gaps": gaps}


# ---------------------------------------------------------------- 分段与结构
def split_blocks(text: str) -> list[dict]:
    """按空行切块，char 区间精确指向原文（不重排、不规范化，保证可回指）。"""
    blocks: list[dict] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        start, j = i, i
        while j < n:
            if text[j] == "\n":
                k = j + 1
                while k < n and text[k] in " \t\r":
                    k += 1
                if k < n and text[k] == "\n":
                    break
            j += 1
        body = text[start:j].rstrip()
        if body.strip():
            blocks.append({"source": body, "char_start": start,
                           "char_end": start + len(body)})
        i = j
    return blocks


def split_sentences(text: str, base: int, abbr: tuple[str, ...] = ()) -> list[dict]:
    """句边界：.?! 后接空白/句尾；。！？；直接结束（中文不加空格）。缩写与小数字点不切。"""
    out: list[dict] = []
    start, i, n = 0, 0, len(text)
    cjk_end = "。！？；"
    while i < n:
        c = text[i]
        if c in ".!?。！？；;":
            j = i + 1
            while j < n and text[j] in "\"'”’）)]】」":
                j += 1
            if c in cjk_end or j >= n or text[j].isspace():
                prev = re.findall(r"[A-Za-z]+$", text[max(0, i - 12):i])
                w = prev[0].lower() if prev else ""
                decimal = (c == "." and i > 0 and text[i - 1].isdigit()
                           and j < n and text[j].isdigit())
                if decimal or (c == "." and (w in abbr or len(w) == 1)):
                    i += 1
                    continue
                chunk = text[start:j]
                body = chunk.strip()
                if body:
                    off = start + (len(chunk) - len(chunk.lstrip()))
                    out.append({"char_start": base + off,
                                "char_end": base + off + len(body),
                                "source": body})
                start = j
                i = j
                continue
        i += 1
    tail = text[start:].strip()
    if tail:
        off = start + (len(text[start:]) - len(text[start:].lstrip()))
        out.append({"char_start": base + off, "char_end": base + off + len(tail),
                    "source": tail})
    return out


def is_table(s: str) -> bool:
    lines = [ln for ln in s.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    pipes = sum(1 for ln in lines if ln.lstrip().startswith("|"))
    sep = any(re.match(r"^\|?[\s:\-|]{5,}\|?\s*$", ln) and "-" in ln for ln in lines)
    return pipes >= 2 and (sep or pipes == len(lines))


def is_equation(s: str) -> bool:
    one = flat(s)
    if one.startswith("$$") and one.endswith("$$"):
        return True
    if re.search(r"\\begin\{(equation|align|eqnarray)\*?\}", s):
        return True
    if len(one) <= 240 and ("=" in one or "\\tag{" in one) and \
            (EQ_TAIL_RE.search(one) or EQ_TAG_RE.search(s)):
        return True
    return False


def section_of(head: str) -> str | None:
    key = re.sub(r"^[\d.\s]+", "", head).strip().strip("#").strip().lower()
    key = re.sub(r"[：:.]$", "", key)
    for k, v in SECTIONS_KNOWN.items():
        if key == k or key.startswith(k):
            return v
    return None


def classify(blocks: list[dict], text: str) -> list[dict]:
    has_page = "\x0c" in text
    section: str | None = None
    for idx, b in enumerate(blocks):
        b["id"] = f"S{idx + 1:03d}"
        b["page"] = (1 + text.count("\x0c", 0, b["char_start"])) if has_page else None
        s = b["source"].strip()
        role = "body"
        head = ""
        if s.startswith("#"):
            role, head = "heading", s.lstrip("# ").strip()
        elif re.match(r"^(?:Fig(?:ure)?\.?|图)\s*\d+[A-Za-z]?\s*[.:：]", s):
            role = "caption_fig"
        elif re.match(r"^(?:Tab(?:le)?\.?|表)\s*\d+[A-Za-z]?\s*[.:：]", s):
            role = "caption_tab"
        elif len(s) <= 40 and section_of(s) and \
                re.fullmatch(r"[A-Za-z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff &\-]{1,39}\.?", s):
            role, head = "heading", s
        elif is_table(s):
            role = "table"
        elif is_equation(s):
            role = "equation"
        elif section == "References" and re.match(r"^(\[\d+\]|\d+[.)]\s|[A-Z][a-z]+,\s)",
                                                  s):
            role = "reference"
        elif len(s) <= 120 and re.match(r"^\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z]", s):
            role, head = "heading", s
        if head:
            section = section_of(head) or section
            if idx == 0:
                role = "title"
        if role == "body" and section == "Abstract":
            role = "abstract"
        b["role"] = role
        b["section"] = section
    return blocks


def add_context(blocks: list[dict]) -> None:
    """上下文携带：上一段尾 80 字、下一段首 80 字，供模型逐段独立翻译。"""
    for i, b in enumerate(blocks):
        prev = blocks[i - 1]["source"] if i > 0 else ""
        nxt = blocks[i + 1]["source"] if i + 1 < len(blocks) else ""
        b["prev_tail"] = flat(prev)[-80:]
        b["next_head"] = flat(nxt)[:80]


# ---------------------------------------------------------------- 术语表
def load_glossary(path: Path | None) -> dict:
    """Markdown 表格：原文 | 译名 | 禁用译名(可选) | 备注(可选)。取不到返回空表，不猜。"""
    g: dict[str, dict] = {}
    if not path or not path.exists():
        return g
    for line in read(path).splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set("".join(cells)) <= set("-: "):
            continue
        if cells[0].lower() in ("原文", "source", "en", "术语", "term"):
            continue
        ban = [x.strip() for x in re.split(r"[、,，/;；]", cells[2]) if x.strip()] \
            if len(cells) > 2 else []
        g[cells[0]] = {"to": cells[1], "ban": ban,
                       "note": cells[3] if len(cells) > 3 else ""}
    return g


def _banned_hits(tgt: str, to: str, bans: list[str]) -> list[str]:
    """禁用译名：只算没被正确译名覆盖的出现（`记忆负荷` 落在 `工作记忆负荷` 里不算违规）。"""
    out = []
    for b in bans:
        i = tgt.find(b)
        while i >= 0:
            inside, j = False, tgt.find(to)
            while j >= 0 and to:
                if j <= i and i + len(b) <= j + len(to):
                    inside = True
                    break
                j = tgt.find(to, j + 1)
            if not inside:
                out.append(b)
                break
            i = tgt.find(b, i + 1)
    return out


def term_scan(seg: dict, glossary: dict) -> tuple[list[dict], int]:
    """本段命中的术语（大小写不敏感），并核对译名是否已被译文采用。

    参考文献条目（role=reference）与公式段按惯例原样保留，不参与术语回扫。
    """
    hits, checked = [], 0
    tgt = seg.get("target") or ""
    if seg.get("role") in ("reference", "equation"):
        return hits, checked
    for src, spec in glossary.items():
        n = len(re.findall(re.escape(src), seg["source"], re.I))
        if not n:
            continue
        used = spec["to"] in tgt if tgt else False
        ban_hit = _banned_hits(tgt, spec["to"], spec["ban"]) if tgt else []
        hits.append({"term": src, "to": spec["to"], "count": n,
                     "used": used, "banned_hit": ban_hit})
        checked += 1
    return hits, checked


# ---------------------------------------------------------------- 保护标记
def unit_pairs(text: str) -> list[dict]:
    out = []
    for m in NUM_RE.finditer(text):
        tail = text[m.end():m.end() + 14]
        mm = re.match(r"\s*([A-Za-zµμ°%‰/·]+|％|×|[\u4e00-\u9fff]{1,4})", tail)
        if not mm:
            continue
        tok = mm.group(1)
        low = tok.lower()
        for u in _UNIT_KEYS:
            if not low.startswith(u):
                continue
            nxt = low[len(u):len(u) + 1]
            if nxt.isascii() and nxt.isalpha() and not u.endswith("/"):
                continue
            out.append({"num": m.group(0).replace(",", ""), "unit": u,
                        "canon": UNIT_ALIAS[u],
                        "value": f"{m.group(0).replace(',', '')}{UNIT_ALIAS[u]}",
                        "raw": f"{m.group(0)} {tok}"})
            break
    return out


def tokens_of(text: str, role: str = "body") -> dict:
    """逐类抽保护标记：数字 / 数字-单位 / 引用 / 图表编号 / 公式编号 / 行内公式。"""
    eqs = [m.group(1) for m in EQ_MENTION_RE.finditer(text)]
    if role == "equation":
        m = EQ_TAIL_RE.search(flat(text))
        if m:
            eqs.append(m.group(1))
    eqs += [m.group(1) for m in EQ_TAG_RE.finditer(text)]
    return {
        "num": [m.group(0).replace(",", "") for m in NUM_RE.finditer(text)],
        "unit": [u["value"] for u in unit_pairs(text)],
        "cite": [re.sub(r"\s+", "", m.group(0)).replace("–", "-")
                 for m in CITE_RE.finditer(text)],
        "figtab": [_canon_figtab(m.group(0)) for m in FIGTAB_RE.finditer(text)],
        "eqno": eqs,
        "math": [_canon_math(m.group(0).replace("\n", " ")) for m in MATH_RE.finditer(text)],
    }


def _canon_figtab(s: str) -> str:
    t = re.sub(r"\s+", "", s.lower())
    t = re.sub(r"^(?:figure|fig)\.?", "fig", t)
    t = re.sub(r"^(?:table|tab)\.?", "tab", t)
    return t.replace("图", "fig").replace("表", "tab")


def _canon_math(s: str) -> str:
    t = s.strip()
    if t.startswith("$") and t.endswith("$"):
        t = t[1:-1]
    t = t.removeprefix("\\(").removesuffix("\\)")
    return re.sub(r"\s+", "", t)


def formula_core(seg: dict) -> str:
    """公式段的核心表达式（去编号、去空白），用于核对译文是否原样保留公式。"""
    if seg.get("role") != "equation":
        return ""
    one = flat(seg["source"])
    one = EQ_TAIL_RE.sub("", one).strip()
    one = re.sub(r"^\(?[\d.]+\)?\s*", "", one).strip()
    one = re.sub(r"\s+", "", one)
    return one if len(one) >= 3 else ""


# ---------------------------------------------------------------- 译文配对
MARKER_RE = re.compile(
    r"(?m)^[ \t]*(?:#{1,6}[ \t]*)?(S\d{3})\b[^\n]{0,40}$")


def parse_targets(path: Path | None, blocks: list[dict]) -> tuple[dict, str, list[str]]:
    """译文文件：优先按 `### S001` 段号标记配对（不会错位），否则按空行顺序配对。"""
    gaps: list[str] = []
    if not path or not path.exists():
        return {}, "无", gaps
    txt = read(path)
    marks = list(MARKER_RE.finditer(txt))
    targets: dict[str, str] = {}
    if marks:
        for i, m in enumerate(marks):
            body = txt[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(txt)]
            targets[m.group(1)] = body.strip()
        known = {b["id"] for b in blocks}
        for sid in list(targets):
            if sid not in known:
                gaps.append(f"译文段号 {sid} 在原文分段中不存在（多余译文）")
        gaps.append(f"译文配对方式：段号标记（{len(targets)} 段）")
        return targets, "段号标记", gaps
    parts = [b.strip() for b in re.split(r"\n\s*\n", txt) if b.strip()]
    for b, t in zip(blocks, parts):
        targets[b["id"]] = t
    if len(parts) != len(blocks):
        gaps.append(f"译文按位置配对：原文 {len(blocks)} 段、译文 {len(parts)} 段，"
                    "位置配对在段数不等时会错位，建议改用 `### S001` 段号标记")
    gaps.append(f"译文配对方式：位置顺序（{len(parts)} 段）")
    return targets, "位置顺序", gaps


def auto_translate(blocks: list[dict], glossary: dict) -> tuple[dict, list[dict]]:
    """无外部译文时的术语锁定草案：术语表最长优先替换，只动词面，不动语序。"""
    draft: dict[str, str] = {}
    applied: list[dict] = []
    pairs = sorted(glossary.items(), key=lambda kv: -len(kv[0]))
    for b in blocks:
        if b["role"] in ("equation", "table"):
            draft[b["id"]] = b["source"]
            applied.append({"seg": b["id"], "rule": "公式/表格不替换，保持原样",
                            "replaced": [], "count": 0})
            continue
        text = b["source"]
        hits = []
        for src, spec in pairs:
            n = len(re.findall(re.escape(src), text, re.I))
            if n:
                text = re.sub(re.escape(src), spec["to"], text, flags=re.I)
                hits.append({"term": src, "to": spec["to"], "count": n})
        draft[b["id"]] = text
        applied.append({"seg": b["id"], "rule": "术语表最长优先替换", "replaced": hits,
                        "count": sum(h["count"] for h in hits)})
    return draft, applied


def ascii_words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Za-z]{3,}", text)]


def residue_ratio(src: str, tgt: str) -> float:
    sw = ascii_words(src)
    if not sw or not tgt:
        return 0.0
    tw = set(ascii_words(tgt))
    return round(sum(1 for w in sw if w in tw) / len(sw), 3)


def seg_residue(seg: dict) -> float:
    """参考文献与公式按惯例原样保留，不计入未译；其余段返回拉丁词重合率。"""
    if seg.get("role") in ("reference", "equation"):
        return 0.0
    return residue_ratio(seg["source"], seg.get("target") or "")


def is_residue(seg: dict) -> bool:
    return seg_residue(seg) >= 0.8


def candidate_terms(text: str) -> list[str]:
    """无术语表时抽候选术语：重复出现的 2–4 词英文片段，去停用词、去引用编号。"""
    body = CITE_RE.sub(" ", text)
    stop = set("""a an the of in on at to for from by with and or but as is are was
                  were be been being this that these those it its we our they their
                  study results show shows shown used use using can may might could
                  will would should not no""".split())
    freq: dict[str, int] = {}
    for sent in re.split(r"[.!?;]\s*", body):
        words = re.findall(r"[A-Za-z][A-Za-z-]*", sent)
        for n in (2, 3, 4):
            for i in range(len(words) - n + 1):
                gram = words[i:i + n]
                if any(w.lower() in stop for w in gram):
                    continue
                key = " ".join(gram)
                freq[key] = freq.get(key, 0) + 1
    keep = [k for k, _ in sorted(freq.items(), key=lambda kv: (-len(kv[0].split()), -kv[1]))
            if not any(k != o and k.lower() in o.lower() for o in freq)]
    return keep[:16]


# ---------------------------------------------------------------- 核验
def count_neg(text: str) -> int:
    n = len(NEG_EN_RE.findall(text))
    for m in re.finditer("|".join(map(re.escape, NEG_ZH_WORDS)), text):
        if any(text.startswith(ex, m.start()) for ex in NEG_ZH_EXCEPT):
            continue
        n += 1
    return n


def compare_segment(seg: dict) -> list[dict]:
    """逐段比对：数字/单位/引用/图表编号/公式编号/行内公式多重集 + 否定/限定计数。"""
    src, tgt = seg["source"], seg.get("target") or ""
    if not tgt:
        return []
    out: list[dict] = []
    st, tt = tokens_of(src, seg["role"]), tokens_of(tgt, seg["role"])
    for kind in ("num", "unit", "cite", "figtab", "eqno", "math"):
        a, b = multiset(st[kind]), multiset(tt[kind])
        if a == b:
            continue
        missing = sorted(k for k in a if a[k] > b.get(k, 0))
        extra = sorted(k for k in b if b[k] > a.get(k, 0))
        code = "Q04" if kind == "unit" else ("Q06" if kind in ("figtab", "eqno", "math")
                                             else "Q03")
        out.append({"code": code, "seg": seg["id"], "kind": kind,
                    "detail": f"{KIND_ZH[kind]}不一致：原文 {_ms(a)}"
                              f" → 译文 {_ms(b)}；缺 {missing or '无'}；多 {extra or '无'}",
                    "src": missing, "tgt": extra})
    for kind, fn in (("neg", count_neg), ("hedge", lambda t: len(HEDGE_RE.findall(t)))):
        n1, n2 = fn(src), fn(tgt)
        if n1 != n2:
            out.append({"code": "Q03", "seg": seg["id"], "kind": kind,
                        "detail": f"{'否定' if kind == 'neg' else '限定/模糊'}数量不一致："
                                  f"原文 {n1} → 译文 {n2}",
                        "src_spans": _spans(src, kind), "tgt_spans": _spans(tgt, kind)})
    if seg["role"] == "equation":
        core = formula_core(seg)
        if core and core not in re.sub(r"\s+", "", tgt):
            out.append({"code": "Q06", "seg": seg["id"], "kind": "formula",
                        "detail": f"公式未原样保留：原文核心 `{core[:60]}` 在译文中找不到"})
    return out


def _ms(d: dict) -> str:
    return "、".join(f"{k}×{d[k]}" if d[k] > 1 else k for k in sorted(d)) or "0"


def _spans(text: str, kind: str) -> list[str]:
    if kind == "neg":
        pats = [m.group(0) for m in NEG_EN_RE.finditer(text)]
        pats += [m.group(0) for m in re.finditer(
            "|".join(map(re.escape, NEG_ZH_WORDS)), text)
            if not any(text.startswith(ex, m.start()) for ex in NEG_ZH_EXCEPT)]
        return pats[:12]
    return [m.group(0) for m in HEDGE_RE.finditer(text)][:12]


def term_check(blocks: list[dict], glossary: dict) -> dict:
    if not glossary:
        return {"rate": None, "checked": 0, "missed": [], "banned": [], "terms": []}
    checked, missed, banned, rows = 0, [], [], []
    for b in blocks:
        hits, _ = term_scan(b, glossary)
        for h in hits:
            checked += 1
            if b.get("target") and not h["used"]:
                missed.append({"seg": b["id"], "term": h["term"], "want": h["to"]})
            if h["banned_hit"]:
                banned.append({"seg": b["id"], "term": h["term"],
                               "banned": h["banned_hit"]})
        b["terms"] = hits
    for src, spec in glossary.items():
        rows.append({"source": src, "target": spec["to"], "banned": spec["ban"],
                     "segments": [b["id"] for b in blocks
                                  if src.lower() in b["source"].lower()],
                     "occurrences": sum(1 for b in blocks
                                        for _ in re.finditer(re.escape(src),
                                                             b["source"], re.I)),
                     "missing": [m["seg"] for m in missed if m["term"] == src]})
    return {"rate": round(1 - len(missed) / checked, 3) if checked else None,
            "checked": checked, "missed": missed, "banned": banned, "terms": rows}


def continuity(blocks: list[dict], text: str) -> dict:
    covered = set()
    for b in blocks:
        covered.update(range(b["char_start"], b["char_end"]))
    total = sum(1 for i, ch in enumerate(text) if not ch.isspace())
    hit = sum(1 for i in covered if not text[i].isspace())
    gaps, dups = [], []
    for a, b in zip(blocks, blocks[1:]):
        if b["char_start"] < a["char_end"]:
            gaps.append({"after": a["id"], "kind": "区间交叠",
                         "detail": f"{a['id']} 结束 {a['char_end']} > {b['id']} 开始 "
                                   f"{b['char_start']}"})
    seen: dict[str, str] = {}
    for b in blocks:
        key = re.sub(r"\s+", " ", b["source"]).strip()
        if len(key) < 40:
            continue
        if key in seen:
            dups.append({"first": seen[key], "repeat": b["id"], "len": len(key)})
        else:
            seen[key] = b["id"]
    return {"coverage": round(hit / total, 4) if total else 1.0,
            "gaps": gaps, "duplicates": dups,
            "order_ok": all(b["char_start"] >= a["char_start"]
                            for a, b in zip(blocks, blocks[1:])),
            "cross_page": [b["id"] for b in blocks
                           if b["page"] and "\x0c" in text[b["char_start"]:b["char_end"]]]}


# ---------------------------------------------------------------- 契约回显
CONTRACT_ALIAS = {
    "translation": "阅读/解析结果", "translations": "阅读/解析结果",
    "bilingual": "阅读/解析结果", "main_result": "阅读/解析结果",
    "reading": "阅读/解析结果", "analysis": "阅读/解析结果",
    "terms": "阅读/解析结果", "glossary": "阅读/解析结果",
    "segments": "证据与边界", "segment": "证据与边界", "evidence": "证据与边界",
    "sources": "证据与边界", "anchors": "证据与边界", "citations": "证据与边界",
    "status": "运行状态", "run_status": "运行状态",
    "gaps": "gaps", "uncertainty": "uncertainty", "checks": "checks",
    "case_id": "case_id", "change_records": "change_records", "notes": "gaps",
    "metrics": "stats", "stats": "stats",
}


def echo_contract(oc, result: dict) -> dict:
    """按 case.json 的 output_contract 逐字段回显：该字段落到本结果哪一处。"""
    if not oc:
        return {}
    if isinstance(oc, dict):
        fields = list(oc.keys())
    elif isinstance(oc, list):
        fields = [x if isinstance(x, str) else (x.get("name") or x.get("field") or "")
                  for x in oc]
    else:
        return {}
    out = {}
    keys = {k.lower(): k for k in result}
    for f in fields:
        f = str(f).strip()
        if not f:
            continue
        if f in result:
            out[f] = {"present": True, "path": f, "mode": "同名字段回显"}
            continue
        alias = CONTRACT_ALIAS.get(f.lower())
        if alias and alias in result:
            out[f] = {"present": True, "path": alias, "mode": "别名映射"}
            continue
        hit = next((k for k, orig in keys.items() if f.lower() in k or k in f.lower()), None)
        if hit:
            out[f] = {"present": True, "path": keys[hit], "mode": "子串匹配"}
        else:
            out[f] = {"present": False, "path": None,
                      "mode": "本 Skill 无对应产物，需调用方补"}
    return out


# ---------------------------------------------------------------- 导出
def write_json(p: Path, data) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                 encoding="utf-8")


def build_records(blocks: list[dict], applied: list[dict], mode: str,
                  text: str) -> list[dict]:
    """每段一条记录：角色/页码/区间/句数/术语/保护标记/检查结果/标注/依据。"""
    recs = []
    for b in blocks:
        ap = next((a for a in applied if a["seg"] == b["id"]), None)
        labels, manual = [], []
        tgt = b.get("target") or ""
        rr = seg_residue({**b, "target": tgt}) if tgt else 0.0
        if not tgt:
            labels.append({"label": "未译", "detail": "本段无译文"})
        elif rr >= 0.8:
            labels.append({"label": "未译残留", "detail":
                           f"译文与原文同形（拉丁词重合 {rr:.0%}），不是译文"})
        elif rr >= 0.5:
            manual.append({"kind": "漏译可能",
                           "detail": f"拉丁词与原文重合 {rr:.0%}，需确认是否漏译/未译"})
        if ap is not None and ap["count"]:
            labels.append({"label": "规则草案", "detail":
                           f"术语替换 {ap['count']} 处，语序与虚词未动，待润色"})
        for h in b.get("terms", []):
            if tgt and not h["used"]:
                labels.append({"label": "术语未锁定",
                               "detail": f"`{h['term']}` 应作 `{h['to']}`，译文未见该译名"})
            if h["banned_hit"]:
                labels.append({"label": "禁用译名",
                               "detail": f"`{h['term']}` 出现禁用译名 "
                                         f"{'、'.join(h['banned_hit'])}"})
        pron = PRONOUN_RE.findall(b["source"])
        if pron and b["role"] != "equation":
            manual.append({"kind": "指代", "detail":
                           f"{len(pron)} 处代词/指示词"
                           f"（{','.join(sorted(set(w.lower() for w in pron))[:5])}），"
                           "指代对象需人工确认（脚本不做指代消解）"})
        if re.search(r"[A-Za-z]-\s*\n\s*[a-z]", b["source"]):
            manual.append({"kind": "断词连字符",
                           "detail": "段内含行末连字符断词，句边界与词形需人工确认"})
        if b["page"] and "\x0c" in text[b["char_start"]:b["char_end"]]:
            labels.append({"label": "跨页段",
                           "detail": "本段区间跨分页符，接续译文需带上一页末尾上下文"})
        if b.get("sentences") and tgt and b["role"] != "reference":
            nt = len(split_sentences(tgt, 0))
            if nt != len(b["sentences"]):
                manual.append({"kind": "句边界", "detail":
                               f"原文 {len(b['sentences'])} 句 / 译文 {nt} 句，"
                               "句级对应关系需人工确认"})
        recs.append({"id": b["id"], "role": b["role"], "section": b["section"],
                     "page": b["page"], "char_start": b["char_start"],
                     "char_end": b["char_end"], "sentences": len(b.get("sentences") or []),
                     "pair_mode": mode, "labels": labels, "manual_check": manual,
                     "term_hits": b.get("terms", []),
                     "protect": {k: len(v) for k, v in
                                 tokens_of(b["source"], b["role"]).items()}})
    return recs


def write_bilingual(p: Path, blocks: list[dict], glossary: dict, cons: dict,
                    problems: list[dict]) -> None:
    L = ["# 双语对照（逐段 + 逐句）", "",
         f"- 段数：{len(blocks)}；句数：{sum(len(b.get('sentences') or []) for b in blocks)}；"
         f"术语表条目：{len(glossary)}；术语一致率：{cons['rate'] if cons['rate'] is not None else '未核验'}",
         "- 段号 `S001`、句号 `S001.01` 与字符区间（UTF-8 字符下标，左闭右开）指向原文；"
         "译法依据与待确认项每段单列。", ""]
    if glossary:
        L += ["## 术语表（锁定口径）", "",
              "| 原文 | 指定译名 | 禁用译名 | 出现段 | 次数 | 译文是否采用 |",
              "|-|-|-|-|-|-|"]
        for t in cons["terms"]:
            L.append(f"| {cell(t['source'])} | {cell(t['target'])} | "
                     f"{cell('、'.join(t['banned'])) or '—'} | "
                     f"{', '.join(t['segments']) or '—'} | {t['occurrences']} | "
                     f"{'未采用：' + ','.join(t['missing']) if t['missing'] else '是'} |")
        L.append("")
    for b in blocks:
        role = ROLE_ZH.get(b["role"], b["role"])
        page = f"第 {b['page']} 页" if b["page"] else "页码未标（原文无分页符）"
        L += [f"## {b['id']} ｜ {role}"
              + (f" ｜ {b['section']}" if b["section"] else "")
              + f" ｜ {page} ｜ 字符 {b['char_start']}–{b['char_end']}", ""]
        if b.get("prev_tail"):
            L += [f"> 上段末尾：…{cell(b['prev_tail'], 60)}", ""]
        L += ["**原文**", "", flat(b["source"]), "", "**译文**", "",
              flat(b.get("target") or "（待译）"), ""]
        sents = b.get("sentences") or []
        if sents:
            tgt_sents = split_sentences(b.get("target") or "", 0)
            L += ["**句对齐**", "", "| 句 | 字符区间 | 原文 | 译文 |", "|-|-|-|-|"]
            for i, s in enumerate(sents):
                t = flat(tgt_sents[i]["source"]) if i < len(tgt_sents) else "（未对齐）"
                L.append(f"| {b['id']}.{i + 1:02d} | {s['char_start']}–"
                         f"{s['char_end']} | {cell(s['source'], 140)} | {cell(t, 140)} |")
            if len(tgt_sents) != len(sents):
                L.append("")
                L.append(f"句数不一致：原文 {len(sents)} / 译文 {len(tgt_sents)}，"
                         "句级对应需人工确认（见 `verify.json.manual_check`）。")
            L.append("")
        basis = []
        for h in b.get("terms", []):
            basis.append(f"术语 `{h['term']}` → `{h['to']}`（{h['count']} 处，"
                         f"{'译文已用该译名' if h['used'] else '译文未见该译名'}）")
        if not b.get("terms"):
            basis.append("术语：本段无术语表命中")
        tok = tokens_of(b["source"], b["role"])
        kept = []
        for k in ("num", "unit", "cite", "figtab", "eqno", "math"):
            if tok[k]:
                kept.append(f"{KIND_ZH[k]} {len(tok[k])}")
        basis.append("保护标记：" + ("、".join(kept) if kept else "本段无"))
        mine = [x for x in problems if x.get("seg") == b["id"]]
        basis.append("核验：" + ("全部通过" if not mine else "；".join(
            f"{x['code']} {x['detail'][:60]}" for x in mine)))
        L += ["**译法依据**", ""] + [f"- {x}" for x in basis] + [""]
        labels = b.get("labels") or []
        if labels:
            L += ["**标注**", ""] + [f"- {x['label']}：{x['detail']}" for x in labels] + [""]
        mc = b.get("manual") or []
        if mc:
            L += ["**需人工确认**", ""] + [f"- {x['kind']}：{x['detail']}" for x in mc] + [""]
    p.write_text("\n".join(L), encoding="utf-8")


def write_brief(p: Path, blocks: list[dict], glossary: dict,
                problems: list[dict]) -> None:
    """给调用方模型的逐段翻译 brief：上下文 + 必用译名 + 必须保留的标记 + 输出格式。"""
    bad = {x["seg"] for x in problems if x.get("seg") and not x.get("acknowledged")}
    need = [b for b in blocks if not (b.get("target") or "").strip()
            or seg_residue(b) >= 0.5
            or b["id"] in bad]
    need_ids = {id(b) for b in need}
    L = ["# 逐段翻译 brief（交给模型用；脚本不翻译）", "",
         f"共 {len(blocks)} 段，其中 {len(need)} 段需要（重新）翻译。"
         "按下面的段号逐段译，译文写进 `targets.txt`，每段前加 `### S001` 这样的段号标记。",
         "",
         "硬约束（写进每条译文）：",
         "1. 术语用「必用译名」列的译名，全文不得换用其它译法（禁用译名不得出现）。",
         "2. 数字、数字-单位、文献引用标记、图表编号、公式编号、行内公式原样保留，不改写、不换算。",
         "3. 否定与限定（not/no/never/may/might/suggest…）在译文里必须仍是同强度的否定/限定。",
         "4. 只译本段，不要合并、不要重排段序；段内句子边界与原文句数尽量一一对应。",
         "5. 译不准、指代不明、材料冲突的部分，在该段后写 `> 未确定：…`，不要含混过去。",
         ""]
    for b in blocks:
        if id(b) not in need_ids:
            continue
        role = ROLE_ZH.get(b["role"], b["role"])
        page = f"第 {b['page']} 页" if b["page"] else "页码未标"
        L += [f"## {b['id']} ｜ {role}"
              + (f" ｜ {b['section']}" if b["section"] else "") + f" ｜ {page}", ""]
        if b.get("prev_tail"):
            L += [f"上段末尾（只作上下文，不要译进本段）：…{flat(b['prev_tail'])}", ""]
        if b.get("next_head"):
            L += [f"下段开头（只作上下文）：{flat(b['next_head'])}…", ""]
        L += ["原文：", "", "```text", b["source"], "```", ""]
        terms = [t for t in (b.get("terms") or []) if t["count"]]
        if terms:
            L.append("必用译名：")
            L += [f"- `{t['term']}` → `{t['to']}`"
                  + (f"（禁用：{'、'.join(_ban(glossary, t['term']))}）"
                     if _ban(glossary, t["term"]) else "") for t in terms]
            L.append("")
        keep = [f"{KIND_ZH[k]}：{'、'.join(tokens_of(b['source'], b['role'])[k])}"
                for k in ("num", "unit", "cite", "figtab", "eqno", "math")
                if tokens_of(b["source"], b["role"])[k]]
        L += ["必须原样保留：" + ("；".join(keep) if keep else "本段无") , ""]
        if b.get("labels"):
            L.append("脚本标注：" + "；".join(x["label"] for x in b["labels"]))
            L.append("")
    L += ["## 回填格式", "", "```text",
          "### S001", "（S001 的中文译文，一段一段按段号写）", "",
          "### S002", "（S002 的中文译文）", "```", "",
          "写好后重跑：`python run.py --input <目录> --targets targets.txt "
          "--work <w> --out <o>`，脚本会重新做 Q01–Q06 与段/句对照。", ""]
    if not need:
        L.insert(2, "当前每段都已有非残留译文且无未确认问题，本文件只作接口说明。")
    p.write_text("\n".join(L), encoding="utf-8")


def _ban(glossary: dict, term: str) -> list[str]:
    spec = glossary.get(term) or {}
    return spec.get("ban") or []


def write_worklist(p: Path, blocks: list[dict], glossary: dict, text: str) -> None:
    rows = ["# 译前底稿（逐段清单；术语未锁定时承担可读性）", "",
            "| 段 | 角色 | 页 | 字符区间 | 句 | 词 | 数字 | 单位 | 引用 | 图表 | 公式 | 否定 | 限定 | 术语命中 | 候选术语 |",
            "|-|-|-|-|-|-|-|-|-|-|-|-|-|-|-|-|"]
    for b in blocks:
        tok = tokens_of(b["source"], b["role"])
        terms = "、".join(f"{h['term']}→{h['to']}" if h["to"] else h["term"]
                          for h in b.get("terms") or []) or "—"
        cand = "、".join(candidate_terms(b["source"])[:3]) or "—"
        rows.append(
            f"| {b['id']} | {ROLE_ZH.get(b['role'], b['role'])} | {b['page'] or '—'} | "
            f"{b['char_start']}–{b['char_end']} | {len(b.get('sentences') or [])} | "
            f"{len(b['source'].split())} | {len(tok['num'])} | {len(tok['unit'])} | "
            f"{len(tok['cite'])} | {len(tok['figtab'])} | {len(tok['eqno'])} | "
            f"{count_neg(b['source'])} | {len(HEDGE_RE.findall(b['source']))} | "
            f"{cell(terms, 60)} | {cell(cand, 60)} |")
    rows += ["", f"- 全文候选术语（去停用词，最长优先）："
                 f"{'、'.join(candidate_terms(text)[:12]) or '无'}",
             "- 术语表格式：`| 原文 | 译名 | 禁用译名(可选) | 备注(可选) |`，见 `rules/conventions.md`。",
             "- 缺术语表时本节即译前底稿：术语无法锁定，`bilingual.md` 的译文区保留原文并标「未译残留」。"]
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")


def write_retranslate(p: Path, blocks: list[dict], problems: list[dict]) -> None:
    need = []
    for b in blocks:
        reasons = []
        for x in problems:
            if x.get("seg") == b["id"] and not x.get("acknowledged"):
                reasons.append(f"{x['code']} {x['detail']}")
        for lb in b.get("labels") or []:
            if lb["label"] in ("未译", "未译残留", "术语未锁定", "禁用译名", "规则草案"):
                reasons.append(f"{lb['label']}：{lb['detail']}")
        if reasons:
            need.append((b, reasons))
    L = ["# 需（重）译清单", "",
         f"{len(need)} 段需要处理。每段给出段号、字符区间、原因与原文，改完写回 "
         "`targets.txt`（`### S001` 标记）后重跑。", ""]
    for b, reasons in need:
        L += [f"## {b['id']} ｜ 字符 {b['char_start']}–{b['char_end']}", ""]
        L += [f"- {r}" for r in reasons]
        L += ["", "原文：", "", "```text", b["source"], "```", ""]
    p.write_text("\n".join(L), encoding="utf-8")


def write_missing(p: Path, root: Path, gaps: list[str], notes: list[str]) -> None:
    L = ["# 缺料说明：没有可切分的原文", "",
         f"- 输入目录：`{root}`",
         "- 找过：case.json 的 material/paper/source/document 字段；目录内 "
         "`.pdf` `.tex` `.docx` `.md` `.txt`（排除 `glossary*` `targets*` "
         "`task.md` `case.json` `SKILL.md`）",
         "- 需要补什么才能出产物：", ""]
    L += [f"  - {g}" for g in (gaps or ["待译原文（.txt/.md 文本层，或可抽取文字层的 .pdf）"])]
    L += ["", "已做的尝试：", ""]
    L += [f"- {n}" for n in notes]
    L += ["", "- 没有原文就无从分段、无段可译，`bilingual.md` / `segments.json` 不生成；"
          "不拿图像或领域标签编造译文。",
          "- 补齐后重跑：`python run.py --input <目录> --work <工作目录> --out <输出目录>`。"]
    p.write_text("\n".join(L) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 主流程
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="文献翻译：段/句对齐、术语锁定、译后核验")
    ap.add_argument("--input", default=None, help="输入目录（含原文/case.json/glossary.md/targets.txt）")
    ap.add_argument("--work", default="work", help="中间产物目录")
    ap.add_argument("--out", default="output", help="交付产物目录")
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--glossary", default=None, help="术语表（Markdown 表格）")
    ap.add_argument("--targets", default=None, help="译文：按段号标记或按空行顺序")
    ap.add_argument("--source", default=None, help="显式指定原文文件")
    a = ap.parse_args(argv)

    t0 = time.perf_counter()
    work, out = Path(a.work), Path(a.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    root = Path(a.input) if a.input else Path(".")

    case = load_case(root)
    case_id = a.case_id or str(case.get("case_id") or case.get("id") or "") or \
        (root.name or "case")
    change: list[dict] = []
    gaps: list[str] = []

    if a.source:
        src_text = read(Path(a.source))
        src_path: Path | None = Path(a.source)
        read_notes = [f"原文由 --source 指定：{a.source}"]
        read_gaps: list[str] = []
    else:
        r = read_source(root, case, work)
        src_text, src_path = r["text"], r["path"]
        read_notes, read_gaps = r["notes"], r["gaps"]
    change += [{"step": "load", "detail": n} for n in read_notes]
    gaps += read_gaps

    if not src_text.strip():
        res = {"case_id": case_id, "skill_version": VERSION,
               "status": "blocked", "status_reason": "没有可切分的原文",
               "gaps": gaps or ["未找到可切分的原文 .txt/.md，PDF 也无法抽取文字层"],
               "uncertainty": gaps, "deliverables": ["missing_input.md"]}
        write_json(out / "result.json", res)
        write_missing(out / "missing_input.md", root, gaps, read_notes)
        print(json.dumps({"ok": False, "status": "blocked"}, ensure_ascii=False))
        return 1

    # ---- 分段 / 角色 / 句子
    blocks = split_blocks(src_text)
    seg_by = "空行切段"
    if not blocks:
        blocks = [{"source": src_text, "char_start": 0, "char_end": len(src_text)}]
    longest = max(len(b["source"]) for b in blocks)
    if (len(blocks) <= max(1, src_text.count("\n") // 40)) or longest > 3000:
        blocks = _segment_by_sentence(src_text)
        seg_by = "长文无空行：按句群切段（每段 ≤4 句/≤1200 字符）"
        gaps.append("原文空行极少或单块过长，已改按句群切段；段边界=句群边界，见 segments.json")
    blocks = classify(blocks, src_text)
    for i, b in enumerate(blocks):
        b["sentences"] = split_sentences(b["source"], b["char_start"],
                                         abbr=("al", "fig", "figs", "eq", "eqs", "ref",
                                               "refs", "eg", "ie", "vs", "approx",
                                               "no", "resp", "cf", "tab", "dr", "st"))
    add_context(blocks)
    change.append({"step": "segment", "detail":
                   f"{len(blocks)} 段 / "
                   f"{sum(len(b['sentences']) for b in blocks)} 句（{seg_by}）"})

    # ---- 术语表
    gl_path = Path(a.glossary) if a.glossary else next(
        iter(sorted(root.rglob("glossary*.md"))), None)
    glossary = load_glossary(gl_path)
    change.append({"step": "glossary", "detail":
                   f"{len(glossary)} 条"
                   + (f"，禁用译名 {sum(1 for v in glossary.values() if v['ban'])} 条"
                      if glossary else "（缺术语表，术语不锁定）")})

    # ---- 译文
    tp = Path(a.targets) if a.targets else next(
        iter(sorted(root.rglob("targets*.txt"))), None)
    targets, mode, tgaps = parse_targets(tp, blocks)
    gaps += tgaps
    for b in blocks:
        b["target"] = targets.get(b["id"], "")
    applied: list[dict] = []
    missing = [b["id"] for b in blocks if not b["target"]]
    if missing:
        draft, applied = auto_translate(blocks, glossary)
        for b in blocks:
            if not b["target"]:
                b["target"] = draft[b["id"]]
        detail = "、".join(f"{x['seg']} 换 {x['count']} 处" for x in applied if x["count"])
        gaps.append("未提供的段由内置规则补草案：" + (detail or "术语表为空，仅段对齐"))
        gaps.append("草案只做术语表最长优先替换，语序与虚词未动，不是模型译文；"
                    "见 `translation_brief.md` 逐段译完再回填")
        change.append({"step": "draft", "detail":
                       f"{len(missing)} 段用内置规则补草案，术语替换 "
                       f"{sum(x['count'] for x in applied)} 处"})
    else:
        change.append({"step": "targets", "detail": f"{len(targets)} 段外部译文（{mode}）"})

    # ---- 核验
    cons = term_check(blocks, glossary)
    problems: list[dict] = []
    for b in blocks:
        problems += compare_segment(b)
    if cons["rate"] is not None and cons["rate"] < 1:
        problems.append({"code": "Q01", "seg": None, "kind": "term",
                         "detail": f"术语一致率 {cons['rate']}，"
                                   f"{len(cons['missed'])} 处未用指定译名",
                         "missed": cons["missed"][:20]})
    if cons["banned"]:
        problems.append({"code": "Q05", "seg": None, "kind": "term",
                         "detail": f"出现禁用译名 {len(cons['banned'])} 处",
                         "banned": cons["banned"][:20]})
    cont = continuity(blocks, src_text)
    if cont["gaps"]:
        problems.append({"code": "Q02", "seg": None, "kind": "continuous",
                         "detail": f"段区间交叠/错位 {len(cont['gaps'])} 处",
                         "items": cont["gaps"][:10]})
    if cont["duplicates"]:
        problems.append({"code": "Q02", "seg": None, "kind": "duplicate",
                         "detail": f"重复段 {len(cont['duplicates'])} 处（疑似漏切/错位）",
                         "items": cont["duplicates"][:10]})
    if cont["coverage"] < 0.999:
        problems.append({"code": "Q02", "seg": None, "kind": "coverage",
                         "detail": f"原文非空白字符覆盖率 {cont['coverage']}，"
                                   "有字符未进入任何段"})
    if targets and missing:
        problems.append({"code": "Q02", "seg": None, "kind": "count",
                         "detail": f"段数不匹配：原文 {len(blocks)}，外部译文 "
                                   f"{len(blocks) - len(missing)}，缺 {','.join(missing[:8])}"})

    # ---- 人工确认清单
    manual: list[dict] = []
    for b in blocks:
        if b["role"] == "reference":
            continue
        rr = seg_residue(b)
        if 0.5 <= rr < 0.8:
            manual.append({"seg": b["id"], "kind": "漏译可能",
                           "detail": f"拉丁词重合 {rr:.0%}，需确认是否漏译"})
        pron = PRONOUN_RE.findall(b["source"])
        if pron and b["role"] != "equation":
            manual.append({"seg": b["id"], "kind": "指代",
                           "detail": f"{len(pron)} 处代词/指示词"
                                     f"（{','.join(sorted(set(w.lower() for w in pron))[:5])}）"
                                     "，指代对象需人工确认（脚本不做指代消解）"})
        if re.search(r"[A-Za-z]-\s*\n\s*[a-z]", b["source"]):
            manual.append({"seg": b["id"], "kind": "断词连字符",
                           "detail": "行末连字符断词，词形与句边界需人工确认"})
        if b["role"] == "reference":
            continue
        nt = len(split_sentences(b["target"], 0))
        if len(b["sentences"]) != nt:
            manual.append({"seg": b["id"], "kind": "句边界",
                           "detail": f"原文 {len(b['sentences'])} 句 / 译文 {nt} 句，"
                                     "句级对应需人工确认"})
        for x in problems:
            if x.get("seg") == b["id"]:
                manual.append({"seg": b["id"], "kind": x["code"],
                               "detail": x["detail"]})

    # ---- 人工确认豁免（外部文件，脚本从不自己写）
    ack_path = next(iter(sorted(root.rglob("acknowledged*.json"))), None)
    acked: list[dict] = []
    if ack_path and ack_path.exists():
        try:
            data = json.loads(read(ack_path))
            for item in (data if isinstance(data, list) else data.get("items", [])):
                if not isinstance(item, dict) or not item.get("reason"):
                    continue
                code, seg = item.get("code"), item.get("seg")
                for pr in problems:
                    if pr["code"] == code and (seg in (None, "", pr.get("seg"))):
                        pr["acknowledged"] = True
                acked.append({"code": code, "seg": seg, "reason": item["reason"]})
            if acked:
                change.append({"step": "acknowledge", "detail":
                               f"外部确认 {len(acked)} 条（来自 {ack_path.name}，附理由）"})
        except Exception as e:  # noqa: BLE001
            gaps.append(f"{ack_path.name} 解析失败，未应用任何确认：{e}")

    # ---- 状态
    residue = [b["id"] for b in blocks if is_residue(b)]
    unacked = [p for p in problems if not p.get("acknowledged")]
    if unacked:
        status, reason = "partial", f"{len(unacked)} 条核验未通过（Q 码见 checks）"
    elif residue:
        status, reason = "partial", (f"{len(residue)} 段译文与原文同形（未译残留），"
                                     "不是可用译文")
    elif any(not b["target"] for b in blocks):
        status, reason = "partial", "仍有段无译文"
    else:
        status, reason = "success", "每段都有译文，Q01–Q06 全部通过"
    if residue:
        gaps.append(f"{len(residue)} 段译文与原文同形（未译残留）：{','.join(residue[:8])}；"
                    "不写成成功，按 `translation_brief.md` 逐段译完再回填")

    # ---- 逐段记录
    records = build_records(blocks, applied, mode, src_text)
    by_id = {r["id"]: r for r in records}
    for b in blocks:
        b["labels"] = by_id[b["id"]]["labels"]
        b["manual"] = by_id[b["id"]]["manual_check"]

    seg_out = {"case_id": case_id, "skill_version": VERSION, "count": len(blocks),
               "segmented_by": seg_by,
               "pages": (1 + src_text.count("\x0c")) if "\x0c" in src_text else None,
               "coverage": cont["coverage"], "segments": []}
    for b in blocks:
        tgt_sents = split_sentences(b["target"], 0)
        seg_out["segments"].append({
            "id": b["id"], "role": b["role"], "section": b["section"],
            "page": b["page"], "char_start": b["char_start"], "char_end": b["char_end"],
            "prev_tail": b["prev_tail"], "next_head": b["next_head"],
            "source": b["source"], "target": b.get("target", ""),
            "sentences": [{"sid": f"{b['id']}.{i + 1:02d}",
                           "char_start": s["char_start"], "char_end": s["char_end"],
                           "source": s["source"],
                           "target": (tgt_sents[i]["source"] if i < len(tgt_sents) else None)}
                          for i, s in enumerate(b["sentences"])],
            "term_hits": b.get("terms", []),
            "protect": tokens_of(b["source"], b["role"]),
        })
    write_json(out / "segments.json", seg_out)
    write_json(work / "segments.json", seg_out)

    verify = {
        "case_id": case_id, "skill_version": VERSION, "rules_version": RULES_VERSION,
        "term_consistency": cons,
        "profile_src": {k: len(v) for k, v in
                        tokens_of(src_text).items()},
        "profile_tgt": {k: len(v) for k, v in
                        tokens_of("\n\n".join(b["target"] for b in blocks)).items()},
        "per_segment": [{"id": b["id"], "role": b["role"], "page": b["page"],
                         "src": {k: len(v) for k, v in
                                 tokens_of(b["source"], b["role"]).items()},
                         "tgt": {k: len(v) for k, v in
                                 tokens_of(b["target"], b["role"]).items()},
                         "ok": not any(x.get("seg") == b["id"] and
                                       not x.get("acknowledged") for x in problems)}
                        for b in blocks],
        "structure": {
            "by_role": {r: sum(1 for b in blocks if b["role"] == r)
                        for r in sorted({b["role"] for b in blocks})},
            "sections": sorted({b["section"] for b in blocks if b["section"]}),
            "captions": {"fig": sum(1 for b in blocks if b["role"] == "caption_fig"),
                         "tab": sum(1 for b in blocks if b["role"] == "caption_tab")},
            "tables": sum(1 for b in blocks if b["role"] == "table"),
            "equations": [{"seg": b["id"], "eqno": tokens_of(b["source"], "equation")["eqno"]}
                          for b in blocks if b["role"] == "equation"],
            "references": sum(1 for b in blocks if b["role"] == "reference")},
        "continuity": cont,
        "problems": problems,
        "acknowledged": acked,
        "manual_check": manual,
        "labels": [dict(seg=r["id"], **lb) for r in records for lb in r["labels"]],
        "stats": {"segments": len(blocks),
                  "sentences": sum(len(b["sentences"]) for b in blocks),
                  "terms": len(glossary), "problems": len(unacked),
                  "acknowledged": len(acked), "manual_check": len(manual)},
    }
    write_json(out / "verify.json", verify)

    write_bilingual(out / "bilingual.md", blocks, glossary, cons, problems)
    write_brief(out / "translation_brief.md", blocks, glossary, problems)
    write_worklist(out / "translation_worklist.md", blocks, glossary, src_text)
    if problems or residue or [b for b in blocks if not b["target"]]:
        write_retranslate(out / "retranslate.md", blocks, problems)

    # ---- result.json
    deliverables = ["bilingual.md", "verify.json", "segments.json",
                    "translation_brief.md", "translation_worklist.md"]
    if (out / "retranslate.md").exists():
        deliverables.append("retranslate.md")
    contract = echo_contract(case.get("output_contract") or case.get("outputContract"),
                             {
                                 "case_id": case_id, "status": status,
                                 "阅读/解析结果": True, "证据与边界": True,
                                 "运行状态": True, "checks": problems,
                                 "gaps": gaps, "uncertainty": gaps,
                                 "change_records": change, "stats": verify["stats"],
                             })
    inputs = []
    if src_path and src_path.is_file():
        inputs.append({"path": str(src_path), "bytes": src_path.stat().st_size,
                       "sha256": sha256(src_path)})
    for extra, why in ((gl_path, "glossary"), (tp, "targets"), (ack_path, "acknowledged")):
        if extra and Path(extra).is_file():
            inputs.append({"path": str(extra), "role": why,
                           "bytes": Path(extra).stat().st_size,
                           "sha256": sha256(Path(extra))})
    manifest = {
        "skill": "bilingual-paper-translator", "skill_version": VERSION,
        "rules_version": RULES_VERSION, "python": platform.python_version(),
        "command": "python run.py " + " ".join(argv if argv is not None else []),
        "params": {"input": str(root), "work": str(work), "out": str(out),
                   "glossary": str(gl_path) if gl_path else None,
                   "targets": str(tp) if tp else None},
        "inputs": inputs,
        "outputs": [{"name": f, "bytes": (out / f).stat().st_size,
                     "sha256": sha256(out / f)}
                    for f in deliverables if (out / f).exists()],
        "elapsed_s": round(time.perf_counter() - t0, 3),
        "note": "result.json 自身不计入 outputs 哈希；重跑同样输入应得到同样的段落/标记计数",
    }
    result = {
        "case_id": case_id, "skill_version": VERSION, "status": status,
        "status_reason": reason,
        "阅读/解析结果": {
            "segments": len(blocks),
            "sentences": verify["stats"]["sentences"],
            "external_translated": len(targets),
            "draft_translated": len(missing),
            "untranslated": [b["id"] for b in blocks if not b["target"]],
            "residue_segments": residue,
            "glossary_terms": len(glossary),
            "term_rate": cons["rate"],
            "roles": verify["structure"]["by_role"],
            "bilingual": "bilingual.md", "segments_json": "segments.json",
            "brief": "translation_brief.md", "worklist": "translation_worklist.md"},
        "证据与边界": {
            "unit": "seg_id + 句号 + 字符区间（左闭右开，UTF-8 字符下标）+ 页码（原文有分页符时）",
            "anchors": [{"seg_id": b["id"], "role": b["role"], "page": b["page"],
                         "char_start": b["char_start"], "char_end": b["char_end"],
                         "sentences": [{"sid": f"{b['id']}.{i + 1:02d}",
                                        "char_start": s["char_start"],
                                        "char_end": s["char_end"]}
                                       for i, s in enumerate(b["sentences"])]}
                        for b in blocks],
            "gaps": gaps, "manual_check": manual,
            "coverage": cont["coverage"], "cross_page": cont["cross_page"]},
        "运行状态": {"status": status, "reason": reason,
                    "handover": "retranslate.md" if (out / "retranslate.md").exists()
                    else "无需接管",
                    "acknowledged": acked},
        "output_contract": contract,
        "checks": problems,
        "labels": verify["labels"],
        "manual_check": manual,
        "gaps": gaps, "uncertainty": gaps,
        "change_records": change,
        "manifest": manifest,
        "stats": {**verify["stats"], "glossary": len(glossary),
                  "elapsed_s": round(time.perf_counter() - t0, 3)},
        "deliverables": deliverables,
    }
    write_json(out / "result.json", result)
    print(json.dumps({"ok": True, "status": status, "segments": len(blocks),
                      "sentences": verify["stats"]["sentences"],
                      "translated": len(targets), "draft": len(missing),
                      "residue": len(residue), "problems": len(unacked),
                      "term_rate": cons["rate"]}, ensure_ascii=False))
    return 0


def _segment_by_sentence(text: str) -> list[dict]:
    """长文无空行时的兜底切分：按句群切，每段 ≤4 句且 ≤1200 字符。"""
    sents = split_sentences(text, 0,
                            abbr=("al", "fig", "figs", "eq", "eqs", "ref", "refs",
                                  "eg", "ie", "vs", "approx", "no", "resp", "cf", "tab"))
    blocks, cur = [], []
    for s in sents:
        cur.append(s)
        span = s["char_end"] - cur[0]["char_start"]
        if len(cur) >= 4 or span >= 1200:
            blocks.append({"source": text[cur[0]["char_start"]:s["char_end"]],
                           "char_start": cur[0]["char_start"], "char_end": s["char_end"]})
            cur = []
    if cur:
        blocks.append({"source": text[cur[0]["char_start"]:cur[-1]["char_end"]],
                       "char_start": cur[0]["char_start"],
                       "char_end": cur[-1]["char_end"]})
    return blocks


if __name__ == "__main__":
    raise SystemExit(main())
