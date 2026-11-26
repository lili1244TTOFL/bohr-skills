#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_gbt.py — GB/T 7714—2015 顺序编码制格式检查与条目渲染。

两种模式：
  --refs refs.txt        检查稿件文末参考文献表（每行一条，可带 [n]）
  --lib  library.json    检查文献库条目，并按国标渲染成条目文本（供 5.3 生成文献表）

错误码 F01–F19，severity = error / warn / info。只有 error 影响退出码。

用法：
  python check_gbt.py --refs work/refs.txt --json work/out/gbt_report.json
  python check_gbt.py --lib work/lib/library.json --render work/out/refs_rendered.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ALLOWED = {"M", "J", "C", "D", "N", "R", "S", "P", "A", "Z", "DB", "CP", "EB"}
CARRIER = {"OL", "CD", "MT", "DK"}
SEV = {
    "F01": "error", "F02": "error", "F03": "error", "F04": "warn", "F05": "error",
    "F06": "error", "F07": "error", "F08": "error", "F09": "warn", "F10": "error",
    "F11": "warn", "F12": "error", "F13": "warn", "F14": "error", "F15": "error",
    "F16": "error", "F17": "warn", "F18": "error", "F19": "info", "F20": "error",
}


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def err(code: str, note: str) -> dict:
    return {"code": code, "severity": SEV[code], "note": note}


TAG_RE = re.compile(r"\[([A-Z]{1,2})(?:/([A-Z]{2}))?\]")
CJK = re.compile(r"[\u4e00-\u9fff]")


# --------------------------------------------------------------- 作者
def gbt_authors(names: list[str]) -> tuple[str, list[dict]]:
    """作者列表 → 国标写法 + 问题。英文：LASTNAME I；中文：原名。

    两种西文写法都要认：
      "Smith, J."        姓, 名（BibTeX/RIS 导出）
      "SMITH J"          姓 名首字母（国标条目原样，不得当成“名 姓”）
    """
    issues, out = [], []
    for raw in names:
        n = raw.strip()
        if not n:
            continue
        if CJK.search(n):
            out.append(re.sub(r"\s+", "", n))
            continue
        if "," in n:
            surname, given = [x.strip() for x in n.split(",", 1)]
        else:
            parts = n.split()
            initials: list[str] = []
            while len(parts) > 1 and re.fullmatch(r"[A-Z]{1,2}\.?", parts[-1]):
                initials.insert(0, parts.pop())
            if initials:
                surname, given = " ".join(parts), " ".join(initials)
            else:
                surname, given = parts[-1], " ".join(parts[:-1])
                if re.fullmatch(r"[A-Z][a-z]{2,}", given.split()[0] if given else ""):
                    issues.append(err("F13", f"作者写作“名 姓”：{n}"))
        initials = " ".join(w[0].upper() for w in re.split(r"[\s.\-]+", given) if w)
        out.append(f"{surname.upper()} {initials}".strip())
    if len(out) > 3:
        tail = "等" if CJK.search(" ".join(out)) else "et al"
        return ", ".join(out[:3]) + f", {tail}", issues
    return ", ".join(out), issues


# --------------------------------------------------------------- 渲染
def _pages(p: str) -> str:
    p = (p or "").strip().replace("--", "-").replace("–", "-")
    return p


def page_order(p: str) -> tuple[int, int] | None:
    """起止页倒序时返回 (起, 止)，否则 None。"""
    m = re.fullmatch(r"[A-Za-z]?(\d+)-[A-Za-z]?(\d+)", _pages(p) if p else "")
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return (a, b) if a > b else None


def render(e: dict) -> str:
    au, _ = gbt_authors(e.get("authors") or [])
    au = au + ". " if au else ""
    title = (e.get("title") or "").strip().rstrip(".")
    t = e.get("type", "Z")
    yr, vol, iss = e.get("year", ""), e.get("volume", ""), e.get("issue", "")
    pg, doi, place = _pages(e.get("pages", "")), e.get("doi", ""), e.get("place", "")
    pub, cont = e.get("publisher", ""), e.get("container", "")

    if t == "J":
        vp = []
        if vol:
            vp.append(str(vol))
        if iss:
            vp.append(f"({iss})")
        body = f"{au}{title}[J]. {cont}, {yr}"
        if vp:
            body += ", " + "".join(vp)
        if pg:
            body += f": {pg}"
        body += "."
        if doi:
            body += f" DOI:{doi}."
        return body
    if t == "M":
        body = f"{au}{title}[M]."
        if e.get("edition"):
            body += f" {e['edition']}版."
        body += f" {place}: {pub}, {yr}"
        if pg:
            body += f": {pg}"
        return body + "."
    if t == "D":
        return f"{au}{title}[D]. {place}: {pub}, {yr}."
    if t == "C":
        ed = (e.get("editor", "") or "").strip()
        head = f"{au}{title}[C]//" + (f"{ed}. " if ed else "")
        return f"{head}{cont}. {place}: {pub}, {yr}" + (f": {pg}" if pg else "") + "."
    if t == "S":
        return f"{title}[S]. {place}: {pub}, {yr}."
    if t in ("EB", "EB/OL"):
        date = (e.get("date", "") or "").strip()
        if not date and yr:
            date = str(yr)
        cite = (e.get("cite", "") or "").strip() or "引用日期待补"
        return f"{au}{title}[EB/OL]. ({date})[{cite}]. {e.get('url', '')}."
    if t == "N":
        return f"{au}{title}[N]. {cont}, {yr}-{e.get('md', '01')}-{e.get('d', '01')}({e.get('issue', '1')})."
    tail = [x for x in (cont, yr) if x]
    return f"{au}{title}[{t}]. " + (", ".join(tail) + "." if tail else "")


