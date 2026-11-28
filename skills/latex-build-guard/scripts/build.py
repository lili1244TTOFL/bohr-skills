#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build.py — 真编译 + 日志解析到「文件:行号 + 根因」+ 可复现记录。

三件事：
  1. 编译链：有 latexmk 用 latexmk，没有就自己排引擎 → bibtex/biber → 再跑 2 轮，
     用 -file-line-error（从不用 -file-line-error，避免停在第一条错）与 -recorder。
  2. 日志解析：把 TeX 的报错拆成 {file, line, kind, raw}；缺宏包/缺字体/缺模板类单独识别
     （这类是环境缺口，不是作者的错），并把其后连锁报错标成 cascade_of（题面："不停在第一条
     错误信息上"、"报错级联"）。
  3. 可复现：记发行版与引擎版本、.fls 里真实用到的 .cls/.sty/.bst/.bib/字体、日志里
     `Package: xxx 2020-01-01 v1.2` 的宏包版本、跑过的命令与轮次、产物 sha256。

没有 TeX 发行版时不做任何假装：返回 skipped=no_tex_distribution，只给静态可编译性闸门。

用法：
    python build.py --input proj --main main.tex --out work/build.json
    python build.py --log proj/main.log --root proj --out work/build.json   # 离线解析已有日志
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

MAX_ROUNDS = 3            # 引擎轮次：首轮 + 最多 2 轮收敛交叉引用
ROUND_TIMEOUT = 180       # 单轮超时（秒）
TOTAL_TIMEOUT = 600       # 整条链超时（秒），给平台 900s 预算留余量

FILE_LINE = re.compile(r"^(?P<file>[^:\n]+\.(?:tex|sty|cls|ltx|def|cfg|bib|aux))"
                       r":(?P<line>\d+):\s*(?P<msg>.*)$")
BANG = re.compile(r"^!\s*(?P<msg>.*)$")
LNN = re.compile(r"^l\.(?P<line>\d+)\s*(?P<ctx>.*)$")
PKG_VER = re.compile(r"^Package:\s+(\S+)\s+(\d{4}/\d{2}/\d{2}|\S+)?\s*(v?\S+)?\s*$")
FONT_VER = re.compile(r"^(?:LaTeX Font Info|Font Info):\s*(.*)$")

