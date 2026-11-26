#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""styles.py — 参考文献样式渲染：GB/T 7714—2015 / APA 7 / IEEE。

只做“能确定”的映射：字段缺失就留空并标 issue，不猜作者、不补卷期。
GB/T 复用 check_gbt.render，保证已有输出逐字节不变。

用法（自检）：
    python styles.py --selftest
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")
STYLES = ("gbt7714", "apa7", "ieee")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _gbt():
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("check_gbt", here / "check_gbt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 作者
def _split_author(a: str) -> tuple[str, str]:
    a = (a or "").strip().rstrip(".")
    if "," in a:
        sur, giv = [x.strip() for x in a.split(",", 1)]
        return sur, giv
    parts = a.split()
    initials = []
    while len(parts) > 1 and re.fullmatch(r"[A-Z]{1,2}\.?", parts[-1]):
        initials.insert(0, parts.pop())
    if initials:
        return " ".join(parts), " ".join(initials)
    return (parts[-1] if parts else ""), " ".join(parts[:-1])


def apa_authors(names: list[str]) -> str:
    out = []
    for n in names or []:
        if CJK.search(n):
            out.append(re.sub(r"\s+", "", n))
            continue
        sur, giv = _split_author(n)
        ini = " ".join(f"{w[0].upper()}." for w in re.split(r"[\s.\-]+", giv) if w)
        out.append(f"{sur}, {ini}".strip().rstrip(","))
    if not out:
        return ""
    if len(out) == 1:
        return out[0]
    if len(out) <= 20:
        return ", ".join(out[:-1]) + ", & " + out[-1]
    return ", ".join(out[:19]) + ", . . . " + out[-1]


def ieee_authors(names: list[str]) -> str:
    out = []
    for n in names or []:
        if CJK.search(n):
            out.append(re.sub(r"\s+", "", n))
            continue
        sur, giv = _split_author(n)
        ini = " ".join(f"{w[0].upper()}." for w in re.split(r"[\s.\-]+", giv) if w)
        out.append(f"{ini} {sur}".strip())
    if len(out) <= 1:
        return "".join(out)
    if len(out) == 2:
        return f"{out[0]} and {out[1]}"
    return ", ".join(out[:-1]) + ", and " + out[-1]


# ---------------------------------------------------------------- 条目
def _year(entry: dict) -> str:
    y = str(entry.get("year") or "").strip()
    return y if re.fullmatch(r"(18|19|20)\d{2}", y) else ""


def _pages_apa(p: str) -> str:
    p = (p or "").replace("--", "-").strip()
    return p.replace("-", "–")


MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
MONTHS_ABBR = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "Jun.", "Jul.", "Aug.",
               "Sep.", "Oct.", "Nov.", "Dec."]


def _parse_date(entry: dict) -> tuple[int, int, int] | None:
    raw = str(entry.get("date") or entry.get("accessed") or "").strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", raw)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return y, mo, d


def _doi_tail(entry: dict, apa: bool) -> str:
    doi = (entry.get("doi") or "").strip()
    return f"https://doi.org/{doi}" if (doi and apa) else (f"doi: {doi}." if doi else "")


