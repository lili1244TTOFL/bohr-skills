#!/usr/bin/env python3
"""selftest.py —— polish-academic 自查脚本：14 个用例，零模型请求。

用法：
    python scripts/selftest.py            # 全部用例，打印表格，全部通过退出码 0
    python scripts/selftest.py -v         # 附带每条实际结果

每个用例在临时目录里造输入、跑 run.py 的 main()、读回 result.json 断言。
不改包内任何文件，也不留 work/ 或 __pycache__。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run.py"


def load_run():
    spec = importlib.util.spec_from_file_location("polish_run", RUN)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


RUN_MOD = load_run()

EN_SRC = ("We did not observe any recovery in the big sample. In order to test the effect, "
          "a lot of participants were assigned, and the results were clearly very "
          "significant. The data was collected in 2021 [2]. Fig. 2 shows the 15% decline.\n")
EN_GOOD = ("We did not observe any recovery in the large sample. To test the effect, many "
           "participants were assigned, and the results were clearly very significant. The "
           "data were collected in 2021 [2]. Fig. 2 shows the 15% decline.\n")
EN_BAD = ("We did not observe any recovery in the sample. To test the effect, participants "
          "were assigned, and the results were significant. The data were collected in "
          "2021 [2]. Fig. 2 shows the 15% decline.\n")
GLOSS = "| 原文 | 译文 |\n|-|-|\n| recovery | 恢复 |\n| participants | 被试 |\n| decline | 下降 |\n"
CN_DRAFT = "本研究进行了工作记忆与抑制控制关系的探讨。我们采用 48 名被试的样本，并对数据进行了分析。\n"
CN_SRC = ("# 摘要\n\n本研究进行了工作记忆（WM）与抑制控制关系的探讨，采用 48 名被试的样本，"
          "实验在 2019 年完成，数据由两名评分者独立编码并逐一核对一致性，最终形成了完整数据集 [3]。\n\n"
          "结果表明，工作记忆容量非常强地预测了抑制控制成绩（r = 0.42，p < 0.01）。\n")
CN_EN = ("# Abstract\n\nThis study examined the relationship between working memory (WM) and "
         "inhibitory control in a sample of 48 participants; the experiment was completed in "
         "2019, and two raters coded the data independently [3].\n\n"
         "The results show that working memory capacity strongly predicted inhibitory control "
         "performance (r = 0.42, p < 0.01).\n")
CN_GLOSS = ("| 原文 | 译文 |\n|-|-|\n| 工作记忆 | working memory |\n| 抑制控制 | "
            "inhibitory control |\n")
CN_BAD_EN = ("# Abstract\n\nThis study examined working memory in a sample of 48 participants, "
             "completed in 2019 [3].\n\n"
             "The results show that capacity strongly predicted performance (r = 0.42, "
             "p < 0.01).\n")


def write(d: Path, name: str, text: str, enc: str = "utf-8") -> Path:
    p = d / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode(enc))
    return p


def run(argv: list) -> tuple:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = RUN_MOD.main(argv)
    try:
        out = json.loads(buf.getvalue().strip().splitlines()[-1])
    except Exception:
        out = {}
    return rc, out


def result(d: Path, out_name: str = "out") -> dict:
    p = d / out_name / "result.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def codes(res: dict) -> list:
    return [p["code"] for p in res.get("版本与边界", {}).get("problems", [])]


CASES = []


def case(name: str, expect: str):
    def deco(fn):
        CASES.append((name, expect, fn))
        return fn
    return deco


@case("规范改后稿（英文）", "success，0 问题，逐句 3 条")
def c01(d: Path) -> tuple:
    write(d, "paper.md", EN_SRC)
    write(d, "polished.md", EN_GOOD)
    write(d, "glossary.md", GLOSS)
    rc, _ = run(["--source", str(d / "paper.md"), "--polished", str(d / "polished.md"),
                 "--glossary", str(d / "glossary.md"), "--out", str(d / "out")])
    r = result(d)
    return (rc == 0 and r["status"] == "success" and not codes(r)
            and len(r["事实与来源映射"]["逐句修改"]) == 3,
            f'status={r["status"]} problems={codes(r)} edits={len(r.get("事实与来源映射", {}).get("逐句修改", []))}')


@case("削强度改后稿", "partial，报 K02")
def c02(d: Path) -> tuple:
    write(d, "paper.md", EN_SRC)
    write(d, "polished_bad.md", EN_BAD)
    write(d, "glossary.md", GLOSS)
    rc, _ = run(["--source", str(d / "paper.md"), "--polished", str(d / "polished_bad.md"),
                 "--glossary", str(d / "glossary.md"), "--out", str(d / "out")])
    r = result(d)
    return rc == 0 and r["status"] == "partial" and "K02" in codes(r), f'status={r["status"]} problems={codes(r)}'


@case("中文稿未改写", "needs_polish，polished.md 回填原文")
def c03(d: Path) -> tuple:
    write(d, "draft_cn.md", CN_DRAFT)
    rc, _ = run(["--source", str(d / "draft_cn.md"), "--out", str(d / "out")])
    r = result(d)
    same = (d / "out" / "polished.md").read_text(encoding="utf-8") == CN_DRAFT
    return (rc == 0 and r["status"] == "needs_polish" and same
            and "未改写" in r["目标产物"]["内容"], f'status={r["status"]} 回填={same}')


@case("英文稿无改后稿", "success，内置词面规则有命中记录")
def c04(d: Path) -> tuple:
    write(d, "paper.md", EN_SRC)
    rc, _ = run(["--source", str(d / "paper.md"), "--out", str(d / "out")])
    r = result(d)
    applied = r["事实与来源映射"]["规则命中"]
    return (rc == 0 and r["status"] == "success" and len(applied) >= 3,
            f'status={r["status"]} 规则命中={len(applied)}')


@case("原稿缺失", "blocked，退出码 1")
def c05(d: Path) -> tuple:
    rc, out = run(["--source", str(d / "nope.md"), "--out", str(d / "out")])
    r = result(d)
    return rc == 1 and r.get("status") == "blocked" and r["gaps"], f"rc={rc} status={r.get('status')}"


@case("基线对照", "生成 compare.md，结论为优于基线")
def c06(d: Path) -> tuple:
    write(d, "paper.md", EN_SRC)
    write(d, "polished.md", EN_GOOD)
    write(d, "weak.md", "We observed recovery in the sample. The results were significant. "
                        "Data were collected in 2021. Fig. 3 shows the 25% decline.\n")
    write(d, "glossary.md", GLOSS)
    rc, _ = run(["--source", str(d / "paper.md"), "--polished", str(d / "polished.md"),
                 "--glossary", str(d / "glossary.md"),
                 "--baseline", str(d / "weak.md"), "--out", str(d / "out")])
    r = result(d)
    cmpf = (d / "out" / "compare.md")
    verdict = (r.get("基线对照") or {}).get("结论") or {}
    return (rc == 0 and cmpf.exists() and "优于基线" in str(verdict.get("结论")),
            f'compare.md={cmpf.exists()} 结论={verdict.get("结论")}')


@case("公式保护", "公式内文本不被改写")
def c07(d: Path) -> tuple:
    src = ("The gain was big in the trial, and the mean was $a lot of\\ big$ units. "
           "In order to check it, a lot of runs were done.\n")
    write(d, "paper.md", src)
    run(["--source", str(d / "paper.md"), "--out", str(d / "out")])
    pol = (d / "out" / "polished.md").read_text(encoding="utf-8")
    return ("$a lot of\\ big$" in pol and "large" in pol and "many runs" in pol,
            f"公式段保留={'$a lot of\\\\ big$' in pol} 外部已改={'large' in pol}")


@case("GBK 编码输入", "按 gb18030 解码并记录编码")
def c08(d: Path) -> tuple:
    write(d, "cn.md", CN_DRAFT, enc="gb18030")
    rc, _ = run(["--source", str(d / "cn.md"), "--out", str(d / "out")])
    r = result(d)
    enc = r.get("输入标识", {}).get("原文", {}).get("编码")
    return rc == 0 and enc == "gb18030", f"编码={enc}"


@case("术语表内部冲突", "报 T02")
def c09(d: Path) -> tuple:
    write(d, "paper.md", EN_SRC)
    write(d, "polished.md", EN_GOOD)
    write(d, "glossary.md", "| 原文 | 译文 |\n|-|-|\n| recovery | 恢复 |\n"
                            "| recovery | 复原 |\n| participants | 被试 |\n")
    run(["--source", str(d / "paper.md"), "--polished", str(d / "polished.md"),
         "--glossary", str(d / "glossary.md"), "--out", str(d / "out")])
    r = result(d)
    return "T02" in codes(r), f"problems={codes(r)}"


@case("中译英漏译术语与缩写", "报 T01 与 A01")
def c10(d: Path) -> tuple:
    write(d, "source_cn.md", CN_SRC)
    write(d, "polished_en.md", CN_BAD_EN)
    write(d, "glossary_cn.md", CN_GLOSS)
    run(["--source", str(d / "source_cn.md"), "--polished", str(d / "polished_en.md"),
         "--glossary", str(d / "glossary_cn.md"), "--out", str(d / "out")])
    r = result(d)
    return ("T01" in codes(r) and "A01" in codes(r),
            f"problems={codes(r)} 语言方向={r['事实与来源映射']['语言方向']}")


@case("中译英完整译文", "success，逐句对应")
def c11(d: Path) -> tuple:
    write(d, "source_cn.md", CN_SRC)
    write(d, "polished_en.md", CN_EN)
    write(d, "glossary_cn.md", CN_GLOSS)
    run(["--source", str(d / "source_cn.md"), "--polished", str(d / "polished_en.md"),
         "--glossary", str(d / "glossary_cn.md"), "--out", str(d / "out")])
    r = result(d)
    return r["status"] == "success" and len(r["事实与来源映射"]["逐句修改"]) >= 2, \
        f'status={r["status"]} problems={codes(r)}'


@case("引用编号与数值被换", "报 K04、K03")
def c12(d: Path) -> tuple:
    write(d, "paper.md", EN_SRC)
    write(d, "polished.md", EN_GOOD.replace("[2]", "[3]").replace("15%", "25%"))
    run(["--source", str(d / "paper.md"), "--polished", str(d / "polished.md"),
         "--out", str(d / "out")])
    r = result(d)
    return "K04" in codes(r) and "K03" in codes(r), f"problems={codes(r)}"


@case("凭空加强结论", "报 K07")
def c13(d: Path) -> tuple:
    write(d, "paper.md", EN_SRC)
    write(d, "polished.md", EN_GOOD.replace("the results were clearly very significant",
                                            "the results were clearly very significantly "
                                            "stronger"))
    run(["--source", str(d / "paper.md"), "--polished", str(d / "polished.md"),
         "--out", str(d / "out")])
    r = result(d)
    return "K07" in codes(r), f"problems={codes(r)}"


@case("整句重写无留痕", "报 M01 与 M02")
def c14(d: Path) -> tuple:
    write(d, "paper.md", EN_SRC)
    write(d, "polished.md", "Recovery was absent. Participants were tested. "
                            "Data came from 2021 [2]. Fig. 2 shows the 15% decline.\n")
    run(["--source", str(d / "paper.md"), "--polished", str(d / "polished.md"),
         "--out", str(d / "out")])
    r = result(d)
    return "M01" in codes(r) and "M02" in codes(r), f"problems={codes(r)}"


@case("重复运行确定性", "两次 result.json 除耗时外逐字相同")
def c15(d: Path) -> tuple:
    write(d, "paper.md", EN_SRC)
    write(d, "polished.md", EN_GOOD)
    write(d, "glossary.md", GLOSS)
    outs = []
    for k in (1, 2):
        run(["--source", str(d / "paper.md"), "--polished", str(d / "polished.md"),
             "--glossary", str(d / "glossary.md"), "--out", str(d / f"out{k}")])
        r = result(d, f"out{k}")
        r["stats"].pop("elapsed_s", None)
        outs.append(json.dumps(r, ensure_ascii=False, sort_keys=True))
    return outs[0] == outs[1], f"两次一致={outs[0] == outs[1]}"


@case("长句与中文覆盖", "报 D07（中英各一）")
def c16(d: Path) -> tuple:
    en_long = ("We " + " ".join(["carefully"] * 45) + " examined the sample in 2021 [2].\n")
    cn_long = ("本研究对 48 名被试的工作记忆容量与抑制控制成绩之间的关系进行了系统考察，"
               "并在控制年龄、教育年限与词汇量之后重复了全部主要分析，结果方向一致但效应量有所下降 [3]。\n")
    write(d, "en.md", en_long)
    write(d, "cn.md", cn_long)
    run(["--source", str(d / "en.md"), "--out", str(d / "out_en")])
    run(["--source", str(d / "cn.md"), "--out", str(d / "out_cn")])
    ren, rcn = result(d, "out_en"), result(d, "out_cn")
    return ("D07" in [i["码"] for i in ren["事实与来源映射"]["逐条"]]
            and "D07" in [i["码"] for i in rcn["事实与来源映射"]["逐条"]],
            f'en D07={[i["码"] for i in ren["事实与来源映射"]["逐条"]].count("D07")} '
            f'cn D07={[i["码"] for i in rcn["事实与来源映射"]["逐条"]].count("D07")}')


def main(argv=None) -> int:
    verbose = bool(argv and "-v" in argv)
    rows, failed = [], 0
    for name, expect, fn in CASES:
        with tempfile.TemporaryDirectory(prefix="polish_selftest_") as td:
            try:
                ok, detail = fn(Path(td))
            except Exception as e:  # 用例自身出错也要报，不静默跳过
                ok, detail = False, f"{type(e).__name__}: {e}"
        failed += 0 if ok else 1
        rows.append((name, expect, "PASS" if ok else "FAIL", detail if (verbose or not ok) else ""))
    w = max(len(r[0]) for r in rows)
    print(f"{'用例'.ljust(w)} | 期望 | 结果 | 实际")
    print("-" * (w + 30))
    for name, expect, verdict, detail in rows:
        print(f"{name.ljust(w)} | {expect} | {verdict} | {detail}")
    print(f"\n共 {len(rows)} 个用例，通过 {len(rows) - failed}，失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
