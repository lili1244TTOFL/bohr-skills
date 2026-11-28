#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""autofix.py — 把检查结果落成改过的 .tex：每条改动都带 文件/行号/原文/改后/依据/回退方式。

与老版本的区别：
  * 规则不再自己重扫一遍再改（两份判断口径必然漂移），改为**吃 texcheck 的问题清单**：
    修什么由 T 码决定，怎么修由 F 码决定，改完由 run.py 用同一套 T 码复检。
  * 每条改动记 `file` `line`（原文件行号）`before` `after` `basis` `reversible`，
    直接对得上题面「事实与来源映射：数字、术语、引用、图表和关键论断的来源/修改记录，
    可逐条回溯」。
  * 修不了的问题不静默丢掉：进 `unapplied`，逐条写清为什么规则修不了、要作者做什么。

规则（判据见 rules/codes.md）：
    F01 label 重复 → 后者改名 <key>_2
    F02 \\ref 指向不存在的 label → 在对应浮动体/章节补 \\label
    F03 浮动体缺 caption → 在浮动体开头补 \\caption{待补图注} + TODO
    F04 bib 条目未被引用 → 只报不删（删了丢信息）
    F05 图片缺失 → 换成带文件名的 \\fbox 占位框 + TODO
    F06 首个分节是 \\subsection → 提升为 \\section（只改层级，不改标题文字）
    F07 缺宏包（编译日志判定）→ 注释掉该 \\usepackage + 写清 tlmgr 处置
    F08 浮动体内 \\label 在 \\caption 之前 → 把 \\label 移到 \\caption 之后
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VERSION = "2.0.0"
CAPTION_PLACEHOLDER = "待补图注"


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


class Doc:
    """带原文件行号映射的可编辑文本：每次改动都回指原始行号。"""

    def __init__(self, rel: str, text: str):
        self.rel, self.orig, self.cur = rel, text, text
        self._edits: list[tuple[int, int]] = []  # (改动点在当时的坐标, delta)

    def orig_line(self, pos_now: int) -> int:
        p = pos_now
        for pos, delta in reversed(self._edits):
            if p >= pos:
                p -= delta
        return self.orig.count("\n", 0, max(p, 0)) + 1

    def orig_snippet(self, line: int, width: int = 120) -> str:
        rows = self.orig.splitlines()
        if 1 <= line <= len(rows):
            return rows[line - 1].strip()[:width]
        return ""

    def edit(self, start: int, end: int, new: str) -> None:
        self.cur = self.cur[:start] + new + self.cur[end:]
        self._edits.append((start, len(new) - (end - start)))


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _locate(doc: Doc, pattern: str, want_line: int, flags: int = 0,
            group: int = 0, nth: int = 0) -> re.Match | None:
    """在当前文本里找 pattern 的匹配，挑"原始行号"离 want_line 最近的那个。"""
    ms = list(re.finditer(pattern, doc.cur, flags))
    if not ms:
        return None
    cands = sorted(ms, key=lambda m: (abs(doc.orig_line(m.start()) - want_line), m.start()))
    return cands[min(nth, len(cands) - 1)]


def _group_end(text: str, start: int) -> int:
    """从 start 处的 `{` 起找配对的 `}`，返回右花括号之后的位置；找不到返回 -1。"""
    if start >= len(text) or text[start] != "{":
        return -1
    depth, i = 0, start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _free_label(doc: Doc, key: str, taken: set[str]) -> str:
    k, i = f"{key}_2", 2
    while k in taken:
        i += 1
        k = f"{key}_{i}"
    return k


def _mk(code: str, tcode: str, doc: Doc, line: int, before: str, after: str,
        reason: str, basis: str, reversible: str) -> dict:
    return {"code": code, "problem_code": tcode, "file": doc.rel, "line": line,
            "before": before, "after": after, "reason": reason, "basis": basis,
            "reversible": reversible}


BASIS = {
    "F01": "题面「改得对：…交叉引用与参考文献…是否既符合原文意图又符合模板要求」",
    "F02": "题面「改得对：…交叉引用与参考文献」+「编译得通」",
    "F03": "题面「改得对：…图表…是否既符合原文意图又符合模板要求」",
    "F05": "题面「编译得通」：缺图会让整份源码编不出来；不造图，只占位",
    "F06": "题面「改得对：…版式与模板必填项」+「交得可用」",
    "F07": "题面「异常处理：缺宏包…的识别与处置」",
    "F08": "题面「改得对：…引用与编号」：\\label 在 \\caption 之前会取到上一个编号",
}


