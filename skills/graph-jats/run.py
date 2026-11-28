#!/usr/bin/env python3
"""run.py — 从两篇论文的 JATS 段落材料构建可查询的知识图谱。

设计口径（对应 SKILL.md 第 5 节与 rules/criteria.md）：
  * 输入是段落 JSON，不是 PDF：位置一律用 source_id + paragraph_id，**不用页码**。
  * 抽取是规则式的（词表 + 邻接共现），不猜语义；置信度只做两档。
  * 方法/任务标签跨篇合并并留依据；数据集/指标默认**保留分离**并记冲突，
    因为两篇共用一个数据集名不等于同一实例、同一划分或同一指标口径。
  * 查询真跑，结果与证据一并落盘；不是"文档里说有查询"。
  * 零模型请求。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

VERSION = "1.1.0"

CODES: dict[str, tuple[str, str]] = {
    "G01": ("error", "节点 id 唯一且非空"),
    "G02": ("error", "边端点必须存在"),
    "G03": ("error", "节点与边至少一条证据，且三字段齐全"),
    "G04": ("error", "类型在允许集合内"),
    "G05": ("error", "标签非空且 ≤80 字"),
}
ALLOWED = {"paper", "method", "dataset", "metric", "task"}

ID_KEYS = ("paragraph_id", "paragraphId", "para_id", "pid", "id")
TEXT_KEYS = ("text", "content", "body", "paragraph", "value")

# 提取词表：只认显式表述，宁可少抽也不错抽
LEX = {
    "method": ["bert", "roberta", "transformer", "attention", "lstm", "cnn", "gan",
               "svm", "random forest", "xgboost", "graph neural network", "gnn",
               "encoder-decoder", "diffusion model", "contrastive learning"],
    "dataset": ["sst-2", "imdb", "mnist", "cifar-10", "cifar-100", "squad", "wmt",
                "conll", "ms marco", "wikitext", "librispeech", "chexpert"],
    "metric": ["accuracy", "f1", "auc", "precision", "recall", "bleu", "rouge",
               "mse", "rmse", "perplexity", "kappa"],
    "task": ["classification", "regression", "translation", "summarization",
             "question answering", "segmentation", "detection", "retrieval"],
}
# 缩写 → 全称：同一对象不因写法不同裂成两个节点
ALIAS = {"gnn": "graph neural network"}
# 跨篇合并策略：只有方法/任务按标签合并；数据集/指标保留分离
MERGE_KINDS = {"method", "task"}
CONFLICT = {
    "dataset": "两篇共用数据集名，但未报告划分/版本/许可，不得视为同一数据集实例",
    "metric": "同名指标的计算口径与所用数据集可能不同，不得合并",
}
TAG = re.compile(r"<[^>]{1,80}>")
SENT_END = re.compile(r"[。！？!?]|\.(?=\s|$)")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


def norm_text(s: str) -> str:
    s = TAG.sub(" ", s)
    s = s.replace("\u3000", " ").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()


def walk_paragraphs(obj, out: list[tuple[str, str]]) -> None:
    """容忍多种段落 JSON 形状：{paragraphs:[...]}、[...]、嵌套 {sections:{body:[]}}。"""
    if isinstance(obj, dict):
        pid = next((str(obj[k]) for k in ID_KEYS
                    if k in obj and isinstance(obj[k], (str, int))), None)
        txt = next((obj[k] for k in TEXT_KEYS
                    if k in obj and isinstance(obj[k], str)), None)
        if txt and len(norm_text(txt)) >= 20:
            out.append((pid or f"p{len(out) + 1:04d}", norm_text(txt)))
        for v in obj.values():
            walk_paragraphs(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk_paragraphs(v, out)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def sentence_quote(text: str, term: str, limit: int = 160) -> str:
    """取关系词所在句作短引，不跨句、不从半个词切起。"""
    i = text.lower().find(term.lower())
    if i < 0:
        return text[:limit].strip()
    starts = [m.end() for m in SENT_END.finditer(text, 0, i)]
    left = max(starts) if starts else 0
    m = SENT_END.search(text, i)
    right = m.end() if m else len(text)
    quote = text[left:right].strip()
    if len(quote) > limit:
        cut = quote.rfind(" ", 0, limit)
        quote = quote[:cut if cut > 40 else limit].strip()
    return quote


def ev(source_id: str, pid: str, text: str, term: str) -> dict:
    return {"source_id": source_id, "paragraph_id": pid,
            "quote": sentence_quote(text, term)}


def extract(paras: list[tuple[str, str]], source_id: str) -> tuple[list, list]:
    nodes, edges = {}, {}

    paper = f"paper:{source_id}"
    first_ev = []
    if paras:
        pid0, txt0 = paras[0]
        first_ev = [{"source_id": source_id, "paragraph_id": pid0,
                     "quote": sentence_quote(txt0, txt0[:1])}]
    nodes[paper] = {"id": paper, "type": "paper", "label": f"论文 {source_id}",
                    "confidence": "high", "evidence": first_ev}

    for pid, text in paras:
        low = text.lower()
        found: dict[str, list[tuple[str, str]]] = {}
        for kind, terms in LEX.items():
            for t in terms:
                if re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low):
                    canon = ALIAS.get(t, t)
                    if canon not in [c for c, _ in found.get(kind, [])]:
                        found.setdefault(kind, []).append((canon, t))
        for kind, pairs in found.items():
            for canon, matched in pairs:
                nid = f"{kind}:{slug(canon)}@{source_id}"
                nodes.setdefault(nid, {"id": nid, "type": kind, "label": canon,
                                       "confidence": "high", "evidence": []})
                e = ev(source_id, pid, text, matched)
                if e not in nodes[nid]["evidence"]:
                    nodes[nid]["evidence"].append(e)
                rel = {"method": "uses_method", "dataset": "evaluates_on",
                       "metric": "reports_metric", "task": "about"}[kind]
                key = (paper, nid, rel)
                edges.setdefault(key, {"source": paper, "target": nid, "type": rel,
                                       "confidence": "high", "evidence": []})
                if e not in edges[key]["evidence"]:
                    edges[key]["evidence"].append(e)
        # 方法—指标共现：同一段里出现，才连一条，并标中置信
        for m, m_matched in found.get("method", []):
            for mt, mt_matched in found.get("metric", []):
                key = (f"method:{slug(m)}@{source_id}",
                       f"metric:{slug(mt)}@{source_id}", "reports_metric")
                if key not in edges:
                    edges[key] = {"source": key[0], "target": key[1],
                                  "type": "reports_metric", "confidence": "medium",
                                  "evidence": [ev(source_id, pid, text, m_matched)]}
    return list(nodes.values()), list(edges.values())


def build_merges(nodes: list, edges: list) -> tuple[list, list]:
    """跨篇候选按类型分流，返回 (合并记录, 需要保留分离的节点 id 集合)。

    method/task：标签归一化相同就合并，依据写明"只合并名称，不断言同一实例"。
    dataset/metric：不合并，两个节点各自留证据，冲突写进 conflict。
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for n in nodes:
        if n["type"] == "paper":
            continue
        groups.setdefault((n["type"], n["label"].lower()), []).append(n)
    merges, keep = [], set()
    for (kind, label), ns in sorted(groups.items()):
        srcs = sorted({e["source_id"] for n in ns for e in n.get("evidence") or []})
        if len(srcs) < 2:
            continue
        cand = [n["id"] for n in ns]
        if kind in MERGE_KINDS:
            merges.append({
                "candidate_ids": cand, "decision": "merge",
                "basis": [f"类型 {kind}，标签归一化相同：{label}",
                          f"两篇都显式出现：{srcs}",
                          "只合并方法/任务名称，不断言同一模型权重、超参或实验条件"],
                "conflict": "",
            })
        else:
            keep.update(cand)
            merges.append({
                "candidate_ids": cand, "decision": "keep_separate",
                "basis": [f"类型 {kind}，标签归一化相同：{label}",
                          f"两篇都显式出现：{srcs}",
                          "同名只说明写法相同，不构成同一实例的证据"],
                "conflict": CONFLICT.get(kind, "同名不同实例，不得合并"),
            })
    return merges, keep


