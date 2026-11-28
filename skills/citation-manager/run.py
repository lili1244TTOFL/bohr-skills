#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py — 引用管理 Skill 的编排入口。

按 SKILL.md 第 5 节的十步串联脚本；两次模型请求由 Agent 完成，本脚本只准备输入、校验输出、落盘。
没有模型输出时不再停在 needs_model：句子分流与句—库匹配各有一套内置规则替身
（auto_pass1 / auto_match），先把 *_cited.docx 跑出来，命中的规则逐条记进 result.json。

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


# ---------------------------------------------------------------------------
# 两次模型请求的确定性替身。没有模型输出时不再停在 needs_model：
#   句子分流 → 按章节 + 标记 + 线索词判 CITE/OWN/STRUCT/AMBIG（Reason Code 见
#              rules/paper_sections.md §4）
#   句—库匹配 → 取 match_candidates.py 已算好的候选分排序首位（MARK 命中优先）
# 两条都只做词面规则，命中的规则逐条写进 result.json 的 规则命中。
# ---------------------------------------------------------------------------
MARK_RE = re.compile(r"\[\s*\d+(?:\s*[,–\-]\s*\d+)*\s*\]")
CITE_HINT = re.compile(r"已有研究|大量研究|研究者|前人|文献(?:中|表明|报道)?|综述|元分析|"
                       r"据.{0,8}报道|被.{0,6}认为|最早由|经典|后续工作")
LATIN_CJK = re.compile(r"[A-Z][A-Za-z]{2,}\s*[\u4e00-\u9fff]")
OWN_HINT = re.compile(r"本研究|本实验|本文|我们|本研究组")
ETHIC_HINT = re.compile(r"伦理|知情同意|批准|基金|资助|致谢")
CAPTION_SRC = re.compile(r"引自|改自|数据来自|采自|转引")

CAT_RC_CITE = {"method": "RC3", "results": "RC4", "discussion": "RC4", "caption": "RC4"}

# 判断层：论断配文献。脚本只算候选（词面/语义分）与已有 [n] 的映射，
# 「这句由哪篇文献支撑」由调用方模型选：写 judgments.json 或给 --match。
JUDGMENT_UNIT = "论断配文献"


def triage(sec: str, text: str) -> tuple[str, str, str]:
    """返回 (类别, 理由码, 命中规则名)。类别四值与理由码范围与 pass1_prep --verify 一致。"""
    if MARK_RE.search(text):
        return ("CITE", CAT_RC_CITE.get(sec, "RC1"), "已有引用标记→CITE")
    if sec == "table":
        return ("STRUCT", "RC8", "表内只读上下文→STRUCT")
    if sec == "caption":
        if CAPTION_SRC.search(text):
            return ("CITE", "RC4", "题注写了出处→CITE")
        return ("STRUCT", "RC8", "图题无来源线索→STRUCT")
    if sec in ("decl", "title", "keywords", "refs"):
        return ("STRUCT", "RC9", "声明/题名/关键词→STRUCT")
    if sec == "method":
        if ETHIC_HINT.search(text):
            return ("STRUCT", "RC9", "伦理/资助/致谢→STRUCT")
        return ("OWN", "RC7", "方法节的自有步骤→OWN")
    if sec == "results":
        return ("OWN", "RC5", "结果节的本文数据→OWN")
    if sec == "limits":
        return ("OWN", "RC6", "局限/讨论→OWN")
    if CITE_HINT.search(text):
        return ("CITE", "RC1", "他人研究/文献线索→CITE")
    if LATIN_CJK.search(text):
        return ("CITE", "RC3", "外文人名/工具名→CITE")
    if OWN_HINT.search(text):
        return ("OWN", "RC6", "本研究相关表述→OWN")
    return ("AMBIG", "R9", "无引用信号→AMBIG")


def auto_pass1(prep: dict, out_path: Path) -> dict:
    skip = set(prep.get("skipped_sections") or ["title", "refs"])
    lines, hits = [], []
    counts = {k: 0 for k in ("CITE", "OWN", "STRUCT", "AMBIG")}
    for s in prep.get("sentences", []):
        if s.get("section") in skip:
            continue
        uid, sec, text = s["uid"], s.get("section", ""), s.get("text", "")
        cat, rc, why = triage(sec, text)
        head = re.sub(r"[\t\r\n]+", " ", text)[:20]
        lines.append(f"{uid}|{cat}|{rc}|{head}")
        counts[cat] += 1
        hits.append({"uid": uid, "section": sec, "类别": cat, "理由码": rc, "规则": why})
    lines.append("#PASS1 " + " ".join(f"{k}:{v}" for k, v in counts.items()))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"rows": len(hits), "counts": counts, "items": hits,
            "path": str(out_path), "rule": "章节 + 引用标记 + 线索词"}


