#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py — LaTeX 写作与编译 Skill 的编排入口。

一次运行按固定顺序做完这些事，每一步都有可复核的落点：

  0 读输入        读 case.json（case_id / output_contract），识别工程结构（主文件、include 树、
                  文献库、图片路径、引擎），识别不到的写进 gaps
  1 静态检查      scripts/texcheck.py 的 T01–T18：每条带 文件:行号
  2 自动修复      autofix.py 吃第 1 步的问题清单，产出 fixed.tex（+ 完整 fixed/ 树）、
                  changes.json（逐条 改前/改后/依据/回退）、fixed.diff
  3 复检          同一套 T 码查修复稿，残留按 可修未清 / 结构性 / 作者素材 / 提示 分桶
  4 编译闭环      有 TeX 才有：先编原稿拿基线错误 → 缺宏包按日志修掉 → 编修复稿 → 记
                  错误数收敛、轮次、命令、发行版/宏包/字体版本（scripts/build.py）
  5 落盘          result.json、report.md、reproduce.md、build.json、toolchain.json、checks.json

用法：
  python run.py --input /input --work /app/work --out /app/output
  python run.py --input examples --work work --out out
  python run.py --tex paper.tex --bib refs.bib --out out --case-id demo
  python run.py --input proj --no-compile          # 只做静态检查 + 修复
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import sys
import time
import traceback
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PKG_ROOT / "scripts"))

import autofix  # noqa: E402
import build as buildmod  # noqa: E402
import project as projectmod  # noqa: E402
import texcheck  # noqa: E402

# autofix 能修的码：修复稿上还在 = 修复失败，压状态
TOOL_CODES = {"T01", "T02", "T06", "T09", "T10", "T13"}
# 结构性/编译必坏的码：规则不替作者决定，修复稿上还在 = 这份源码还不能用，压状态
BLOCK_CODES = {"T07", "T08", "T11", "T12", "T18"}
JUNK = (".aux", ".log", ".out", ".toc", ".lof", ".lot", ".bbl", ".blg", ".fls",
        ".fdb_latexmk", ".synctex.gz", ".nav", ".snm", ".vrb", ".bcf", ".run.xml",
        ".idx", ".ind", ".ilg", ".glo", ".gls", ".xdv")


def _utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


def sha16(data: str | bytes) -> str:
    b = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(b).hexdigest()[:16]


def is_junk(rel: str) -> bool:
    low = rel.lower()
    return low.endswith(JUNK) or "__pycache__" in low


def snapshot(src: Path, dst: Path, excludes: list[Path] | None = None,
             overrides: dict[str, str] | None = None) -> list[str]:
    """把工程原样拷一份（跳过编译中间产物），overrides 里的 .tex 用改后的文本。"""
    excl = [e.resolve() for e in (excludes or [])]
    overrides = overrides or {}
    wrote: list[str] = []
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rp = p.resolve()
        if any(str(rp).startswith(str(e)) for e in excl):
            continue
        rel = p.relative_to(src).as_posix()
        if is_junk(rel):
            continue
        try:
            if p.stat().st_size > 20 * 1024 * 1024:
                continue
        except OSError:
            continue
        out_p = dst / rel
        out_p.parent.mkdir(parents=True, exist_ok=True)
        if rel in overrides:
            out_p.write_text(overrides[rel], encoding="utf-8")
        else:
            shutil.copyfile(p, out_p)
        wrote.append(rel)
    for rel, txt in overrides.items():  # overrides 里可能有工程里没有的新文件
        if rel not in wrote:
            out_p = dst / rel
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(txt, encoding="utf-8")
            wrote.append(rel)
    return sorted(wrote)