# 错误签名 → (kind, 一句话根因, 处置提示, 对应静态码)。判据说明见 rules/errors.md。
SIGNATURES: tuple[tuple[str, str, str, str, str], ...] = (
    (r"File `([^']+\.sty)' not found", "missing_package",
     "宏包未安装：整条编译链在这一步断掉，后面的报错都是它的余波",
     "tlmgr install <包名>（.sty 名去掉扩展名；或用 TeX Live 的包管理器装对应 collection）；"
     "作者确实不需要就删掉该 \\usepackage", "T04"),
    (r"File `([^']+\.cls)' not found", "missing_class",
     "模板类文件缺失：文档类本身没装，版式无从谈起",
     "装对应的期刊/会议模板包（tlmgr install <类名>），或把 .cls 放进工程目录", "T18"),
    (r"File `([^']+)' not found", "missing_file",
     "引用的文件不存在（图片/子文件/文献库）",
     "确认路径与 \\graphicspath 一致，或把文件补进工程", "T09"),
    (r"fontspec Error: The font \"([^\"]+)\" cannot be found", "missing_font",
     "字体缺失：fontspec 找不到该字体，整份文稿可能只排出空白",
     "装字体（或改用系统已有字体），并确认引擎是 XeLaTeX/LuaLaTeX", "T16"),
    (r"Font \\[^ ]+ not loadable", "missing_font",
     "字体不可加载：编码/字体文件不匹配",
     "检查 fontenc/inputenc 与字体包；必要时换 XeLaTeX + fontspec", "T16"),
    (r"Missing \\$ inserted", "math_delim",
     "数学定界符不配对：正文里出现了只开不闭（或反之）的 $",
     "按 T12 定位行号，成对补 $ 或改用 \\[ \\]", "T12"),
    (r"Missing \\} inserted|Too many \\}'s|Missing \{ inserted", "brace",
     "花括号不配对：某个 { 没有闭合，错误位置常在报错行的上文",
     "按 T08 的净值往回报错行之前找", "T08"),
    (r"Misplaced alignment tab character", "table_col",
     "表格列数不符：某行 & 的数量与列声明不一致",
     "按 T11 的行号对齐 & 数量，或改列声明", "T11"),
    (r"Extra alignment tab has been changed to", "table_col",
     "表格多出 & ：该行单元格数超过列声明",
     "按 T11 的行号删掉多余的 &，或加列", "T11"),
    (r"\\begin\{([^}]+)\} on input line \d+ ended by \\end\{([^}]+)\}|"
     r"LaTeX Error: \\end\{([^}]+)\} on input line \d+ ended by \\end", "env_mismatch",
     "环境不配对：begin/end 名字不一致，TeX 会一路吞到 \\end{document}",
     "按 T07 的栈顶提示补上正确的 \\end", "T07"),
    (r"Undefined control sequence", "undefined_command",
     "未定义命令：要么宏包没装/没加载，要么命令拼错",
     "看紧随的上下文取命令名，核对该宏包是否 \\usepackage；拼错就改", "T01"),
    (r"LaTeX Error: \\documentclass|Missing \\begin\{document\}|"
     r"Missing \\end\{document\}", "doc_structure",
     "文档结构缺项：\\documentclass / \\begin{document} / \\end{document} 缺失",
     "按 T18 补齐文档骨架", "T18"),
    (r"Unicode character|inputenc Error", "encoding",
     "编码问题：源文件字符与 inputenc/fontenc 不匹配",
     "改 UTF-8 并加载 inputenc[utf8]，中文稿改用 ctex + XeLaTeX", "T08"),
    (r"TeX capacity exceeded|No room for a new", "capacity",
     "TeX 资源耗尽：通常由宏展开爆栈或超大表格引起",
     "检查递归宏定义/超大 tabular，必要时分页拆表", "T11"),
    (r"Emergency stop", "emergency",
     "TeX 已被迫中止：这是第一条致命错的收尾，不是新错误",
     "修掉它前面那条真正的错误", "T07"),
    (r"Runaway argument", "runaway",
     "参数失控：某个命令的 {...} 没闭合，TeX 把后文全吞了",
     "按上文补右花括号", "T08"),
    (r"LaTeX Warning: Reference `([^']+)' on page .* undefined", "undefined_ref",
     "交叉引用未定义（警告）：要么 label 不存在，要么需要再跑一轮",
     "确认 \\label 存在并再编译一轮；见 T02", "T02"),
    (r"LaTeX Warning: Citation `([^']+)' on page .* undefined", "undefined_cite",
     "文献引用未定义（警告）：cite 键不在 .bib 或 bibtex 未跑",
     "核对 T04，并确认 bibtex/biber 已执行", "T04"),
    (r"Package biblatex Warning: Please (?:re)run Biber|Please \(re\)run BibTeX", "rerun",
     "需要再跑一轮（bibtex/biber 或交叉引用未收敛）",
     "这是正常收敛信号：按轮次再跑", "T17"),
)


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def sha16(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def classify(msg: str) -> tuple[str, str, str, str]:
    """把一条报错文本映射成 (kind, 根因, 处置, 静态码)。"""
    for pat, kind, cause, hint, code in SIGNATURES:
        if re.search(pat, msg, re.I):
            return kind, cause, hint, code
    return "other", "未收录的错误签名", "看 build.log_tail 原文与 l.NN 上下文", ""


def extract_target(kind: str, msg: str) -> str:
    """从报错文本里抠出"缺的是什么"：.sty/.cls 名、字体名、文件路径。"""
    m = re.search(r"File `([^']+)'", msg)
    if m:
        return m.group(1)
    if kind == "missing_font":
        m = re.search(r'font "([^"]+)"', msg) or re.search(r"Font \\([^ ]+)", msg)
        if m:
            return m.group(1)
    return ""


def parse_log_text(text: str, root: Path | str = ".") -> dict:
    """解析 TeX 日志（优先 -file-line-error 形态，兼容 ! 与 l.NN 形态）。"""
    root = Path(root)
    lines = text.splitlines()
    errors: list[dict] = []
    cur_file: str | None = None
    pending: dict | None = None
    for i, ln in enumerate(lines):
        m = FILE_LINE.match(ln.strip())
        if m:
            msg = m.group("msg").strip()
            # 只把真报错算进来：`LaTeX Warning` 单独进 warnings
            kind, cause, hint, code = classify(msg)
            item = {"file": m.group("file").lstrip("./") if m.group("file") else "",
                    "line": int(m.group("line")), "raw": ln.strip()[:300],
                    "message": msg[:300], "kind": kind, "root_cause": cause,
                    "fix_hint": hint, "static_code": code,
                    "target": extract_target(kind, msg)}
            if "warning" in msg.lower():
                item.update({"kind": kind if kind != "other" else "warning",
                             "severity": "warn"})
            else:
                item["severity"] = "error"
            errors.append(item)
            pending = item
            cur_file = item["file"] or cur_file
            continue
        b = BANG.match(ln.strip())
        if b:
            msg = b.group("msg").strip()
            kind, cause, hint, code = classify(msg)
            item = {"file": cur_file or "", "line": None, "raw": ln.strip()[:300],
                    "message": msg[:300], "kind": kind, "root_cause": cause,
                    "fix_hint": hint, "static_code": code, "severity": "error",
                    "target": extract_target(kind, msg)}
            errors.append(item)
            pending = item
            continue
        lm = LNN.match(ln.strip())
        if lm and pending is not None and pending.get("line") is None:
            pending["line"] = int(lm.group("line"))
            ctx = lm.group("ctx").strip()
            if ctx:
                pending["context"] = ctx[:200]
                cm = re.search(r"\\([A-Za-z@]+)", ctx)
                if cm and pending["kind"] == "undefined_command":
                    pending["command"] = "\\" + cm.group(1)
            continue
        fm = FONT_VER.match(ln.strip())
        if fm and pending is not None:
            continue
    # 级联：环境缺口（缺宏包/类/字体）与 emergency 之后的报错都归到它下面
    cascade_root = None
    for idx, e in enumerate(errors):
        if e["kind"] in ("missing_package", "missing_class", "missing_font", "emergency",
                         "runaway") and e.get("severity") == "error":
            if cascade_root is None:
                cascade_root = idx
        elif cascade_root is not None and e.get("severity") == "error" and idx > cascade_root:
            e["cascade_of"] = cascade_root
    errs = [e for e in errors if e.get("severity") == "error"]
    warns = [e for e in errors if e.get("severity") == "warn"]
    kinds: dict[str, int] = {}
    for e in errs:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    return {"n_errors": len(errs), "n_warnings": len(warns), "n_cascades":
            sum(1 for e in errs if "cascade_of" in e), "kinds": kinds,
            "errors": errors,
            "first_error": errs[0] if errs else None,
            "root_causes": [{"kind": k, "count": n} for k, n in
                            sorted(kinds.items(), key=lambda kv: -kv[1])]}


def parse_package_versions(text: str) -> list[dict]:
    """日志里 `Package: geometry 2020-01-01 v5.9` 是宏包版本的真实来源。"""
    out: list[dict] = []
    seen: set[str] = set()
    for ln in text.splitlines():
        m = PKG_VER.match(ln.strip())
        if not m:
            continue
        name = m.group(1)
        if name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "date": m.group(2) or "", "version": m.group(3) or ""})
    return out


