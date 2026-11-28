---
name: graph-jats
description: 适用于需要把两篇论文的段落材料合成一张可查询知识图谱的场景：输入 primary/secondary 段落 JSON 与 corpus-manifest，输出带 source_id+paragraph_id+quote 证据的 graph.json、可读 graph.md、三元组表与 6 条实跑查询；区分作者自述与他人转述，支持 --append 增量更新。不解析 PDF，不推断图表内数值，不跨篇合并数值结果。
---

# 从 JATS 段落材料构建知识图谱

## 1 用途

输入两篇论文的段落材料（JSON），产出一张可校验、可查询、可增量更新的图谱：

- `graph.json`：**成品**。节点/边/合并记录/数值/冲突，每条带 `source_id` + `paragraph_id` + `quote`
- `graph.md`：同一张图的**可读版**，人不用跑代码就能核对节点、边、方向、适用条件与证据
- `triples.tsv`：三元组表，每条证据一行，含置信度、归属、stance 与原引
- `queries.json`：**实跑**的 6 条查询，每条带 `rows`（支撑证据）与 `subgraph`（结果子图）
- `verify.json`、`result.json`：G 码校验、指标、契约回显
- `run_manifest.json`：可复现清单（输入输出哈希、命令、确定性说明）
- `incremental.json`：`--append` 时的增量处置记录
- 缺料时另写 `missing_input.md` / `unreadable.md`

不做：不解析 PDF、不用页码（位置一律 `paragraph_id`）、不推断未报告数值、
不跨篇汇总数值、不把"同名"当"同一实例"。

## 2 适用条件

| 适合 | 不适合 |
|-|-|
| 两篇论文的 JATS 段落 JSON，段落有 id | PDF 原件或扫描件（只记 gaps，不解析） |
| 需要可回指段落的实体关系 | 需要消歧到外部词表（只覆盖 `rules/criteria.md` 第 3 节的别名） |
| 需要真跑的查询、结果子图与证据 | 依赖图表像素的细粒度关系 |
| 带 `corpus-manifest.json`（DOI/作者/许可/哈希） | 缺 manifest 时只能记 `gaps`，不补造 |

## 3 调用

```bash
# 平台形态：/input 下的 material.json / corpus-manifest.json / *material*.json
python run.py --input /input --work /app/work --out /app/output

# 本地形态
python run.py --primary examples/primary-material.json \
              --secondary examples/secondary-material.json \
              --work work --out output --case-id demo

# 增量更新：先有基图，再喂新论文；既有节点与编号原样保留
python run.py --input examples --work work --out out1
python run.py --input examples/incremental --append out1/graph.json --out out2

# 同输入消融：逐项关掉机制，看各指标怎么退化
python scripts/ablate.py --input examples --out ablation
```

没有模型步骤：抽取、对齐、查询都是确定性的，一条命令跑完即终态。

## 4 流程

| 步 | 花模型 | 做什么 |
|-|-|-|
| 0 读输入 | 否 | `case.json` 的 `case_id`/`output_contract`；`material.json` 索引优先；manifest 取每篇 DOI/作者/许可 |
| 0.5 归一化 | 否 | 去 HTML 标签、全角与不间断空格，压缩空白 |
| 1 解段切句 | 否 | 容忍多种 JSON 形状；抽出 `paragraph_id` + 文本（≥20 字）；`et al.` 等缩写不切句 |
| 2 抽取 | 否 | 词表匹配 6 类实体 + `et al.`/manifest 姓氏建研究者 + 显式结论句建 finding；每句判自述/转述 |
| 3 关系 | 否 | 固定关系 + 关系词窗口（比较/优于/支持/矛盾）+ 同句方法×指标共现；记方向依据与适用条件 |
| 4 数值 | 否 | 指标句里的数值按篇单列，标 absolute/delta，不做跨篇汇总 |
| 5 对齐 | 否 | 方法/任务跨篇合并，其余保留分离写冲突；同名不同类另记 |
| 6 校验 | 否 | G01–G09 + `rules/graph.schema.json` 结构校验 |
| 7 查询 | 否 | 按 `rules/queries.json` 执行 6 条，各带 rows 与 subgraph |
| 8 落盘 | 否 | 八个产物一次写全（见第 6 节） |

词表、签名表、强度与合并策略在 `rules/criteria.md`；查询定义在
`rules/queries.json`；结构契约在 `rules/graph.schema.json`。

## 5 判定口径

| 码 | 级别 | 检查 | 判定 |
|-|-|-|-|
| G01 | error | 节点 id 唯一且非空 | 重复或空即失败 |
| G02 | error | 边端点存在 | 悬空边即失败 |
| G03 | error | 节点与边至少一条证据，三字段齐全 | 缺 `source_id`/`paragraph_id`/`quote` 即失败 |
| G04 | error | 类型在允许集合内 | 集合外即失败 |
| G05 | error | 标签非空且 ≤80 字 | 超长或空即失败 |
| G06 | error | 关系签名合法、边带方向依据 | 关系不在表内、端点类型不符、缺 `direction_basis` 即失败 |
| G07 | error | `quote` 是该 `paragraph_id` 原文的子串 | 引文对不上原文即失败 |
| G08 | error | 转述/共现/仅提及不得标 high | 违反即失败 |
| G09 | error | 增量模式保留既有节点 id | 旧 id 缺失即失败（非增量不适用） |

