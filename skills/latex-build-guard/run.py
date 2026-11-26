#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py — LaTeX 写作与编译 Skill 的编排入口。

步骤：
  0 读输入：case.json（case_id、output_contract）、嗅探 .tex/.bib
  1 静态检查：texcheck.py（T01–T10）
  2 可选编译：有 latexmk/pdflatex 才编译，输出 PDF 与日志；没有则如实记缺口
  3 落盘：report.md、checks.json、result.json（含编译状态）

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
        print(json.dumps({"ok": False, "status": "blocked", "error": "找不到 .tex 文件"},
                         ensure_ascii=False))
        return 1
    bib = Path(args.bib) if args.bib else find(tex.parent, ".bib")

    mod = load(Path(__file__).parent / "scripts" / "texcheck.py")
    problems = mod.check(tex.read_text(encoding="utf-8-sig", errors="replace"),
                         mod.bib_keys(bib), tex.parent)
    counts: dict[str, int] = {}
    for x in problems:
        counts[x["code"]] = counts.get(x["code"], 0) + 1
    fatal = [x for x in problems if x.get("severity") != "info"]
    checks = {"ok": not fatal, "n_problems": len(problems), "n_fatal": len(fatal),
              "counts": counts, "problems": problems}
    (work / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=1),
                                      encoding="utf-8")

    gaps: list[str] = []
    build = {"ok": False, "skipped": "no_compile"}
    if not args.no_compile:
        build = compile_tex(tex, out)
        if build.get("skipped"):
            gaps.append(build["skipped"] + "：" + build.get("note", ""))
        elif not build["ok"]:
            gaps.append(f"编译失败：rc={build.get('returncode')}")

    report = ["# LaTeX 检查报告", "",
              f"- 源文件：`{tex.name}`" + (f"，文献库：`{bib.name}`" if bib else ""),
              f"- 检查：{checks['n_problems']} 条（致命 {checks['n_fatal']}）",
              f"- 编译：{'通过' if build.get('ok') else ('跳过 ' + str(build.get('skipped')))}",
              "", "## 问题", ""]
    for x in problems:
        report.append(f"- `{x['code']}` {x.get('severity', 'error')}：{x['detail']}")
    if not problems:
        report.append("- 无")
    (out / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    deliverables = [{"kind": "report", "path": str(out / "report.md"),
                     "bytes": (out / "report.md").stat().st_size}]
    if build.get("pdf"):
        deliverables.append({"kind": "pdf", "path": build["pdf"],
                             "bytes": Path(build["pdf"]).stat().st_size})
    rules = [
        # 平台字段名精确匹配（先于模糊规则；改动见 review/ISSUES.md X-01）
        (r"^目标产物", "report.md"),
        (r"^事实与来源映射", "report.md"),
        (r"^版本与边界", "report.md"),
        (r"^阅读[/／]解析结果", "report.md"),
        (r"^证据与边界", "report.md"),
        (r"^运行状态", "report.md"),
        (r"report|报告|issue|问题", "report.md"),
        (r"pdf|编译|build", "report.md"),
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
            echo_status[key] = {"status": "full" if build.get("pdf") or "pdf" not in key.lower()
                                else "partial", "source": hit}
        else:
            echo[key] = {"note": "本字段无专门产出，见 report.md", "pointer": ["report.md"]}
            echo_status[key] = {"status": "partial", "source": "pointer"}

    status = "success" if checks["ok"] and build.get("ok") else "partial"
    result = {"case_id": case_id, "status": status,
              "source": {"tex": str(tex), "bib": str(bib) if bib else None,
                         "n_lines": len(tex.read_text(encoding="utf-8", errors="replace")
                                        .splitlines())},
              "stats": {"problems": checks["n_problems"], "fatal": checks["n_fatal"],
                        "by_code": counts, "compiled": bool(build.get("ok")),
                        "elapsed_s": round(time.time() - t0, 2)},
              "build": build, "deliverables": deliverables,
              "evidence": {"unit": "file + code",
                           "note": "每条问题给码与位置描述，可回源码定位"},
              "checks": {"静态检查": {"ok": checks["ok"], "detail": counts},
                         "编译": {"ok": bool(build.get("ok")),
                                  "detail": build.get("skipped") or build.get("returncode")}},
              "gaps": gaps, "output_contract": echo, "output_contract_echo": echo_status}
    result["目标产物"] = {"path": str(deliverables[0]["path"]) if deliverables else None,
                     "note": "源码已就地检查，报告列逐条问题与修复建议"}
    result["事实与来源映射"] = {"unit": "file + code",
                        "items": [{"code": c, "count": n} for c, n in counts.items()]}
    result["版本与边界"] = {"gaps": gaps, "compiled": bool(build.get("ok")),
                     "skipped": build.get("skipped")}
    result.setdefault("uncertainty", list(gaps))
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "problems": checks["n_problems"],
                      "fatal": checks["n_fatal"], "compiled": bool(build.get("ok")),
                      "gaps": gaps}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