def unified_diff(orig: dict[str, str], new: dict[str, str]) -> str:
    chunks = []
    for rel in sorted(set(orig) | set(new)):
        a, b = orig.get(rel, ""), new.get(rel, "")
        if a == b:
            continue
        chunks.append("".join(difflib.unified_diff(
            a.splitlines(keepends=True), b.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}", n=2)))
    return "".join(chunks) or "（无差异：修复稿与原稿一致）\n"


def resolve_contract(key: str, ctx: dict) -> tuple[str, dict]:
    """把平台契约字段解析成**真实内容**，不是文件名。

    题面「每个案例返回一份结构化结果，保留输入标识、主结果、证据/来源定位、运行状态和不确定性」：
    每个字段值都带 artifact + 条数 + 前几条实例，评审不用再去翻文件。
    """
    k = key.strip()
    if any(t in k for t in ("目标产物", "产物", "成品", "成果", "稿件", "源码", "tex", "artifact")):
        d = ctx["目标产物"]
        return "full", {"artifact": "fixed.tex + fixed/（整棵修复稿树）",
                        "main": d["main"], "bytes_before": d["bytes_before"],
                        "bytes_after": d["bytes_after"], "n_changes": d["n_changes"],
                        "files": d["files"][:8], "compile": d["compile"],
                        "items": d["changes"][:3]}
    if any(t in k for t in ("事实", "来源", "映射", "证据", "溯源", "引用", "记录", "source")):
        d = ctx["事实与来源映射"]
        return "full", {"artifact": "changes.json / fixes.md / checks.json",
                        "unit": d["unit"], "n_changes": d["n_changes"],
                        "n_cites": d["n_cites"], "n_labels": d["n_labels"],
                        "items": d["changes"][:3], "bib_items": d["bib"][:3]}
    if any(t in k for t in ("版本", "边界", "不确定", "缺口", "残留", "异常", "限制",
                            "边界条件", "风险")):
        d = ctx["版本与边界"]
        return "full", {"artifact": "reproduce.md / build.json / toolchain.json",
                        "versions": d["versions"], "n_rounds": d["n_rounds"],
                        "n_residual": d["n_residual"], "n_unapplied": d["n_unapplied"],
                        "gaps": d["gaps"][:5], "uncertainty": d["uncertainty"][:3]}
    if any(t in k for t in ("编译", "build", "pdf", "构建", "复现", "toolchain", "发行版")):
        d = ctx["build"]
        return ("full" if not d.get("skipped") else "partial",
                {"artifact": "build.json",
                 "ok": d.get("ok"), "engine": d.get("engine"),
                 "skipped": d.get("skipped"), "returncode": d.get("returncode"),
                 "n_errors": d.get("n_errors"), "kinds": d.get("kinds", {}),
                 "rounds": d.get("rounds", []), "pdf": d.get("pdf"),
                 "items": (d.get("errors") or [])[:3]})
    if any(t in k for t in ("问题", "检查", "清单", "报告", "issue", "review", "状态", "status")):
        d = ctx["checks"]
        return "full", {"artifact": "checks.json / report.md",
                        "original": d["original"], "fixed": d["fixed"],
                        "items": d["sample"]}
    return "partial", {"artifact": "report.md",
                       "note": "本字段没有专门产出，按最接近的产物回指",
                       "pointer": ["report.md", "result.json"],
                       "status": ctx["status"], "gaps": ctx["gaps"][:3]}


def write_blocked(out: Path, case_id: str, case_dir: Path, msg: str, extra: dict | None = None) -> int:
    (out / "result.json").write_text(json.dumps(
        {"case_id": case_id, "status": "blocked", "gaps": [msg], "uncertainty": [msg],
         "deliverables": ["missing_input.md"], **(extra or {})},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "missing_input.md").write_text("\n".join([
        "# 缺料说明：没有可检查的 LaTeX 源文件", "",
        f"- 输入目录：`{case_dir}`",
        "- 找过：该目录（含子目录）下所有 `.tex`；`.bib` 可选，缺了就核不了引用键",
        "- 需要补：待检查/修复的 LaTeX 工程（主文件 + `\\input` 的子文件 + 图片 + `.bib`）",
        "- 缺什么才能出产物：没有 `.tex` 就没有源码可修，`fixed.tex` 无从生成",
        "- 补齐后重跑：`python run.py --input <目录> --work <w> --out <o>`",
        "- 只给单个文件也行：`python run.py --tex <文件> --out <o>`",
    ]) + "\n", encoding="utf-8")
    print(json.dumps({"ok": False, "status": "blocked", "error": msg}, ensure_ascii=False))
    return 1


def _run(args, t0: float) -> int:
    work, out = Path(args.work), Path(args.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    # ---------- 0 读输入 ----------
    case_id, contract = args.case_id or "case", {}
    case_dir = Path(args.input) if args.input else (
        Path(args.tex).parent if args.tex else Path("."))
    if args.input:
        cj = Path(args.input) / "case.json"
        if cj.exists():
            try:
                obj = json.loads(read(cj))
                case_id = str(obj.get("case_id") or obj.get("id") or case_id)
                oc = obj.get("output_contract")
                contract = oc if isinstance(oc, dict) else (
                    {str(k): "" for k in oc} if isinstance(oc, list) else {})
            except Exception:
                contract = {}
    root = case_dir
    info = projectmod.discover(root, args.main or (Path(args.tex).name if args.tex else None))
    if not info.get("main"):
        return write_blocked(out, case_id, root, "找不到 .tex 文件", {"project": info})
    main_rel = info["main"]
    files = {f["rel"]: read(root / f["rel"]) for f in info["files"]}
    if args.tex and Path(args.tex).is_file():
        tr = Path(args.tex).resolve().relative_to(root.resolve()).as_posix() \
            if Path(args.tex).resolve().is_relative_to(root.resolve()) else Path(args.tex).name
        if tr not in files:
            files[tr] = read(Path(args.tex))
            main_rel = tr
    bibs = []
    if args.bib:
        for b in args.bib.split(","):
            bp = Path(b.strip())
            if bp.is_file():
                bibs.append((bp.name, read(bp)))
    else:
        bibs = [(b["rel"], read(root / b["rel"])) for b in info["bibs"]]
    gp = info.get("graphicspath", [])
    engine = info.get("engine", "pdflatex")

    # ---------- 1 静态检查（原稿） ----------
    c1 = texcheck.check_project(files, bibs, root, main_rel, PKG_ROOT, gp)
    (work / "checks.json").write_text(json.dumps(c1, ensure_ascii=False, indent=1),
                                      encoding="utf-8")

    # ---------- 2 自动修复 ----------
    fx = autofix.apply_fixes(files, c1["problems"])
    fixed_files = fx["files"]
    changed = {k: v for k, v in fixed_files.items() if v != files.get(k)}

    # ---------- 3 复检 ----------
    c2 = texcheck.check_project(fixed_files, bibs, root, main_rel, PKG_ROOT, gp)
    (work / "checks_fixed.json").write_text(json.dumps(c2, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    res_fatal = [p for p in c2["problems"] if p.get("severity") == "error"]
    left_tool = [p for p in res_fatal if p["code"] in TOOL_CODES]
    left_block = [p for p in res_fatal if p["code"] in BLOCK_CODES]
    author = [p for p in res_fatal if p["code"] not in TOOL_CODES | BLOCK_CODES]
    # 修复幂等：对修复稿再跑一遍规则，不应再有改动（规则收敛）
    fx_again = autofix.apply_fixes(fixed_files, c2["problems"])
    idempotent = not fx_again["changes"]

    # ---------- 4 编译闭环 ----------
    exclude = [out, work]
    build: dict = {"ok": False, "skipped": "no_compile", "engine": engine}
    iterations: list[dict] = []
    pdf_copy = None
    if not args.no_compile:
        src_orig = work / "src_original"
        if src_orig.exists():
            shutil.rmtree(src_orig, ignore_errors=True)
        snapshot(root, src_orig, excludes=exclude)
        b_orig = buildmod.run_build(src_orig, main_rel, work / "build_original", engine,
                                    args.max_rounds, args.timeout)
        iterations.append({"iteration": 1, "target": "original", "ok": b_orig.get("ok"),
                           "skipped": b_orig.get("skipped"),
                           "n_errors": b_orig.get("n_errors"),
                           "kinds": b_orig.get("kinds", {})})
        # 缺宏包只有真编译才知道：按日志补一轮 F07，再复检
        if b_orig.get("errors") and any(e.get("kind") == "missing_package"
                                        for e in b_orig["errors"]):
            fx2 = autofix.apply_fixes(files, c1["problems"], b_orig["errors"])
            if fx2["changes"] != fx["changes"]:
                fx = fx2
                fixed_files = fx["files"]
                changed = {k: v for k, v in fixed_files.items() if v != files.get(k)}
                c2 = texcheck.check_project(fixed_files, bibs, root, main_rel, PKG_ROOT, gp)
                (work / "checks_fixed.json").write_text(
                    json.dumps(c2, ensure_ascii=False, indent=1), encoding="utf-8")
                res_fatal = [p for p in c2["problems"] if p.get("severity") == "error"]
                left_tool = [p for p in res_fatal if p["code"] in TOOL_CODES]
                left_block = [p for p in res_fatal if p["code"] in BLOCK_CODES]
                author = [p for p in res_fatal
                          if p["code"] not in TOOL_CODES | BLOCK_CODES]
                fx_again = autofix.apply_fixes(fixed_files, c2["problems"])
                idempotent = not fx_again["changes"]
                iterations.append({"iteration": 2, "target": "after_log_fix",
                                   "fixes_added": [c["code"] for c in fx["changes"]
                                                   if c["code"] == "F07"]})
        src_fixed = work / "src_fixed"
        if src_fixed.exists():
            shutil.rmtree(src_fixed, ignore_errors=True)
        snapshot(root, src_fixed, excludes=exclude, overrides=fixed_files)
        b_fixed = buildmod.run_build(src_fixed, main_rel, work / "build_fixed", engine,
                                     args.max_rounds, args.timeout)
        iterations.append({"iteration": len(iterations) + 1, "target": "fixed",
                           "ok": b_fixed.get("ok"), "skipped": b_fixed.get("skipped"),
                           "n_errors": b_fixed.get("n_errors"),
                           "kinds": b_fixed.get("kinds", {})})
        if b_fixed.get("pdf"):
            src_pdf = Path(b_fixed["pdf"])
            pdf_copy = out / src_pdf.name
            try:
                shutil.copyfile(src_pdf, pdf_copy)
            except OSError:
                pdf_copy = None
        build = {
            "ok": bool(b_fixed.get("ok")),
            "tool": b_fixed.get("toolchain", {}).get("bins", {}).get(engine) or engine,
            "engine": engine,
            "returncode": b_fixed.get("returncode"),
            "skipped": b_fixed.get("skipped") if not b_fixed.get("ok") else None,
            "n_errors": b_fixed.get("n_errors", 0),
            "n_warnings": b_fixed.get("n_warnings", 0),
            "n_cascades": b_fixed.get("n_cascades", 0),
            "kinds": b_fixed.get("kinds", {}),
            "errors": (b_fixed.get("errors") or [])[:40],
            "first_error": b_fixed.get("first_error"),
            "root_causes": b_fixed.get("root_causes", []),
            "rounds": b_fixed.get("rounds", []),
            "commands": [" ".join(c) for c in b_fixed.get("commands", [])],
            "pdf": str(pdf_copy) if pdf_copy else None,
            "log_tail": (b_fixed.get("log_tail") or "")[-800:],
            "log_path": b_fixed.get("log_path"),
            "baseline": {"n_errors": b_orig.get("n_errors"),
                         "kinds": b_orig.get("kinds", {}),
                         "ok": b_orig.get("ok"),
                         "skipped": b_orig.get("skipped")},
            "error_reduction": (b_orig.get("n_errors") or 0) - (b_fixed.get("n_errors") or 0),
            "iterations": iterations,
            "toolchain": b_fixed.get("toolchain", {}),
            "package_versions": b_fixed.get("package_versions", []),
            "deps_from_fls": (b_fixed.get("fls") or {}).get("deps", [])[:80],
            "fls_available": (b_fixed.get("fls") or {}).get("available", False),
        }
    compile_state = ("pass" if build.get("ok") else
                     "skip" if build.get("skipped") in ("no_compile", "no_tex_distribution")
                     else "fail")

    # ---------- 状态与门槛 ----------
    status = "success" if (changed and not left_tool and not left_block
                           and compile_state != "fail") else "partial"
    if not changed and not left_tool and not left_block and compile_state != "fail":
        status = "success"  # 原稿本来就干净，fixed.tex 与原稿一致也算交付
    gates = {
        "静态结构": {"ok": not left_block, "n": len(left_block),
                     "codes": sorted({p["code"] for p in left_block})},
        "自动修复": {"ok": not left_tool, "n": len(left_tool),
                     "codes": sorted({p["code"] for p in left_tool})},
        "编译": {"state": compile_state, "ok": bool(build.get("ok")),
                 "skipped": build.get("skipped"),
                 "n_errors": build.get("n_errors"),
                 "error_reduction": build.get("error_reduction")},
        "模板必填项": {"n_missing": c2["counts"].get("T16", 0),
                       "documentclass": info.get("documentclass")},
        "作者素材": {"n": len(author), "codes": sorted({p["code"] for p in author})},
        "修复幂等": {"ok": idempotent},
    }

    # ---------- gaps / uncertainty ----------
    gaps: list[str] = []
    uncertainty: list[str] = []
    if fx["changes"]:
        gaps.append(f"[修复] fixed.tex 由内置规则生成 {len(fx['changes'])} 处（"
                    + "、".join(sorted({c['code'] for c in fx['changes']})) + "），明细见 changes.json")
    else:
        gaps.append("[修复] 原稿未命中可机械修复的规则，fixed.tex 与原稿一致")
    for p in left_tool:
        gaps.append(f"[修复失败] {p['code']} {p['file']}:{p['line']} {p['detail']}")
    for p in left_block:
        gaps.append(f"[结构] {p['code']} {p['file']}:{p['line']} {p['detail']}（规则不代改）")
    for u in fx["unapplied"]:
        gaps.append(f"[作者素材] {u['code']} {u['file']}:{u['line']} {u['why']}")
    for p in author:
        if not any(u["code"] == p["code"] and u["line"] == p["line"]
                   and u["file"] == p["file"] for u in fx["unapplied"]):
            gaps.append(f"[作者素材] {p['code']} {p['file']}:{p['line']} {p['detail']}")
    if info.get("skipped_tex"):
        gaps.append(f"[边界] 有 {len(info['skipped_tex'])} 个 .tex 未被主文件引入，未纳入检查："
                    + "、".join(info["skipped_tex"][:5]))
    if args.no_compile:
        gaps.append("[环境] --no-compile：未编译，PDF 未生成（静态检查与修复结果仍可用）")
    elif build.get("skipped") == "no_tex_distribution":
        gaps.append("[环境] 本机没有 " + engine + "（也没有 latexmk）：未编译，PDF 未生成；"
                    "用固定结构闸门替代（T07/T08/T11/T12/T18 全过 = 结构上可编译）")
    elif compile_state == "fail":
        for rc in (build.get("root_causes") or [])[:5]:
            gaps.append(f"[编译] 根因 {rc['kind']} 出现 {rc['count']} 次（见 build.json）")
    for kind, names in (("缺宏包", (build.get("kinds") or {})),):
        if names.get("missing_package"):
            gaps.append(f"[环境] 编译日志报缺宏包 {names['missing_package']} 处："
                        "F07 已注释掉对应 \\usepackage，装好后按 reproduce.md 复编")
        if names.get("missing_font"):
            gaps.append(f"[环境] 编译日志报缺字体 {names['missing_font']} 处："
                        "需装字体并确认引擎为 XeLaTeX/LuaLaTeX")
        if names.get("missing_class"):
            gaps.append(f"[环境] 编译日志报缺模板类 {names['missing_class']} 处：需装期刊/会议模板包")
    uncertainty += [
        "F02 补的 \\label 是根据 \\ref 键名就近挂的，具体指向哪一个图/表需作者核对",
        "占位 caption（" + autofix.CAPTION_PLACEHOLDER + "）与 \\fbox 图片占位是给编译用的，不是最终内容",
        "未做版式视觉比对：PDF 外观、列宽溢出、字体嵌入不在本 Skill 判定范围",
        "未校验公式语义与符号正误：只做定界符与编号层面的形式检查",
    ]
    if not idempotent:
        uncertainty.append("规则在修复稿上仍产生新改动：两次修复不收敛，需人工确认")

    # ---------- 逐项覆盖：每个问题要么已修，要么写明为什么没修 ----------
    coverage = []
    fixed_index = {}
    for ch in fx["changes"]:
        fixed_index.setdefault((ch.get("problem_code"), ch.get("file"), ch.get("line")), ch)
    for p in c1["problems"]:
        key = (p["code"], p["file"], p["line"])
        if key in fixed_index:
            ch = fixed_index[key]
            coverage.append({"problem": f"{p['code']} {p['file']}:{p['line']}",
                             "severity": p.get("severity"), "detail": p["detail"],
                             "disposition": f"已修（{ch['code']}）",
                             "evidence": f"{ch['file']}:{ch['line']}",
                             "undo": ch["reversible"]})
            continue
        u = next((u for u in fx["unapplied"] if u["code"] == p["code"]
                  and u["file"] == p["file"] and u["line"] == p["line"]), None)
        if u:
            coverage.append({"problem": f"{p['code']} {p['file']}:{p['line']}",
                             "severity": p.get("severity"), "detail": p["detail"],
                             "disposition": "需作者处理" if p.get("severity") != "info" else "仅提示",
                             "evidence": u["why"], "undo": u["author_action"]})
        elif p.get("severity") == "info":
            coverage.append({"problem": f"{p['code']} {p['file']}:{p['line']}",
                             "severity": "info", "detail": p["detail"],
                             "disposition": "仅提示（info，不影响交付）",
                             "evidence": "信息级：不压状态、不自动改",
                             "undo": ""})
        else:
            coverage.append({"problem": f"{p['code']} {p['file']}:{p['line']}",
                             "severity": p.get("severity"), "detail": p["detail"],
                             "disposition": "未处置（需复核）", "evidence": "", "undo": ""})

    # ---------- 5 落盘 ----------
    fixed_dir = out / "fixed"
    if fixed_dir.exists():
        shutil.rmtree(fixed_dir, ignore_errors=True)
    fixed_wrote = snapshot(root, fixed_dir, excludes=exclude, overrides=fixed_files)
    main_fixed_path = out / "fixed.tex"
    main_fixed_path.write_text(fixed_files.get(main_rel, files[main_rel]), encoding="utf-8")
    (out / "fixed.diff").write_text(unified_diff(files, fixed_files), encoding="utf-8")
    (out / "changes.json").write_text(json.dumps(
        {"case_id": case_id, "main": main_rel,
         "changes": fx["changes"], "unapplied": fx["unapplied"],
         "stats": fx["stats"], "idempotent": idempotent,
         "unit": "file:line + before/after"}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "fixes.md").write_text(_fixes_md(case_id, main_rel, files, fixed_files, fx),
                                  encoding="utf-8")

    prov = buildmod.provenance(root, main_rel,
                              build if not build.get("skipped") == "no_compile" else
                              {"engine": engine, "toolchain": build.get("toolchain", {})},
                              {"no_compile": args.no_compile, "status": status})
    (out / "toolchain.json").write_text(json.dumps(prov, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    (out / "build.json").write_text(json.dumps(build, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    (out / "reproduce.md").write_text(_reproduce_md(prov, build, info, args), encoding="utf-8")
    (out / "checks.json").write_text(json.dumps(
        {"case_id": case_id, "original": c1, "fixed": c2},
        ensure_ascii=False, indent=1), encoding="utf-8")

    smap = c2.get("map", {})
    ctx = {
        "status": status,
        "目标产物": {"main": str(main_fixed_path), "files": fixed_wrote,
                 "bytes_before": len(files[main_rel].encode("utf-8")),
                 "bytes_after": len(fixed_files[main_rel].encode("utf-8")),
                 "n_changes": len(fx["changes"]),
                 "changes": fx["changes"],
                 "compile": {"state": compile_state, "ok": bool(build.get("ok")),
                             "skipped": build.get("skipped")}},
        "事实与来源映射": {"unit": "file:line + 改前/改后 + 依据",
                    "n_changes": len(fx["changes"]), "changes": fx["changes"],
                    "n_cites": len(smap.get("cites", [])),
                    "n_labels": len(smap.get("labels", [])),
                    "bib": smap.get("bib", [])},
        "版本与边界": {"versions": {"distribution": prov.get("distribution"),
                              "engine": prov.get("engine"),
                              "toolchain": prov.get("toolchain_versions", {}),
                              "packages": (build.get("package_versions") or [])[:20]},
                   "n_rounds": prov.get("n_rounds"),
                   "n_residual": len(c2["problems"]), "n_unapplied": len(fx["unapplied"]),
                   "gaps": gaps, "uncertainty": uncertainty},
        "build": build,
        "checks": {"original": {"n_problems": c1["n_problems"], "n_error": c1["n_error"],
                                "counts": c1["counts"]},
                   "fixed": {"n_problems": c2["n_problems"], "n_error": c2["n_error"],
                             "counts": c2["counts"]},
                   "sample": c1["problems"][:5]},
        "gaps": gaps,
    }
    contract_echo, contract_status = {}, {}
    for key in contract:
        st, val = resolve_contract(key, ctx)
        contract_echo[key] = val
        contract_status[key] = {"status": st, "artifact": val.get("artifact"),
                                "n_items": val.get("n_changes") or val.get("n_problems")
                                or val.get("n_missing") or len(val.get("gaps") or [])}
    if not contract:
        for key in ("目标产物", "事实与来源映射", "版本与边界"):
            st, val = resolve_contract(key, ctx)
            contract_echo[key] = val
            contract_status[key] = {"status": st, "artifact": val.get("artifact"),
                                    "n_items": val.get("n_changes")}

    n_with_line = sum(1 for p in c1["problems"] if p.get("line"))
    result = {
        "case_id": case_id,
        "status": status,
        "status_reason": ("修复稿结构闸门全过" if not (left_tool or left_block)
                          else "修复稿仍有结构性或修复失败项") +
                         ("；编译通过" if compile_state == "pass" else
                          "；编译跳过（无发行版）" if compile_state == "skip" else "；编译失败"),
        "source": {"input_dir": str(root), "main": main_rel,
                   "files": [{"rel": f["rel"], "role": f["role"], "lines": f["lines"]}
                             for f in info["files"]],
                   "skipped_tex": info.get("skipped_tex", []),
                   "bib": [b["rel"] for b in info["bibs"]],
                   "documentclass": info.get("documentclass"),
                   "engine": engine, "engine_reason": info.get("engine_reason"),
                   "graphicspath": gp,
                   "n_files": len(info["files"]), "n_lines": info["stats"]["n_lines"],
                   "sha256_16": {k: sha16(v) for k, v in files.items()}},
        "project": {k: info.get(k) for k in ("stats", "packages", "toolchain_available",
                                             "notes")},
        "build": build,
        "checks": {"静态检查": {"ok": c1["n_error"] == 0, "n_errors": c1["n_error"],
                            "n_warnings": c1["n_warn"], "detail": c1["counts"]},
                   "修复": {"ok": not (left_tool or left_block), "n_changes": len(fx["changes"]),
                          "n_unapplied": len(fx["unapplied"]),
                          "idempotent": idempotent},
                   "复检": {"ok": c2["n_error"] == 0, "n_errors": c2["n_error"],
                          "n_warnings": c2["n_warn"], "detail": c2["counts"]},
                   "编译": {"ok": bool(build.get("ok")),
                          "detail": build.get("skipped") or build.get("returncode")}},
        "gate": gates,
        "stats": {"problems": c1["n_problems"], "fatal": c1["n_error"],
                  "warnings": c1["n_warn"], "by_code": c1["counts"],
                  "fixes": len(fx["changes"]), "unapplied": len(fx["unapplied"]),
                  "residual": len(c2["problems"]), "residual_error": c2["n_error"],
                  "residual_tool": len(left_tool), "residual_block": len(left_block),
                  "residual_author": len(author), "compiled": bool(build.get("ok")),
                  "compile_rounds": len(build.get("rounds") or []),
                  "model_requests": 0, "elapsed_s": round(time.time() - t0, 2)},
        "evidence": {"unit": "file:line",
                     "n_items_with_line": n_with_line,
                     "coverage": round(n_with_line / c1["n_problems"], 3) if c1["n_problems"] else 1.0,
                     "samples": [{"code": p["code"], "at": f"{p['file']}:{p['line']}",
                                  "detail": p["detail"], "severity": p.get("severity")}
                                 for p in c1["problems"][:8]],
                     "compile_samples": [{"at": f"{e.get('file')}:{e.get('line')}",
                                          "kind": e.get("kind"), "root_cause": e.get("root_cause"),
                                          "cascade_of": e.get("cascade_of")}
                                         for e in (build.get("errors") or [])[:5]],
                     "rerun": "python run.py --input <输入目录> --work <work> --out <out>"},
        "deliverables": _deliverables(out, fixed_dir, fixed_wrote, pdf_copy),
        "规则命中": {"修复": fx["changes"],
                  "可机械修复类残留": left_tool,
                  "结构性残留": left_block,
                  "需作者处理": author,
                  "复检条数": len(c2["problems"])},
        "gaps": gaps,
        "uncertainty": uncertainty,
        "逐项覆盖": coverage,
        "事实与来源映射": {"unit": "file:line + 改前/改后 + 依据",
                     "changes": fx["changes"],
                     "unapplied": fx["unapplied"],
                     "引用映射": smap.get("cites", []),
                     "label 映射": smap.get("labels", []),
                     "图表映射": smap.get("graphics", []),
                     "文献条目": smap.get("bib", [])},
        "目标产物": {"path": str(main_fixed_path), "tree": str(fixed_dir),
                 "files": fixed_wrote, "main": main_rel,
                 "bytes_before": len(files[main_rel].encode("utf-8")),
                 "bytes_after": len(fixed_files[main_rel].encode("utf-8")),
                 "fixes": len(fx["changes"]), "idempotent": idempotent,
                 "compile": {"state": compile_state, "skipped": build.get("skipped"),
                             "ok": bool(build.get("ok"))},
                 "note": "fixed.tex 是主文件成品；fixed/ 是整棵可编译工程树（含 .bib 与图片）"},
        "版本与边界": {"gaps": gaps, "uncertainty": uncertainty,
                  "compiled": bool(build.get("ok")), "compile_state": compile_state,
                  "skipped": build.get("skipped"),
                  "versions": {"distribution": prov.get("distribution"),
                               "engine": prov.get("engine"),
                               "toolchain": prov.get("toolchain_versions", {}),
                               "packages": (build.get("package_versions") or [])[:20]},
                  "rounds": prov.get("rounds", []),
                  "residual": c2["problems"], "unapplied": fx["unapplied"]},
        "output_contract": contract_echo,
        "output_contract_echo": contract_status,
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    (out / "report.md").write_text(_report_md(case_id, result, info, c1, c2, fx, build,
                                              coverage, args), encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "main": main_rel,
                      "problems": c1["n_problems"], "fixes": len(fx["changes"]),
                      "residual_error": c2["n_error"],
                      "compiled": bool(build.get("ok")), "compile": compile_state,
                      "gaps": len(gaps)}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- 文档渲染
def _fixes_md(case_id: str, main_rel: str, files: dict[str, str],
              fixed: dict[str, str], fx: dict) -> str:
    rows = ["# 自动修复记录", "",
            f"- case_id：`{case_id}`；主文件：`{main_rel}`",
            f"- 改动：**{len(fx['changes'])}** 处，涉及 {fx['stats']['files_changed']} 个文件；"
            f"修不了：{len(fx['unapplied'])} 条（逐条列在下面，不静默丢）",
            f"- 字节：主文件 {len(files[main_rel].encode('utf-8'))} → "
            f"{len(fixed[main_rel].encode('utf-8'))}", "",
            "| # | 码 | 文件:行 | 改前 | 改后 | 为什么改 | 依据（题面） | 怎么回退 |",
            "|-|-|-|-|-|-|-|-|"]
    for i, c in enumerate(fx["changes"], 1):
        rows.append(f"| {i} | {c['code']} | `{c['file']}:{c['line']}` | `{c['before']}` | "
                    f"`{c['after']}` | {c['reason']} | {c['basis']} | {c['reversible']} |")
    if not fx["changes"]:
        rows.append("| — | — | — | — | — | 无可机械修复项 | — | — |")
    if fx["unapplied"]:
        rows += ["", "## 规则修不了的（逐条列出，不静默丢）", "",
                 "| 码 | 文件:行 | 为什么规则不修 | 要作者做什么 |", "|-|-|-|-|"]
        for u in fx["unapplied"]:
            rows.append(f"| {u['code']} | `{u['file']}:{u['line']}` | {u['why']} | "
                        f"{u['author_action']} |")
    return "\n".join(rows) + "\n"


def _reproduce_md(prov: dict, build: dict, info: dict, args) -> str:
    rows = ["# 复现记录（按这份记录应得到同一份产物）", "",
            "## 1 版本", "",
            f"- 发行版：{prov.get('distribution') or '（未探测到 TeX 发行版）'}",
            f"- 引擎：`{prov.get('engine')}`（依据：{info.get('engine_reason')}）",
            "- 工具版本："]
    for k, v in (prov.get("toolchain_versions") or {}).items():
        rows.append(f"  - `{k}`：{v}")
    rows += ["", "## 2 命令与轮次", ""]
    cmds = prov.get("commands") or []
    if cmds:
        for i, c in enumerate(cmds, 1):
            rows.append(f"{i}. `{c}`")
    else:
        rows.append("（本机没有 TeX 发行版，未执行编译。装好 TeX Live 后按下面第 5 节命令跑。）")
    rows.append("")
    rows.append(f"- 轮次：{prov.get('n_rounds')}（"
                + "、".join(f"{r.get('engine')} rc={r.get('rc')} {r.get('seconds')}s"
                           for r in (prov.get("rounds") or [])) + "）" if prov.get("rounds")
                else "- 轮次：0（未编译）")
    rows += ["", "## 3 真实用到的模板/宏包/字体（来自 .fls 与日志）", ""]
    deps = prov.get("build_deps") or []
    if deps:
        rows.append(f"- `.fls` 记录 {len(deps)} 个依赖文件，前 20 个：")
        rows += [f"  - `{d}`" for d in deps[:20]]
    else:
        rows.append("- 无 `.fls`（未编译）：宏包版本待首次真实编译后由 build.py 自动记录")
    pkgs = prov.get("package_versions") or []
    if pkgs:
        rows.append("- 日志里的宏包版本：")
        rows += [f"  - `{p['name']}` {p['date']} {p['version']}" for p in pkgs[:20]]
    rows += ["", "## 4 产物哈希（对方跑完比对这一列）", ""]
    rows.append(f"- PDF：`{prov.get('pdf_sha256_16') or '（未生成）'}`")
    sh = prov.get("source_hashes") or {}
    if sh:
        rows.append("- 输入源码：")
        rows += [f"  - `{k}`：`{v}`" for k, v in list(sh.items())[:10]]
    rows += ["", "## 5 复跑步骤", "", "```bash",
             "# 1) 静态检查 + 修复（不需要 TeX）",
             "python run.py --input <输入目录> --work work --out out",
             "# 2) 只想离线解析一份已有日志",
             "python scripts/build.py --log <主文件>.log --root <输入目录> --out build.json",
             "# 3) 确认修复可复现（两次 fixed.tex 应逐字节一致）",
             "python -c \"import hashlib;print(hashlib.sha256(open('out/fixed.tex','rb').read()).hexdigest())\"",
             "```", "",
             "说明：本 Skill 全程 0 次模型请求、不联网；同一输入重复运行，`fixed.tex` 的 sha256 不变"
             "（规则按文件顺序单遍应用，没有随机量）。"]
    return "\n".join(rows) + "\n"


def _deliverables(out: Path, fixed_dir: Path, wrote: list[str],
                  pdf: Path | None) -> list[dict]:
    items = []
    for name in ("fixed.tex", "fixes.md", "changes.json", "fixed.diff", "report.md",
                 "reproduce.md", "result.json", "checks.json", "build.json", "toolchain.json"):
        p = out / name
        if p.exists():
            items.append({"kind": name.split(".")[0], "path": str(p),
                          "bytes": p.stat().st_size, "sha256_16": sha16(p.read_bytes())})
    for rel in wrote:
        p = fixed_dir / rel
        if p.exists():
            items.append({"kind": "fixed_tree", "path": str(p), "bytes": p.stat().st_size})
    if pdf and Path(pdf).exists():
        items.append({"kind": "pdf", "path": str(pdf), "bytes": Path(pdf).stat().st_size,
                      "sha256_16": sha16(Path(pdf).read_bytes())})
    return items


def _report_md(case_id: str, result: dict, info: dict, c1: dict, c2: dict, fx: dict,
               build: dict, coverage: list, args) -> str:
    L = [f"# LaTeX 检查与修复报告（case_id：{case_id}）", ""]
    L += ["## 1 工程识别", "",
          f"- 主文件：`{info['main']}`（识别依据：内容评分，不是文件名排序）",
          f"- 结构：{info['stats']['n_in_tree']} 个文件在树内，"
          f"{len(info.get('skipped_tex') or [])} 个未纳入",
          f"- 文档类：`{(info.get('documentclass') or {}).get('name')}`；"
          f"引擎：`{info.get('engine')}`（{info.get('engine_reason')}）",
          f"- 文献库：{', '.join(b['rel'] for b in info['bibs']) or '（无）'}；"
          f"graphicspath：{info.get('graphicspath') or '（无）'}", "",
          "| 文件 | 角色 | 行数 |", "|-|-|-|"]
    for f in info["files"]:
        L.append(f"| `{f['rel']}` | {f['role']} | {f['lines']} |")
    if info.get("skipped_tex"):
        L += ["", "未纳入检查：" + "、".join(f"`{s}`" for s in info["skipped_tex"])]
    L += ["", "## 2 原稿问题清单（每条带文件:行号）", "",
          f"共 {c1['n_problems']} 条：error {c1['n_error']} / warn {c1['n_warn']} / "
          f"info {c1['n_info']}", "",
          "| 码 | 严重度 | 位置 | 说明 |", "|-|-|-|-|"]
    for p in c1["problems"]:
        L.append(f"| {p['code']} | {p.get('severity')} | `{p['file']}:{p['line']}` | "
                 f"{p['detail']} |")
    if not c1["problems"]:
        L.append("| — | — | — | 无问题 |")
    L += ["", "## 3 修复记录", "",
          f"改动 {len(fx['changes'])} 处，明细与回退方式见 `fixes.md` / `changes.json`。", "",
          "| 码 | 位置 | 改前 | 改后 |", "|-|-|-|-|"]
    for c in fx["changes"]:
        L.append(f"| {c['code']} | `{c['file']}:{c['line']}` | `{c['before']}` | "
                 f"`{c['after']}` |")
    if not fx["changes"]:
        L.append("| — | — | — | 无可机械修复项 |")
    L += ["", "## 4 修复稿残留", "",
          f"复检 {c2['n_problems']} 条：error {c2['n_error']} / warn {c2['n_warn']} / "
          f"info {c2['n_info']}", "",
          "| 码 | 严重度 | 位置 | 说明 |", "|-|-|-|-|"]
    for p in c2["problems"]:
        L.append(f"| {p['code']} | {p.get('severity')} | `{p['file']}:{p['line']}` | "
                 f"{p['detail']} |")
    if not c2["problems"]:
        L.append("| — | — | — | 无残留 |")
    L += ["", "## 5 逐项处置（每个问题要么已修，要么写明为什么没修）", "",
          f"共 {len(coverage)} 条：已修 {sum(1 for c in coverage if c['disposition'].startswith('已修'))}"
          f" / 需作者处理 {sum(1 for c in coverage if c['disposition'] == '需作者处理')}"
          f" / 仅提示 {sum(1 for c in coverage if c['disposition'] == '仅提示')}", "",
          "| 问题 | 严重度 | 处置 | 依据/要作者做什么 |", "|-|-|-|-|"]
    for c in coverage:
        L.append(f"| `{c['problem']}` | {c['severity']} | {c['disposition']} | "
                 f"{c['evidence'] or '—'} |")
    L += ["", "## 6 编译记录", ""]
    if build.get("skipped") == "no_compile":
        L.append("- `--no-compile`：未编译。")
    elif build.get("skipped") == "no_tex_distribution":
        L.append(f"- 本机没有 `{build.get('engine')}`（也没有 latexmk）：未编译、未生成 PDF。")
        L.append("- 用固定结构闸门替代：T07 环境配对 / T08 花括号 / T11 表格列数 / "
                 "T12 数学定界符 / T18 文档骨架 全过 = 结构上可编译；"
                 f"修复稿这几项残留 {len(result['规则命中']['结构性残留'])} 条。")
    else:
        base = build.get("baseline") or {}
        L += [f"- 引擎：`{build.get('engine')}`；轮次：{len(build.get('rounds') or [])}",
              f"- 原稿编译错误 {base.get('n_errors')} 条 → 修复稿 {build.get('n_errors')} 条"
              f"（收敛 {build.get('error_reduction')}）",
              f"- 根因：{build.get('root_causes') or '（无）'}；级联 {build.get('n_cascades')} 条",
              f"- PDF：{build.get('pdf') or '（未生成）'}", "",
              "| 文件:行 | 类型 | 根因 | 是否级联 |", "|-|-|-|-|"]
        for e in (build.get("errors") or [])[:20]:
            L.append(f"| `{e.get('file')}:{e.get('line')}` | {e.get('kind')} | "
                     f"{e.get('root_cause')} | {'是' if e.get('cascade_of') is not None else '否'} |")
    L += ["", "## 7 复现", "",
          "见 `reproduce.md`：发行版与引擎版本、命令、轮次、宏包版本、产物 sha256、复跑步骤。", "",
          "## 8 边界与待确认", ""]
    L += [f"- {g}" for g in result["gaps"]] or ["- （无）"]
    L.append("")
    L += [f"- 不确定：{u}" for u in result["uncertainty"]]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="LaTeX 写作与编译")
    ap.add_argument("--input", default=None)
    ap.add_argument("--tex", default=None)
    ap.add_argument("--bib", default=None)
    ap.add_argument("--main", default=None)
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="output")
    ap.add_argument("--no-compile", action="store_true")
    ap.add_argument("--max-rounds", type=int, default=buildmod.MAX_ROUNDS)
    ap.add_argument("--timeout", type=int, default=buildmod.ROUND_TIMEOUT)
    args = ap.parse_args(argv)
    t0 = time.time()
    try:
        return _run(args, t0)
    except Exception as e:  # 任何异常都要留一份可读结果，不能空手返回
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        tb = traceback.format_exc()
        (out / "error.md").write_text("# 运行失败\n\n```\n" + tb[-4000:] + "\n```\n",
                                      encoding="utf-8")
        (out / "result.json").write_text(json.dumps(
            {"case_id": args.case_id or "case", "status": "blocked",
             "error": f"{type(e).__name__}: {e}",
             "gaps": [f"运行异常：{type(e).__name__}: {e}", "详见 error.md"],
             "uncertainty": ["本轮结果不完整：异常发生在产物落盘之前"],
             "deliverables": ["error.md"],
             "stats": {"model_requests": 0, "elapsed_s": round(time.time() - t0, 2)}},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked", "error": str(e)},
                         ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