def apply_fixes(files: dict[str, str], problems: list[dict],
                build_errors: list[dict] | None = None) -> dict:
    """files = {相对路径: 原文}；problems = texcheck 的问题清单。

    返回 {files, changes, unapplied, stats}：files 是改后的文本，changes 是逐条改动记录，
    unapplied 是"规则修不了"的问题及原因（不静默丢）。
    """
    docs = {rel: Doc(rel, txt) for rel, txt in files.items()}
    changes: list[dict] = []
    unapplied: list[dict] = []
    done: set[tuple[str, int, str]] = set()

    def doc_of(rel: str) -> Doc | None:
        return docs.get(rel)

    # ---- F01 label 重复：后出现的改名
    taken = set()
    for rel, txt in files.items():
        for m in re.finditer(r"\\label\{([^}]*)\}", txt):
            taken.add(m.group(1).strip())
    for p in problems:
        if p["code"] != "T01" or p.get("severity") == "info":
            continue
        d = doc_of(p["file"])
        if not d:
            continue
        key = re.search(r"label 重复：([^（(]+)", p["detail"]).group(1).strip()
        m = _locate(d, r"\\label\{" + re.escape(key) + r"\}", p["line"])
        if not m:
            continue
        new = _free_label(d, key, taken)
        line = d.orig_line(m.start())
        d.edit(m.start(), m.end(), "\\label{" + new + "}")
        taken.add(new)
        changes.append(_mk("F01", "T01", d, line, d.orig_snippet(line),
                           "\\label{" + new + "}",
                           f"重复 label 改名，保留首个 {key}，后出现者改 {new}",
                           BASIS["F01"], f"把 {new} 改回 {key}"))
        done.add(("T01", p["line"], p["file"]))

    # ---- F03 浮动体缺 caption：在浮动体开头补占位 caption（放开头可同时避免 label 取错编号）
    for p in problems:
        if p["code"] != "T06":
            continue
        d = doc_of(p["file"])
        if not d:
            continue
        env = re.search(r"(figure|table)", p["detail"]).group(1)
        m = _locate(d, r"\\begin\{" + env + r"\*?\}", p["line"])
        if not m:
            continue
        line = d.orig_line(m.start())
        ins = (f"\n  \\caption{{{CAPTION_PLACEHOLDER}}}% TODO 补真实图/表注（F03 自动补占位，"
               f"勿留空）")
        d.edit(m.end(), m.end(), ins)
        changes.append(_mk("F03", "T06", d, line, d.orig_snippet(line),
                           ins.strip(), f"{env} 缺 caption，补占位并标 TODO",
                           BASIS["F03"],
                           f"删掉 \\caption{{{CAPTION_PLACEHOLDER}}} 这一行"))
        done.add(("T06", p["line"], p["file"]))

    # ---- F08 浮动体内 label 在 caption 之前：把 label 移到 caption 之后
    for p in problems:
        if p["code"] != "T13" or "caption 之前" not in p["detail"]:
            continue
        d = doc_of(p["file"])
        if not d:
            continue
        env = re.search(r"（(figure|table)", p["detail"])
        env = env.group(1) if env else "figure"
        m = _locate(d, r"\\begin\{" + env + r"\*?\}(.*?)\\end\{" + env + r"\*?\}",
                    p["line"], re.S)
        if not m:
            continue
        body_start = m.start(1)
        lab = re.search(r"\\label\{[^}]*\}", m.group(1))
        cap = re.search(r"\\caption\*?(?:\[[^\]]*\])?\s*\{", m.group(1))
        if not lab or not cap or lab.start() > cap.start():
            continue
        cap_end = _group_end(d.cur, body_start + cap.end() - 1)
        if cap_end < 0 or cap_end > m.end(1):
            continue
        line = d.orig_line(body_start + lab.start())
        lab_text = lab.group(0)
        before = d.orig_snippet(line)
        d.edit(body_start + lab.start(), body_start + lab.end(), "")
        ins_at = cap_end - (lab.end() - lab.start())
        d.edit(ins_at, ins_at, "\n  " + lab_text)
        changes.append(_mk("F08", "T13", d, line, before,
                           f"\\caption{{…}} 之后放 {lab_text}",
                           "把 \\label 移到 \\caption 之后，编号才指向本图/本表",
                           BASIS["F08"], f"把 {lab_text} 移回 \\caption 之前"))
        done.add(("T13", p["line"], p["file"]))

    # ---- F02 引用不存在：在对应浮动体/章节补 label
    for p in problems:
        if p["code"] != "T02" or p.get("severity") == "info":
            continue
        d = doc_of(p["file"])
        if not d:
            continue
        key = re.search(r"引用不存在：([^（(]+)", p["detail"]).group(1).strip()
        mk = _locate(d, r"\\(?:ref|eqref|autoref|cref|pageref|nameref)\{" + re.escape(key) + r"\}",
                     p["line"])
        line = d.orig_line(mk.start()) if mk else p["line"]
        kind = ("figure" if re.search(r"^fig|figure", key, re.I) else
                "table" if re.search(r"^tab|table", key, re.I) else None)
        ins = ("\n  \\label{" + key + "}% 自动补：依据 \\ref{" + key + "}（F02，"
               "请核对该编号是否指向此处）")
        placed = False
        if kind:
            fm = re.search(r"\\begin\{" + kind + r"\*?\}(.*?)\\end\{" + kind + r"\*?\}",
                           d.cur, re.S)
            if fm:
                body_start = fm.start(1)
                cap = re.search(r"\\caption\*?(?:\[[^\]]*\])?\s*\{", fm.group(1))
                if cap:
                    cap_end = _group_end(d.cur, body_start + cap.end() - 1)
                    at = cap_end if cap_end > 0 else body_start
                    # 若 caption 那一行后面只剩注释，插到行尾，免得 label 和注释挤在同一行
                    eol = d.cur.find("\n", at)
                    if eol > 0 and re.fullmatch(r"[ \t]*(%.*)?", d.cur[at:eol]):
                        at = eol
                else:
                    at = body_start
                d.edit(at, at, ins)
                placed = True
        if not placed:
            secs = list(re.finditer(r"\\(?:section|chapter)\*?\{", d.cur))
            if secs:
                sec_end = _group_end(d.cur, secs[-1].end() - 1)
                at = sec_end if sec_end > 0 else secs[-1].end()
                d.edit(at, at, ins)
                placed = True
        if placed:
            changes.append(_mk("F02", "T02", d, line, d.orig_snippet(line), ins.strip(),
                               f"\\ref{{{key}}} 找不到 label，就近补上并标出可核对点",
                               BASIS["F02"],
                               f"删掉 \\label{{{key}}} 这一行并手工定义正确锚点"))
            done.add(("T02", p["line"], p["file"]))
        else:
            unapplied.append({"code": "T02", "problem_code": "T02", "file": p["file"],
                              "line": p["line"], "name": key,
                              "why": "文件里没有 figure/table/section 可挂 label",
                              "author_action": "在正确位置手工补 \\label{" + key + "}"})

    # ---- F05 图片缺失：换成占位框
    for p in problems:
        if p["code"] != "T09":
            continue
        d = doc_of(p["file"])
        if not d:
            continue
        name = re.search(r"图片不存在：([^（(]+)", p["detail"]).group(1).strip()
        m = _locate(d, r"\\includegraphics\*?(?:\[[^\]]*\])?\{([^}]*)\}", p["line"])
        if not m:
            continue
        line = d.orig_line(m.start())
        before = d.orig_snippet(line)
        rep = (f"\\fbox{{\\small 图片缺失：{name}}}% TODO 补图：{name}（F05 占位，"
               f"保证源码可继续编译）")
        d.edit(m.start(), m.end(), rep)
        changes.append(_mk("F05", "T09", d, line, before, rep,
                           f"图片 {name} 不存在，换占位框保留文件名，不造图",
                           BASIS["F05"], f"换回 \\includegraphics{{{name}}} 并补上图片"))
        done.add(("T09", p["line"], p["file"]))

    # ---- F06 首个分节是 subsection：提升为 section（只改层级）
    for p in problems:
        if p["code"] != "T10":
            continue
        d = doc_of(p["file"])
        if not d:
            continue
        m = _locate(d, r"\\(subsection|subsubsection)\*?\{", p["line"])
        if not m:
            continue
        line = d.orig_line(m.start())
        before = d.orig_snippet(line)
        d.edit(m.start(1), m.end(1), "section")
        changes.append(_mk("F06", "T10", d, line, before,
                           before.replace("\\" + m.group(1), "\\section", 1),
                           "首个分节前没有 section：按层级提升为 section，标题文字不动",
                           BASIS["F06"],
                           f"把该行的 \\section 改回 \\{m.group(1)}"))
        done.add(("T10", p["line"], p["file"]))

    # ---- F07 缺宏包（只有真编译日志能判）：注释掉该 usepackage 并写清处置
    for e in (build_errors or []):
        if e.get("kind") != "missing_package" or e.get("severity") != "error":
            continue
        stem = (e.get("target") or "").replace(".sty", "")
        if not stem:
            m = re.search(r"File `([^']+)\.sty' not found", e.get("message", "") or "")
            stem = m.group(1) if m else ""
        if not stem:
            continue
        hit = False
        for rel, d in docs.items():
            m = re.search(r"[ \t]*\\(usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{[^}]*\b"
                          + re.escape(stem) + r"\b[^}]*\}[ \t]*\n?", d.cur)
            if not m:
                continue
            line = d.orig_line(m.start())
            before = d.orig_snippet(line)
            txt = m.group(0)
            inner = re.search(r"\{([^}]*)\}", txt)
            names = [n.strip() for n in inner.group(1).split(",") if n.strip()] if inner else []
            rep = ""
            if len(names) <= 1:
                rep = ("% " + txt.strip() + f"  % F07 缺宏包 {stem}：装好后取消注释"
                       f"（tlmgr install {stem}）\n")
            else:
                rest = ",".join(n for n in names if n != stem)
                opt = re.search(r"(?:\[([^\]]*)\])?\s*\{", txt)
                opts = opt.group(1) if opt and opt.group(1) else ""
                rep = (f"% F07 缺宏包 {stem}：已从下面这行移除，装好后加回"
                       f"（tlmgr install {stem}）\n"
                       + "\\" + m.group(1) + (f"[{opts}]" if opts else "") + "{" + rest + "}\n")
            d.edit(m.start(), m.end(), rep)
            changes.append(_mk("F07", "build", d, line, before, rep.strip(),
                               f"编译日志显示 {stem}.sty 缺失：注释掉该宏包让编译能继续，"
                               "缺口写进 gaps 与 reproduce.md",
                               BASIS["F07"],
                               f"取消注释 \\usepackage{{{stem}}}"))
            hit = True
            break
        if not hit:
            unapplied.append({"code": "F07", "problem_code": "build", "file": e.get("file", ""),
                              "line": e.get("line"), "name": stem,
                              "why": "源文件里没有对应的 \\usepackage 行（可能由模板类间接加载）",
                              "author_action": f"安装宏包后重编译：tlmgr install {stem}"})

    # ---- 没被任何 F 码覆盖的致命问题：逐条记"为什么修不了"
    fixable = {"T01", "T02", "T06", "T09", "T10", "T13"}
    for p in problems:
        if p.get("severity") == "info":
            continue
        if (p["code"], p["line"], p["file"]) in done:
            continue
        if p["code"] in fixable and any(u.get("line") == p["line"] and u.get("file") == p["file"]
                                        for u in unapplied):
            continue
        unapplied.append({"code": p["code"], "problem_code": p["code"], "file": p["file"],
                          "line": p["line"], "name": "",
                          "why": NO_FIX.get(p["code"], "规则不覆盖此类问题"),
                          "author_action": AUTHOR_ACTION.get(p["code"], "按 report.md 手工处理")})
    # 去重（同一 file:line:code 只留一条）
    seen_u: set[tuple] = set()
    uniq: list[dict] = []
    for u in unapplied:
        k = (u.get("code"), u.get("file"), u.get("line"), u.get("name"))
        if k in seen_u:
            continue
        seen_u.add(k)
        uniq.append(u)
    return {"files": {rel: d.cur for rel, d in docs.items()},
            "changes": sorted(changes, key=lambda c: (c["file"], c["line"], c["code"])),
            "unapplied": uniq,
            "touched": sorted({c["file"] for c in changes}),
            "stats": {"n_changes": len(changes), "n_unapplied": len(uniq),
                      "files_changed": len({c["file"] for c in changes})}}


