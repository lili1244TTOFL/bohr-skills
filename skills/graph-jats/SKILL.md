---
name: graph-jats
description: 适用于需要把两篇论文的段落材料合成一张可查询知识图谱的场景：输入 primary/secondary 段落 JSON 与 corpus-manifest，输出带 source_id 与 paragraph_id 证据的 graph.json、三元组与实跑查询结果。不做 PDF 解析，不推断图表内数值。
---

# 从 JATS 段落材料构建知识图谱

## 1 用途

输入两篇论文的段落材料（JSON），输出可校验、可查询的图谱：

本 Skill 产出 `graph.json`、`triples.tsv`、`queries.json`、`verify.json` 与 `result.json`：

- `graph.json`：**成品**，节点/边/合并记录，每条带 `source_id` + `paragraph_id` + `quote`
- `triples.tsv`：三元组表，每条证据一行，含来源定位与原引
- `queries.json`：**实跑**的 4 条查询及结果与证据
- `verify.json`、`result.json`：校验结果与契约回显

不做：

- 不解析 PDF，不使用页码；位置一律用组织方给的 `paragraph_id`。
- 不推断未报告的数值，不合并不同论文的条件与样本。
- 不联网、不提交、不上传。

## 2 适用条件

| 适合 | 不适合 |
|-|-|
| 两篇论文的 JATS 段落 JSON，段落有 id | PDF 原件或扫描件 |
| 需要可回指段落的实体关系 | 需要消歧到外部词表 |
| 需要真跑的查询与证据 | 依赖图表像素的细粒度关系 |
| 带 `corpus-manifest.json`（DOI/作者/许可/哈希） | 缺 manifest 时只能记 `gaps`，不补造 |

## 3 调用

```bash
# 平台形态：/input 下的 material.json / corpus-manifest.json / *material*.json
python run.py --input /input --work /app/work --out /app/output

# 本地形态
python run.py --primary examples/primary-material.json \
              --secondary examples/secondary-material.json \
              --work work --out output --case-id demo
```

本 Skill 没有模型步骤：抽取与查询都是确定性的，一条命令跑完即终态。

## 4 流程

| 步 | 花模型 | 做什么 |
|-|-|-|
| 0 读输入 | 否 | `case.json` 的 `case_id`/`output_contract`；`material.json` 索引优先，其次按文件名找两篇材料；`corpus-manifest.json` 取每篇 DOI/标题/作者/许可 |
| 0.5 归一化 | 否 | 去 HTML 标签、全角与不间断空格，压缩空白 |
| 1 解段 | 否 | 容忍多种 JSON 形状，抽出 `paragraph_id` + 文本（≥20 字） |
| 2 抽取 | 否 | 词表匹配方法/数据集/指标/任务；别名归一（`gnn`→`graph neural network`）；同段共现连方法—指标边 |
| 3 合并 | 否 | 方法/任务标签跨篇合并；数据集/指标保留分离并记冲突，候选 id 按 `id@source` 展开 |
| 4 改写 | 否 | 合并的节点收成一个 id，保留分离的节点带 `@source` 后缀，边端点同步改写 |
| 5 校验 | 否 | G01–G05 |
| 6 查询 | 否 | `node` / `neighbors` / `by-source` / `unresolved` 四条，结果带证据 |
| 7 落盘 | 否 | `graph.json`、`triples.tsv`、`queries.json`、`verify.json`、`result.json` |

词表、合并策略与阈值在 `rules/criteria.md`。

## 5 判定口径

| 码 | 级别 | 检查 | 判定 |
|-|-|-|-|
| G01 | error | 节点 id 唯一且非空 | 重复或空即失败 |
| G02 | error | 边端点存在 | 悬空边即失败 |
| G03 | error | 节点与边至少一条证据，且三字段齐全 | 缺 `source_id`/`paragraph_id`/`quote` 即失败 |
| G04 | error | 类型在允许集合内 | 集合外即失败 |
| G05 | error | 标签非空且 ≤80 字 | 超长或空即失败 |

证据单位固定为 `source_id + paragraph_id + quote`。

- `source_id` 取 `primary` 或 `secondary`。
- `paragraph_id` 用材料里的 id，例如 `JATS-P-0007`；**不是**页码。
- `quote` 取关系词所在句，不跨句、不超过 160 字。
- 本流程没有 PDF，任何页码都不得出现在证据里。

节点 id 约定：

- 论文：`paper:primary` / `paper:secondary`，各自挂自己的 `meta`（DOI/标题/作者/许可）。
- 只在单篇出现的实体：`method:bert` 这种不带来源后缀的 id。
- 跨篇但**保留分离**的实体：`dataset:sst-2@primary` 与 `dataset:sst-2@secondary` 两个节点。
- 跨篇合并的实体：收成一个 `method:bert`，证据并列两篇。

## 6 输出契约

| 字段 | 含义 |
|-|-|
| `case_id` | 取自 `case.json`；缺失用输入目录名 |
| `status` | `success`（图建出来且 G01–G05 全过）/ `partial`（图不成立或有 G 码问题）/ `blocked`（找不到材料） |
| `skill_version` | 版本号 |
| `sources` | 解析出的材料来源列表 |
| `papers` | 两篇各自的 `{source_id, doi, title, authors, license}` |
| `stats` | 每源段数、节点数、边数、合并数、查询命中数、耗时 |
| `checks` | G01–G05 逐项 `ok` |
| `evidence` | 单位 `source_id+paragraph_id+quote`；每条节点给出定位 |
| `queries` | 四条查询名、参数、命中数与行（含证据） |
| `merges` | 每条含 `decision`（merge/keep_separate）、`basis`、`conflict` |
| `规则命中` | 实体/关系按类型计数、合并计数、查询命中数、读不出的材料清单 |
| `gaps` | 缺 manifest、缺材料、只解析到一篇、查询无结果、G 码问题（单源、缺 DOI 这类边界不再压状态） |
| `output_contract` | 三个平台字段装真实内容；未登记的键置 `null` 并记 `gaps` |
| `output_contract_echo` | 每字段 `full` / `partial` |

