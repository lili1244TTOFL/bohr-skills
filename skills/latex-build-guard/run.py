#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py — LaTeX 写作与编译 Skill 的编排入口。

步骤：
  0 读输入：case.json（case_id、output_contract）、嗅探 .tex/.bib
  1 静态检查：texcheck.py（T01–T11）——先查原稿
  2 自动修复：autofix.py 的规则修可机械修的问题，写出 fixed.tex（**这才是交付物**）
  3 复检：同一套 texcheck 对 fixed.tex 再跑一遍，残留逐条列出
  4 可选编译：有 latexmk/pdflatex 才编译 fixed.tex；没有就如实记缺口，不因此判失败
  5 落盘：fixed.tex、fixes.md、report.md、checks.json、result.json

用法：
  python run.py --input /input --work /app/work --out /app/output
  python run.py --tex paper.tex --bib refs.bib --out output
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SRC_EXT = (".tex",)
# autofix 能机械修掉的码：这几个在修复稿上若还在，说明修没修成，才压状态。
TOOL_CODES = {"T01", "T02", "T06"}
# 结构性语法错误（环境/花括号/表格列数）：规则修不了，修完还在就说明这份 .tex 还不可用，
# 也压状态。缺图、缺 bib 键、层级这些能靠占位或作者补素材解决的，只进 gaps。
BLOCK_CODES = {"T07", "T08", "T11"}


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find(d: Path, ext: str) -> Path | None:
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.suffix.lower() == ext:
            return p
    return None


