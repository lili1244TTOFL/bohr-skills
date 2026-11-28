# 最小调用示例（示意输出，非正式案例结果）

输入：`examples/` 下的两篇示例材料、`material.json`（汇总索引）、`corpus-manifest.json`
（两篇各自的 DOI/作者/许可/哈希）与 `case.json`。示例材料是演示用（DOI `10.1000/example.*`），
不是真实论文。

## 1 调用

```bash
python run.py --input examples --work work --out output
```

## 2 示意输出（本次实跑）

```json
{"ok": true, "status": "success", "case_id": "graph-demo",
 "sources": ["primary", "secondary"], "nodes": 14, "edges": 15,
 "queries_ok": 4, "gaps": 0}
```

## 3 图谱概要

```text
nodes 14  edges 15  merges 2（merge 1 + keep_separate 1）
method bert（两篇证据并列，degree 4）  method transformer  method contrastive learning
method graph neural network（仅 secondary）
dataset sst-2@primary 与 sst-2@secondary（保留分离，未合并）
dataset wikitext  dataset ms-marco  metric accuracy@primary  metric f1@secondary
task classification@primary  task retrieval@secondary
```

## 4 合并记录

```text
merge          ['method:bert@primary', 'method:bert@secondary']
               依据：类型 method，标签归一化相同；两篇都显式出现；只合并名称，
               不断言同一模型权重、超参或实验条件
keep_separate  ['dataset:sst-2@primary', 'dataset:sst-2@secondary']
               冲突：两篇共用数据集名，但未报告划分/版本/许可，不得视为同一数据集实例
```

候选 id 按 `id@source` 展开，这样"为什么算同一条 / 为什么不算"可以直接核对到篇。

## 5 查询（四条都实跑）

```text
node        命中 1 条：method:bert，度数 4，证据并列 primary JATS-P-0007 与 secondary JATS-P-0012
neighbors   命中 4 条：两条 uses_method + 两条 method→metric，各带 quote
by-source   命中 5 条：仅由 secondary 支撑的实体，每行带 paragraph_id 与 quote
unresolved  命中 3 条：1 条 sst-2 keep_separate 冲突 + 2 条 medium 置信共现边
```

## 6 证据形状

```json
{"source_id": "primary", "paragraph_id": "JATS-P-0007",
 "quote": "We fine-tune BERT on SST-2 and report accuracy of 0.91 in our classification experiments."}
```

没有页码——本流程拿不到 PDF 页序，把自建页序写成页码就是伪造证据。

## 7 材料不全时的形态

只给 `primary-material.json`（无 secondary、无 manifest）时仍出图，`status` 仍是 `success`：

```json
{"ok": true, "status": "success", "sources": ["primary"], "nodes": 8, "edges": 8, "queries_ok": 3, "gaps": 3}
```

`gaps` 会写：只解析到一篇材料（跨源一致率无法计算）、缺 `corpus-manifest.json`、`primary` 缺 DOI——这些是材料边界，不下压状态。

材料 JSON 读不出段落（字段名不认识、正文为空、JSON 非法）时，另写 `unreadable.md` 逐份说明原因，图谱只包含读得出的部分，不补造实体或关系；此时若无节点/边则 `status=partial`。

## 8 说明

示例材料与结果是演示用，不是组织方固定案例，也不代表评分结果。
词表、别名与合并策略在 `rules/criteria.md`；换领域要改词表并同步 `run.py`。