# --------------------------------------------------------------- 字段检查
def check_entry(e: dict) -> list[dict]:
    """文献库条目检查：字段级 + 渲染后文本的标点级。"""
    out: list[dict] = []
    text = render(e)
    t = e.get("type", "")
    if not t:
        out.append(err("F01", "缺文献类型标识"))
    elif t == "Z" and not TAG_RE.search(e.get("raw", "") or ""):
        out.append(err("F01", "原始条目无类型标识，类型按 Z 兜底"))
    elif t not in {x for x in ALLOWED} | {"EB/OL"}:
        out.append(err("F02", f"类型标识不合法：{t}"))

    if not (e.get("title") or "").strip():
        out.append(err("F06", "缺题名"))
    if not e.get("authors"):
        if t not in {"S", "EB", "EB/OL"}:
            out.append(err("F03", "缺作者"))
    else:
        for a in e["authors"]:
            if re.search(r"[;、]", a) or re.search(r"\sand\s", a):
                out.append(err("F04", f"作者字段含分隔符：{a}"))
        _, ai = gbt_authors(e["authors"])
        out.extend(ai)

    yr = str(e.get("year", ""))
    if not re.fullmatch(r"(18|19|20)\d{2}", yr):
        out.append(err("F08", "缺年份或年份格式错"))
    if t == "J":
        if not e.get("container"):
            out.append(err("F07", "期刊条目缺刊名"))
        if not e.get("volume") and not e.get("issue"):
            out.append(err("F09", "期刊条目缺卷或期"))
        if not e.get("pages"):
            out.append(err("F10", "缺起止页"))
        elif not re.fullmatch(r"[A-Za-z]?\d+(?:-[A-Za-z]?\d+)?", _pages(e["pages"])):
            out.append(err("F10", f"页码格式异常：{e['pages']}"))
        order = page_order(e["pages"])
        if order:
            out.append(err("F20", f"页码倒序：{order[0]}-{order[1]}"))
    if t == "D" and (not e.get("publisher") or not e.get("place")):
        out.append(err("F14", "学位论文缺保存地或保存单位"))
    if t in ("EB", "EB/OL"):
        if not e.get("url"):
            out.append(err("F15", "电子资源缺 URL"))
        if not re.search(r"\[\d{4}-\d{2}-\d{2}\]", text):
            out.append(err("F15", "电子资源缺引用日期"))
    if t == "C" and not e.get("container"):
        out.append(err("F16", "会议论文缺论文集名"))
    if t == "C" and "//" not in text:
        out.append(err("F16", "会议论文缺 //"))

    doi = (e.get("doi") or "").strip()
    if doi and not re.match(r"^10\.\d{4,9}/\S+$", doi):
        out.append(err("F12", f"DOI 格式错：{doi}"))
    if re.search(r"DOI:\s+10\.", text):
        out.append(err("F12", "DOI 后不应有空格"))

    if re.search(r"[，．：；]", text.replace((e.get("title") or ""), "")):
        out.append(err("F11", "条目含全角标点"))
    if re.search(r"[\u4e00-\u9fff],(?!\s)", text):
        out.append(err("F11", "逗号后缺空格"))
    if re.search(r"\.\s*\.", text) or text.rstrip().endswith(".."):
        out.append(err("F17", "条目末标点重复"))
    elif text.strip() and not text.rstrip().endswith("."):
        out.append(err("F17", "条目末缺句点"))

    if re.fullmatch(r"(19|20)\d\d", str(e.get("volume", ""))) and e.get("issue"):
        out.append(err("F19", "卷号为四位数字，疑似与年份错位"))
    return out