平台字段到产物的显式映射：

| platform 字段 | 回显内容 | 状态 |
|-|-|-|
| 阅读/解析结果 | `{papers, nodes, edges, node_types, merges}` | `full` |
| 证据与边界 | `{unit, checks, gaps, sample}` | `full` |
| 运行状态 | `{status, reason, queries_with_results, n_queries}` | `full` |

未登记的字段名才回落 `partial` + `null`。

## 7 限制

- 只认词表里的显式表述；隐式关系（如"优于 baseline"）抽不到，需人工补边。
- 方法—指标边靠同段共现，标 `medium` 置信；不等于因果或对比关系。
- 方法/任务按名称合并，**不代表**同一模型权重、超参或实验条件；数据集/指标一律保留分离，避免把"同名"写成"同一实例"。
- 不做实体消歧到外部词表；别名只覆盖 `rules/criteria.md` 的词表内缩写。
- 段落形状靠宽容解析，字段命名差异过大时会解析不出段落，此时 `gaps` 会说明。
- 只处理两篇；第三篇会被忽略并在 `gaps` 之外的情况需人工确认。

## 8 异常处理

| 情况 | 行为 | 状态 |
|-|-|-|
| 找不到任何 material JSON | 写 `result.json` + `missing_input.md`（列出找过的文件名与要补什么），退出 1 | `blocked` |
| 缺 `corpus-manifest.json` | `gaps` 记"DOI/作者/许可/哈希未核"，不借用另一篇的元数据 | 不压状态 |
| 只解析到一篇 | 照常出图，`gaps` 记"题目要求两篇；跨源一致率无法计算" | 不压状态 |
| 某篇 JSON 非法 | 跳过该篇，`gaps` 记原因，另写 `unreadable.md` 逐份说明 | 有图则 `success`，无图 `partial` |
| 抽出的段落为空 | `gaps` 记"未解析出段落（≥20 字）"，另写 `unreadable.md` | 有图则 `success`，无图 `partial` |
| 查询命中 < 3 条 | `gaps` 记 `n/3`，仍写出全部查询 | 不压状态 |
| G01–G05 有失败 | 逐条进 `gaps`，不静默 | `partial` |

## 9 自查与预算

```bash
python run.py --input examples --work work --out output
python -c "import json;r=json.load(open('output/result.json'));print(r['status'],r['stats'],r['gaps'])"
python -c "import json;q=json.load(open('output/queries.json'));print([(x['q'],x['n']) for x in q['queries']])"
```

1. `status` 为 `success` 或 `partial`：`success` = 节点与边都非空且 G01–G05 全过；`partial` 的 `gaps` 每条可执行
2. `queries.json` 里至少 3 条查询有结果；`node/neighbors/by-source/unresolved` 各自带证据
3. 抽查 3 条 evidence，`source_id` 与 `paragraph_id` 能在材料里找到
4. `merges` 每条都有 `decision` 与 `basis`；`keep_separate` 必须有非空 `conflict`
5. `triples.tsv` 每条证据一行；行数 = 各边证据数之和 + 1（表头）
6. 只给一篇材料跑一遍：仍应出图且 `success`，`gaps` 里写明"只有一篇"；材料读不出时应出现 `unreadable.md`

预算：零模型请求。两篇共 4 段时本地处理 < 0.1 s；100 段量级 < 1 s（本地 CPU）。

## 10 题目对齐

| 评分轴 | 机制 | 落在哪里 | 自查 |
|-|-|-|-|
| 抽得准 | 词表只认显式表述；G03 要求证据三字段齐全 | `graph.json` 的 `nodes[].evidence` | 第 9 节第 3 条 |
| 连得对 | G02 拦悬空边；G04 约束类型；关系类型写在边上 | `graph.json` 的 `edges` | 第 9 节第 1 条 |
| 对得齐 | 方法/任务合并给依据，数据集/指标保留分离并写冲突 | `graph.json` 的 `merges` | 第 9 节第 4 条 |
| 查得到、回得去 | 四条查询真跑，结果逐条带 `paragraph_id` 与 quote | `queries.json` | 第 9 节第 2 条 |

## 输入修订要点

- 两篇独立论文：`primary-material.json` 与 `secondary-material.json`；`corpus-manifest.json` 记 DOI、作者、来源哈希与许可证；`material.json` 是两篇正文段落的汇总索引。
- 根 `DOI/title/authors` 属第一篇，不得套到第二篇；代码只从 manifest 的对应条目取每篇元数据。
- 位置用组织方 JATS `paragraph_id`，不是 PDF 页码；不得把自建页面称作原 PDF 页。
- 同篇多格式不算跨篇证据；相同方法名不代表同一物理样品、模型实例或数据集。
- 只提供两份 JSON 时，缺 `corpus-manifest.json` 记入 `gaps`，不补造许可信息。
- 包内 `examples/` 是示例材料（DOI `10.1000/example.*`，非真实论文），真实论文输入由平台提供。