def resolve(nodes: list, edges: list, merges: list, keep: set) -> tuple[list, list]:
    """按合并记录改写节点 id 与边端点：合并的收成一个节点，保留分离的加 @source。"""
    merge_map = {}
    for m in merges:
        if m["decision"] != "merge":
            continue
        ids = m["candidate_ids"]
        canon = f"{ids[0].split(':')[0]}:{slug(ids[0].split(':', 1)[1].split('@')[0])}"
        for i in ids:
            merge_map[i] = canon
    by_id = {n["id"]: n for n in nodes}
    out_nodes: dict[str, dict] = {}
    for n in nodes:
        if n["type"] == "paper":
            out_nodes[n["id"]] = n
            continue
        if n["id"] in merge_map:
            cid = merge_map[n["id"]]
            tgt = out_nodes.setdefault(cid, {"id": cid, "type": n["type"],
                                             "label": n["label"], "confidence": "high",
                                             "evidence": []})
            for e in n.get("evidence") or []:
                if e not in tgt["evidence"]:
                    tgt["evidence"].append(e)
        else:
            out_nodes[n["id"]] = n
    out_edges: dict[tuple, dict] = {}
    for e in edges:
        s = merge_map.get(e["source"], e["source"])
        t = merge_map.get(e["target"], e["target"])
        k = (s, t, e["type"])
        if k in out_edges:
            for evd in e.get("evidence") or []:
                if evd not in out_edges[k]["evidence"]:
                    out_edges[k]["evidence"].append(evd)
        else:
            out_edges[k] = {**e, "source": s, "target": t}
    return list(out_nodes.values()), list(out_edges.values())