# --------------------------------------------------------------- 整行检查（稿件参考文献表）
def check_line(line: str) -> tuple[list[dict], str]:
    out: list[dict] = []
    body = re.sub(r"^\s*\[\d+\]\s*", "", line).strip()
    if not body:
        return [err("F06", "空条目")], ""
    m = TAG_RE.search(body)
    if not m:
        return [err("F01", "缺文献类型标识")], body
    tag = m.group(1)
    if tag not in ALLOWED:
        out.append(err("F02", f"类型标识不合法：[{tag}]"))
    head, tail = body[:m.start()], body[m.end():]
    if head.rstrip().endswith("."):
        out.append(err("F11", "题名后不应加句点"))
    if not tail.startswith("."):
        out.append(err("F11", "类型标识后缺句点"))
    au_block = head.split(".")[0] if head else ""
    if not au_block.strip() and tag not in {"S"}:
        out.append(err("F03", "缺作者"))
    if re.search(r"[，．：；]", body):
        out.append(err("F11", "条目含全角标点"))
    if not re.search(r"(18|19|20)\d{2}", tail):
        out.append(err("F08", "缺年份"))
    if tag == "J":
        if not re.match(r"^\s*[.．]?\s*\S.*,\s*(18|19|20)\d{2}", tail):
            out.append(err("F07", "期刊条目刊名或年份格式错"))
        if not re.search(r"\b(18|19|20)\d{2}\b\s*,\s*\d+", tail) \
                and not re.search(r"\(\s*\d+\s*\)", tail):
            out.append(err("F09", "缺卷或期"))
        if not re.search(r":\s*\d", tail):
            out.append(err("F10", "缺起止页"))
        pm = re.search(r":\s*([A-Za-z]?\d+(?:-[A-Za-z]?\d+)?)", tail)
        if pm and page_order(pm.group(1)):
            a, b = page_order(pm.group(1))
            out.append(err("F20", f"页码倒序：{a}-{b}"))
        if re.search(r"DOI:\s", tail):
            out.append(err("F12", "DOI 后多空格"))
        if re.search(r"doi\.org", tail, re.I) and "DOI:" not in tail:
            out.append(err("F12", "DOI 未用 DOI: 形式"))
    if tag == "D" and not re.search(r"[:：]\s*\S+,\s*(18|19|20)\d{2}", tail):
        out.append(err("F14", "学位论文缺保存地或保存单位"))
    if tag == "C" and "//" not in body:
        out.append(err("F16", "会议论文缺 //"))
    if tag in ("EB", "EB/OL"):
        if not re.search(r"\(\d{4}-\d{2}-\d{2}\)\[\d{4}-\d{2}-\d{2}\]", body):
            out.append(err("F15", "电子资源缺日期对 (更新日期)[引用日期]"))
        if not re.search(r"https?://", body):
            out.append(err("F15", "电子资源缺 URL"))
    if body.rstrip().endswith(".."):
        out.append(err("F17", "条目末标点重复"))
    elif not re.search(r"[.．]\s*$", body):
        out.append(err("F17", "条目末缺句点"))
    return out, body


# --------------------------------------------------------------- 主流程
def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="GB/T 7714—2015 格式检查")
    ap.add_argument("--refs", help="稿件参考文献表文本")
    ap.add_argument("--lib", help="library.json")
    ap.add_argument("--render", help="渲染结果输出文件（配合 --lib）")
    ap.add_argument("--json", help="检查报告输出文件")
    args = ap.parse_args(argv)
    if not args.refs and not args.lib:
        ap.error("需要 --refs 或 --lib")

    report = {"generated_by": "check_gbt.py", "mode": "refs" if args.refs else "lib",
              "n": 0, "errors": [], "counts": {}, "rendered": []}

    if args.refs:
        text = Path(args.refs).read_text(encoding="utf-8", errors="replace")
        lines = [l for l in text.splitlines() if l.strip()]
        seen = {}
        for i, line in enumerate(lines, 1):
            num = re.match(r"^\s*\[(\d+)\]", line)
            if num:
                k = num.group(1)
                if k in seen:
                    report["errors"].append({"line": i, "code": "F18",
                                             "severity": "error", "note": f"编号重复 {k}"})
                seen[k] = i
            issues, body = check_line(line)
            for x in issues:
                report["errors"].append({"line": i, **x})
        report["n"] = len(lines)
    else:
        lib = json.loads(Path(args.lib).read_text(encoding="utf-8"))
        ents = lib.get("entries", [])
        report["n"] = len(ents)
        ids = {}
        for e in ents:
            for x in check_entry(e):
                report["errors"].append({"id": e.get("id"), **x})
            if e.get("id") in ids:
                report["errors"].append({"id": e.get("id"), "code": "F18",
                                         "severity": "error", "note": "条目 id 重复"})
            ids[e.get("id")] = 1
            report["rendered"].append({"id": e.get("id"), "text": render(e)})
        if args.render:
            Path(args.render).parent.mkdir(parents=True, exist_ok=True)
            Path(args.render).write_text(
                "\n".join(f"[{i}] {r['text']}" for i, r in enumerate(report["rendered"], 1)) + "\n",
                encoding="utf-8")

    counts = {}
    for x in report["errors"]:
        counts[x["code"]] = counts.get(x["code"], 0) + 1
    report["counts"] = counts
    report["n_error"] = sum(1 for x in report["errors"] if x["severity"] == "error")
    report["n_warn"] = sum(1 for x in report["errors"] if x["severity"] == "warn")
    report["n_info"] = sum(1 for x in report["errors"] if x["severity"] == "info")
    report["ok"] = report["n_error"] == 0

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "n": report["n"], "counts": counts,
                      "n_error": report["n_error"], "n_warn": report["n_warn"]},
                     ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


