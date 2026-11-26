#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py — 引用管理 Skill 的编排入口。

按 SKILL.md 第 5 节的十步串联脚本；两次模型请求由 Agent 完成，本脚本只准备输入、校验输出、落盘。
未提供模型输出时，跑到需要模型的位置就停，状态记 needs_model，并列出待读的提示词与输入文件。

用法：
  python run.py --docx manuscript.docx --refs refs.txt --lib library.bib --work work --out output
  python run.py ... --no-model --pass1 work/pass1_out.txt --match work/pass3_match.txt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE / "scripts"

LIMITS = [
    "不联网核验条目真伪、不解析 DOI",
    "旧标记不移位：原句单个标记按语义拆成两个时，第二个留在原位",
    "图表题注、脚注、附录、表格内文字不参与",
    "著者-出版年制、脚注制未实现",
    "PDF 预览需外部 LibreOffice",
]

SKIP_STEP = {
    "mklib": "无文献库输入",
    "ingest": "无稿件文献表输入",
    "keywords": "无需关键词",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Runner:
    def __init__(self, args: argparse.Namespace):
        self.a = args
        self.work = Path(args.work)
        self.out = Path(args.out)
        self.work.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        self.steps: list[dict] = []
        self.status = "success"
        self.blocked_at: str | None = None
        self.needs: list[str] = []

    def step(self, name: str, script: str, argv: list[str], required: bool = True) -> tuple[bool, dict]:
        t0 = time.perf_counter()
        cmd = [sys.executable, str(SCRIPTS / script), *argv]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        dt = round((time.perf_counter() - t0) * 1000, 1)
        try:
            payload = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except Exception:
            payload = {"raw": (proc.stdout or "").strip()[-800:]}
        ok = proc.returncode == 0
        self.steps.append({"name": name, "script": script, "ok": ok,
                           "exit": proc.returncode, "ms": dt, "result": payload,
                           "stderr": (proc.stderr or "").strip()[-400:]})
        if not ok:
            if required:
                self.status = "blocked"
                self.blocked_at = name
            elif self.status == "success":
                # 非必需步骤失败=成品有缺口：必须降级，不能仍旧报 success（见 ISSUES CM-02）
                self.status = "partial"
                self.blocked_at = self.blocked_at or name
        return ok, payload

    def need_model(self, what: str, prompt: str, inp: str) -> None:
        self.needs.append({"request": what, "prompt": prompt, "input": inp})
        self.status = "needs_model"
        self.blocked_at = what

    # ---------------------------------------------------------------- 十步
    def run(self) -> None:
        a, work, out = self.a, self.work, self.out
        lib = work / "library.json"
        merged = work / "library_merged.json"

        if a.lib:
            ok, _ = self.step("1 建库", "mklib.py",
                              [*a.lib, "--out", str(lib), "--tsv", str(work / "library.tsv"),
                               "--min-tsv", str(work / "lib_min.tsv")])
            if not ok:
                return

        active_lib = lib
        self.lib_path = None
        if a.refs and a.lib:
            ok, _ = self.step("2 并库查重", "ingest_refs.py",
                              ["--refs", a.refs, "--lib", str(lib),
                               "--out-lib", str(merged), "--out-map", str(work / "orig_map.json")])
            if ok:
                active_lib = merged

        if active_lib.exists():
            self.lib_path = active_lib
            self.step("3a DOI 核验", "doi_check.py",
                      ["--lib", str(active_lib), "--out", str(work / "doi_status.json")]
                      + (["--online"] if a.online_doi else []), required=False)
            self.step("3 关键词", "keywords.py",
                      [str(active_lib), "--out", str(work / "keywords.json"),
                       "--tsv", str(work / "keywords.tsv")], required=False)

        ok, prep = self.step("4 分句编号", "pass1_prep.py",
                             [a.docx, "--out", str(work / "prep.json"),
                              "--emit", str(work / "pass1_input.txt")])
        if not ok:
            return

        if a.lib_triage and active_lib.exists():
            self.step("3b 库整理校验", "check_pass2.py",
                      [a.lib_triage, "--lib", str(active_lib)], required=False)

        p1 = Path(a.pass1) if a.pass1 else None
        if p1 is None:
            if a.no_model:
                self.status = "blocked"
                self.blocked_at = "缺 --pass1：--no-model 只重放已给的模型输出"
                return
            self.need_model("句子分流", "prompts/pass1_sentence_triage.md",
                            str(work / "pass1_input.txt"))
            return
        ok, _ = self.step("5 分流校验", "pass1_prep.py",
                          ["--verify", str(p1), "--prep", str(work / "prep.json")], required=False)

        if not active_lib.exists():
            self.status = "partial"
            self.blocked_at = "缺文献库"
            return

        ok, _ = self.step("6 候选检索", "match_candidates.py",
                          ["--prep", str(work / "prep.json"), "--pass1", str(p1),
                           "--lib", str(active_lib), "--tsv", str(work / "match_input.tsv"),
                           *(("--refs", a.refs) if a.refs else ()),
                           "--glossary", str(HERE / "rules" / "glossary_zh_en.tsv")])
        if not ok:
            return

        m = Path(a.match) if a.match else None
        if m is None:
            self.need_model("句—库匹配", "prompts/pass3_match.md",
                            str(work / "match_input.tsv"))
            return
        self.step("7 匹配校验", "check_pass3.py",
                  [str(m), "--pass1", str(p1), "--lib", str(active_lib)], required=False)

        argv = ["--prep", str(work / "prep.json"), "--match", str(m),
                "--lib", str(active_lib), "--out", str(work / "numbering.json"),
                "--cands", str(work / "match_input.tsv")]
        if (work / "orig_map.json").exists():
            argv += ["--orig-map", str(work / "orig_map.json")]
        elif a.refs:
            argv += ["--refs", a.refs]
        argv += ["--style", a.style, "--lang", a.lang]
        if a.keep_orig_numbers:
            argv += ["--keep-orig-numbers"]
        if a.merge_new_into_mark:
            argv += ["--merge-new-into-mark"]
        ok, num = self.step("8 编号重排", "renumber.py", argv)
        if not ok:
            return

        cited = out / (Path(a.docx).stem + "_cited.docx")
        ok, ins = self.step("9 写入 docx", "insert_refs.py",
                            ["--docx", a.docx, "--prep", str(work / "prep.json"),
                             "--numbering", str(work / "numbering.json"), "--out", str(cited),
                             "--changelog", str(work / "changelog.jsonl")])
        if not ok:
            return

        if a.renumber_captions:
            self.step("9b 图表编号", "captions.py",
                      ["--docx", str(cited), "--out", str(cited), "--apply",
                       "--report", str(work / "captions.json")], required=False)

        # 重建文献表文本，供第 11 步按国标复查
        refs_txt = out / "refs_rendered.txt"
        num_json = work / "numbering.json"
        numbering = json.loads(num_json.read_text(encoding="utf-8")) if num_json.exists() else {}
        if numbering.get("refs"):
            refs_txt.write_text(
                "\n".join(f"[{r['n']}] {r['text']}" for r in numbering["refs"]) + "\n",
                encoding="utf-8")

        self.step("10 成品校验", "check_refs.py",
                  ["--docx", str(cited), "--numbering", str(num_json)], required=False)
        if a.style == "gbt7714":
            self.step("11 文献表格式", "check_gbt.py",
                      ["--refs", str(refs_txt), "--json", str(work / "gbt_final.json")],
                      required=False)
        else:
            self.steps.append({"name": "11 文献表格式", "script": "check_gbt.py", "ok": True,
                               "exit": 0, "ms": 0.0,
                               "result": {"ok": True, "skipped": f"样式 {a.style} 不适用 GB/T 检查"},
                               "stderr": ""})
        if a.export:
            self.step("13 导出文献库", "export.py",
                      ["--lib", str(active_lib), "--out-dir", str(out), "--formats", a.export],
                      required=False)
        self.step("12 渲染预览", "preview.py",
                  ["--docx", str(cited), "--out", str(out / "preview")], required=False)

    # ---------------------------------------------------------------- 落盘
    def library(self) -> dict:
        path = getattr(self, "lib_path", None)
        if path and Path(path).exists():
            lib = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            return {e["id"]: e for e in lib.get("entries", [])}
        return {}

    def fact_map(self, numbering: dict, entries: dict) -> list[dict]:
        rows = []
        for c in numbering.get("cites", []):
            head = (c.get("text") or "")[:20]
            for k, a in enumerate(c.get("anchors", [])):
                rec = {}
                if a["ids"]:
                    e = entries.get(a["ids"][0], {})
                    rec = {"n": (a["nums"] or [None])[0], "id": a["ids"][0],
                           "title": e.get("title", ""), "authors": e.get("authors", []),
                           "year": e.get("year", ""), "container": e.get("container", ""),
                           "doi": e.get("doi", ""), "type": e.get("type", "")}
                rows.append({
                    "location": f"{c.get('pid')} 段，句首「{head}」",
                    "judgement": "换号" if a["kind"] == "replace" else "补引",
                    "marker": "[" + ",".join(str(n) for n in a.get("nums") or []) + "]",
                    "anchor": {"uid": c["uid"], "kind": a["kind"], "index": k,
                               "offset": a.get("offset"), "start": a.get("start"),
                               "old": a.get("old")},
                    "record": rec,
                    "records": [{"n": n, "id": i, "title": entries.get(i, {}).get("title", "")}
                                for n, i in zip(a.get("nums") or [], a["ids"])],
                    "evidence": (f"prep {c['uid']} 偏移 {c.get('sentence_offset')} → "
                                 f"numbering.cites[{c['uid']}].anchors[{k}] → changelog"),
                })
        return rows

    CONTRACT_RULES = [
        # 平台字段名精确匹配（先于模糊规则；改动见 review/ISSUES.md X-01）
        (r"^目标产物", "cited_manuscript"),
        (r"^事实与来源映射", "citation_actions"),
        (r"^版本与边界", "changes"),
        (r"^阅读[/／]解析结果", "cited_manuscript"),
        (r"^证据与边界", "verification"),
        (r"^运行状态", "status"),
        (r"manuscript|稿件|改稿|正文|修订|draft|article", "cited_manuscript"),
        (r"reference|bibliograph|文献表|参考文献|refs", "reference_list"),
        (r"citation|引用|标注|in_text", "citation_actions"),
        (r"dedupe|duplicate|去重|合并", "dedupe"),
        (r"verif|audit|核验|检查|一致", "verification"),
        (r"change|diff|changelog|修改|变更", "changes"),
        (r"status|outcome|状态", "status"),
    ]

    def echo_contract(self, contract: dict, deliverables: list, checks: dict,
                      changes: list) -> dict:
        """按 case 的 output_contract 逐字段回显；认不出的字段写指针并标 partial。"""
        by_kind = {d["kind"]: d for d in deliverables}
        pool = {
            "cited_manuscript": by_kind.get("cited_manuscript", {}).get("path"),
            "reference_list": by_kind.get("reference_list", {}).get("path"),
            "citation_actions": {"count": len(self._facts) if hasattr(self, "_facts") else None,
                                 "table": "fact_source_map"},
            "dedupe": {"table": "orig_map / ingest_report", "note": "见 work/ingest_report.json"},
            "verification": checks,
            "changes": changes,
            "status": self.status,
        }
        self.echo_status = {}
        out = {}
        for key in contract:
            hit = None
            for pat, kind in self.CONTRACT_RULES:
                if re.search(pat, key, re.I):
                    hit = kind
                    break
            if hit and pool.get(hit) not in (None, {}, []):
                out[key] = pool[hit]
                self.echo_status[key] = {"status": "full", "source": hit}
            else:
                out[key] = {"note": "本字段无专门产出，内容见 fact_source_map / checks / changes",
                            "pointer": ["fact_source_map", "checks", "version_boundary"]}
                self.echo_status[key] = {"status": "partial", "source": "pointer"}
        return out

    def write_result(self, case_id: str, t0: float) -> Path:
        work, out = self.work, self.out
        stem = Path(self.a.docx).stem
        cited = out / f"{stem}_cited.docx"
        num_json = work / "numbering.json"
        numbering = json.loads(num_json.read_text(encoding="utf-8")) if num_json.exists() else {}
        entries = self.library()
        contract = (getattr(self.a, "plan", None) or {}).get("output_contract") or {}

        refs_txt = out / "refs_rendered.txt"
        if numbering.get("refs"):
            refs_txt.write_text(
                "\n".join(f"[{r['n']}] {r['text']}" for r in numbering["refs"]) + "\n",
                encoding="utf-8")

        cl = sorted(work.glob("changelog.jsonl")) + sorted(out.glob("*_cited.changelog.jsonl"))
        changes: list[dict] = []
        if cl:
            for line in cl[0].read_text(encoding="utf-8").splitlines():
                if line.strip():
                    changes.append(json.loads(line))
        # 交付物用 .md 表格：.jsonl 不在平台的 Skill 资源白名单里（预检会拦）
        if changes:
            # 键名来自 insert_refs.py：uid/para_index/kind/offset/old/nums/marker/section
            def cell(entry: dict, key: str) -> str:
                val = entry.get(key)
                if val is None or val == "":
                    return "—"
                if isinstance(val, list):
                    return "[" + ", ".join(str(v) for v in val) + "]"
                return str(val).replace("|", "\\|")

            rows = "\n".join(
                "| {i} | {para} | {uid} | {kind} | {pos} | {old} | {ins} | {sec} |".format(
                    i=i, para=cell(c, "para_index"), uid=cell(c, "uid"),
                    kind=cell(c, "kind"), pos=cell(c, "offset"),
                    old=cell(c, "old"), ins=cell(c, "marker") if c.get("marker")
                    else cell(c, "nums"), sec=cell(c, "section"))
                for i, c in enumerate(changes, 1))
            (out / "changelog.md").write_text(
                "# 逐处改动\n\n"
                "| # | 段 | 引用 uid | 类型 | 段内位置 | 原标 | 插入 | 章节 |\n"
                "|-|-|-|-|-|-|-|-|\n" + rows + "\n",
                encoding="utf-8")

        checks = {}
        for s in self.steps:
            if s["name"] in {"5 分流校验", "7 匹配校验", "10 成品校验", "11 文献表格式"}:
                r = s["result"] or {}
                checks[s["name"]] = {"ok": r.get("ok"),
                                     "n_problems": r.get("n_problems", r.get("n_error")),
                                     "counts": r.get("counts")}

        preview_step = next((s for s in self.steps if s["name"] == "12 渲染预览"), None)
        preview = (preview_step or {}).get("result") or {}

        deliverables = []
        for kind, path, fmt, extra in [
            ("cited_manuscript", cited, "docx", {"editable": True}),
            ("reference_list", refs_txt, "txt", {"entries": len(numbering.get("refs") or []),
                                                 "style": "GB/T 7714-2015 顺序编码制"}),
            ("numbering_table", work / "numbering.tsv", "tsv",
             {"entries": numbering.get("n_refs")}),
            ("changelog", out / "changelog.md", "md", {"entries": len(changes)}),
        ]:
            if path.exists():
                deliverables.append({"kind": kind, "path": str(path), "format": fmt,
                                     "bytes": path.stat().st_size,
                                     "sha256": sha256(path)[:16], **extra})

        for kind, pat, fmt in [("preview_pdf", "preview*/**/*.pdf", "pdf"),
                               ("preview_png", "preview*/**/*.png", "png")]:
            for p in sorted(out.glob(pat))[:6]:
                deliverables.append({"kind": kind, "path": str(p), "format": fmt,
                                     "bytes": p.stat().st_size,
                                     "sha256": sha256(p)[:16]})
        caps_file = work / "captions.json"
        if caps_file.exists():
            deliverables.append({"kind": "caption_map", "path": str(caps_file), "format": "json",
                                 "bytes": caps_file.stat().st_size,
                                 "sha256": sha256(caps_file)[:16]})
        for name in ("references.bib", "references.ris"):
            p = out / name
            if p.exists():
                deliverables.append({"kind": "library_export", "path": str(p),
                                     "format": name.rsplit(".", 1)[1],
                                     "bytes": p.stat().st_size,
                                     "sha256": sha256(p)[:16]})

        uncertainty = []
        for name, c in checks.items():
            if c.get("n_problems"):
                uncertainty.append({"kind": "check", "detail": f"{name} 未通过：{c['n_problems']} 项"})
        if numbering.get("unmapped_markers"):
            uncertainty.append({"kind": "marker",
                                "detail": f"正文标记无对应条目：{numbering['unmapped_markers'][:5]}"})
        if numbering.get("dropped_ids"):
            uncertainty.append({"kind": "record",
                                "detail": f"匹配到库外 id，已跳过：{numbering['dropped_ids'][:5]}"})

        gaps = []
        if not self.a.lib:
            gaps.append({"kind": "input", "detail": "无文献库，仅用稿件原有文献表"})
        if not self.a.refs:
            gaps.append({"kind": "input", "detail": "无稿件文献表，原有 [n] 未做编号映射"})
        for g in (getattr(self.a, "plan", None) or {}).get("gaps") or []:
            gaps.append({"kind": "input", "detail": g})
        if not preview.get("produced"):
            gaps.append({"kind": "preview",
                         "detail": f"未生成渲染预览：{preview.get('skipped') or '未执行'}"})
        for s in self.steps:
            r = s.get("result") or {}
            if s["name"] == "9 写入 docx" and r.get("mismatch"):
                gaps.append({"kind": "docx",
                             "detail": f"{r['mismatch']} 段文本与 prep 不一致，已跳过"})

        exceptions = [{"step": s["name"], "exit": s["exit"], "stderr": s["stderr"]}
                      for s in self.steps if not s["ok"] and s["name"] != "12 渲染预览"]

        result = {
            "case_id": case_id,
            "status": self.status,
            "blocked_at": self.blocked_at,
            "needs": self.needs,
            "skill": {"name": "citation-manager", "version": "1.1.0"},
            "inputs": {"docx": self.a.docx, "refs": self.a.refs, "lib": self.a.lib,
                       "pass1": self.a.pass1, "match": self.a.match},
            "deliverables": deliverables,
            "fact_source_map": self.fact_map(numbering, entries),
            "version_boundary": {"changes": changes,
                                 "uncertainty": uncertainty,
                                 "material_gaps": gaps,
                                 "exceptions": exceptions},
            "output_contract": self.echo_contract(contract, deliverables, checks, changes),

            "numbering": {"mode": numbering.get("mode"), "n_refs": numbering.get("n_refs"),
                          "order": [{"n": r["n"], "id": r["id"]} for r in numbering.get("order", [])]},
            "checks": checks,
            "sources": {"library": self.a.lib, "manuscript_refs": self.a.refs,
                        "rule_gbt": "GB/T 7714—2015",
                        "rule_source": "https://www.jsczz.cn/CN/column/column17.shtml"},
            "preview": {"produced": preview.get("produced") or [],
                        "skipped": preview.get("skipped")},
            "limits": LIMITS,
            "steps": self.steps,
        }
        p = out / "result.json"
        # 题面：result.json 含全部 output_contract 字段 → 平铺到顶层
        for _k, _v in (result.get("output_contract") or {}).items():
            result.setdefault(_k, _v)
        result["目标产物"] = result.get("deliverables") or []
        result["事实与来源映射"] = result.get("fact_source_map") or {}
        result["版本与边界"] = result.get("version_boundary") or {}
        result.setdefault("uncertainty", (result.get("version_boundary") or {}).get("gaps") or [])
        p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

        rep = {"case_id": case_id, "status": self.status,
               "steps": [{"name": s["name"], "ok": s["ok"], "exit": s["exit"]} for s in self.steps],
               "checks": checks, "n_changes": len(changes),
               "n_fact_rows": len(result["fact_source_map"])}
        (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                         encoding="utf-8")

        refs_tsv = work / "numbering.tsv"
        if refs_tsv.exists():
            shutil.copyfile(refs_tsv, out / "numbering.tsv")
        return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="引用管理 Skill 编排")
    ap.add_argument("--docx", default=None, help="稿件；给了 --input 时可省")
    ap.add_argument("--input", default=None, help="平台输入目录（含 case.json / task.md / 稿件 / 文献库）")
    ap.add_argument("--refs", default=None)
    ap.add_argument("--lib", nargs="*", default=None)
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="output")
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--pass1", default=None, help="已完成的句子分流结果")
    ap.add_argument("--lib-triage", default=None, help="可选的库整理输出（关键词/格式/查重），用 check_pass2 校验")
    ap.add_argument("--match", default=None, help="已完成的句—库匹配结果")
    ap.add_argument("--no-model", action="store_true", help="只重放已给的模型输出，不再请求")
    ap.add_argument("--style", default="gbt7714", choices=["gbt7714", "apa7", "ieee"])
    ap.add_argument("--lang", default="zh", choices=["zh", "en"])
    ap.add_argument("--online-doi", action="store_true", help="联网核验 DOI（默认离线）")
    ap.add_argument("--export", default=None, help="导出 bib/ris，例如 --export bib,ris")
    ap.add_argument("--renumber-captions", action="store_true",
                    help="图/表题注与正文提及编号连锁重排")
    ap.add_argument("--keep-orig-numbers", action="store_true")
    ap.add_argument("--merge-new-into-mark", action="store_true")
    args = ap.parse_args(argv)

    t0 = time.time()
    if args.input:
        import importlib.util as _u
        spec = _u.spec_from_file_location("caseinput", Path(__file__).parent / "scripts" / "caseinput.py")
        ci = _u.module_from_spec(spec)
        spec.loader.exec_module(ci)
        plan = ci.load(args.input)
        args.docx = args.docx or plan.get("docx")
        args.refs = args.refs or plan.get("refs")
        args.lib = args.lib or plan.get("libs") or None
        args.case_id = args.case_id or plan.get("case_id")
        args.plan = plan
        if not args.docx:
            print(json.dumps({"ok": False, "status": "blocked",
                              "error": "输入目录里没找到稿件", "gaps": plan.get("gaps")},
                             ensure_ascii=False))
            return 1
    if not args.docx:
        ap.error("需要 --docx 或 --input")
    if Path(args.docx).suffix.lower() != ".docx":
        import importlib.util as _u2
        spec2 = _u2.spec_from_file_location("caseinput2", Path(__file__).parent / "scripts" / "caseinput.py")
        ci2 = _u2.module_from_spec(spec2)
        spec2.loader.exec_module(ci2)
        conv = Path(args.work) / "_converted" / (Path(args.docx).stem + ".docx")
        args.docx = str(ci2.md_to_docx(Path(args.docx), conv))
    case_id = args.case_id or Path(args.docx).stem
    r = Runner(args)
    r.run()
    p = r.write_result(case_id, t0)
    print(json.dumps({"ok": r.status in {"success", "partial", "needs_model"},
                      "status": r.status, "blocked_at": r.blocked_at,
                      "needs": [n["request"] for n in r.needs],
                      "result": str(p), "steps": len(r.steps)}, ensure_ascii=False))
    return 0 if r.status != "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())