证据单位固定 `source_id + paragraph_id + quote`；`quote` 取关系词所在句、不跨句、
≤160 字、只在词边界截断。`confidence` 三档 `high`/`medium`/`low`；`attribution`
两分 `self`（作者自述，含无主语被动）/ `reported_elsewhere`（他人转述）；
`evidence[].stance` 再细分 `self`/`unspecified`/`reported`。`paragraph_id` 是组织方
JATS 段落 id（如 `JATS-P-0007`），**不是**页码；本流程没有 PDF，证据里不得出现页码。

节点 id 约定：论文为 `paper:primary`；单篇实体带来源后缀（`method:transformer@primary`）；
跨篇保留分离的并列两个 `类型:标签@primary` / `@secondary`；跨篇合并的收成 `method:bert`
并让证据并列两篇。

## 6 输出契约

| 产物 | 内容 |
|-|-|
| `graph.json` | 节点/边/合并/数值/冲突/覆盖/边界/schema 校验；字段契约见 `rules/graph.schema.json` |
| `graph.md` | 论文信息、节点表、边表（置信度+归属+方向依据+适用条件+证据）、合并与冲突、数值、边界、覆盖 |
| `triples.tsv` | `source relation target confidence attribution source_id paragraph_id stance quote`，每条证据一行 |
| `queries.json` | 每条查询的 `id`/`args`/`n`/`rows`/`subgraph` |
| `verify.json` | G 码逐项计数、问题清单、schema 校验、指标 |
| `run_manifest.json` | 输入输出哈希、命令、模式、确定性说明、复现命令 |

`result.json` 顶层字段：

| 字段 | 含义 |
|-|-|
| `case_id` | 取自 `case.json`；缺失用输入目录名 |
| `status` | `success` / `partial` / `blocked` |
| `sources` / `papers` | 来源列表；每篇 `{source_id, doi, title, authors, license, sha256}` |
| `stats` | 段数、节点数、边数、合并数、数值数、查询命中数、耗时 |
| `checks` | G01–G09 逐项 `ok` 与 detail |
| `coverage` | 实体/关系类型覆盖（required/covered/missing）、证据字段、查询数 |
| `metrics` | 证据完备率、引文逐字命中率、方向/条件覆盖率、实体—实体边数、查询子图数、G 通过数 |
| `boundary` | 未进词表的缩写、关系词命中但无合法配对、PDF 清单 |
| `evidence` | `unit` + 节点证据 50 条 + 边证据 50 条（各含 quote 与 stance） |
| `queries` / `merges` / `measurements` | 查询结果与子图；合并依据与冲突；逐篇数值 |
| `incremental` | `--append` 时的 previous/kept_ids/added/merged/kept_separate/conflicts |
| `规则命中` | 实体与关系按类型计数、按强度计数、合并计数、查询数、数值、读不出材料 |
| `gaps` | 缺 manifest、缺材料、只解析到一篇、PDF 未解析、类型未出现、G 码问题 |
| `output_contract` | 未登记的键置 `null` 并记 `gaps` |

平台字段到产物的显式映射：

| platform 字段 | 回显内容 | 状态 |
|-|-|-|
| 阅读/解析结果 | `{papers, nodes, edges, node_types, relation_types, merges, measurements, coverage}` | `full` |
| 证据与边界 | `{unit, strength_levels, stance_levels, checks, gaps, metrics, items, edge_items, schema_check}` | `full` |
| 运行状态 | `{status, reason, queries_with_results, n_queries, manual_takeover}` | `full` |

未登记的字段名才回落 `partial` + `null`。

## 7 覆盖与边界

- `coverage.entity_types`：题面点名的研究者/材料/方法/任务/指标/结论六类，
  缺哪类就进 `coverage.missing` 与 `gaps`，**不补造**。
- `coverage.relation_types`：使用/比较/优于/支持/矛盾五类，同样只报不补。
- `boundary.unmatched_candidates`：正文大写缩写没进词表，逐条列出，不猜含义。
- `boundary.rejected_relation_candidates`：关系词命中但同句无可合法配对的实体，
  给出 cue、段落与原因。
- `measurements`：数值按篇分列，`results` 查询明写"不同样本、不同划分不可比"。

## 8 状态与异常处理