NO_FIX = {
    "T04": "cite 键不在 .bib：补条目等于造材料外事实，规则不猜",
    "T07": "环境不配对：要读懂作者本意（该补 end 还是该删 begin），规则不替作者决定",
    "T08": "花括号不配对：缺的 } 位置有多种可能，改错会把正文吞进去",
    "T11": "表格列数不符：该补空格子还是改列声明是内容决策",
    "T12": "数学定界符不配对：$ 该补在句中还是句末取决于公式边界",
    "T14": "bib 条目缺字段：字段值属于文献事实，不能编",
    "T15": "bib 键重复：合并哪两条、保留哪个版本是作者判断",
    "T16": "模板必填项缺失：标题/作者/摘要是作者内容",
    "T17": "参考文献资源缺失：.bib 文件本身不在工程里",
    "T18": "主文件结构缺项：\\documentclass 与 document 环境要作者补",
}
AUTHOR_ACTION = {
    "T04": "在 .bib 补该键或改用已有键",
    "T07": "按 report.md 的栈顶提示补 \\end 或删多余 \\begin",
    "T08": "从报错行往前找未闭合的 { 补 }",
    "T11": "逐行对齐 & 数量或改列声明",
    "T12": "按行号成对补 $ / \\[ \\]",
    "T14": "补 author/title/journal/year 等必填字段",
    "T15": "合并重复条目，保留一个键",
    "T16": "补 \\title/\\author/\\maketitle/abstract 等模板必填项",
    "T17": "把 .bib 放进工程或改 \\bibliography 路径",
    "T18": "补 \\documentclass / \\begin{document} / \\end{document}",
}


