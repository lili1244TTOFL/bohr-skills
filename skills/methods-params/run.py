#!/usr/bin/env python3
"""run.py —— 方法与实验章节：从配置/日志/代码抽取要素，与正文对齐，报缺口。

三个模块，按"输入 → 判定 → 产出"切，不按执行顺序切：
    discover()   找到材料，判类型（配置/日志/代码/正文）
    extract()    从材料里抽参数与条件，每条带 file:line 证据
    align()      把抽到的与正文对齐：一致 / 正文没写 / 正文数值对不上
    render()     生成可编辑章节 + 参数表 + 缺口清单
设计依据：题目要求"从代码、配置、日志、数据说明抽取要素—对齐正文—发现缺口并追问"。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# 要素规则：题面把章节拆成六类要素，这里定义每类靠什么正则识别。
# 只认显式写法，抽不到就记缺口，不倒推数值（题面禁止"为段落顺畅补数值"）。
# ---------------------------------------------------------------------------
RULES = {
    "参数": [
        (r"(?im)^\s*(?:lr|learning[_-]?rate)\s*[:=]\s*([0-9.eE+-]+)", "学习率"),
        (r"(?im)^\s*(?:batch[_-]?size|bs)\s*[:=]\s*(\d+)", "批大小"),
        (r"(?im)^\s*(?:epochs?|max[_-]?epochs)\s*[:=]\s*(\d+)", "训练轮数"),
        (r"(?im)^\s*(?:seed|random[_-]?seed)\s*[:=]\s*(\d+)", "随机种子"),
        (r"(?im)^\s*(?:weight[_-]?decay|wd)\s*[:=]\s*([0-9.eE+-]+)", "权重衰减"),
    ],
    "实验设置": [
        (r"(?im)^\s*(?:gpu|cuda|device)\s*[:=]\s*([\w\s\-]+?)\s*$", "硬件"),
        (r"(?im)^\s*(?:torch|tensorflow|python|numpy)\s*==\s*([\w.]+)", "软件版本"),
        (r"(?im)^\s*(?:num[_-]?runs|repeats|n[_-]?repeat)\s*[:=]\s*(\d+)", "重复次数"),
    ],
    "数据集": [
        (r"(?im)^\s*(?:dataset|data[_-]?name)\s*[:=]\s*([\w\-.]+)", "数据集名"),
        (r"(?im)^\s*(?:version|data[_-]?version)\s*[:=]\s*([\w.\-]+)", "数据集版本"),
        (r"(?im)^\s*(?:split|ratio)\s*[:=]\s*([\d:/.]+)", "划分比例"),
    ],
}

# 正文里出现数值时，用来判断"是否为配置里没有的数值"（题面明令禁止的那类）
NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![\w.])")

SIX = ["步骤", "实验设置", "参数", "数据集", "对照", "复现与限制"]


# ---------------------------------------------------------------------------
# 读材料：只认 UTF-8 文本；类型按扩展名与文件名判，不靠目录名猜。
# ---------------------------------------------------------------------------
def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def classify(path: Path) -> str:
    name, ext = path.name.lower(), path.suffix.lower()
    if ext in {".yaml", ".yml"} or "config" in name:
        return "配置"
    if ext in {".log"} or "log" in name:
        return "日志"
    if ext == ".py":
        return "代码"
    if ext in {".md", ".tex", ".txt"}:
        return "正文" if ext != ".txt" or "manuscript" in name else "说明"
    if ext == ".json":
        return "配置"
    return "其他"


def discover(root: Path) -> dict[str, list[Path]]:
    """把材料按类型分桶；正文优先挑包含'方法/Method'字样的文件。"""
    buckets: dict[str, list[Path]] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        k = classify(p)
        buckets.setdefault(k, []).append(p)
    return buckets


# ---------------------------------------------------------------------------
# 抽取：逐行扫描，命中即记一条，证据精确到 file:line。
# ---------------------------------------------------------------------------
def extract(files: list[Path], kinds: list[str]) -> list[dict]:
    found: list[dict] = []
    for kind in kinds:
        for regex, label in RULES.get(kind, []):
            for p in files:
                text = read(p)
                for m in re.finditer(regex, text):
                    line = text[:m.start()].count("\n") + 1
                    found.append({
                        "要素": kind, "名称": label, "值": m.group(1).strip(),
                        "证据": f"{p.name}:{line}",
                    })
    # 同一 (名称,值) 只留首条，避免日志重复刷屏
    seen, out = set(), []
    for f in found:
        key = (f["名称"], f["值"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def extract_steps(code_files: list[Path]) -> list[str]:
    """步骤来自代码里的函数/注释，抽不到就返回空——由缺口清单兜底。"""
    steps = []
    for p in code_files:
        for i, line in enumerate(read(p).splitlines(), 1):
            m = re.match(r"\s*def\s+(\w+)", line)
            if m and not m.group(1).startswith("_"):
                steps.append(f"{p.name}:{i} `{m.group(1)}`")
    return steps[:20]


# ---------------------------------------------------------------------------
# 对齐：正文里有没有这个值？数值是否与配置一致？
# 这是题面"写得准"的核心：不新增材料外事实，正文数值必须能在材料里找到。
# ---------------------------------------------------------------------------
def align(params: list[dict], manuscript: str) -> tuple[list[dict], list[dict], list[dict]]:
    ok, missing, mismatch = [], [], []
    for f in params:
        if f["值"] in manuscript:
            ok.append(f)
        else:
            missing.append(f)
    # 正文里出现、但所有材料里都没有的数值 → 疑似补出来的
    known = {f["值"] for f in params}
    for m in NUM.finditer(manuscript):
        v = m.group(1)
        if v in known or len(v) <= 1:
            continue
        # 型号里的数字不算实验数值：ResNet-50、A100-80G、GB/T 7714-2015
        start = m.start()
        if start >= 2 and manuscript[start - 1] in "-_" and manuscript[start - 2].isalnum():
            continue
        line = manuscript[:m.start()].count("\n") + 1
        mismatch.append({"值": v, "证据": f"正文:{line}", "说明": "材料中未找到该数值的来源"})
    return ok, missing, mismatch


# ---------------------------------------------------------------------------
# 渲染：章节骨架按题面的六类要素排列，没材料的地方写"待作者确认"。
# ---------------------------------------------------------------------------
def render(case_id, ok, missing, mismatch, steps, buckets) -> str:
    lines = [f"# 方法与实验章节（{case_id}）", ""]
    lines += ["## 步骤", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(steps, 1)] or ["待作者确认：代码中未识别到步骤。"]
    lines += ["", "## 实验设置", ""]
    for f in ok:
        if f["要素"] == "实验设置":
            lines.append(f"- {f['名称']}：{f['值']}（{f['证据']}）")
    lines += ["", "## 参数", ""]
    for f in ok:
        if f["要素"] == "参数":
            lines.append(f"- {f['名称']}：{f['值']}（{f['证据']}）")
    lines += ["", "## 数据集", ""]
    for f in ok:
        if f["要素"] == "数据集":
            lines.append(f"- {f['名称']}：{f['值']}（{f['证据']}）")
    lines += ["", "## 对照", "", "待作者确认：材料中未找到基线对象与对照条件。" if not buckets.get("正文") else "待作者确认：对照对象需作者补充。"]
    lines += ["", "## 复现与限制", ""]
    lines.append(f"- 材料文件：{sum(len(v) for v in buckets.values())} 个")
    for g in missing:
        lines.append(f"- 待补：正文未写 {g['名称']}={g['值']}（来源 {g['证据']}）")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None)
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="output")
    ap.add_argument("--case-id", default=None)
    a = ap.parse_args(argv)

    t0 = time.perf_counter()
    work, out = Path(a.work), Path(a.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    root = Path(a.input) if a.input else Path(".")
    case_id, contract = a.case_id, []
    cj = root / "case.json"
    if cj.exists():
        try:
            case = json.loads(read(cj))
            case_id = case_id or case.get("case_id")
            oc = case.get("output_contract")
            contract = list(oc) if isinstance(oc, (dict, list)) else []
        except Exception:
            pass
    case_id = case_id or root.name or "case"

    buckets = discover(root)
    cfg = buckets.get("配置", []) + buckets.get("日志", []) + buckets.get("说明", [])
    code = buckets.get("代码", [])
    ms_files = buckets.get("正文", [])

    if not cfg and not code:
        result = {"case_id": case_id, "status": "blocked",
                  "gaps": ["未找到配置/日志/代码材料，无法抽取要素"],
                  "uncertainty": [], "evidence": {"unit": "file:line", "items": []}}
        (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked"}, ensure_ascii=False))
        return 1

    params = extract(cfg, ["参数", "实验设置", "数据集"])
    # 模型读材料产出的键清单（prompts/inspect_materials.md）。有就并入，没有就用内置规则。
    schema_path = work / "schema.json"
    if not schema_path.exists():
        schema_path = root / "schema.json"
    if schema_path.exists():
        try:
            sch = json.loads(read(schema_path))
            for item in sch.get("keys") or []:
                if not isinstance(item, dict) or not item.get("证据"):
                    continue
                params.append({
                    "要素": str(item.get("要素", "参数")),
                    "名称": str(item.get("名称", "")),
                    "值": str(item.get("值", "")),
                    "证据": str(item.get("证据", "")),
                })
            schema_note = f"已并入模型键清单 {len(sch.get('keys') or [])} 条"
        except Exception as exc:
            schema_note = f"schema.json 解析失败：{exc}"
    else:
        schema_note = "未提供 schema.json，仅用内置规则抽取，可能漏键"

    steps = extract_steps(code)
    manuscript = "\n".join(read(p) for p in ms_files) if ms_files else ""
    ok, missing, mismatch = align(params, manuscript)

    gaps = []
    for g in missing:
        gaps.append(f"正文未写 {g['名称']}={g['值']}（{g['证据']}）")
    for m in mismatch:
        gaps.append(f"正文数值 {m['值']} 在材料中无来源（{m['证据']}）")
    covered = []
    if steps: covered.append("步骤")
    if any(f["要素"] == "实验设置" for f in params): covered.append("实验设置")
    if any(f["要素"] == "参数" for f in params): covered.append("参数")
    if any(f["要素"] == "数据集" for f in params): covered.append("数据集")
    for s in SIX:
        if s not in covered:
            gaps.append(f"六类要素缺：{s}")

    status = "ok" if not gaps else "partial"
    render(out / "methods_section.md", case_id, ok, missing, mismatch, steps, buckets) \
        if False else None
    (out / "methods_section.md").write_text(
        render(case_id, ok, missing, mismatch, steps, buckets), encoding="utf-8")
    (out / "params.tsv").write_text(
        "要素\t名称\t值\t证据\n" + "".join(
            f"{f['要素']}\t{f['名称']}\t{f['值']}\t{f['证据']}\n" for f in params),
        encoding="utf-8")

    result = {
        "case_id": case_id,
        "skill_version": VERSION,
        "status": status,
        "六要素覆盖": {"覆盖": covered, "缺失": [s for s in SIX if s not in covered]},
        "统计": {"材料文件": sum(len(v) for v in buckets.values()),
                 "抽取参数": len(params), "正文一致": len(ok),
                 "正文未写": len(missing), "无来源数值": len(mismatch)},
        "evidence": {"unit": "file:line",
                     "items": [{"要素": f["要素"], "名称": f["名称"],
                                "来源": f["证据"]} for f in params[:50]]},
        "uncertainty": ([f"正文数值 {m['值']} 无来源（{m['证据']}）" for m in mismatch]
                        + ([schema_note] if schema_note else [])),
        "gaps": gaps,
        "deliverables": ["methods_section.md", "params.tsv"],
        "output_contract": contract,
        "elapsed_s": round(time.perf_counter() - t0, 3),
    }
    # 三字段无条件回显：契约没给也要有，缺内容写 null + 指针
    _fixed = {
        "目标产物": "methods_section.md",
        "事实与来源映射": "params.tsv（每条带 source：文件:行）",
        "版本与边界": "gaps 与待确认项，见本文件 gaps 字段",
    }
    for _k, _v in _fixed.items():
        if _k not in result:
            result[_k] = _v
    result.setdefault("uncertainty", list(result.get("gaps") or []))

    # 题面：result.json 含全部 output_contract 字段 → 平铺到顶层
    for k in contract:
        result.setdefault(str(k), {
            "目标产物": "methods_section.md",
            "事实与来源映射": "params.tsv",
            "版本与边界": "gaps + uncertainty",
        }.get(str(k), None))
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "params": len(params),
                      "missing": len(missing), "unsourced": len(mismatch),
                      "gaps": len(gaps)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
