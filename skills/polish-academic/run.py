#!/usr/bin/env python3
"""run.py —— 学术语言润色：问题检测、改写核验、逐句记录。

设计原则：文件由参数显式指定，不靠扫描猜；凡是"数一遍"的检查，两侧口径完全一致。

分工：脚本检测硬问题、核对改写有没有改坏；改写由调用方模型完成。

题面四关注点与检查码的对应：
    改得对  D 组：语法、搭配、冗余、口语、长句、名词化、时态
    说得像  T 组：术语表锁定，译名/写法全篇一致
    没改坏  K 组：数字、单位、引用、图号、否定、限定强度
    讲得清  changes.md：逐条原文片段、类型、置信度、理由
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

VERSION = "3.0.0"

# ---------------- 问题检测：中英各一套，避免只覆盖一种语言 ----------------
DETECT = [
    ("D01", "主谓一致", r"\b(data|results?)\s+was\b", 0.9, "data/results 为复数"),
    ("D02", "被动堆叠", r"\b(was|were)\s+\w+ed\b[^.]{0,40}\b(was|were)\s+\w+ed\b", 0.7, "一句内两处被动"),
    ("D03", "模糊加强", r"\b(clearly|obviously|extremely|very)\b|非常|极其|显然", 0.8, "加强词可删或换具体量"),
    ("D04", "名词化堆叠", r"\bthe\s+\w+tion\s+of\s+the\s+\w+\s+of\b", 0.7, "多层 of 结构改动词"),
    ("D05", "冗余短语", r"\b(in order to|due to the fact that|it is worth noting that)\b", 0.9, "可省为更短表达"),
    ("D06", "口语词", r"\b(a lot of|big|huge|stuff|get)\b", 0.8, "学术文体应换书面词"),
    ("D07", "长句", None, 0.6, "超过阈值应切分"),
    ("D08", "时态混用", r"\b(will be|is)\s+\w+ed\b[^.]{0,50}\b(was|were)\b", 0.7, "同句时态需统一"),
    ("D09", "中式表达", r"进行(了)?[^。，]{0,8}(研究|分析|探讨)|具有[^。，]{0,8}(意义|作用)|众所周知|值得一提的是", 0.8, "空转铺垫或翻译腔"),
    ("D10", "绝对化", r"\b(always|never|all|none|proves?)\b|必然|完全|一定", 0.6, "绝对表述需限定"),
]

CITE = re.compile(r"\[\d+(?:[-,]\d+)*\]")
FIGREF = re.compile(r"\b(?:Fig(?:ure)?\.?\s*\d+|Table\s*\d+|Eq\.?\s*\(?\d+)\b", re.I)
UNIT = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|g|kg|mL|ml|L|mm|cm|m|s|ms|h|min|Hz|kHz|GHz|°C|K|mol|mM|μM|nM)\b")
NUM = re.compile(r"\d+(?:\.\d+)?%?")
BOOST = re.compile(r"\b(significantly|substantially|markedly|considerably|strongly|highly"
                   r"|clearly|obviously|very|extremely)\b|显著|明显|大幅|非常|极其")
HEDGE = re.compile(r"\b(may|might|could|possibly|likely|suggest|appears?|tends? to)\b|可能|或许|倾向于")
NEG = re.compile(r"\b(not|no|never|none|cannot|without)\b|不|无|未|没有")
LONG_WORDS = 40
LONG_CHARS = 80


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


def load_glossary(p: Path | None) -> dict:
    """术语表：Markdown 两列表。取不到返回空表，不猜。"""
    g = {}
    if not p or not p.exists():
        return g
    for line in read(p).splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        if cells[0].lower() in ("原文", "source", "en", "术语"):
            continue
        g[cells[0]] = cells[1]
    return g


def strip_citations(t: str) -> str:
    """数数字前先去掉引用标记，避免引用编号被重复计成正文数字。"""
    return CITE.sub(" ", FIGREF.sub(" ", t))


def sentences(t: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[。！？；.!?;])\s*", t) if s.strip()]


def locate(text: str, pos: int) -> tuple[int, int]:
    """把命中位置换成（段号, 句号），供回原稿检索；不提供字符偏移。"""
    para = text[:pos].count("\n\n") + 1
    sent = len([s for s in re.split(r"(?<=[。！？；.!?;])\s*", text[:pos]) if s.strip()])
    return para, sent


def detect(text: str) -> list[dict]:
    out = []
    for code, name, pat, conf, why in DETECT:
        if code == "D07":
            for s in sentences(text):
                if len(s.split()) > LONG_WORDS or len(s) > LONG_CHARS:
                    para, sent = locate(text, max(text.find(s), 0))
                    out.append({"code": code, "name": name, "span": s[:70],
                                "para": para, "sent": sent,
                                "confidence": conf, "reason": why})
            continue
        for m in re.finditer(pat, text, re.I):
            para, sent = locate(text, m.start())
            out.append({"code": code, "name": name, "span": m.group(0)[:70],
                        "para": para, "sent": sent,
                        "confidence": conf, "reason": why})
    return out


REWRITE = [
    (r"\bIn order to\b", "To"),
    (r"\ba lot of\b", "many"),
    (r"\bbig\b", "large"),
    (r"\bhuge\b", "large"),
    (r"\bthe data was\b", "the data were"),
    (r"\bthe results was\b", "the results were"),
    (r"\bdue to the fact that\b", "because"),
    (r"\bit is worth noting that\b ", ""),
    (r"\bvery \s+", ""),
    (r"\bclearly \s+", ""),
]


def auto_rewrite(text: str) -> tuple[str, list]:
    """无改后稿时，用规则改出可交付的版本；只做词面修复，不动结论强度以外的东西。"""
    out, applied = text, []
    for pat, rep in REWRITE:
        hits = re.findall(pat, out, re.I)
        if hits:
            out = re.sub(pat, rep, out, flags=re.I)
            applied.append({"pattern": pat, "replacement": rep, "count": len(hits)})
    out = re.sub(r"\\s{2,}", " ", out)
    return out, applied


def profile(text: str) -> dict:
    """两侧用同一口径：先剥引用与图表指向，再数数字。"""
    body = strip_citations(text)
    return {
        "num": len(NUM.findall(body)),
        "unit": len(UNIT.findall(body)),
        "cite": len(CITE.findall(text)),
        "fig": len(FIGREF.findall(text)),
        "boost": len(BOOST.findall(text)),
        "hedge": len(HEDGE.findall(text)),
        "neg": len(NEG.findall(text)),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="原稿文件（.md/.txt）")
    ap.add_argument("--polished", default=None, help="改后文件；不给则只出问题清单")
    ap.add_argument("--glossary", default=None, help="术语表（Markdown 两列表）")
    ap.add_argument("--out", default="output")
    ap.add_argument("--work", default="work")
    ap.add_argument("--case-id", default=None)
    a = ap.parse_args(argv)

    t0 = time.perf_counter()
    src_p = Path(a.source)
    if not src_p.exists():
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        res = {"case_id": a.case_id or src_p.stem, "status": "blocked",
               "gaps": [f"原文不存在：{src_p}"], "uncertainty": ["无输入"]}
        (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked"}, ensure_ascii=False))
        return 1

    out, work = Path(a.out), Path(a.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    case_id = a.case_id or src_p.stem
    # 平台形态：case.json 与原稿同目录，带 case_id 与 output_contract
    contract: list = []
    cj = src_p.parent / "case.json"
    if cj.exists():
        try:
            case = json.loads(read(cj))
            case_id = a.case_id or str(case.get("case_id") or case.get("id") or case_id)
            oc = case.get("output_contract")
            contract = list(oc) if isinstance(oc, (dict, list)) else []
        except Exception:
            pass

    src = read(src_p)
    glossary = load_glossary(Path(a.glossary) if a.glossary else None)
    issues = detect(src)

    pol = read(Path(a.polished)) if a.polished and Path(a.polished).exists() else ""
    gaps, problems = [], []
    applied = []
    if not pol:
        # 没有外部改后稿时自己改，交一件真产物，而不是返回待办状态
        pol, applied = auto_rewrite(src)
        gaps.append("改后稿由内置规则生成" if applied else "原文未命中可机械修复的模式")
    for g in (["改后稿由内置规则生成，规则明细见 事实与来源映射"] if applied else []):
        gaps.append(g)
    if not pol:
        gaps.append("未产出改后文本")
    if not glossary:
        gaps.append("未提供术语表：术语一致性未核验")

    drift = {}
    if pol:
        ps, pp = profile(src), profile(pol)
        for k, label in (("num", "数字"), ("unit", "单位"), ("cite", "引用"),
                         ("fig", "图表指向"), ("neg", "否定")):
            if ps[k] != pp[k]:
                drift[k] = {"source": ps[k], "polished": pp[k]}
                problems.append({"code": "K01",
                                 "detail": f"{label}数量变化：原文 {ps[k]} → 改后 {pp[k]}"})
        if pp["boost"] < ps["boost"] or pp["hedge"] < ps["hedge"]:
            problems.append({"code": "K02", "detail":
                             f"限定强度被削弱：加强 {ps['boost']}→{pp['boost']}，"
                             f"留有余地 {ps['hedge']}→{pp['hedge']}"})
        if glossary:
            missed = [s for s, t in glossary.items() if s in src and t not in pol]
            if missed:
                problems.append({"code": "T01", "detail": "术语译名不一致：" + "、".join(missed[:6])})

    status = "success" if pol and not problems else ("partial" if pol else "needs_polish")

    (work / "issues.json").write_text(json.dumps(
        {"case_id": case_id, "source": str(src_p), "issues": issues,
         "counts": {c: sum(1 for i in issues if i["code"] == c) for c, *_ in DETECT}},
        ensure_ascii=False, indent=1), encoding="utf-8")

    (out / "polished.md").write_text(pol if pol else src, encoding="utf-8")
    rows = ["| # | 码 | 类型 | 原文片段 | 位置 | 理由 | 置信度 |", "|-|-|-|-|-|-|-|"]
    for i, x in enumerate(issues, 1):
        rows.append(f"| {i} | {x['code']} | {x['name']} | `{x['span']}` | "
                    f"段{x['para']}·句{x['sent']} | {x['reason']} | {x['confidence']} |")
    (out / "changes.md").write_text("# 修改记录\n\n" + "\n".join(rows) + "\n", encoding="utf-8")

    result = {
        "case_id": case_id,
        "skill_version": VERSION,
        "status": status,
        "目标产物": {"path": "polished.md", "字符数": len(pol or src),
                   "句数": len(sentences(pol or src)),
                   "改后稿已给": bool(pol),
                   "内容": "改后全文" if pol else "未提供改后稿，回填原稿"},
        "事实与来源映射": {
            "证据单位": "code + span（问题码 + 原文片段）+ 段号/句号",
            "规则命中": applied,
            "条数": len(issues),
            "逐条": [{"码": x["code"], "类型": x["name"], "原文片段": x["span"],
                     "段": x["para"], "句": x["sent"], "理由": x["reason"],
                     "置信度": x["confidence"]} for x in issues],
            "术语表条数": len(glossary),
            "改后稿计数": profile(pol) if pol else None,
        },
        "版本与边界": {"gaps": gaps, "problems": problems, "drift": drift},
        "stats": {"issues": len(issues), "polished": bool(pol),
                  "glossary_terms": len(glossary),
                  "profile_source": profile(src),
                  "profile_polished": profile(pol) if pol else None,
                  "elapsed_s": round(time.perf_counter() - t0, 3)},
        "checks": problems,
        "gaps": gaps,
        "uncertainty": gaps,
        "deliverables": ["polished.md", "changes.md", "issues.json"],
        "output_contract": contract,
    }
    # 题面：result.json 含全部 output_contract 字段。三项固定产出先占位，
    # 认不得的字段名置 null 并记进 gaps（gaps 与 uncertainty 是同一个列表）。
    for k in contract:
        if str(k) in result:
            continue
        result[str(k)] = None
        gaps.append(f"output_contract 字段「{k}」无对应产出，置 null")
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "issues": len(issues),
                      "polished": bool(pol), "problems": len(problems)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