def auto_match(match_json: Path, out_path: Path, decisions: dict | None = None,
               pass3: dict | None = None) -> dict:
    """句—库匹配：已有 [n] 照映射，其余由模型选。

    改前这里直接取候选分首位；现在只给候选。模型的选择来自两处：judgments.json
    （uid → ids）或 --match 交的 pass3 文件；两处都没有的句子记 NONE + pending，
    不再按词面重合替模型挑一篇。
    """
    rows = json.loads(match_json.read_text(encoding="utf-8-sig")) if match_json.exists() else []
    lines, hits = [], []
    n_assign = n_none = n_new = n_ambig = n_pending = 0
    for r in rows:
        cands = r.get("cands") or []
        marked = [c for c in cands if c.get("src") == "MARK"]
        got = (decisions or {}).get(r["uid"]) or {}
        p3 = (pass3 or {}).get(r["uid"]) or {}
        if marked:
            ids = [c["id"] for c in marked][:3]
            conf, why = "H", "已有标记映射到库条目"
            basis = "标记 " + "/".join(t for c in marked for t in (c.get("terms") or []))
            ambig, second = False, None
        elif got.get("ids") or p3.get("ids"):
            ids = list(got.get("ids") or p3.get("ids") or [])
            conf = str(got.get("conf") or p3.get("conf") or "H")
            why = str(got.get("reason") or "模型判断（judgments.json）" if got else "--match 文件")
            basis = str(got.get("basis") or p3.get("basis") or " ".join(
                t for c in cands[:1] for t in (c.get("terms") or [])))[:120]
            ambig, second = False, None
        else:
            top = cands[0] if cands else None
            ids, conf, ambig, second = [], "L", False, None
            n_pending += 1
            cand_show = "、".join(f"{c['id']}({c.get('score')})" for c in cands[:3]) or "无候选"
            why = f"待模型选：候选 {cand_show}"
            basis = f"pending：候选 {cand_show}"
        if ids:
            n_assign += 1
        else:
            n_none += 1
            n_new += 1
        if ambig:
            n_ambig += 1
        lines.append(f"{r['uid']}|{','.join(ids) if ids else 'NONE'}|{conf}|{basis}")
        hits.append({"uid": r["uid"], "ids": ids, "置信度": conf, "规则": why,
                     "依据": basis, "候选分": (cands[0].get("score") if cands else None),
                     "候选": [{"id": c["id"], "score": c.get("score"), "src": c.get("src"),
                               "terms": (c.get("terms") or [])[:6]} for c in cands[:3]],
                     "已映射标记": [c.get("terms") for c in marked][:3] if marked else None,
                     "并列": ("|".join(c["id"] for c in cands[1:2]) or None) if ambig else None,
                     "歧义": ambig, "待判": not ids and not marked, "说明": r.get("status")})
    lines.append(f"#MATCH n={len(rows)} n_assign={n_assign} n_none={n_none} n_new={n_new}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"rows": len(rows), "n_assign": n_assign, "n_none": n_none,
            "n_ambiguous": n_ambig, "n_pending": n_pending,
            "path": str(out_path), "items": hits,
            "rule": "已有 [n] 照映射；其余只给候选，选哪篇由调用方模型定（judgments.json / --match）"}


def load_match_judgments(p: Path | None) -> tuple:
    """读 judgments.json：items[{uid, ids, basis, reason, filled_by}]。"""
    if not p or not p.exists():
        return {}, f"未找到 {p}" if p else "未提供 judgments.json"
    try:
        obj = json.loads(p.read_text(encoding="utf-8-sig"))
        raw = obj.get("items") if isinstance(obj, dict) else obj
        got = {}
        for it in (raw or []):
            if isinstance(it, dict) and it.get("uid"):
                ids = it.get("ids")
                if isinstance(ids, str):
                    ids = [x.strip() for x in ids.replace("，", ",").split(",") if x.strip()]
                got[str(it["uid"])] = {**it, "ids": list(ids or [])}
        return got, f"{p}（{len(got)} 条）"
    except Exception as e:
        return {}, f"{p} 读不动：{type(e).__name__}: {e}"


def parse_pass3(p: Path | None) -> dict:
    """pass3 文件：`uid|ids|conf|basis`，跳过 # 行。"""
    out: dict[str, dict] = {}
    if not p or not Path(p).exists():
        return out
    for line in Path(p).read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < 2:
            continue
        ids = [] if parts[1].strip().upper() in ("NONE", "") else \
            [x.strip() for x in parts[1].split(",") if x.strip()]
        out[parts[0].strip()] = {"ids": ids,
                                 "conf": parts[2].strip() if len(parts) > 2 else "",
                                 "basis": parts[3].strip() if len(parts) > 3 else ""}
    return out