def load_manifest(root: Path) -> tuple[dict, list]:
    """读 corpus-manifest.json：每篇的 DOI/标题/作者/许可；根元数据只归第一篇。"""
    meta: dict[str, dict] = {}
    gaps: list[str] = []
    mp = root / "corpus-manifest.json"
    if not mp.exists():
        return meta, ["缺 corpus-manifest.json：DOI/作者/许可/哈希未核，未补造"]
    try:
        obj = json.loads(read(mp))
    except Exception as exc:
        return meta, [f"corpus-manifest.json 不是合法 JSON：{exc}"]
    papers = obj.get("papers") if isinstance(obj, dict) else None
    papers = papers if isinstance(papers, list) else []
    for p in papers:
        if isinstance(p, dict) and p.get("source_id"):
            meta[str(p["source_id"])] = {
                k: str(p.get(k) or "unknown")
                for k in ("doi", "title", "authors", "license", "sha256")}
    root_meta = obj.get("root") if isinstance(obj, dict) else None
    if isinstance(root_meta, dict):
        first = str((papers[0] or {}).get("source_id")) if papers else "primary"
        meta.setdefault(first, {})
        for k in ("doi", "title", "authors"):
            if root_meta.get(k):
                meta[first].setdefault(k, str(root_meta[k]))
    if len(papers) < 2:
        gaps.append(f"corpus-manifest.json 只登记 {len(papers)} 篇，题目要求两篇")
    return meta, gaps


def verify(nodes: list, edges: list) -> tuple[dict, list]:
    probs = []
    ids = [n["id"] for n in nodes]
    for n in nodes:
        if not n["id"]:
            probs.append(("G01", f"节点缺 id：{n.get('label')}"))
        if len(n.get("label") or "") > 80:
            probs.append(("G05", f"标签过长：{n['id']}"))
        if n["type"] not in ALLOWED:
            probs.append(("G04", f"类型非法：{n['type']}"))
        if not n.get("evidence"):
            probs.append(("G03", f"节点无证据：{n['id']}"))
    if len(ids) != len(set(ids)):
        probs.append(("G01", "节点 id 重复"))
    for e in edges:
        if e["source"] not in ids or e["target"] not in ids:
            probs.append(("G02", f"悬空边：{e['source']} -> {e['target']}"))
        if not e.get("evidence"):
            probs.append(("G03", f"边无证据：{e['source']}->{e['target']}"))
        for evd in e.get("evidence") or []:
            if not all(evd.get(k) for k in ("source_id", "paragraph_id", "quote")):
                probs.append(("G03", f"证据缺字段：{e['source']}->{e['target']}"))
                break
    counts = {c: sum(1 for p in probs if p[0] == c) for c in CODES}
    return {c: {"ok": counts[c] == 0, "detail": f"{CODES[c][1]}：{counts[c]} 处"}
            for c in CODES}, probs