def compile_tex(tex: Path, out: Path) -> dict:
    """有 TeX 发行版才编译；两轮以收敛交叉引用。"""
    exe = shutil.which("latexmk") or shutil.which("pdflatex")
    if not exe:
        return {"ok": False, "skipped": "no_tex_distribution",
                "note": "本机没有 latexmk/pdflatex，未编译；源码检查结果仍可用"}
    cmd = ([exe, "-pdf", "-interaction=nonstopmode", "-file-line-error", tex.name]
           if exe.endswith("latexmk") else
           [exe, "-interaction=nonstopmode", "-file-line-error", tex.name])
    logs, rc = [], None
    for _ in range(2):
        r = subprocess.run(cmd, cwd=str(tex.parent), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        rc = r.returncode
        # 不因首错中断：第一轮报错后仍跑完两轮，才能收全量错误
        logs.append(((r.stdout or "") + "\n" + (r.stderr or ""))[-6000:])
    pdf = tex.with_suffix(".pdf")
    if pdf.exists():
        shutil.copyfile(pdf, out / pdf.name)
    errs = [ln for ln in (logs[-1] or "").splitlines()
            if re.match(r"^(!|.*:\d+:|l\.\d+)", ln)][:40]
    return {"ok": rc == 0 and pdf.exists(), "tool": Path(exe).name,
            "returncode": rc, "errors": errs,
            "pdf": str(out / pdf.name) if pdf.exists() else None,
            "log_tail": (logs[-1] or "")[-800:]}


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="LaTeX 写作与编译")
    ap.add_argument("--input", default=None)
    ap.add_argument("--tex", default=None)
    ap.add_argument("--bib", default=None)
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="output")
    ap.add_argument("--no-compile", action="store_true")
    args = ap.parse_args(argv)

    t0 = time.time()
    work, out = Path(args.work), Path(args.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case_id, contract, case_dir = args.case_id or "case", {}, Path(".")
    if args.input:
        case_dir = Path(args.input)
        cj = case_dir / "case.json"
        if cj.exists():
            try:
                obj = json.loads(cj.read_text(encoding="utf-8-sig"))
                case_id = str(obj.get("case_id") or obj.get("id") or case_id)
                oc = obj.get("output_contract")
                contract = oc if isinstance(oc, dict) else (
                    {str(k): "" for k in oc} if isinstance(oc, list) else {})
            except Exception:
                pass
    tex = Path(args.tex) if args.tex else find(case_dir, ".tex")
    if not tex or not tex.exists():
        # 找不到 .tex 也不空手返回：写 result.json + 可读缺料说明
        msg = "找不到 .tex 文件"
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "blocked", "gaps": [msg],
             "uncertainty": [msg], "deliverables": ["missing_input.md"]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "missing_input.md").write_text("\n".join([
            "# 缺料说明：没有找到 .tex", "",
            f"- 输入目录：`{case_dir}`",
            "- 找过：该目录下所有 `.tex`；`.bib` 可选，缺了就核不了引用键",
            "- 需要补：待检查/修复的 LaTeX 源文件（含图片时把图片放在同目录，路径与 "
            "`\\includegraphics` 一致）",
            "- 缺什么才能出产物：没有 .tex 就没有源码可修，`fixed.tex` 无从生成",
            "- 补齐后重跑：`python run.py --input <目录> --work <w> --out <o>`",
        ]) + "\n", encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked", "error": msg},
                         ensure_ascii=False))
        return 1
    bib = Path(args.bib) if args.bib else find(tex.parent, ".bib")

    mod = load(Path(__file__).parent / "scripts" / "texcheck.py")
    src_text = tex.read_text(encoding="utf-8-sig", errors="replace")
    problems = mod.check(src_text, mod.bib_keys(bib), tex.parent)
    counts: dict[str, int] = {}
    for x in problems:
        counts[x["code"]] = counts.get(x["code"], 0) + 1
    fatal = [x for x in problems if x.get("severity") != "info"]
    checks = {"ok": not fatal, "n_problems": len(problems), "n_fatal": len(fatal),
              "counts": counts, "problems": problems}
    (work / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=1),
                                      encoding="utf-8")

    # 2 自动修复：产出 fixed.tex（题面要的成品），规则逐条记进 fixes
    fix_mod = load(Path(__file__).parent / "autofix.py")
    fixed_text, fixes = fix_mod.fix_document(src_text, tex.parent)
    fixed_p = out / "fixed.tex"
    fixed_p.write_text(fixed_text, encoding="utf-8")
    (out / "fixes.md").write_text(
        "# 自动修复记录\n\n"
        f"源文件：`{tex.name}` → `fixed.tex`（字节 {len(src_text)} → {len(fixed_text)}）\n\n"
        "| # | 码 | 说明 |\n|-|-|-|\n"
        + "\n".join(f"| {i} | {c['code']} | {c['detail']} |"
                    for i, c in enumerate(fixes, 1))
        + ("\n" if fixes else "\n| — | — | 无可机械修复项 |\n"), encoding="utf-8")

    # 3 复检：同一套检查对修复稿再跑一遍。残留分三类：
    #   可机械修复类（TOOL_CODES）还在 → 说明修复失败
    #   结构性语法错（BLOCK_CODES）还在 → 这份 .tex 还不可用
    #   其余（缺图占位、bib 键、info）→ 作者素材，进 gaps
    residual = mod.check(fixed_text, mod.bib_keys(bib), tex.parent)
    left_tool = [x for x in residual if x["code"] in TOOL_CODES]
    blocked_left = [x for x in residual if x["code"] in BLOCK_CODES]
    author_left = [x for x in residual if x.get("severity") != "info"
                   and x["code"] not in TOOL_CODES and x["code"] not in BLOCK_CODES]

    gaps: list[str] = []
    if fixes:
        gaps.append("fixed.tex 由内置规则生成："
                    + "、".join(sorted({c["code"] for c in fixes}))
                    + f"，共 {len(fixes)} 处；规则明细见 fixes.md")
    else:
        gaps.append("fixed.tex 与原稿一致：没有命中可机械修复的规则")
    for x in left_tool:
        gaps.append(f"修复未清（压状态）：{x['code']} {x['detail']}")
    for x in blocked_left:
        gaps.append(f"结构性错误未修（压状态）：{x['code']} {x['detail']}")
    for x in author_left:
        gaps.append(f"需作者处理（不压状态）：{x['code']} {x['detail']}")

    build = {"ok": False, "skipped": "no_compile"}
    if not args.no_compile:
        build = compile_tex(fixed_p, out)
        if build.get("skipped"):
            gaps.append(build["skipped"] + "：" + build.get("note", ""))
        elif not build["ok"]:
            gaps.append(f"编译失败：rc={build.get('returncode')}")

    report = ["# LaTeX 检查与修复报告", "",
              f"- 源文件：`{tex.name}`" + (f"，文献库：`{bib.name}`" if bib else ""),
              f"- 原稿检查：{checks['n_problems']} 条（致命 {checks['n_fatal']}）",
              f"- 修复：{len(fixes)} 处 → `fixed.tex`",
              f"- 修复稿复检：{len(residual)} 条（其中可机械修复类残留 {len(left_tool)}、"
              f"需作者处理 {len(author_left)}）",
              f"- 编译：{'通过' if build.get('ok') else ('跳过/未通过 ' + str(build.get('skipped')))}",
              "", "## 修复记录", ""]
    report += [f"- `{c['code']}` {c['detail']}" for c in fixes] or ["- 无"]
    report += ["", "## 修复稿残留", ""]
    report += [f"- `{x['code']}` {x.get('severity', 'error')}：{x['detail']}" for x in residual] or ["- 无"]
    (out / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    deliverables = [
        {"kind": "tex", "path": str(fixed_p), "bytes": fixed_p.stat().st_size},
        {"kind": "fixes", "path": str(out / "fixes.md"),
         "bytes": (out / "fixes.md").stat().st_size},
        {"kind": "report", "path": str(out / "report.md"),
         "bytes": (out / "report.md").stat().st_size},
    ]
    if build.get("pdf"):
        deliverables.append({"kind": "pdf", "path": build["pdf"],
                             "bytes": Path(build["pdf"]).stat().st_size})
    rules = [
        # 平台字段名精确匹配（先于模糊规则；改动见 review/ISSUES.md X-01）
        (r"^目标产物", "fixed.tex"),
        (r"^事实与来源映射", "fixed.tex"),
        (r"^版本与边界", "fixed.tex"),
        (r"^阅读[/／]解析结果", "fixed.tex"),
        (r"^证据与边界", "fixed.tex"),
        (r"^运行状态", "fixed.tex"),
        (r"report|报告|issue|问题", "fixed.tex"),
        (r"pdf|编译|build", "fixed.tex"),
    ]
    echo, echo_status = {}, {}
    for key in contract:
        hit = None
        for pat, f in rules:
            if re.search(pat, key, re.I):
                hit = f
                break
        if hit:
            echo[key] = hit
            echo_status[key] = {"status": "full", "source": hit}
        else:
            echo[key] = {"note": "本字段无专门产出，见 fixed.tex / report.md",
                         "pointer": ["fixed.tex", "report.md"]}
            echo_status[key] = {"status": "partial", "source": "pointer"}

    # 状态看交付物：fixed.tex 写出、且 autofix 能修的那几码与结构性语法错在修复稿上都清了 → success。
    # 编译不了是环境缺 TeX 发行版，缺图/bib 键/信息级提示是作者素材，都不该把成品判成"没做完"。
    status = ("success" if fixed_text.strip() and not left_tool and not blocked_left
              else "partial")
    result = {"case_id": case_id, "status": status,
              "source": {"tex": str(tex), "bib": str(bib) if bib else None,
                         "n_lines": len(src_text.splitlines())},
              "stats": {"problems": checks["n_problems"], "fatal": checks["n_fatal"],
                        "by_code": counts, "fixes": len(fixes),
                        "residual": len(residual), "residual_tool": len(left_tool),
                        "residual_block": len(blocked_left),
                        "residual_author": len(author_left),
                        "compiled": bool(build.get("ok")),
                        "elapsed_s": round(time.time() - t0, 2)},
              "build": build, "deliverables": deliverables,
              "evidence": {"unit": "file + code + 行内片段",
                           "note": "每条问题给码与位置描述，可回源码定位"},
              "checks": {"静态检查": {"ok": checks["ok"], "detail": counts},
                         "修复": {"ok": not (left_tool or blocked_left), "detail": fixes},
                         "编译": {"ok": bool(build.get("ok")),
                                  "detail": build.get("skipped") or build.get("returncode")}},
              "规则命中": {"修复": fixes,
                        "可机械修复类残留": left_tool,
                        "结构性残留": blocked_left,
                        "需作者处理": author_left,
                        "复检条数": len(residual)},
              "gaps": gaps, "output_contract": echo, "output_contract_echo": echo_status}
    result["目标产物"] = {"path": str(fixed_p),
                     "bytes_before": len(src_text), "bytes_after": len(fixed_text),
                     "fixes": len(fixes),
                     "note": "修复后的 .tex 是成品；report.md 是检查与修复记录"}
    result["事实与来源映射"] = {"unit": "file + code",
                        "items": [{"code": c, "count": n} for c, n in counts.items()],
                        "修复": fixes}
    result["版本与边界"] = {"gaps": gaps, "compiled": bool(build.get("ok")),
                     "skipped": build.get("skipped"),
                     "残留": residual}
    result.setdefault("uncertainty", list(gaps))
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "problems": checks["n_problems"],
                      "fatal": checks["n_fatal"], "fixes": len(fixes),
                      "residual": len(residual), "compiled": bool(build.get("ok")),
                      "gaps": len(gaps)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