def parse_fls(p: Path) -> dict:
    """-recorder 生成的 .fls 列出本次编译真实读过的每个文件，用它记模板/宏包/字体来源。"""
    if not p.exists():
        return {"available": False, "inputs": []}
    inputs, outputs = [], []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.startswith("INPUT "):
            inputs.append(ln[6:].strip())
        elif ln.startswith("OUTPUT "):
            outputs.append(ln[7:].strip())
    inputs = list(dict.fromkeys(inputs))
    keep = tuple(x for x in (".cls", ".sty", ".bst", ".bib", ".def", ".cfg", ".clo",
                             ".ttf", ".otf", ".pfb", ".enc") if x)

    def ext_ok(s: str) -> bool:
        return s.lower().endswith(keep)

    dep = [i for i in inputs if ext_ok(i)]
    return {"available": True, "n_inputs": len(inputs), "n_outputs": len(outputs),
            "deps": dep, "outputs": outputs}


def probe_toolchain() -> dict:
    """引擎与发行版版本 —— 题面「复现得了：模板版本、宏包与字体版本、编译命令与轮次」。"""
    info: dict = {"bins": {}, "versions": {}, "distribution": None}
    for b in ("latexmk", "pdflatex", "xelatex", "lualatex", "bibtex", "biber", "kpsewhich",
              "tlmgr"):
        p = shutil.which(b)
        info["bins"][b] = p
        if not p:
            continue
        flag = "--version"
        try:
            r = subprocess.run([p, flag], capture_output=True, text=True, timeout=30,
                               encoding="utf-8", errors="replace")
            first = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
            info["versions"][b] = first[0][:200] if first else ""
            for ln in first[:6]:
                if re.search(r"TeX Live|MiKTeX|MacTeX|Tectonic", ln, re.I):
                    info["distribution"] = ln.strip()[:200]
        except Exception as e:  # 引擎在但调不动：如实记，不装作没有
            info["versions"][b] = f"（{b} --version 调用失败：{type(e).__name__}）"
    return info


