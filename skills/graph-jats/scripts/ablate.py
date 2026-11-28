#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ablate.py — 同输入消融：把 Skill 的机制逐项关掉，量化每一项的贡献。

这不是平台白板，也不冒充基础 Agent 的产出。它做的是同一份输入、同一段代码、
只切换一个机制的开关，看产物在哪些可核验指标上退化。判据全部由 run.py 自己
从落盘产物算出来（metrics 字段），不手写数字。

    python scripts/ablate.py --input examples --out <可写目录>

输出：<out>/ablation.json 与 <out>/ablation.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
sys.path.insert(0, str(SKILL_ROOT))

import run as R  # noqa: E402

VARIANTS = [
    ("full", (), "全部机制开启"),
    ("no_evidence", ("evidence",), "关掉证据绑定：节点与边只留空证据位"),
    ("no_align", ("align",), "关掉跨篇对齐：不产出合并/保留分离的判定"),
    ("no_direction", ("direction",), "关掉方向与关系词：不建实体之间的边，不记适用条件"),
    ("no_numbers", ("numbers",), "关掉数值抽取：不记 measurements"),
    ("all_off", ("evidence", "align", "direction", "numbers"), "全部关掉"),
]

METRIC_KEYS = [
    ("evidence_complete_rate", "证据三字段完备率", "越高越好"),
    ("evidence_verbatim_rate", "引文可在原段逐字命中率", "越高越好"),
    ("edge_direction_rate", "边带方向依据的比例", "越高越好"),
    ("edge_condition_rate", "边带适用条件的比例", "越高越好"),
    ("n_entity_entity_edges", "实体—实体边条数（比较/优于/支持/矛盾只能这么表达）", "越高越好"),
    ("n_cross_paper_records", "跨篇对齐判定条数", "越高越好"),
    ("n_merge_basis", "带依据的跨篇判定条数", "越高越好"),
    ("n_measurements", "逐篇数值条目", "越高越好"),
    ("queries_with_results", "有结果的查询数", "越高越好"),
    ("queries_with_subgraph", "带结果子图的查询数", "越高越好"),
    ("gates_ok", "机检通过项数", "越高越好"),
]


def one(name: str, disable: tuple, note: str, input_dir: str,
        out_root: Path) -> dict:
    out_dir = out_root / "_runs" / name
    res = R.pipeline(input_dir=input_dir, work=str(out_root / "_work" / name),
                     out=str(out_dir), case_id=f"ablate-{name}",
                     disable=disable, argv=["ablate.py", name])
    metrics = res.get("metrics") or {}
    return {"variant": name, "note": note, "disable": list(disable),
            "status": res.get("status"),
            "nodes": (res.get("stats") or {}).get("nodes"),
            "edges": (res.get("stats") or {}).get("edges"),
            "gaps": len(res.get("gaps") or []),
            "metrics": {k: metrics.get(k) for k, _d, _w in METRIC_KEYS},
            "out": str(out_dir)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="同输入消融：逐项关闭机制并对比产物")
    ap.add_argument("--input", default=str(SKILL_ROOT / "examples"))
    ap.add_argument("--out", default=str(SKILL_ROOT / "_ablation"))
    a = ap.parse_args(argv)
    out_root = Path(a.out)
    out_root.mkdir(parents=True, exist_ok=True)

    rows = [one(n, d, note, a.input, out_root) for n, d, note in VARIANTS]
    payload = {
        "what": "同输入消融：同一份材料、同一段代码，只切换一个机制开关",
        "not_a_baseline": "这不是平台白板，也不是基础 Agent 的产出；"
                          "它只证明每个机制在本实现里具体贡献了哪些可核验指标",
        "input": a.input,
        "variants": rows,
        "metrics_definition": {k: d for k, d, _w in METRIC_KEYS},
    }
    (out_root / "ablation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    L = ["# 同输入消融：每项机制贡献了哪些可核验指标", "",
         f"- 输入：`{a.input}`（同一份材料，所有变体共用）",
         "- 做法：同一段代码 + `--disable <机制>`，只关一个开关，其余不变",
         "- **这不是平台白板**，也不冒充基础 Agent 的产出；它只说明本实现里"
         "每个机制各自撑起了哪几个指标", "",
         "| 指标 | " + " | ".join(r["variant"] for r in rows) + " |",
         "|-|" + "|".join("-" for _ in rows) + "|"]
    for key, desc, _w in METRIC_KEYS:
        cells = []
        for r in rows:
            v = r["metrics"].get(key)
            cells.append("—" if v is None else f"{v}")
        L.append(f"| {desc} | " + " | ".join(cells) + " |")
    L += ["", "| 变体 | 关掉什么 | status | 节点 | 边 | gaps |",
          "|-|-|-|-|-|-|"]
    for r in rows:
        L.append(f"| {r['variant']} | {r['note']} | {r['status']} | {r['nodes']} | "
                 f"{r['edges']} | {r['gaps']} |")
    L += ["", "## 怎么读这张表", "",
          "- `evidence_*` 掉下来，说明节点与边就回不到原文片段了："
          "这条路只有证据绑定能补。",
          "- `n_entity_entity_edges` 掉到 0，说明比较/优于/支持/矛盾这四类关系"
          "只能靠方向与关系词窗口建，光靠「论文→实体」永远拿不到。",
          "- `n_cross_paper_records` 掉到 0，说明跨篇对齐不是副产品，"
          "是单独的判定步骤，且每条都带依据与冲突。",
          "- `n_measurements` 掉到 0，说明逐篇数值是单列的，"
          "不做跨篇汇总这件事本身也要有记录撑住。",
          "- `queries_with_subgraph` 掉下来，说明查询只回文本行、回不到子图。", ""]
    (out_root / "ablation.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "variants": [r["variant"] for r in rows],
                      "out": str(out_root), "markdown": "ablation.md"},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