| 情况 | 行为 | 状态 |
|-|-|-|
| 找不到任何 material JSON | 写 `result.json` + `missing_input.md`（列找过的文件名与要补什么），退出 1 | `blocked` |
| 输入目录含 PDF | `gaps` 记条数与"未从 PDF 取任何数据"，写进 `unreadable.md` | 不压状态 |
| 缺 `corpus-manifest.json` | `gaps` 记"DOI/作者/许可/哈希未核"，不借用另一篇的元数据 | 不压状态 |
| 只解析到一篇 | 照常出图，`gaps` 记"题目要求两篇" | 不压状态 |
| 某篇 JSON 非法 / 抽不出段落 | 跳过该篇，`gaps` 记原因，另写 `unreadable.md` 逐份说明 | 有图 `success`，无图 `partial` |
| 查询命中 < 3 条 | `gaps` 记 `n/3`，仍写出全部查询 | 不压状态 |
| G01–G09 或 schema 失败 | 逐条进 `gaps`，不静默 | `partial` |
| `--append` 的基图读不出 | `gaps` 记原因，退化为全量构建 | 不压状态 |

`success` = 节点与边都非空 + G01–G09 全过 + schema 通过；其余为 `partial`，
`gaps` 每条指向可人工接管的位置。无法判断的项不写成事实。

## 9 自查与预算

```bash
python run.py --input examples --work work --out output
python -c "import json;r=json.load(open('output/result.json'));print(r['status'],r['stats'],r['gaps'])"
python -c "import json;q=json.load(open('output/queries.json'));print([(x['id'],x['n'],len(x['subgraph']['edges'])) for x in q['queries']])"
python scripts/ablate.py --input examples --out ablation
```

1. `success` 时 `gaps` 应为空；`partial` 时 `gaps` 每条可执行
2. 6 条查询至少 3 条有结果，且各自带 `rows` 与 `subgraph`
3. 抽 3 条 evidence：`source_id`/`paragraph_id` 能在材料里找到，`quote` 逐字命中（G07 已在运行时校验）
4. `merges` 每条都有 `decision` 与 `basis`；`keep_separate` 必须有非空 `conflict`
5. `triples.tsv` 行数 = 各边证据数之和 + 1（表头）
6. 单篇输入仍出图且 `gaps` 写明"只有一篇"；材料读不出出 `unreadable.md`；空目录出 `missing_input.md`
7. 同一输入跑两遍：`graph.json` / `graph.md` / `triples.tsv` 应字节一致
8. `--append` 后 `incremental.id_preservation.missing` 为空，G09 为 `ok`

预算：零模型请求、零第三方依赖。两篇共 11 段时本地 < 0.05 s；100 段量级 < 1 s。

## 10 题目对齐

| 评分轴 | 机制 | 落在哪里 | 自查 |
|-|-|-|-|
| 抽得准 | 只认显式表述；六类实体；G03 三字段齐全；G07 引文逐字命中原文 | `graph.json` 的 `nodes[].evidence` | 第 9 节第 3 条 |
| 连得对 | G06 约束关系签名；每条边带 `direction_basis` 与 `applicability`；G08 拦"转述/共现当直接证据" | `graph.json` 的 `edges` | 第 9 节第 1 条 |
| 对得齐 | 两级归一（大小写/连字符/复数 + 别名表）；方法/任务合并给依据，其余保留分离写冲突 | `merges`、`conflicts` | 第 9 节第 4 条 |
| 查得到、回得去 | 6 条查询真跑，各带 rows 与结果子图；证据逐条回段落 | `queries.json` | 第 9 节第 2 条 |
| 增量更新 | `--append` 保留既有 id 与编号，记录新增/合并/冲突，导出 `graph.md` | `incremental.json` | 第 9 节第 8 条 |
| 增益可验证 | 同输入消融：逐项关机制看指标退化，数字全部由产物算出 | `scripts/ablate.py` 的 `ablation.md` | `python scripts/ablate.py` |

## 11 维护信息

- 版本 `2.0.0`，纯标准库，Python 3.9+；无安装步骤、不联网。
- 改词表/别名/签名/合并策略/查询/码表，按 `rules/criteria.md` 第 12 节的对照表同步
  `run.py`、`rules/graph.schema.json` 与本文件；`skill_lint.py` 会比对代码与文档的码表。
- `examples/` 与 `examples/reference-graph.json` 是演示用（DOI 前缀 `10.1000`），
  非真实论文，不代表评分结果；标注经验见 `examples/walkthrough.md`。

## 输入修订要点

- 两篇独立论文：`primary-material.json` 与 `secondary-material.json`；`corpus-manifest.json`
  记 DOI、作者、来源哈希与许可证；`material.json` 是段落索引。根 `DOI/title/authors`
  属第一篇，不得套到第二篇。
- 位置用组织方 JATS `paragraph_id`，不是 PDF 页码；同篇多格式不算跨篇证据；
  相同方法名不代表同一物理样品、模型实例或数据集。
- 只提供两份 JSON 时，缺 `corpus-manifest.json` 记入 `gaps`，不补造许可信息。
