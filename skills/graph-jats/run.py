#!/usr/bin/env python3
"""run.py — 从两篇论文的 JATS 段落材料构建可查询的知识图谱。

设计口径（对应 SKILL.md 第 5 节与 rules/criteria.md）：
  * 输入是段落 JSON，不是 PDF：位置一律用 source_id + paragraph_id，**不用页码**。
  * 抽取是规则式的（词表 + 邻接共现），不猜语义；置信度只做两档。
  * 两篇的实体默认**合并同名**并留合并依据；不同对象需在 merges 里标 keep_separate。
  * 查询真跑，结果与证据一并落盘；不是"文档里说有查询"。
  * 零模型请求。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

VERSION = "1.0.0"

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
TAG = re.compile(r"<[^>]{1,80}>")


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


def extract(paras: list[tuple[str, str]], source_id: str) -> tuple[list, list]:
    nodes, edges = {}, {}

    def ev(pid: str, text: str, term: str) -> dict:
        i = text.lower().find(term.lower())
        quote = text[max(0, i - 40): i + 100] if i >= 0 else text[:140]
        return {"source_id": source_id, "paragraph_id": pid, "quote": quote.strip()}

    paper = f"paper:{source_id}"
    first_ev = []
    if paras:
        pid0, txt0 = paras[0]
        first_ev = [{"source_id": source_id, "paragraph_id": pid0,
                     "quote": txt0[:140].strip()}]
    nodes[paper] = {"id": paper, "type": "paper", "label": f"论文 {source_id}",
                    "confidence": "high", "evidence": first_ev}

    for pid, text in paras:
        low = text.lower()
        found: dict[str, list[str]] = {}
        for kind, terms in LEX.items():
            for t in terms:
                if re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low):
                    found.setdefault(kind, []).append(t)
        for kind, terms in found.items():
            for t in terms:
                nid = f"{kind}:{slug(t)}"
                nodes.setdefault(nid, {"id": nid, "type": kind, "label": t,
                                       "confidence": "high", "evidence": []})
                e = ev(pid, text, t)
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
        for m in found.get("method", []):
            for mt in found.get("metric", []):
                key = (f"method:{slug(m)}", f"metric:{slug(mt)}", "reports_metric")
                if key not in edges:
                    edges[key] = {"source": key[0], "target": key[1], "type": "reports_metric",
                                  "confidence": "medium",
                                  "evidence": [ev(pid, text, m)]}
    return list(nodes.values()), list(edges.values())


def build_merges(nodes: list) -> list:
    """跨篇同名归一化 → 合并并留依据；只差后缀的 → 保留分离并记冲突。

    candidate_ids 按 source 展开（如 method:bert@primary），否则"合并依据"无从核对。
    """
    per_label: dict[str, dict[str, str]] = {}
    for n in nodes:
        if n["type"] == "paper":
            continue
        for ev in n.get("evidence") or []:
            per_label.setdefault(n["label"].lower(), {}).setdefault(ev["source_id"], n["id"])

    merges = []
    for label, by_src in sorted(per_label.items()):
        if len(by_src) > 1:
            merges.append({
                "candidate_ids": [f"{vid}@{src}" for src, vid in sorted(by_src.items())],
                "decision": "merge",
                "basis": [f"标签归一化相同：{label}",
                          f"出现于 {len(by_src)} 篇：{sorted(by_src)}"],
                "conflict": "",
            })
    labels = sorted(per_label)
    for a in labels:
        for b in labels:
            if a != b and b.startswith(a) and b[len(a):] in ("-base", "-large", "-small"):
                ids = [f"{vid}@{src}" for src, vid in sorted(per_label[a].items())]
                ids += [f"{vid}@{src}" for src, vid in sorted(per_label[b].items())]
                merges.append({"candidate_ids": ids, "decision": "keep_separate",
                               "basis": ["后缀表示不同规模的实例"],
                               "conflict": "同名不同实例，不得合并"})
    return merges


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
        for ev in e.get("evidence") or []:
            if not all(ev.get(k) for k in ("source_id", "paragraph_id", "quote")):
                probs.append(("G03", f"证据缺字段：{e['source']}->{e['target']}"))
                break
    counts = {c: sum(1 for p in probs if p[0] == c) for c in CODES}
    return {c: {"ok": counts[c] == 0, "detail": f"{CODES[c][1]}：{counts[c]} 处"}
            for c in CODES}, probs


def query(nodes: list, edges: list) -> list:
    """自动执行 3 条查询；结果与证据一并返回。"""
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
                              "evidence": top["evidence"][:1]}]})
        nb = [e for e in edges if top["id"] in (e["source"], e["target"])]
        out.append({"q": "neighbors", "args": [top["id"]], "n": len(nb),
                    "rows": [{"edge": f"{e['source']} -{e['type']}-> {e['target']}",
                              "evidence": e["evidence"][:1]} for e in nb[:10]]})
    else:
        out.append({"q": "node", "args": [], "n": 0, "rows": []})
        out.append({"q": "neighbors", "args": [], "n": 0, "rows": []})

    sec = [n for n in nodes if (n.get("evidence") or [{}])[0].get("source_id") == "secondary"]
    out.append({"q": "by-source", "args": ["secondary"], "n": len(sec),
                "rows": [{"node": n["id"], "label": n["label"],
                          "paragraph_id": n["evidence"][0]["paragraph_id"]}
                         for n in sec[:10]]})
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
    if a.primary:
        srcs.append(("primary", Path(a.primary)))
    if a.secondary:
        srcs.append(("secondary", Path(a.secondary)))
    if a.input:
        d = Path(a.input)
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
            for sid, pat in (("primary", "*primary*material*.json"),
                             ("secondary", "*secondary*material*.json")):
                hit = sorted(d.rglob(pat))
                if hit:
                    srcs.append((sid, hit[0]))
            if not srcs:
                any_json = [p for p in sorted(d.rglob("*.json"))
                            if p.name != "case.json" and "manifest" not in p.name]
                if any_json:
                    srcs.append(("primary", any_json[0]))
        case_id = case_id or d.name
    case_id = case_id or "case"

    if not srcs:
        r = {"case_id": case_id, "status": "blocked",
             "error": "没有找到段落材料 JSON",
             "checks": {}, "gaps": ["缺 material JSON"], "deliverables": [],
             "evidence": {"unit": "source_id+paragraph_id", "items": []},
             "output_contract": {k: {"note": "未运行", "pointer": []} for k in contract},
             "output_contract_echo": {k: {"status": "partial", "source": "pointer"}
                                      for k in contract}}
        (out / "result.json").write_text(json.dumps(r, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked",
                          "result": str(out / "result.json")}, ensure_ascii=False))
        return 1

    gaps: list[str] = []
    nodes: list[dict] = []
    edges: list[dict] = []
    per_source: dict[str, int] = {}
    for sid, path in srcs:
        if not path.exists():
            gaps.append(f"{sid} 材料不存在：{path}")
            continue
        try:
            obj = json.loads(read(path))
        except Exception as exc:
            gaps.append(f"{sid} 材料不是合法 JSON：{exc}")
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
            continue
        n, e = extract(uniq, sid)
        nodes.extend(n)
        edges.extend(e)

    if len(per_source) < 2:
        gaps.append("只解析到一篇材料；题目要求两篇进同一图谱")

    # 去重节点（同 id 合并证据）
    merged: dict[str, dict] = {}
    for n in nodes:
        if n["id"] in merged:
            for ev in n["evidence"]:
                if ev not in merged[n["id"]]["evidence"]:
                    merged[n["id"]]["evidence"].append(ev)
        else:
            merged[n["id"]] = n
    nodes = list(merged.values())
    # 去重边
    em: dict[tuple, dict] = {}
    for e in edges:
        k = (e["source"], e["target"], e["type"])
        if k in em:
            for ev in e["evidence"]:
                if ev not in em[k]["evidence"]:
                    em[k]["evidence"].append(ev)
        else:
            em[k] = e
    edges = list(em.values())

    checks, probs = verify(nodes, edges)
    merges = build_merges(nodes)
    queries = query(nodes, edges)

    q_ok = sum(1 for q in queries if q["n"] > 0)
    if q_ok < 3:
        gaps.append(f"只有 {q_ok}/3 条查询有结果")
    for p in probs:
        gaps.append(f"{p[0]}：{p[1]}")

    status = "success" if (not probs and len(per_source) >= 2 and q_ok >= 3
                           and not gaps) else "partial"

    graph = {"case_id": case_id, "nodes": nodes, "edges": edges, "merges": merges,
             "sources": sorted(per_source), "paragraphs_per_source": per_source}
    gp = out / "graph.json"
    gp.write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
    tp = out / "triples.tsv"
    tp.write_text("source\trelation\ttarget\tsource_id\tparagraph_id\n" + "".join(
        f"{e['source']}\t{e['type']}\t{e['target']}\t"
        f"{e['evidence'][0]['source_id']}\t{e['evidence'][0]['paragraph_id']}\n"
        for e in edges if e.get("evidence")), encoding="utf-8")
    qp = out / "queries.json"
    qp.write_text(json.dumps({"case_id": case_id, "queries": queries},
                             ensure_ascii=False, indent=1), encoding="utf-8")
    vp = out / "verify.json"
    vp.write_text(json.dumps({"counts": {c: (0 if checks[c]["ok"] else 1) for c in CODES},
                              "problems": probs}, ensure_ascii=False, indent=1),
                  encoding="utf-8")

    arts = {"graph": str(gp), "triples": str(tp), "queries": str(qp), "verify": str(vp)}
    echo, echo_status = {}, {}
    for key in contract:
        srcs_flag = "explicit"
        if "解析结果" in key:
            echo[key] = graph["sources"] and {"nodes": len(nodes), "edges": len(edges),
                                              "graph": arts["graph"]}
            st = "full" if nodes else "partial"
        elif "证据与边界" in key:
            echo[key], st = {"evidence": "source_id+paragraph_id+quote", "gaps": gaps}, "full"
        elif key.startswith("运行状态"):
            echo[key], st = status, "full"
        elif key.startswith("目标产物"):
            echo[key], st = arts["graph"], "full"
        elif key.startswith("事实与来源映射"):
            echo[key], st = arts["triples"], "full"
        elif key.startswith("版本与边界"):
            echo[key], st = {"merges": len(merges), "gaps": gaps}, "full"
        else:
            echo[key] = {"note": "未识别的契约字段", "pointer": [arts["graph"]]}
            st, srcs_flag = "partial", "pointer"
        echo_status[key] = {"status": st, "source": srcs_flag}

    result = {
        "output_contract_echo": echo_status,
        "case_id": case_id,
        "skill_version": VERSION,
        "status": status,
        "sources": sorted(per_source),
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
        "deliverables": [{"kind": "graph", "path": arts["graph"]},
                         {"kind": "triples", "path": arts["triples"]},
                         {"kind": "queries", "path": arts["queries"]},
                         {"kind": "verify", "path": arts["verify"]}],
        "gaps": gaps,
        "output_contract": echo,

    }
    result["目标产物"] = "graph.json（节点/边 + 来源绑定）"
    result["事实与来源映射"] = "evidence 字段：source_id + paragraph_id + 原引"
    result["版本与边界"] = {"gaps": gaps, "merged_candidates": len(result.get("merges") or [])}
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