def fix_document(tex: str, base: Path | None = None,
                 problems: list[dict] | None = None,
                 build_errors: list[dict] | None = None) -> tuple[str, list]:
    """向后兼容单文件入口：返回 (改后文本, 改动记录)。不给 problems 时只跑与文本无关的规则。"""
    res = apply_fixes({"input.tex": tex}, problems or [], build_errors)
    return res["files"]["input.tex"], res["changes"]


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="LaTeX 自动修复（吃 checks.json 的问题清单）")
    ap.add_argument("--input", required=True, help="工程目录")
    ap.add_argument("--checks", default=None, help="texcheck 的 checks.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--case-id", default=None)
    a = ap.parse_args(argv)
    src, out = Path(a.input), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = {p.relative_to(src).as_posix(): p.read_text(encoding="utf-8-sig", errors="replace")
             for p in sorted(src.rglob("*.tex"))}
    problems = []
    if a.checks and Path(a.checks).exists():
        problems = json.loads(Path(a.checks).read_text(encoding="utf-8-sig")).get("problems", [])
    res = apply_fixes(files, problems)
    for rel, txt in res["files"].items():
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(txt, encoding="utf-8")
    (out / "changes.json").write_text(json.dumps(
        {"case_id": a.case_id or src.name, "version": VERSION,
         "changes": res["changes"], "unapplied": res["unapplied"],
         "stats": res["stats"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    rows = ["# 自动修复记录", "", "| # | 码 | 文件:行 | 改前 | 改后 | 依据 | 回退 |", "|-|-|-|-|-|-|-|"]
    for i, c in enumerate(res["changes"], 1):
        rows.append(f"| {i} | {c['code']} | `{c['file']}:{c['line']}` | `{c['before']}` | "
                    f"`{c['after']}` | {c['reason']} | {c['reversible']} |")
    if not res["changes"]:
        rows.append("| — | — | — | — | — | 无可机械修复项 | — |")
    if res["unapplied"]:
        rows += ["", "## 规则修不了的（逐条列出，不静默丢）", "",
                 "| 码 | 文件:行 | 为什么规则不修 | 要作者做什么 |", "|-|-|-|-|"]
        for u in res["unapplied"]:
            rows.append(f"| {u['code']} | `{u['file']}:{u['line']}` | {u['why']} | "
                        f"{u['author_action']} |")
    (out / "fixes.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (out / "result.json").write_text(json.dumps(
        {"case_id": a.case_id or src.name, "status": "success" if res["files"] else "blocked",
         "目标产物": {"files": sorted(res["files"]), "n_changes": res["stats"]["n_changes"]},
         "事实与来源映射": {"changes": res["changes"]},
         "版本与边界": {"unapplied": res["unapplied"]},
         "gaps": [f"{u['code']} {u['file']}:{u['line']} {u['why']}" for u in res["unapplied"]],
         "skill_version": VERSION}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "files": len(res["files"]),
                      "changes": res["stats"]["n_changes"],
                      "unapplied": res["stats"]["n_unapplied"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
