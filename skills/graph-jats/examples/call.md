# 最小调用示例（示意输出，非正式案例结果）

输入：`examples/primary-material.json` 与 `examples/secondary-material.json`，各 2 段 JATS 段落，共有的实体是 `BERT` 与 `SST-2`。

## 1 调用

```bash
python run.py --primary examples/primary-material.json \
              --secondary examples/secondary-material.json \
              --work work --out output --case-id demo
```

## 2 示意输出

```json
{"ok": true, "status": "success", "case_id": "demo",
 "sources": ["primary", "secondary"], "nodes": 13, "edges": 15,
 "queries_ok": 3, "gaps": 0}
```

## 3 图谱概要（示例）

```text
nodes 13  edges 15  merges 2
method bert（2 条证据，跨两篇）    dataset sst-2（2 条证据，跨两篇）
method transformer  method contrastive learning  dataset wikitext
metric accuracy  metric f1  task classification  task retrieval
```

## 4 合并记录（示例）

```text
merge  ['method:bert@primary', 'method:bert@secondary']
       依据：标签归一化相同；出现于 2 篇
merge  ['dataset:sst-2@primary', 'dataset:sst-2@secondary']
       依据：标签归一化相同；出现于 2 篇
```

候选 id 按 `id@source` 展开，这样"为什么算同一条"可以直接核对到篇。

## 5 查询（示例）

```text
node        命中 1 条：method:bert，度数 4，首条证据 JATS-P-0007
neighbors   命中 4 条：bert 的四条相邻边，各带 paragraph_id
by-source   命中 4 条：仅由 secondary 支撑的节点
```

## 6 证据形状（示例）

```json
{"source_id": "primary", "paragraph_id": "JATS-P-0007",
 "quote": "We fine-tune BERT on SST-2 and report accuracy of 0.91 ..."}
```

没有页码——本流程拿不到 PDF 页序，把自建页序写成页码就是伪造证据。

## 7 说明

示例材料与结果是演示用，不是组织方固定案例，也不代表评分结果。
词表只覆盖常见方法/数据集/指标；换领域需改 `rules/criteria.md` 的词表。