def render_apa(entry: dict) -> tuple[str, list[str]]:
    issues: list[str] = []
    au = apa_authors(entry.get("authors") or [])
    yr = _year(entry) or "n.d."
    if yr == "n.d.":
        issues.append("缺年份，按 n.d.")
    title = (entry.get("title") or "").strip().rstrip(".")
    t = (entry.get("type") or "J").upper()
    cont = (entry.get("container") or "").strip()
    vol = str(entry.get("volume") or "").strip()
    iss = str(entry.get("issue") or "").strip()
    pg = _pages_apa(entry.get("pages") or "")
    pub = (entry.get("publisher") or "").strip()
    place = (entry.get("place") or "").strip()

    if t in ("J", "J/OL"):
        body = f"{cont}"
        if vol:
            body += f", {vol}"
        if iss:
            body += f"({iss})"
        if pg:
            body += f", {pg}"
        tail = _doi_tail(entry, True)
        s = f"{au.rstrip(chr(46))}. ({yr}). {title}. {body}."
        return (f"{s} {tail}".strip(), issues)
    if t == "M":
        s = f"{au.rstrip(chr(46))}. ({yr}). {title}."
        if pub:
            s += f" {pub}."
        return (f"{s} {_doi_tail(entry, True)}".strip(), issues)
    if t in ("A", "C"):
        ed = (entry.get("editor") or "").strip()
        s = f"{au.rstrip(chr(46))}. ({yr}). {title}."
        if cont:
            inner = f"{cont}" + (f" (pp. {pg})" if pg else "")
            s += f" In {ed + ' (Ed.), ' if ed else ''}{inner}."
        if pub:
            s += f" {pub}."
        return (f"{s} {_doi_tail(entry, True)}".strip(), issues)
    if t == "D":
        s = f"{au.rstrip(chr(46))}. ({yr}). {title} [Doctoral dissertation"
        s += f", {pub}]." if pub else "]."
        return (s, issues)
    if t in ("R", "S", "N", "P"):
        label = {"R": "Report No.", "S": "Standard No.", "N": "", "P": "Patent No."}[t]
        s = f"{au.rstrip(chr(46))}. ({yr}). {title}"
        if t == "N":
            s += "."
            if cont:
                s += f" {cont}."
        else:
            if cont:
                s += f" ({label} {cont})"
            s += "."
            if pub:
                s += f" {pub}."
        return (f"{s} {_doi_tail(entry, True)}".strip(), issues)
    if t.startswith("EB"):
        url = (entry.get("url") or "").strip()
        dt = _parse_date(entry)
        stamp = f"{dt[0]}, {MONTHS[dt[1] - 1]} {dt[2]}" if dt else yr
        s = f"{au.rstrip(chr(46))}. ({stamp}). {title}."
        if cont:
            s += f" {cont}."
        return (f"{s} {url}".strip(), issues)
    issues.append(f"APA 未实现的类型：{t}")
    return (f"{au.rstrip(chr(46))}. ({yr}). {title}. {cont}.", issues)


def render_ieee(entry: dict) -> tuple[str, list[str]]:
    issues: list[str] = []
    au = ieee_authors(entry.get("authors") or [])
    yr = _year(entry)
    title = (entry.get("title") or "").strip().rstrip(".")
    t = (entry.get("type") or "J").upper()
    cont = (entry.get("container") or "").strip()
    vol = str(entry.get("volume") or "").strip()
    iss = str(entry.get("issue") or "").strip()
    pg = (entry.get("pages") or "").replace("--", "-").strip()
    pub = (entry.get("publisher") or "").strip()
    place = (entry.get("place") or "").strip()
    doi = (entry.get("doi") or "").strip()

    if t in ("J", "J/OL"):
        s = f'{au}, "{title}," {cont}'
        if vol:
            s += f", vol. {vol}"
        if iss:
            s += f", no. {iss}"
        if pg:
            s += f", pp. {pg.replace(chr(45), chr(8211))}"
        if yr:
            s += f", {yr}"
        s += "."
        if doi:
            s += f" doi: {doi}."
        return (s, issues)
    if t == "M":
        s = f"{au}, {title}."
        if place and pub:
            s += f" {place}: {pub}"
        elif pub:
            s += f" {pub}"
        if yr:
            s += f", {yr}"
        return (s + ".", issues)
    if t in ("A", "C"):
        s = f'{au}, "{title}," in {cont}'
        if yr:
            s += f", {yr}"
        if pg:
            s += f", pp. {pg.replace(chr(45), chr(8211))}"
        return (s + ".", issues)
    if t == "D":
        s = f'{au}, "{title}," Ph.D. dissertation'
        if pub:
            s += f", {pub}"
        if yr:
            s += f", {yr}"
        return (s + ".", issues)
    if t == "S":
        s = f'{au}, "{title}," {cont}.'
        if yr:
            s += f" {yr}."
        return (s, issues)
    if t == "R":
        s = f'{au}, "{title}," {pub}'
        if cont:
            s += f", Rep. {cont}"
        if yr:
            s += f", {yr}"
        return (s + ".", issues)
    if t == "N":
        s = f'{au}, "{title}," {cont}'
        if yr:
            s += f", {yr}"
        return (s + ".", issues)
    if t == "P":
        s = f'{au}, "{title}," Patent {cont}'
        if yr:
            s += f", {yr}"
        return (s + ".", issues)
    if t.startswith("EB"):
        s = f'{au}, "{title},"'
        if cont:
            s += f" {cont},"
        if yr:
            s += f" {yr}."
        dt = _parse_date(entry)
        if dt:
            s += f" Accessed: {MONTHS_ABBR[dt[1] - 1]} {dt[2]}, {dt[0]}."
        s += f' [Online]. Available: {entry.get("url", "")}'
        return (s, issues)
    issues.append(f"IEEE 未实现的类型：{t}")
    return (f'{au}, "{title}," {cont}, {yr}.', issues)