def _run(cmd: list[str], cwd: Path, timeout: int) -> dict:
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        return {"cmd": cmd, "rc": r.returncode, "seconds": round(time.time() - t0, 2),
                "output": out[-20000:], "timeout": False}
    except subprocess.TimeoutExpired as e:
        out = e.output or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return {"cmd": cmd, "rc": None, "seconds": round(time.time() - t0, 2),
                "output": str(out)[-4000:], "timeout": True}
    except OSError as e:
        return {"cmd": cmd, "rc": None, "seconds": round(time.time() - t0, 2),
                "output": f"OSError: {e}", "timeout": False}


def run_build(root: Path | str, main_rel: str, build_dir: Path | str, engine: str = "pdflatex",
              max_rounds: int = MAX_ROUNDS, timeout: int = ROUND_TIMEOUT,
              total_timeout: int = TOTAL_TIMEOUT) -> dict:
    root, build_dir = Path(root), Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    main_name = Path(main_rel).name
    toolchain = probe_toolchain()
    if not toolchain["bins"].get(engine):
        return {"ok": False, "skipped": "no_tex_distribution",
                "engine": engine,
                "note": f"本机没有 {engine}（也没有 latexmk）：未编译。"
                        "静态检查与修复结果仍可用，PDF 未生成",
                "toolchain": toolchain, "rounds": [], "errors": [], "n_errors": 0,
                "pdf": None, "commands": []}
    t_all = time.time()
    rounds: list[dict] = []
    commands: list[list[str]] = []
    log_path = build_dir / (Path(main_name).stem + ".log")
    latexmk = toolchain["bins"].get("latexmk")
    if latexmk:
        flag = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}.get(
            engine, "-pdf")
        cmd = [latexmk, flag, "-interaction=nonstopmode", "-file-line-error", "-recorder",
               f"-outdir={build_dir}", main_name]
        commands.append(cmd)
        r = _run(cmd, root, timeout)
        rounds.append({"round": 1, "engine": "latexmk", "rc": r["rc"],
                       "seconds": r["seconds"], "timeout": r["timeout"]})
        if r["timeout"]:
            rounds[-1]["note"] = "单轮超时"
    else:
        base = [toolchain["bins"][engine], "-interaction=nonstopmode", "-file-line-error",
                "-recorder", f"-output-directory={build_dir}", main_name]
        r = _run(base, root, timeout)
        commands.append(base)
        rounds.append({"round": 1, "engine": engine, "rc": r["rc"], "seconds": r["seconds"],
                       "timeout": r["timeout"]})
        # bibtex / biber：看 aux / bcf 决定
        aux = build_dir / (Path(main_name).stem + ".aux")
        bcf = build_dir / (Path(main_name).stem + ".bcf")
        need_bibtex = False
        if aux.exists():
            try:
                need_bibtex = "\\bibdata" in aux.read_text(encoding="utf-8", errors="replace")
            except OSError:
                need_bibtex = False
        if bcf.exists() and toolchain["bins"].get("biber"):
            cmd = [toolchain["bins"]["biber"], "--input-directory", str(build_dir), f"{Path(main_name).stem}"]
            r2 = _run(cmd, root, timeout)
            commands.append(cmd)
            rounds.append({"round": len(rounds) + 1, "engine": "biber", "rc": r2["rc"],
                           "seconds": r2["seconds"], "timeout": r2["timeout"]})
        elif need_bibtex and toolchain["bins"].get("bibtex"):
            cmd = [toolchain["bins"]["bibtex"], f"{Path(main_name).stem}"]
            r2 = _run(cmd, build_dir, timeout)
            commands.append(cmd)
            rounds.append({"round": len(rounds) + 1, "engine": "bibtex", "rc": r2["rc"],
                           "seconds": r2["seconds"], "timeout": r2["timeout"]})
        for _ in range(max(1, max_rounds - 1)):
            if time.time() - t_all > total_timeout:
                rounds.append({"round": len(rounds) + 1, "engine": engine, "rc": None,
                               "note": "总预算用尽，未再跑"})
                break
            rr = _run(base, root, timeout)
            commands.append(base)
            rounds.append({"round": len(rounds) + 1, "engine": engine, "rc": rr["rc"],
                           "seconds": rr["seconds"], "timeout": rr["timeout"]})
            if rr["rc"] == 0 and log_path.exists() and \
                    "Rerun to get cross-references right" not in log_path.read_text(
                        encoding="utf-8", errors="replace"):
                break
    log_text = ""
    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    elif build_dir.joinpath(Path(main_name).stem + ".log").exists():
        log_text = build_dir.joinpath(Path(main_name).stem + ".log").read_text(
            encoding="utf-8", errors="replace")
    parsed = parse_log_text(log_text, root)
    pdf = None
    for cand in (build_dir / (Path(main_name).stem + ".pdf"),
                 root / (Path(main_name).stem + ".pdf")):
        if cand.is_file():
            pdf = cand
            break
    fls = parse_fls(build_dir / (Path(main_name).stem + ".fls"))
    ok = pdf is not None and parsed["n_errors"] == 0
    last_rc = next((r.get("rc") for r in reversed(rounds)
                    if r.get("engine") in (engine, "latexmk")), None)
    return {"ok": ok, "engine": engine, "rounds": rounds, "commands": commands,
            "pdf": str(pdf) if pdf else None, "returncode": last_rc,
            "n_errors": parsed["n_errors"], "n_warnings": parsed["n_warnings"],
            "n_cascades": parsed["n_cascades"], "kinds": parsed["kinds"],
            "errors": parsed["errors"], "first_error": parsed["first_error"],
            "root_causes": parsed["root_causes"],
            "log_tail": log_text[-1200:] if log_text else "",
            "log_path": str(log_path) if log_path.exists() else None,
            "package_versions": parse_package_versions(log_text),
            "fls": fls, "toolchain": toolchain,
            "skipped": None if ok else ("no_pdf" if pdf is None else "compile_failed"),
            "seconds": round(time.time() - t_all, 2)}


