#!/usr/bin/env python3
"""selftest.py — 回归题集：换材料形态、重复运行，检查产物与状态是否符合预期。

题集覆盖六种形态：完整材料 / 带缺陷原稿 / 缺数据集版本 / 只有日志 / 只有正文 / 空目录。
每条断言都是"能判真假"的：字段存在、状态取值、条数下限、产物文件存在。
用法：python scripts/selftest.py
退出码：0 全过；1 有失败。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import run as engine  # noqa: E402

from contextlib import redirect_stdout  # noqa: E402
import io  # noqa: E402


def _write(d: Path, files: dict) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


def _run(inp: Path, out: Path) -> tuple[dict, int]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = engine.main(["--input", str(inp), "--work", str(out / "_w"), "--out", str(out)])
    return json.loads((out / "result.json").read_text(encoding="utf-8")), rc


CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("完整材料 → success")
def c1(base: Path):
    out = base / "c1"
    r, rc = _run(ROOT / "examples" / "case", out)
    return [
        ("exit=0", rc == 0),
        ("status=success", r["status"] == "success"),
        ("六要素全覆盖", len(r["六要素覆盖"]["缺失"]) == 0),
        ("证据条数≥30", r["evidence"]["条数"] >= 30),
        ("无冲突", not r["冲突"]),
        ("复现五项齐", not r["复现清单"]["缺项"]),
        ("四产物存在", all((out / f).exists() for f in
                        ("methods_section.md", "params.tsv", "repro.md", "questions.md"))),
    ]


@case("带缺陷原稿 → partial + 改值 + 无来源")
def c2(base: Path):
    out = base / "c2"
    r, rc = _run(ROOT / "examples" / "case_revision", out)
    diffs = {d["类型"] for d in r["修改差异"]}
    return [
        ("exit=0", rc == 0),
        ("status=partial", r["status"] == "partial"),
        ("有改值行", "改值" in diffs),
        ("有补入行", "补入" in diffs),
        ("无来源数值=1", r["统计"]["无来源数值"] == 1),
        ("正文不符=1", r["统计"]["正文与材料不符"] == 1),
        ("版本不明进 gaps", any("数据集版本不明" in g for g in r["gaps"])),
        ("追问≥10", len(r["追问清单"]) >= 10),
    ]


@case("参数冲突与版本缺失 → 冲突 + partial")
def c3(base: Path):
    out = base / "c3"
    inp = _write(base / "in" / "c3", {
        "config.yaml": "lr: 3e-4\nbatch_size: 64\nseed: 7\ndataset: SQuAD\n",
        "train.log": "2026-03-01 08:00 start training\n2026-03-01 09:00 lr=1e-3 epoch 3 done\n",
        "case.json": '{"case_id":"conflict-demo"}',
    })
    r, rc = _run(inp, out)
    return [
        ("exit=0", rc == 0),
        ("status=partial", r["status"] == "partial"),
        ("学习率冲突", any(c["名称"] == "学习率" for c in r["冲突"])),
        ("版本不明", any("数据集版本不明" in g for g in r["gaps"])),
        ("冲突进追问", any(q["类型"] == "配置冲突" for q in r["追问清单"])),
    ]


@case("只有日志 → 从日志抽参数与步骤")
def c4(base: Path):
    out = base / "c4"
    inp = _write(base / "in" / "c4", {
        "run.log": ("2026-04-02 10:00 start training lr=2e-5 batch_size=32\n"
                    "2026-04-02 11:00 epoch 5 done, val_acc=0.81\n"),
    })
    r, rc = _run(inp, out)
    names = {i["名称"] for i in r["evidence"]["items"]}
    return [
        ("exit=0", rc == 0),
        ("状态非 blocked", r["status"] in {"success", "partial"}),
        ("抽到学习率", "学习率" in names),
        ("抽到批大小", "批大小" in names),
        ("有步骤", r["统计"]["步骤"] >= 1),
        ("无正文事项标注", any("未提供正文" in g for g in r["gaps"])),
    ]


@case("只有正文 → partial，不 blocked，给追问")
def c5(base: Path):
    out = base / "c5"
    inp = _write(base / "in" / "c5", {
        "manuscript.md": "# 方法\n\n我们在 8 张 V100 上训练，学习率 1e-4，训练 30 个 epoch。\n",
    })
    r, rc = _run(inp, out)
    return [
        ("exit=0", rc == 0),
        ("status=partial", r["status"] == "partial"),
        ("六要素全缺", len(r["六要素覆盖"]["缺失"]) == 6),
        ("追问≥3", len(r["追问清单"]) >= 3),
        ("章节已出", (out / "methods_section.md").exists()),
    ]


@case("空目录 → blocked + missing_input.md")
def c6(base: Path):
    out = base / "c6"
    inp = base / "in" / "c6"
    inp.mkdir(parents=True, exist_ok=True)
    r, rc = _run(inp, out)
    return [
        ("exit=1", rc == 1),
        ("status=blocked", r["status"] == "blocked"),
        ("missing_input.md 存在", (out / "missing_input.md").exists()),
        ("三字段平铺", all(k in r for k in ("目标产物", "事实与来源映射", "版本与边界"))),
    ]


@case("重复运行结果稳定")
def c7(base: Path):
    a, _ = _run(ROOT / "examples" / "case", base / "c7a")
    b, _ = _run(ROOT / "examples" / "case", base / "c7b")
    keys = ("status", "六要素覆盖", "统计", "冲突", "gaps")
    same = all(json.dumps(a[k], sort_keys=True, ensure_ascii=False)
               == json.dumps(b[k], sort_keys=True, ensure_ascii=False) for k in keys)
    return [("两次结果一致", same)]


def main() -> int:
    fails = 0
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        for name, fn in CASES:
            checks = fn(base)
            bad = [c for c, ok in checks if not ok]
            fails += len(bad)
            mark = "PASS" if not bad else "FAIL"
            print(f"[{mark}] {name}（{len(checks) - len(bad)}/{len(checks)}）")
            for c in bad:
                print(f"       ✗ {c}")
    print(f"\n合计：{len(CASES)} 组题，{fails} 条断言失败")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