def match_judgment_layer(match_json: Path, decisions: dict, dec_src: str,
                         pass3: dict, pass3_src: str, out: Path) -> dict:
    """判断层：论断配文献。一条判断一个 uid，候选是脚本算的，选哪篇是模型的事。"""
    rows = json.loads(match_json.read_text(encoding="utf-8-sig")) if match_json.exists() else []
    items, exempt, filled = [], [], 0
    for r in rows:
        cands = r.get("cands") or []
        marked = [c for c in cands if c.get("src") == "MARK"]
        uid = r["uid"]
        if marked:
            exempt.append({"uid": uid, "ids": [c["id"] for c in marked][:3],
                           "理由": "稿件原有 [n] 已映射到库条目，是编号映射不是选文献"})
            continue
        got = decisions.get(uid) or {}
        p3 = pass3.get(uid) or {}
        ids = list(got.get("ids") or p3.get("ids") or [])
        basis = str(got.get("basis") or p3.get("basis") or "")
        text = str(r.get("text") or "")
        all_ids = [c["id"] for c in cands]
        filled_now = bool(ids)
        if filled_now:
            filled += 1
        items.append({
            "id": uid, "item": JUDGMENT_UNIT, "section": r.get("section"),
            "原文": text[:160],
            "候选": [{"id": c["id"], "score": c.get("score"),
                      "terms": (c.get("terms") or [])[:6]} for c in cands[:3]],
            "候选全量": all_ids,
            "value": ",".join(ids) if ids else "pending",
            "basis": basis[:120],
            "basis_source": "judgments.json" if got else ("--match 文件" if p3 else ""),
            "filled_by": (("调用方模型（judgments.json）" if got else "调用方模型（--match）")
                          if filled_now else ""),
            "status": "filled" if filled_now else "pending",
            "in_candidates": (all(i in all_ids for i in ids) if ids else None),
            # --match 的 basis 是 pass3 文件自带的依据词，不是句子原文，故只核 judgments.json 的
            "basis_verbatim": ((basis in text) if (ids and basis and got) else None),
            "model_note": str(got.get("reason") or "")[:200],
        })
    n = len(items)
    L = [f"# 判断单：{JUDGMENT_UNIT}", "",
         f"- 待判 {n} 句；已判 {filled} 句；`exempt` 另有 {len(exempt)} 句走已有 [n] 映射",
         "- 填到：`judgments.json`：`{\"items\":[{\"uid\",\"ids\",\"basis\",\"reason\",\"filled_by\"}]}`",
         "- `ids` 只能从该句候选里选（0 篇以上）；`basis` 必须是该句原文的逐字片段",
         "- 不填的句子由 `run.py` 标 `pending`，不按词面重合替模型挑", "",
         "| uid | 章节 | 句 | 候选（id / 分 / 命中词） |", "|-|-|-|-|"]
    for it in items:
        c = str(it["原文"]).replace("|", "\\|")[:70]
        cs = "；".join(f"{x['id']} {x['score']} {'/'.join(x['terms'])}" for x in it["候选"]) or "无"
        extra = [i for i in it["候选全量"] if i not in {x['id'] for x in it['候选']}]
        if extra:
            cs += f"；另有低分候选 {len(extra)} 条（{'/'.join(extra[:4])}）"
        L.append(f"| `{it['id']}` | {it.get('section') or ''} | {c} | {cs.replace('|', '/')} |")
    (out / "match_worklist.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return {
        "unit": JUDGMENT_UNIT,
        "who_fills": "调用方模型：读 match_worklist.md，把 uid→ids 写进 judgments.json（或交 --match 的 pass3 文件）",
        "who_checks": "run.py 核 ids 是否在该句候选内、basis 是否该句原文子串；再由 verify_sources 核证据强弱",
        "source": dec_src if decisions else (pass3_src if pass3 else "未填：只给候选，不替模型选"),
        "n": n, "filled": filled, "pending": n - filled,
        "exempt": exempt,
        "items": items,
    }


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
        self.auto: dict[str, dict] = {}

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

        # --refs 单源：先落一个空库，再让第 2 步把稿件原有文献表并进去。
        # 改前这里要求 --refs 与 --lib 同时给，只给 --refs 时库始终不存在，
        # 流水线跑到「缺文献库」就早退，deliverables 恒为空、从不产出 *_cited.docx。
        if a.refs and not a.lib and not lib.exists():
            lib.parent.mkdir(parents=True, exist_ok=True)
            lib.write_text(json.dumps(
                {"generated_by": "run.py", "n": 0, "entries": [],
                 "note": "无 --lib 输入：以空库为底，仅并入稿件原有文献表"},
                ensure_ascii=False, indent=1), encoding="utf-8")

        active_lib = lib
        self.lib_path = None
        if a.refs and lib.exists():
            ok, _ = self.step("2 并库查重", "ingest_refs.py",
                              ["--refs", a.refs, "--lib", str(lib),
                               "--out-lib", str(merged), "--out-map", str(work / "orig_map.json")])
            if ok:
                active_lib = merged

        # 2b 库内去重：同一文献的不同导出/中英题名/预印本形式合并成一条，逐对写判据；
        # 合并后 orig_map 改指 canonical，编号才不会出现两条同 DOI 的条目。
        self.dedupe_ok = False
        if active_lib.exists():
            dd_argv = ["--lib", str(active_lib),
                       "--out", str(work / "library_deduped.json"),
                       "--audit", str(work / "dedupe_audit.json"),
                       "--md", str(out / "merge_basis.md"),
                       "--glossary", str(HERE / "rules" / "glossary_zh_en.md")]
            if (work / "orig_map.json").exists():
                dd_argv += ["--orig-map", str(work / "orig_map.json"),
                            "--out-map", str(work / "orig_map_dedup.json")]
            ok_dd, dd = self.step("2b 库内去重", "dedupe_audit.py", dd_argv, required=False)
            if ok_dd and (work / "library_deduped.json").exists():
                active_lib = work / "library_deduped.json"
                self.dedupe_ok = True
                self.dedupe_report = dd

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
            # 改前：没有 --pass1 就停在 needs_model（--no-model 时记 blocked），
            # 后面十步一步都不跑，永远不出 *_cited.docx。改后用内置规则自己分流。
            prep_obj = json.loads((work / "prep.json").read_text(encoding="utf-8-sig"))
            p1 = work / "pass1_auto.txt"
            self.auto["pass1"] = auto_pass1(prep_obj, p1)
        ok, _ = self.step("5 分流校验", "pass1_prep.py",
                          ["--verify", str(p1), "--prep", str(work / "prep.json")], required=False)

        if not active_lib.exists():
            self.status = "partial"
            self.blocked_at = "缺文献库"
            return

        # 术语表是 Markdown 两列表（rules/glossary_zh_en.md）；改前这里指向不存在的
        # glossary_zh_en.tsv，load_glossary 静默返回空表，中英题名合并不生效。
        ok, glos = self.step("6 候选检索", "match_candidates.py",
                             ["--prep", str(work / "prep.json"), "--pass1", str(p1),
                              "--lib", str(active_lib), "--tsv", str(work / "match_input.tsv"),
                              *(("--refs", a.refs) if a.refs else ()),
                              "--glossary", str(HERE / "rules" / "glossary_zh_en.md")])
        self.glossary = (glos or {}).get("glossary") or {}
        if not ok:
            return

        m = Path(a.match) if a.match else None
        self.match_decisions = {}
        self.match_dec_src = ""
        self.match_pass3 = {}
        self.match_pass3_src = ""
        if m is None:
            # 没有 --match：读模型的 judgments.json（缺省在原稿同目录），仍没有就只给候选
            jp = Path(a.judgments) if a.judgments else (Path(a.docx).parent / "judgments.json")
            if jp.exists() or a.judgments:
                self.match_decisions, self.match_dec_src = load_match_judgments(jp)
            m = work / "pass3_auto.txt"
            self.auto["match"] = auto_match(work / "match_input.json", m,
                                            decisions=self.match_decisions)
        else:
            self.match_pass3 = parse_pass3(m)
            self.match_pass3_src = f"--match {m}（{len(self.match_pass3)} 行）"
            self.auto["match"] = auto_match(work / "match_input.json",
                                            work / "pass3_auto.txt",
                                            pass3=self.match_pass3)
        self.judgment = match_judgment_layer(
            work / "match_input.json", self.match_decisions, self.match_dec_src,
            self.match_pass3, self.match_pass3_src, out)
        self.step("7 匹配校验", "check_pass3.py",
                  [str(m), "--pass1", str(p1), "--lib", str(active_lib)], required=False)

        argv = ["--prep", str(work / "prep.json"), "--match", str(m),
                "--lib", str(active_lib), "--out", str(work / "numbering.json"),
                "--cands", str(work / "match_input.tsv")]
        omap = work / "orig_map_dedup.json" if getattr(self, "dedupe_ok", False) else work / "orig_map.json"
        if omap.exists():
            argv += ["--orig-map", str(omap)]
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

        # 9c 来源核验：条目元数据 + 逐句「论断↔文献」判定，产出证据不足清单
        self.step("9c 来源核验", "verify_sources.py",
                  ["--lib", str(active_lib), "--numbering", str(num_json),
                   "--cands", str(work / "match_input.json"),
                   "--out", str(work / "source_audit.json"),
                   "--tsv", str(out / "evidence_map.tsv"),
                   "--md", str(out / "evidence_gaps.md")], required=False)

        # 9d 覆盖表：全部章节 / 全部引用点 / 全部条目
        cov_argv = ["--prep", str(work / "prep.json"), "--pass1", str(p1),
                    "--numbering", str(num_json), "--lib", str(active_lib),
                    "--out", str(work / "coverage.json"),
                    "--md", str(out / "coverage.md")]
        for flag, f in (("--ingest", work / "ingest_report.json"),
                        ("--dedupe", work / "dedupe_audit.json"),
                        ("--audit", work / "source_audit.json")):
            if f.exists():
                cov_argv += [flag, str(f)]
        self.step("9d 覆盖表", "coverage_report.py", cov_argv, required=False)

        # 9e 快照与回溯链：输入 sha256 + 每条编号的回溯链
        prov_argv = ["--numbering", str(num_json), "--lib", str(active_lib),
                     "--out", str(work / "provenance.json"),
                     "--md", str(out / "trace.md"),
                     "--input", f"docx={a.docx}"]
        if a.refs:
            prov_argv += ["--input", f"refs={a.refs}"]
        for lp in a.lib or []:
            prov_argv += ["--input", f"lib={lp}"]
        if (work / "changelog.jsonl").exists():
            prov_argv += ["--changelog", str(work / "changelog.jsonl")]
        self.step("9e 快照回溯", "provenance.py", prov_argv, required=False)

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
                    "evidence": (f"{c.get('pid')} 段第 {k + 1} 个锚点，句内偏移 "
                                 f"{a.get('offset') if a.get('offset') is not None else a.get('start')}"
                                 f"（uid {c['uid']}）→ numbering.cites[{c['uid']}].anchors[{k}]"
                                 f" → changelog"),
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
        (r"覆盖|coverage|章节|条目清单|清单", "coverage"),
        (r"快照|回溯|provenance|snapshot|trace|来源定位", "provenance"),
        (r"度量|指标|metrics|对照|comparison|baseline|效率", "metrics"),
        (r"manuscript|稿件|改稿|正文|修订|draft|article", "cited_manuscript"),
        (r"reference|bibliograph|文献表|参考文献|refs", "reference_list"),
        (r"citation|引用|标注|in_text|论断", "source_audit"),
        (r"dedupe|duplicate|去重|合并", "dedupe"),
        (r"verif|audit|核验|检查|一致", "verification"),
        (r"change|diff|changelog|修改|变更", "changes"),
        (r"status|outcome|状态", "status"),
    ]

    def dedupe_echo(self) -> dict:
        """并库查重的真实判定：动作、依据码与得分，逐条可核（不写文件名了事）。"""
        rep = self.work / "ingest_report.json"
        rows: list[dict] = []
        if rep.exists():
            try:
                rows = json.loads(rep.read_text(encoding="utf-8-sig"))
            except Exception:
                rows = []
        merged = [r for r in rows if r.get("action") == "merged"]
        return {"n_entries": len(rows), "n_merged": len(merged),
                "n_added": len(rows) - len(merged),
                "basis": sorted({str(r.get("basis")) for r in rows if r.get("basis")}),
                "items": rows}

    def echo_contract(self, contract: dict, deliverables: list, checks: dict,
                      changes: list, fmap: list, gaps: list, uncertainty: list,
                      exceptions: list, extra: dict | None = None) -> dict:
        """按 case 的 output_contract 逐字段回显。

        每个字段填能核对的真实内容：产物路径+字节数+sha256、逐条匹配依据、改动条数与
        缺口/异常；确实没有对应产出时才退化为 pointer 并标 partial。
        """
        def blk(rows: list, unit: str) -> dict:
            return {"n": len(rows), "unit": unit, "items": rows}

        pool = {
            "cited_manuscript": blk(deliverables, "file:bytes:sha256"),
            "reference_list": blk([d for d in deliverables if d["kind"] == "reference_list"],
                                  "file:bytes"),
            "citation_actions": blk(fmap, "pid + 句内偏移"),
            "dedupe": self.dedupe_echo(),
            "verification": checks,
            "changes": {"n": len(changes), "items": changes,
                        "uncertainty": uncertainty, "material_gaps": gaps,
                        "exceptions": exceptions},
            "status": self.status,
        }

        def empty(v) -> bool:
            if v in (None, {}, []):
                return True
            return isinstance(v, dict) and v.get("n") == 0 and "items" in v

        self.echo_status = {}
        out = {}
        for key in contract:
            hit = None
            for pat, kind in self.CONTRACT_RULES:
                if re.search(pat, key, re.I):
                    hit = kind
                    break
            if hit and not empty(pool.get(hit)):
                out[key] = pool[hit]
                self.echo_status[key] = {"status": "full", "source": hit}
            else:
                out[key] = {"note": "本字段无专门产出，内容见 fact_source_map / checks / version_boundary",
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
        for s in self.steps:
            r = s["result"] or {}
            if s["name"] == "2b 库内去重":
                checks[s["name"]] = {"ok": s["ok"], "n_pairs": r.get("n_pairs"),
                                     "n_merged": r.get("n_merged_pairs"),
                                     "n_keep": r.get("n_kept"),
                                     "n_conflict": r.get("n_conflict"),
                                     "by_code": r.get("by_code")}
            elif s["name"] == "9c 来源核验":
                checks[s["name"]] = {"ok": r.get("ok"), "n_error": r.get("n_error"),
                                     "n_warn": r.get("n_warn"),
                                     "n_need_human": r.get("n_need_human"),
                                     "verdicts": r.get("verdicts")}
            elif s["name"] == "9d 覆盖表":
                checks[s["name"]] = {"ok": s["ok"], "n_manual": r.get("n_manual"),
                                     "n_cite_points": r.get("n_cite_points"),
                                     "n_uncited": r.get("n_uncited")}
            elif s["name"] == "9e 快照回溯":
                checks[s["name"]] = {"ok": s["ok"], "n_inputs": r.get("n_inputs"),
                                     "n_refs": r.get("n_refs"),
                                     "n_orphan": r.get("n_orphan")}

        preview_step = next((s for s in self.steps if s["name"] == "12 渲染预览"), None)
        preview = (preview_step or {}).get("result") or {}

        # 新增证据件的读取（缺文件不报错，相关字段就为空并在 gaps 里说明）
        def _load(name: str) -> dict:
            p = work / name
            if not p.exists():
                return {}
            try:
                return json.loads(p.read_text(encoding="utf-8-sig"))
            except Exception:
                return {}

        dedupe = _load("dedupe_audit.json")
        src_audit = _load("source_audit.json")
        coverage = _load("coverage.json")
        prov = _load("provenance.json")

        deliverables = []
        for kind, path, fmt, extra in [
            ("cited_manuscript", cited, "docx", {"editable": True}),
            ("reference_list", refs_txt, "txt", {"entries": len(numbering.get("refs") or []),
                                                 "style": "GB/T 7714-2015 顺序编码制"}),
            ("numbering_table", work / "numbering.tsv", "tsv",
             {"entries": numbering.get("n_refs")}),
            ("changelog", out / "changelog.md", "md", {"entries": len(changes)}),
            ("dedupe_basis", out / "merge_basis.md", "md",
             {"pairs": dedupe.get("n_pairs_examined"),
              "merged": dedupe.get("n_merged_pairs"),
              "kept": dedupe.get("n_kept")}),
            ("coverage", out / "coverage.md", "md",
             {"sections": (coverage.get("totals") or {}).get("n_sections"),
              "cite_points": (coverage.get("totals") or {}).get("n_cite_points"),
              "entries": (coverage.get("totals") or {}).get("n_entries")}),
            ("evidence_map", out / "evidence_map.tsv", "tsv",
             {"cites": src_audit.get("n_cites"),
              "weak": (src_audit.get("verdict_counts") or {}).get("weak"),
              "no_source": (src_audit.get("verdict_counts") or {}).get("no_source")}),
            ("evidence_gaps", out / "evidence_gaps.md", "md",
             {"need_human": src_audit.get("n_need_human")}),
            ("trace", out / "trace.md", "md", {"refs": prov.get("n_refs")}),
        ]:
            if path.exists():
                deliverables.append({"kind": kind, "path": str(path), "format": fmt,
                                     "bytes": path.stat().st_size,
                                     "sha256": sha256(path)[:16], **extra})

        # 中间证据件复制进 out，评审只看 out 也能复核（脚本仍写 work）
        for name in ("source_audit.json", "coverage.json", "dedupe_audit.json",
                     "provenance.json"):
            srcp = work / name
            if not srcp.exists():
                continue
            dst = out / name
            try:
                shutil.copyfile(srcp, dst)
            except Exception:
                dst = srcp
            deliverables.append({"kind": name.rsplit(".", 1)[0], "path": str(dst),
                                 "format": "json", "bytes": dst.stat().st_size,
                                 "sha256": sha256(dst)[:16]})

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
        for p in sorted(out.glob("references.*")):
            deliverables.append({"kind": "library_export", "path": str(p),
                                 "format": p.suffix.lstrip("."),
                                 "bytes": p.stat().st_size,
                                 "sha256": sha256(p)[:16]})

        uncertainty = []
        for name, c in checks.items():
            n = c.get("n_problems")
            if isinstance(n, int) and n > 0:
                uncertainty.append({"kind": "check",
                                    "detail": f"{name} 未通过：{n} 项"})
        if numbering.get("unmapped_markers"):
            uncertainty.append({"kind": "marker",
                                "detail": f"正文标记无对应条目：{numbering['unmapped_markers'][:5]}"})
        if numbering.get("dropped_ids"):
            uncertainty.append({"kind": "record",
                                "detail": f"匹配到库外 id，已跳过：{numbering['dropped_ids'][:5]}"})
        # 逐句核验的弱证据/歧义：进不确定项，不用 0 掩盖
        for r in (src_audit.get("sentences") or [])[:60]:
            if r.get("need_human"):
                uncertainty.append({"kind": "source", "uid": r.get("uid"),
                                    "detail": f"{r.get('verdict')}（{'/'.join(r.get('codes') or [])}）"
                                              f"：{r.get('action')}"})
        for r in (src_audit.get("entries") or []):
            errs = [i for i in (r.get("issues") or []) if i.get("severity") == "error"]
            if errs:
                uncertainty.append({"kind": "metadata", "id": r.get("id"),
                                    "detail": "；".join(i["note"] for i in errs)})
        for sec in (coverage.get("sections") or []):
            for u in sec.get("uncited_cite_uids") or []:
                uncertainty.append({"kind": "cite_missing", "uid": u,
                                    "detail": f"{sec.get('name')} 判定需引但未挂编号"})

        gaps = []
        auto = getattr(self, "auto", {}) or {}
        if auto.get("pass1"):
            gaps.append({"kind": "auto",
                         "detail": f"句子分流由内置规则生成（{auto['pass1']['rule']}），"
                                   f"{auto['pass1']['rows']} 句，"
                                   f"计数 {auto['pass1']['counts']}；不是模型判断"})
        if auto.get("match"):
            gaps.append({"kind": "auto",
                         "detail": f"句—库匹配：已有 [n] 照映射，其余只给候选"
                                   f"（{auto['match']['rule']}），"
                                   f"{auto['match']['n_assign']} 句配上、"
                                   f"{auto['match']['n_none']} 句 NONE、"
                                   f"{auto['match'].get('n_pending', 0)} 句待模型选"})
        _jd = getattr(self, "judgment", None) or {}
        if _jd.get("pending"):
            gaps.append({"kind": "judgment",
                         "detail": f"判断层：{_jd['pending']} 句论断配文献待模型选"
                                   f"（候选见 match_worklist.md，未按词面重合替模型挑）"})
        if not self.a.lib and self.a.refs:
            gaps.append({"kind": "input",
                         "detail": f"无 --lib 文献库输入：以空库为底并入稿件原有文献表，"
                                   f"库内 {len(entries)} 条，均来自稿件文献表"})
        elif not self.a.lib:
            gaps.append({"kind": "input", "detail": "无文献库，仅用稿件原有文献表"})
        if not self.a.refs:
            gaps.append({"kind": "input", "detail": "无稿件文献表，原有 [n] 未做编号映射"})
        glos = getattr(self, "glossary", {}) or {}
        if glos and (glos.get("n") == 0 or glos.get("warning")):
            gaps.append({"kind": "glossary",
                         "detail": glos.get("warning")
                         or f"术语表解析到 0 条：{glos.get('path')}"})
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
            if s["name"] == "1 建库":
                for u in r.get("unparsed") or []:
                    gaps.append({"kind": "format",
                                 "detail": f"文献库文件未解析出条目：{u.get('path')}"
                                           f"（{u.get('why')}）"})
        for p in (dedupe.get("pairs") or []):
            if p.get("decision") in ("keep_confirm", "conflict"):
                gaps.append({"kind": "dedupe",
                             "detail": f"{p.get('a')} / {p.get('b')}：{p.get('code')} "
                                       f"{p.get('basis')}（未自动合并，需人工确认）"})
        for g in prov.get("gaps") or []:
            gaps.append({"kind": "provenance", "detail": g})
        if not dedupe:
            gaps.append({"kind": "step", "detail": "库内去重未产出：dedupe_audit.json 缺失"})
        if not src_audit:
            gaps.append({"kind": "step", "detail": "来源核验未产出：source_audit.json 缺失"})
        if not coverage:
            gaps.append({"kind": "step", "detail": "覆盖表未产出：coverage.json 缺失"})

        exceptions = [{"step": s["name"], "exit": s["exit"], "stderr": s["stderr"]}
                      for s in self.steps if not s["ok"] and s["name"] != "12 渲染预览"]

        fmap = self.fact_map(numbering, entries)
        # 逐条引用补上核验结论与匹配分，事实与来源映射因此能回指到「为什么是这条」
        sent_by_uid = {r.get("uid"): r for r in (src_audit.get("sentences") or [])}
        for row in fmap:
            uid = (row.get("anchor") or {}).get("uid")
            srow = sent_by_uid.get(uid) or {}
            if srow:
                row["verification"] = {"verdict": srow.get("verdict"),
                                       "codes": srow.get("codes"),
                                       "top_score": srow.get("top_score"),
                                       "ambiguous": srow.get("ambiguous"),
                                       "need_human": srow.get("need_human")}
        result = {
            "case_id": case_id,
            "status": self.status,
            "blocked_at": self.blocked_at,
            "needs": self.needs,
            "skill": {"name": "citation-manager", "version": "1.2.0"},
            "inputs": {"docx": self.a.docx, "refs": self.a.refs, "lib": self.a.lib,
                       "pass1": self.a.pass1, "match": self.a.match},
            "deliverables": deliverables,
            "fact_source_map": fmap,
            "version_boundary": {"changes": changes,
                                 "uncertainty": uncertainty,
                                 "material_gaps": gaps,
                                 "exceptions": exceptions},
            "numbering": {"mode": numbering.get("mode"), "n_refs": numbering.get("n_refs"),
                          "order": [{"n": r["n"], "id": r["id"]} for r in numbering.get("order", [])]},
            "规则命中": {k: {"规则": v.get("rule"), "条数": v.get("rows"),
                           "逐条": v.get("items")} for k, v in auto.items()},
            "checks": checks,
            "input_snapshot": prov.get("inputs") or [],
            "provenance": {"path": str(out / "provenance.json") if prov else None,
                           "n_inputs": prov.get("n_inputs"),
                           "n_refs": prov.get("n_refs"),
                           "n_orphan": prov.get("n_orphan"),
                           "n_changes_total": prov.get("n_changes_total"),
                           "chain": (prov.get("chain") or [])[:20]},
            "dedupe": {"n_in": dedupe.get("n_in"), "n_out": dedupe.get("n_out"),
                       "n_pairs_examined": dedupe.get("n_pairs_examined"),
                       "n_merged_pairs": dedupe.get("n_merged_pairs"),
                       "n_kept": dedupe.get("n_kept"),
                       "n_keep_confirm": dedupe.get("n_keep_confirm"),
                       "n_conflict": dedupe.get("n_conflict"),
                       "by_code": dedupe.get("by_code"),
                       "merged": dedupe.get("merged_index") or [],
                       "pairs": dedupe.get("pairs") or [],
                       "rule_file": "rules/dedupe_rules.md"},
            "coverage": {"totals": coverage.get("totals") or {},
                         "sections": coverage.get("sections") or [],
                         "multi_sentence_refs": coverage.get("multi_sentence_refs") or {},
                         "entries": coverage.get("entries") or [],
                         "manual": coverage.get("manual") or [],
                         "path": str(out / "coverage.md") if coverage else None},
            "source_audit": {"ok": src_audit.get("ok"),
                             "n_entries": src_audit.get("n_entries"),
                             "n_error": src_audit.get("n_error"),
                             "n_warn": src_audit.get("n_warn"),
                             "n_cites": src_audit.get("n_cites"),
                             "verdict_counts": src_audit.get("verdict_counts"),
                             "code_counts": src_audit.get("code_counts"),
                             "n_need_human": src_audit.get("n_need_human"),
                             "n_ambiguous": src_audit.get("n_ambiguous"),
                             "n_missing_author": src_audit.get("n_missing_author"),
                             "manual_items": src_audit.get("manual_items") or [],
                             "sentences": src_audit.get("sentences") or [],
                             "entries": src_audit.get("entries") or [],
                             "limits": src_audit.get("limits") or [],
                             "path": str(out / "source_audit.json") if src_audit else None},
            "sources": {"library": self.a.lib, "manuscript_refs": self.a.refs,
                        "rule_gbt": "GB/T 7714—2015",
                        "rule_source": "https://www.jsczz.cn/CN/column/column17.shtml"},
            "preview": {"produced": preview.get("produced") or [],
                        "skipped": preview.get("skipped")},
            "glossary": glos,
            "limits": LIMITS,
            "steps": self.steps,
        }

        # 口径度量：与 score_run.py 同一实现，对照方可对基础 Agent 成品重算
        metrics = {}
        try:
            import importlib.util as _u5
            spec5 = _u5.spec_from_file_location("score_run", SCRIPTS / "score_run.py")
            mod5 = _u5.module_from_spec(spec5)
            spec5.loader.exec_module(mod5)
            metrics = mod5.assemble(cited, num_json, getattr(self, "active_lib", None),
                                    work / "source_audit.json",
                                    work / "coverage.json", result=result)
        except Exception as ex:      # 度量失败不影响成品
            metrics = {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
        result["metrics"] = metrics
        if metrics.get("ok"):
            (out / "metrics.json").write_text(
                json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
            deliverables.append({"kind": "metrics", "path": str(out / "metrics.json"),
                                 "format": "json",
                                 "bytes": (out / "metrics.json").stat().st_size,
                                 "sha256": sha256(out / "metrics.json")[:16]})
        else:
            gaps.append({"kind": "metrics", "detail": f"口径度量未生成：{metrics.get('error')}"})

        result["output_contract"] = self.echo_contract(
            contract, deliverables, checks, changes, fmap, gaps, uncertainty, exceptions,
            extra={"coverage": {"n": len(coverage.get("manual") or []),
                                "unit": "条目/句子覆盖",
                                "items": coverage.get("manual") or []},
                   "provenance": {"n": len(prov.get("inputs") or []), "unit": "file:sha256",
                                  "items": prov.get("inputs") or []},
                   "source_audit": {"n": src_audit.get("n_cites") or 0,
                                    "unit": "uid + 判定码",
                                    "items": src_audit.get("sentences") or []},
                   "metrics": metrics})
        result["output_contract_echo"] = getattr(self, "echo_status", {})

        p = out / "result.json"
        # 样式合规检查：题面「格式转换与字段补齐」要求保留 DOI/卷期页/类型标识
        try:
            import importlib.util as _ilu
            _sp = _ilu.spec_from_file_location(
                "check_style", Path(__file__).resolve().parent / "scripts" / "check_style.py")
            _cs = _ilu.module_from_spec(_sp)
            _sp.loader.exec_module(_cs)
            _bibp = None
            for _c in (root / "refs.bib", root / "lib" / "sample_refs.bib"):
                if _c.exists():
                    _bibp = _c
                    break
            if _bibp is None:
                _cands = sorted(root.rglob("*.bib"))
                _bibp = _cands[0] if _cands else None
            if _bibp is not None:
                _sty = getattr(self, "style", "gbt7714")
                _ents = _cs.parse_bib(_bibp.read_text(encoding="utf-8-sig", errors="replace"))
                _rows, _okn = [], 0
                for _k, _f in _ents.items():
                    _e = _cs.norm_entry(_f)
                    try:
                        _rend, _ = _cs.load_styles().render(_e, _sty)
                    except Exception:
                        _rend = None
                    _r = _cs.check_one(_e, _sty, _rend or "")
                    _r["key"] = _k
                    _okn += 1 if _r["ok"] else 0
                    _rows.append({"key": _k, "ok": _r["ok"], "gaps": _r["gaps"]})
                result["样式合规"] = {"style": _sty, "entries": len(_rows), "ok": _okn,
                                 "rate": round(_okn / len(_rows), 3) if _rows else None,
                                 "rows": _rows}
                if _rows and _okn < len(_rows):
                    gaps.append(f"样式 {_sty} 合规 {_okn}/{len(_rows)} 条，"
                                f"缺口见 result.json 样式合规")
        except Exception as _ex:
            gaps.append(f"样式合规检查未完成：{type(_ex).__name__}")

        # 题面：result.json 含全部 output_contract 字段 → 平铺到顶层
        for _k, _v in (result.get("output_contract") or {}).items():
            result.setdefault(_k, _v)
        result["目标产物"] = result.get("deliverables") or []
        result["事实与来源映射"] = result.get("fact_source_map") or {}
        result["版本与边界"] = result.get("version_boundary") or {}
        result["判断层"] = getattr(self, "judgment", None) or {
            "unit": JUDGMENT_UNIT, "n": 0, "filled": 0, "pending": 0,
            "source": "未跑到句—库匹配步骤，没有可判句", "items": [], "exempt": [],
            "who_fills": "调用方模型", "who_checks": "run.py"}
        result.setdefault("uncertainty",
                          (result.get("version_boundary") or {}).get("material_gaps") or [])
        p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

        rep = {"case_id": case_id, "status": self.status,
               "steps": [{"name": s["name"], "ok": s["ok"], "exit": s["exit"]} for s in self.steps],
               "checks": checks, "n_changes": len(changes),
               "n_fact_rows": len(result["fact_source_map"]),
               "n_gaps": len(gaps), "n_uncertainty": len(uncertainty),
               "metrics": {k: metrics.get(k) for k in ("manual_review", "quality")
                           if k in metrics}}
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
    ap.add_argument("--judgments", default=None,
                    help="论断配文献的判断（judgments.json）；不给则找稿件同目录的 judgments.json")
    ap.add_argument("--no-model", action="store_true",
                    help="不交模型输出，两次模型步骤都用内置规则替身")
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
            out_dir = Path(args.out)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "result.json").write_text(json.dumps(
                {"case_id": args.case_id or "case", "status": "blocked",
                 "gaps": ["输入目录里没找到稿件"], "detail": plan.get("gaps"),
                 "deliverables": ["missing_input.md"]}, ensure_ascii=False, indent=1),
                encoding="utf-8")
            (out_dir / "missing_input.md").write_text("\n".join([
                "# 缺料说明：没有找到稿件", "",
                f"- 输入目录：`{args.input}`",
                "- 找过：该目录下所有 `.docx` / `.md`（`.md` 会先转成 docx 再跑）",
                "- 需要补：待整理引用的稿件；可选 `refs.txt`（稿件原文献表）与 `.bib`（文献库）",
                "- 缺什么才能出产物：没有稿件就无从分句与写入引用，`*_cited.docx` 无法生成",
                "- 补齐后重跑：`python run.py --input <目录> --work <w> --out <o>`",
                f"- 本次判定细节：{plan.get('gaps')}",
            ]) + "\n", encoding="utf-8")
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