def provenance(root: Path | str, main_rel: str, build: dict, extra: dict | None = None) -> dict:
    """把"别人凭什么复现同一份产物"记全：版本、命令、轮次、输入/输出哈希。"""
    root = Path(root)
    ds = (build.get("toolchain") or {}).get("distribution")
    return {
        "distribution": ds,
        "engine": build.get("engine"),
        "toolchain_versions": (build.get("toolchain") or {}).get("versions", {}),
        "toolchain_bins": (build.get("toolchain") or {}).get("bins", {}),
        "commands": [" ".join(c) for c in build.get("commands", [])],
        "rounds": [{k: r.get(k) for k in ("round", "engine", "rc", "seconds", "timeout", "note")}
                   for r in build.get("rounds", [])],
        "n_rounds": len(build.get("rounds", [])),
        "package_versions": build.get("package_versions", []),
        "build_deps": (build.get("fls") or {}).get("deps", [])[:200],
        "fls_available": (build.get("fls") or {}).get("available", False),
        "pdf_sha256_16": sha16(Path(build["pdf"])) if build.get("pdf") else None,
        "log_path": build.get("log_path"),
        "main": main_rel,
        "source_hashes": {p.relative_to(root).as_posix(): sha16(p)
                          for p in sorted(root.rglob("*.tex")) if p.is_file()},
        "bib_hashes": {p.relative_to(root).as_posix(): sha16(p)
                       for p in sorted(root.rglob("*.bib")) if p.is_file()},
        "env": {k: os.environ.get(k, "") for k in ("SOURCE_DATE_EPOCH", "TEXMFHOME",
                                                    "TEXINPUTS", "PYTHONHASHSEED")},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "extra": extra or {},
    }


def _cli(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="LaTeX 编译链与日志解析")
    ap.add_argument("--input", default=None, help="工程目录")
    ap.add_argument("--main", default=None, help="主文件相对路径")
    ap.add_argument("--engine", default="pdflatex")
    ap.add_argument("--build-dir", default=None)
    ap.add_argument("--log", default=None, help="离线解析已有 .log（不编译）")
    ap.add_argument("--root", default=".", help="离线模式的工程根")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--timeout", type=int, default=ROUND_TIMEOUT)
    a = ap.parse_args(argv)
    if a.log:
        tp = Path(a.log)
        text = tp.read_text(encoding="utf-8", errors="replace")
        parsed = parse_log_text(text, a.root)
        res = {"mode": "offline_log", "log": str(tp), **parsed,
               "package_versions": parse_package_versions(text),
               "provenance": provenance(Path(a.root), a.main or "", {},
                                        {"log_parsed_offline": True})}
    else:
        if not a.input or not a.main:
            print("需要 --input 与 --main（或用 --log 离线解析）", file=sys.stderr)
            return 2
        bdir = Path(a.build_dir) if a.build_dir else Path(a.out).parent / "texbuild"
        res = run_build(a.input, a.main, bdir, a.engine, a.max_rounds, a.timeout)
        res["provenance"] = provenance(a.input, a.main, res)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": res.get("ok"), "skipped": res.get("skipped"),
                      "n_errors": res.get("n_errors"),
                      "kinds": res.get("kinds")}, ensure_ascii=False))
    return 0 if res.get("ok") or res.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