def query(nodes: list, edges: list, merges: list) -> list:
    """自动执行 4 条查询；结果与证据一并返回。"""
    deg: dict[str, int] = {}
    for e in edges:
        deg[e["source"]] = deg.get(e["source"], 0) + 1
        deg[e["target"]] = deg.get(e["target"], 0) + 1
    methods = [n for n in nodes if n["type"] == "method"]
    top = max(methods, key=lambda n: deg.get(n["id"], 0), default=None)
    out = []

    if top:
        out.append({"q": "node", "args": [top["label"]], "n": 1,
                    "rows": [{"node": top["id"], "degree": deg.get(top["id"], 0),
                              "evidence": top["evidence"][:2]}]})
        nb = [e for e in edges if top["id"] in (e["source"], e["target"])]
        out.append({"q": "neighbors", "args": [top["id"]], "n": len(nb),
                    "rows": [{"edge": f"{e['source']} -{e['type']}-> {e['target']}",
                              "confidence": e.get("confidence"),
                              "evidence": e["evidence"][:2]} for e in nb[:10]]})
    else:
        out.append({"q": "node", "args": [], "n": 0, "rows": []})
        out.append({"q": "neighbors", "args": [], "n": 0, "rows": []})

    sec = [n for n in nodes if n["type"] != "paper" and n.get("evidence")
           and all(e["source_id"] == "secondary" for e in n["evidence"])]
    out.append({"q": "by-source", "args": ["secondary"], "n": len(sec),
                "rows": [{"node": n["id"], "label": n["label"],
                          "paragraph_id": n["evidence"][0]["paragraph_id"],
                          "quote": n["evidence"][0]["quote"]}
                         for n in sec[:10]]})

    medium = [e for e in edges if e.get("confidence") == "medium"]
    keep = [m for m in merges if m["decision"] == "keep_separate"]
    rows = [{"kind": "keep_separate", "candidate_ids": m["candidate_ids"],
             "conflict": m["conflict"]} for m in keep]
    rows += [{"kind": "medium_edge",
              "edge": f"{e['source']} -{e['type']}-> {e['target']}",
              "evidence": e["evidence"][:1]} for e in medium[:10]]
    out.append({"q": "unresolved", "args": [], "n": len(rows), "rows": rows})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 JATS 段落材料构建知识图谱")
    ap.add_argument("--input", default=None)
    ap.add_argument("--primary", default=None)
    ap.add_argument("--secondary", default=None)
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="output")
    ap.add_argument("--case-id", default=None)
    a = ap.parse_args(argv)

    t0 = time.perf_counter()
    work, out = Path(a.work), Path(a.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    case_id, contract = a.case_id, []
    srcs: list[tuple[str, Path]] = []
    root: Path | None = None
    if a.primary:
        srcs.append(("primary", Path(a.primary)))
    if a.secondary:
        srcs.append(("secondary", Path(a.secondary)))
    if a.input:
        d = Path(a.input)
        root = d
        cj = d / "case.json"
        if cj.exists():
            try:
                case = json.loads(read(cj))
                case_id = case_id or case.get("case_id")
                oc = case.get("output_contract")
                contract = list(oc) if isinstance(oc, (dict, list)) else []
            except Exception:
                pass
        if not srcs:
            # material.json 是汇总索引：能按它列的文件找到两篇，就不靠文件名猜
            idx = d / "material.json"
            if idx.exists():
                try:
                    obj = json.loads(read(idx))
                    for p in (obj.get("papers") or []) if isinstance(obj, dict) else []:
                        sid, fn = p.get("source_id"), p.get("file")
                        if sid and fn and (d / fn).exists():
                            srcs.append((str(sid), d / fn))
                except Exception:
                    pass
            if not srcs:
                for sid, pat in (("primary", "*primary*material*.json"),
                                 ("secondary", "*secondary*material*.json")):
                    hit = sorted(d.rglob(pat))
                    if hit:
                        srcs.append((sid, hit[0]))
            if not srcs:
                any_json = [p for p in sorted(d.rglob("*.json"))
                            if p.name not in ("case.json", "material.json")
                            and "manifest" not in p.name]
                if any_json:
                    srcs.append(("primary", any_json[0]))
        case_id = case_id or d.name
    case_id = case_id or "case"

    if not srcs:
        # 找不到材料也不空手返回：写 result.json + 可读缺料说明
        msg = "没有找到段落材料 JSON"
        r = {"case_id": case_id, "status": "blocked", "error": msg,
             "checks": {}, "gaps": [msg], "deliverables": ["missing_input.md"],
             "evidence": {"unit": "source_id+paragraph_id", "items": []},
             "output_contract": {k: None for k in contract},
             "output_contract_echo": {k: {"status": "partial", "source": "pointer"}
                                      for k in contract}}
        (out / "result.json").write_text(json.dumps(r, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        (out / "missing_input.md").write_text("\n".join([
            "# 缺料说明：没有找到段落材料", "",
            f"- 输入目录：`{root}`" if root else "- 未给 `--input`，也没有 `--primary/--secondary`",
            "- 找过：`material.json` 索引 → `*primary*material*.json` / `*secondary*material*.json`"
            " → 目录下任意 `.json`（排除 case.json / manifest）",
            "- 需要补：至少一份带段落的材料 JSON（两篇更好，能算跨源一致率）",
            "- 缺什么才能出产物：没有段落就没有实体与关系可抽，`graph.json` / `triples.tsv` "
            "无从生成",
            "- 补齐后重跑：`python run.py --input <目录> --work <w> --out <o>`",
        ]) + "\n", encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked",
                          "result": str(out / "result.json")}, ensure_ascii=False))
        return 1

    gaps: list[str] = []
    bad_sources: list[dict] = []
    meta, mgaps = load_manifest(root) if root else ({}, [])
    gaps.extend(mgaps)
    nodes: list[dict] = []
    edges: list[dict] = []
    per_source: dict[str, int] = {}
    for sid, path in srcs:
        if not path.exists():
            gaps.append(f"{sid} 材料不存在：{path}")
            bad_sources.append({"source_id": sid, "path": str(path),
                                "reason": "文件不存在"})
            continue
        try:
            obj = json.loads(read(path))
        except Exception as exc:
            gaps.append(f"{sid} 材料不是合法 JSON：{exc}")
            bad_sources.append({"source_id": sid, "path": str(path),
                                "reason": f"不是合法 JSON：{exc}"})
            continue
        paras: list[tuple[str, str]] = []
        walk_paragraphs(obj, paras)
        # 去重（同一 pid 同一文本）
        seen, uniq = set(), []
        for pid, txt in paras:
            k = (pid, txt)
            if k not in seen:
                seen.add(k)
                uniq.append((pid, txt))
        per_source[sid] = len(uniq)
        if not uniq:
            gaps.append(f"{sid} 未解析出段落（≥20 字）")
            bad_sources.append({"source_id": sid, "path": str(path),
                                "reason": "JSON 里没有 ≥20 字的段落（字段名不认识或正文为空）"})
            continue
        n, e = extract(uniq, sid)
        nodes.extend(n)
        edges.extend(e)

    if len(per_source) < 2:
        gaps.append("只解析到一篇材料；题目要求两篇进同一图谱")

    # 论文节点带各自元数据；根 DOI/标题/作者只挂第一篇，不套到第二篇
    for n in nodes:
        if n["type"] == "paper":
            sid = n["id"].split(":", 1)[1]
            info = meta.get(sid, {})
            n["meta"] = {"doi": info.get("doi", "unknown"),
                         "title": info.get("title", "unknown"),
                         "authors": info.get("authors", "unknown"),
                         "license": info.get("license", "unknown")}
            if info.get("doi") in (None, "", "unknown"):
                gaps.append(f"{sid} 缺 DOI（manifest 未登记，未借用另一篇的）")

    # 去重节点（同 id 合并证据）
    merged: dict[str, dict] = {}
    for n in nodes:
        if n["id"] in merged:
            for evd in n["evidence"]:
                if evd not in merged[n["id"]]["evidence"]:
                    merged[n["id"]]["evidence"].append(evd)
        else:
            merged[n["id"]] = n
    nodes = list(merged.values())
    # 去重边
    em: dict[tuple, dict] = {}
    for e in edges:
        k = (e["source"], e["target"], e["type"])
        if k in em:
            for evd in e["evidence"]:
                if evd not in em[k]["evidence"]:
                    em[k]["evidence"].append(evd)
        else:
            em[k] = e
    edges = list(em.values())

    merges, keep = build_merges(nodes, edges)
    nodes, edges = resolve(nodes, edges, merges, keep)

    checks, probs = verify(nodes, edges)
    queries = query(nodes, edges, merges)

    q_ok = sum(1 for q in queries if q["n"] > 0)
    if q_ok < 3:
        gaps.append(f"只有 {q_ok}/3 条查询有结果")
    for p in probs:
        gaps.append(f"{p[0]}：{p[1]}")
    for n in nodes:
        if n["type"] == "paper" and not n["evidence"]:
            gaps.append(f"{n['id']} 无段落证据")

    # 读不出的材料要留一份可读记录，不只是在 result.json 里记一句
    if bad_sources or not nodes or not edges:
        lines = ["# 读不出或读不全的材料", "",
                 f"- 输入：`{root}`" if root else "- 输入：命令行参数",
                 f"- 材料 {len(srcs)} 份，读出段落 {sum(per_source.values())} 段，"
                 f"节点 {len(nodes)}、边 {len(edges)}", "",
                 "| source_id | 材料 | 读不出的原因 |", "|-|-|-|"]
        for b in bad_sources:
            lines.append(f"| {b['source_id']} | `{Path(b['path']).name}` | {b['reason']} |")
        if not bad_sources:
            lines.append("| — | — | 材料都读出了段落，但抽取后无节点或无边 |")
        lines += ["", "图谱只包含读得出的部分；缺失部分是材料本身的问题，未补造实体或关系。"]
        (out / "unreadable.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 状态看交付物：图建出来了（节点与边都非空）且 G01–G05 全过 → success。
    # 单源、缺 DOI、缺 manifest、查询覆盖不足都是材料边界，进 gaps 不压状态。
    status = "success" if nodes and edges and not probs else "partial"

    rules = {
        "实体抽取": {"按类型": {k: sum(1 for n in nodes if n["type"] == k)
                            for k in sorted({n["type"] for n in nodes})},
                 "规则": "LEX 词表按段匹配（method/dataset/metric/task），同段去重后建节点"},
        "关系抽取": {"按类型": {k: sum(1 for e in edges if e["type"] == k)
                            for k in sorted({e["type"] for e in edges})},
                 "规则": "论文→实体固定关系（uses_method/evaluates_on/reports_metric/about）"
                         "＋同段方法×指标共现（medium 置信）"},
        "合并": {"merge": sum(1 for m in merges if m["decision"] == "merge"),
               "keep_separate": sum(1 for m in merges if m["decision"] == "keep_separate"),
               "规则": "同类型同标签归一，跨篇同标签只记合并候选、不强行合并"},
        "查询": {"有结果的查询": q_ok, "查询总数": len(queries)},
        "读不出材料": bad_sources,
    }

    graph = {"case_id": case_id, "nodes": nodes, "edges": edges, "merges": merges,
             "sources": sorted(per_source), "paragraphs_per_source": per_source,
             "规则命中": rules,
             "papers": [{"source_id": n["id"].split(":", 1)[1], **n.get("meta", {})}
                        for n in nodes if n["type"] == "paper"]}
    gp = out / "graph.json"
    gp.write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
    tp = out / "triples.tsv"
    rows = []
    for e in edges:
        for evd in e.get("evidence") or []:
            rows.append(f"{e['source']}\t{e['type']}\t{e['target']}\t"
                        f"{evd['source_id']}\t{evd['paragraph_id']}\t{evd['quote']}\n")
    tp.write_text("source\trelation\ttarget\tsource_id\tparagraph_id\tquote\n"
                  + "".join(rows), encoding="utf-8")
    qp = out / "queries.json"
    qp.write_text(json.dumps({"case_id": case_id, "queries": queries},
                             ensure_ascii=False, indent=1), encoding="utf-8")
    vp = out / "verify.json"
    vp.write_text(json.dumps({"counts": {c: (0 if checks[c]["ok"] else 1) for c in CODES},
                              "problems": probs}, ensure_ascii=False, indent=1),
                  encoding="utf-8")

    arts = {"graph": str(gp), "triples": str(tp), "queries": str(qp), "verify": str(vp)}
    payload = {
        "parsed": {"papers": graph["papers"],
                   "nodes": len(nodes), "edges": len(edges),
                   "node_types": {t: sum(1 for n in nodes if n["type"] == t)
                                  for t in sorted({n["type"] for n in nodes})},
                   "merges": {"merge": sum(1 for m in merges if m["decision"] == "merge"),
                              "keep_separate": sum(1 for m in merges
                                                   if m["decision"] == "keep_separate")}},
        "evidence": {"unit": "source_id+paragraph_id+quote",
                     "checks": checks, "gaps": gaps,
                     "sample": [{"node": n["id"],
                                 "source_id": n["evidence"][0]["source_id"],
                                 "paragraph_id": n["evidence"][0]["paragraph_id"],
                                 "quote": n["evidence"][0]["quote"]}
                                for n in nodes if n.get("evidence")][:3]},
        "status": {"status": status,
                   "reason": gaps[0] if gaps else "两篇材料、G01–G05、4 条查询全部通过",
                   "queries_with_results": q_ok, "n_queries": len(queries)},
    }
    echo, echo_status = {}, {}
    for key in contract:
        if key == "阅读/解析结果":
            echo[key], echo_status[key] = payload["parsed"], {"status": "full",
                                                              "source": "graph.json"}
        elif key == "证据与边界":
            echo[key], echo_status[key] = payload["evidence"], {"status": "full",
                                                                "source": "evidence"}
        elif key == "运行状态":
            echo[key], echo_status[key] = payload["status"], {"status": "full",
                                                              "source": "status"}
        else:
            echo[key], echo_status[key] = None, {"status": "partial", "source": "pointer"}
            gaps.append(f"契约字段「{key}」未识别，已置 null")

    result = {
        "output_contract_echo": echo_status,
        "case_id": case_id,
        "skill_version": VERSION,
        "status": status,
        "sources": sorted(per_source),
        "papers": graph["papers"],
        "stats": {"paragraphs": per_source, "nodes": len(nodes), "edges": len(edges),
                  "merges": len(merges), "queries_with_results": q_ok,
                  "elapsed_s": round(time.perf_counter() - t0, 3)},
        "checks": checks,
        "evidence": {"unit": "source_id+paragraph_id+quote",
                     "items": [{"node": n["id"], "source_id": n["evidence"][0]["source_id"],
                                "paragraph_id": n["evidence"][0]["paragraph_id"]}
                               for n in nodes if n.get("evidence")][:50]},
        "queries": queries,
        "merges": merges,
        "规则命中": rules,
        "deliverables": [{"kind": "graph", "path": arts["graph"]},
                         {"kind": "triples", "path": arts["triples"]},
                         {"kind": "queries", "path": arts["queries"]},
                         {"kind": "verify", "path": arts["verify"]}],
        "gaps": gaps,
        "output_contract": echo,
    }
    result["阅读/解析结果"] = payload["parsed"]
    result["证据与边界"] = payload["evidence"]
    result["运行状态"] = payload["status"]
    result.setdefault("uncertainty", list(gaps))
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "case_id": case_id,
                      "sources": sorted(per_source), "nodes": len(nodes),
                      "edges": len(edges), "queries_ok": q_ok, "gaps": len(gaps)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