def render(entry: dict, style: str = "gbt7714") -> tuple[str, list[str]]:
    if style == "gbt7714":
        g = _gbt()
        return g.render(entry), [x["code"] for x in g.check_entry(entry)]
    if style == "apa7":
        return render_apa(entry)
    if style == "ieee":
        return render_ieee(entry)
    raise ValueError(f"未知样式：{style}")


# ---------------------------------------------------------------- 正文标记
def intext(nums: list[int], style: str, entries: dict, lang: str = "zh") -> str:
    ns = sorted(set(nums))
    if not ns:
        return ""
    if style in ("gbt7714", "ieee"):
        if len(ns) > 1 and ns == list(range(ns[0], ns[-1] + 1)):
            return f"[{ns[0]}-{ns[-1]}]"
        return "[" + ",".join(str(n) for n in ns) + "]"
    # APA：作者-年
    parts = []
    for n in ns:
        e = entries.get(n) or {}
        au = e.get("authors") or []
        sur = "等" if CJK.search(" ".join(au)) else "et al."
        first = _split_author(au[0])[0] if au else "Anon"
        if len(au) == 1:
            label = first
        elif len(au) == 2 and not CJK.search(" ".join(au)):
            label = f"{first} & {_split_author(au[1])[0]}"
        else:
            label = f"{first} {sur}"
        yr = _year(e) or "n.d."
        parts.append(f"{label}, {yr}")
    joined = "; ".join(parts)
    return f"（{joined}）" if lang == "zh" else f"({joined})"


def list_order(order: list[dict], style: str, entries: dict) -> list[dict]:
    """GB/T 与 IEEE 按首现；APA 按第一作者姓氏字母序（同年再按题名）。"""
    if style != "apa7":
        return order
    def key(item):
        e = entries.get(item["id"]) or {}
        au = (e.get("authors") or [""])[0]
        return (_split_author(au)[0].lower(), _year(e), (e.get("title") or "").lower())
    return sorted(order, key=key)


# ---------------------------------------------------------------- 自检
def _selftest() -> int:
    _utf8()
    cases = [
        ({"type": "J", "authors": ["Smith, J.", "Brown, L."],
          "title": "Sleep duration and cognitive performance in adults",
          "container": "Sleep", "year": "2018", "volume": "41", "issue": "3",
          "pages": "1--12", "doi": "10.1093/sleep/zsy001"}, "journal-en"),
        ({"type": "J", "authors": ["张三", "李四"], "title": "睡眠与记忆",
          "container": "心理学报", "year": "2021", "volume": "53", "issue": "4",
          "pages": "401-415"}, "journal-zh"),
        ({"type": "M", "authors": ["Rosenberg, M."], "title": "Society and the adolescent self-image",
          "publisher": "Princeton University Press", "place": "Princeton", "year": "1965"}, "book"),
        ({"type": "C", "authors": ["Dong, J."], "title": "Combined therapy",
          "container": "Proceedings of the 3rd Symposium", "year": "1985", "pages": "203--212"}, "conf"),
    ]
    out = {}
    for entry, name in cases:
        out[name] = {s: render(entry, s)[0] for s in STYLES}
    # 正文标记
    entries = {1: cases[0][0], 2: cases[1][0], 3: cases[2][0]}
    out["intext"] = {
        "gbt": intext([1, 2], "gbt7714", entries),
        "apa7-zh": intext([1], "apa7", entries, "zh"),
        "apa7-en": intext([1], "apa7", entries, "en"),
        "apa7-2au": intext([1], "apa7", {1: {"authors": ["Smith, J.", "Brown, L."], "year": "2018"}}, "en"),
        "ieee": intext([1, 2], "ieee", entries),
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="样式渲染")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

