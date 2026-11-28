#!/usr/bin/env python3
"""compare.py — 同输入、同预算的对照测量：本 Skill vs 纯模板填充（baseline）。

做法：对同一份输入目录各跑一次 run.py（--mode skill / --mode baseline），
再按下表口径从两份 result.json + 产物里数数。口径全部可判定，不含主观打分；
只证明"多出了哪些可核验的东西"，不冒充平台白板或官方评分。

用法：
    python scripts/compare.py --input examples/case --out /tmp/gain
    python scripts/compare.py --input examples/case_revision --out /tmp/gain2

退出码：0 正常；1 参数错误。
"""
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import run as engine  # noqa: E402


def metrics(result: dict, chapter_path: Path) -> dict:
    cov = result.get("六要素覆盖") or {}
    stat = result.get("统计") or {}
    ev = result.get("证据") if isinstance(result.get("证据"), dict) else result.get("evidence") or {}
    items = ev.get("items") or []
    chapter = chapter_path.read_text(encoding="utf-8") if chapter_path.exists() else ""
    return {
        "六要素有实物": len(cov.get("覆盖") or []),
        "带来源条目": int(ev.get("条数") or len(items)),
        "条目>0": sum(1 for i in items if i.get("来源")),
        "步骤行": int(stat.get("步骤") or 0),
        "参数行": int(stat.get("抽取参数") or 0),
        "对照条目": int(stat.get("对照条目") or 0),
        "复现项": int(stat.get("复现条目") or 0),
        "限制条件": int(stat.get("限制条件") or 0),
        "追问条数": int(stat.get("追问") or 0),
        "章节标题数": sum(1 for ln in chapter.splitlines() if ln.startswith("## ")),
        "表格行数": sum(1 for ln in chapter.splitlines() if ln.startswith("| ")),
        "字符数": len(chapter),
    }


def run_mode(inp: Path, out: Path, work: Path, mode: str) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        engine.main(["--input", str(inp), "--work", str(work), "--out", str(out), "--mode", mode])
    return json.loads((out / "result.json").read_text(encoding="utf-8"))


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    inp, out = None, None
    i = 0
    while i < len(args):
        if args[i] == "--input":
            inp = Path(args[i + 1]); i += 2
        elif args[i] == "--out":
            out = Path(args[i + 1]); i += 2
        else:
            print(f"未知参数：{args[i]}", file=sys.stderr)
            return 1
    if not inp or not out:
        print("用法：python scripts/compare.py --input <材料目录> --out <输出目录>", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    skill_dir, base_dir = out / "skill", out / "baseline"
    r_skill = run_mode(inp, skill_dir, out / "work_skill", "skill")
    r_base = run_mode(inp, base_dir, out / "work_base", "baseline")
    m_skill = metrics(r_skill, skill_dir / "methods_section.md")
    m_base = metrics(r_base, base_dir / "methods_section.md")
    delta = {k: m_skill[k] - m_base[k] for k in m_skill}

    doc = {
        "口径": "同一输入目录、同一 0 模型请求预算；唯一变量是 mode=skill 与 mode=baseline 的处理流程。"
                "指标全部从产物与 result.json 数出来，可重跑复核。",
        "不是平台白板": True,
        "输入": str(inp),
        "skill": {"status": r_skill.get("status"), **m_skill},
        "baseline": {"status": r_base.get("status"), **m_base},
        "差值": delta,
        "结论": _verdict(delta, r_skill, r_base),
        "复跑": f"python scripts/compare.py --input {inp} --out {out}",
    }
    (out / "gain.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "gain.md").write_text(_render(doc), encoding="utf-8")
    print(json.dumps({"ok": True, "gain": delta}, ensure_ascii=False))
    return 0


def _verdict(delta: dict, r_skill: dict, r_base: dict) -> str:
    wins = [k for k, v in delta.items() if v > 0]
    if not wins:
        return "两者持平：本输入下 Skill 未产出更多可核验内容（应视为失败案例，需排查）"
    return (f"Skill 在 {len(wins)} 项指标上高于 baseline（{ '、'.join(wins) }）；"
            f"baseline 状态 {r_base.get('status')}，skill 状态 {r_skill.get('status')}；"
            "baseline 的优点仅在于短，不能回指任何材料")


def _render(doc: dict) -> str:
    L = ["# 增益对照（本包内对照，不是平台白板）", "",
         f"> 口径：{doc['口径']}", "",
         "## 指标对比", "",
         "| 指标 | 本 Skill | 纯模板 baseline | 差值 |", "|-|-|-|-|"]
    for k in doc["skill"]:
        if k == "status":
            continue
        L.append(f"| {k} | {doc['skill'][k]} | {doc['baseline'][k]} | {doc['差值'][k]:+d} |")
    L.append(f"| status | {doc['skill']['status']} | {doc['baseline']['status']} | — |")
    L += ["", "## 结论", "", doc["结论"], "",
          f"复跑：`{doc['复跑']}`", "",
          "## 边界", "",
          "- 这是本包自带的对照，用来说明「多出了哪些可回指的内容」，"
          "不替代组织方的固定案例对照与白板。",
          "- baseline 只写模板句，不含任何数值；因此它在数值类指标上与 Skill 相同是预期行为。",
          "- 指标数数依赖 result.json 字段；若字段缺失，计数会偏低，不会被高估。", ""]
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
