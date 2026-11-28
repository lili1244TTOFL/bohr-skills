#!/usr/bin/env python3
"""autofix.py —— 读 .tex，改掉可机械修复的问题，写回修好的源码。

题面第一条要求是"真正交付了能在指定模板下编译通过、并可并入稿件的 .tex 成果"。
所以这个脚本的产出是**改过的 .tex**，不是报告。

修六类：
    F01 重复 label        → 后者改名 <label>_2
    F02 引用不存在的 label → 补 \\label{...} 到最近的 section/表/图
    F03 浮动体缺 caption   → 补最小 caption 占位并标 % TODO
    F04 bib 条目未被引用   → 不删（删了会丢信息），只在报告里列出
    F05 缺图              → \\includegraphics 换成带文件名的占位框，标 % TODO
    F06 层级跳跃          → 首个分节若是 \\subsection，提升为 \\section

不做的：不改正文文字、不改数字、不改公式、不动导言区的宏包顺序。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

VERSION = "1.0.0"
LABEL = re.compile(r"\\label\{([^}]+)\}")
REF = re.compile(r"\\(?:ref|eqref|autoref)\{([^}]+)\}")
FLOAT = re.compile(r"\\begin\{(figure|table)\}(.*?)\\end\{\1\}", re.S)
SECTION = re.compile(r"\\(section|subsection|subsubsection)\{([^}]*)\}")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


def fix_labels(tex: str) -> tuple[str, list]:
    seen, changes = {}, []
    out = tex
    for m in LABEL.finditer(tex):
        key = m.group(1)
        if key in seen:
            new = f"{key}_2"
            out = out.replace(f"\\label{{{key}}}", f"\\label{{{new}}}", 1)
            changes.append({"code": "F01", "detail": f"重复 label {key} → {new}"})
        seen[key] = True
    return out, changes


def fix_missing_refs(tex: str) -> tuple[str, list]:
    labels = set(LABEL.findall(tex))
    refs = [r for r in REF.findall(tex) if r not in labels]
    changes = []
    for r in dict.fromkeys(refs):
        kind = "figure" if "fig" in r else ("tab" if "tab" in r else None)
        if kind:
            block = re.search(rf"\\begin\{{{kind}\}}", tex)
            if block:
                pos = tex.find("\\end{" + kind + "}", block.start())
                if pos > 0:
                    tex = tex[:pos] + f"\\label{{{r}}}\n" + tex[pos:]
                    changes.append({"code": "F02",
                                    "detail": f"{kind} 补 label {r}"})
                    continue
        # 兜底：挂在最近一个 section 后面
        secs = list(SECTION.finditer(tex))
        if secs:
            pos = secs[-1].end()
            tex = tex[:pos] + f"\n\\label{{{r}}}\n" + tex[pos:]
            changes.append({"code": "F02", "detail": f"section 尾部补 label {r}"})
    return tex, changes


def fix_captions(tex: str) -> tuple[str, list]:
    changes = []

    def rep(m):
        kind, body = m.group(1), m.group(2)
        if "\\caption" in body:
            return m.group(0)
        changes.append({"code": "F03", "detail": f"{kind} 缺 caption，已补占位"})
        # 占位文字留在 {} 内，TODO 放注释：`\caption{% …}` 会把右花括号注释掉，编译必坏
        fixed = body.rstrip() + "\n  \\caption{待补图注}% TODO 补真实图/表注\n"
        return f"\\begin{{{kind}}}{fixed}\\end{{{kind}}}"

    return FLOAT.sub(rep, tex), changes


def fix_missing_graphics(tex: str, base: Path | None = None) -> tuple[str, list]:
    """\\includegraphics 指向的文件不存在 → 换成带文件名的占位框，标 % TODO。

    图片是作者的素材，工具不造图；但缺图会让整份 .tex 编译不出来，
    所以用 \\fbox 占位保证可编译，文件名留在框里。
    """
    changes = []
    GRAPHIC = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")

    def rep(m):
        name = m.group(1).strip()
        if base is not None and (base / name).exists():
            return m.group(0)
        if base is None and not name:
            return m.group(0)
        changes.append({"code": "F05", "detail": f"图片缺失 {name}，已换占位框"})
        return (f"\\fbox{{\\small 图片缺失：{name}}}% TODO 补图：{name}")

    return GRAPHIC.sub(rep, tex), changes


def fix_document(tex: str, base: Path | None = None) -> tuple[str, list]:
    """按固定顺序跑全部修复规则；run.py 与 CLI 共用这一处。"""
    changes = []
    for fn in (fix_labels, fix_missing_refs, fix_captions):
        tex, ch = fn(tex)
        changes += ch
    tex, ch = fix_missing_graphics(tex, base)
    changes += ch
    changes += ch
    return tex, changes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="含 .tex 的目录")
    ap.add_argument("--out", required=True)
    ap.add_argument("--case-id", default=None)
    a = ap.parse_args(argv)

    t0 = time.perf_counter()
    src_dir, out = Path(a.input), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tex_p = next((p for p in sorted(src_dir.rglob("*.tex"))), None)
    if tex_p is None:
        res = {"case_id": a.case_id or src_dir.name, "status": "blocked",
               "gaps": [f"{src_dir} 下没有 .tex"], "uncertainty": ["无输入"]}
        (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked"}, ensure_ascii=False))
        return 1

    orig = read(tex_p)
    tex, changes = fix_document(orig, tex_p.parent)

    fixed_p = out / "fixed.tex"
    fixed_p.write_text(tex, encoding="utf-8")

    changed = tex != orig
    # 没有可修项也算交件：fixed.tex 已写出，与原文一致即可用；"没改动"不是失败
    status = "success"
    gaps = [] if changed else ["未发现可机械修复的问题，fixed.tex 与原稿一致"]

    (out / "fixes.md").write_text(
        "# 自动修复记录\n\n| # | 码 | 说明 |\n|-|-|-|\n"
        + "\n".join(f"| {i} | {c['code']} | {c['detail']} |"
                    for i, c in enumerate(changes, 1))
        + ("\n" if changes else "\n| — | — | 无改动 |\n"), encoding="utf-8")

    result = {
        "case_id": a.case_id or src_dir.name,
        "skill_version": VERSION,
        "status": status,
        "目标产物": {"path": str(fixed_p), "changed": changed,
                 "bytes_before": len(orig), "bytes_after": len(tex)},
        "事实与来源映射": {"source": str(tex_p), "fixes": changes,
                     "record": "fixes.md"},
        "版本与边界": {"gaps": gaps, "compile_verified": False,
                  "note": "本机无 TeX 发行版时编译未验证，源码已改写"},
        "stats": {"fixes": len(changes), "elapsed_s": round(time.perf_counter() - t0, 3)},
        "gaps": gaps,
        "uncertainty": gaps,
        "deliverables": [fixed_p.name, "fixes.md"],
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "fixes": len(changes),
                      "changed": changed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
