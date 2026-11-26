---
name: graph-jats
description: 适用于需要把两篇论文的段落材料合成一张可查询知识图谱的场景：输入 primary/secondary 段落 JSON，输出带 source_id 与 paragraph_id 证据的 graph.json、三元组与实跑查询结果。不做 PDF 解析，不推断图表内数值。
---

# 从 JATS 段落材料构建知识图谱

## 1 用途

输入两篇论文的段落材料（JSON），输出可校验、可查询的图谱：

- `graph.json`：**成品**，节点/边/合并记录，每条带 `source_id` + `paragraph_id` + `quote`
- `triples.tsv`：三元组表，含来源定位两列
- `queries.json`：**实跑**的 3 条查询及结果与证据
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

## 3 调用

```bash
# 平台形态：/input 下的 *primary*material*.json 与 *secondary*material*.json
python run.py --input /input --work /app/work --out /app/output

# 本地形态
python run.py --primary examples/primary-material.json \
              --secondary examples/secondary-material.json \
              --work work --out output --case-id demo
```

本 Skill 没有模型步骤：抽取与查询都是确定性的，一条命令跑完即终态。

## 4 流程

| 步 | 脚本 | 花模型 | 做什么 |
|-|-|-|-|
| 0 读输入 | `run.py` | 否 | `case.json` 的 `case_id`/`output_contract`；按文件名找两篇材料 |
| 0.5 归一化 | `run.py` | 否 | 去 HTML 标签、全角与不间断空格，压缩空白 |
| 1 解段 | `run.py` | 否 | 容忍多种 JSON 形状，抽出 `paragraph_id` + 文本（≥20 字） |
| 2 抽取 | `run.py` | 否 | 词表匹配方法/数据集/指标/任务；同段共现连方法—指标边 |
| 3 合并 | `run.py` | 否 | 跨篇同名归一化合并，按 source 展开候选 id |
| 4 校验 | `run.py` | 否 | G01–G05 |
| 5 查询 | `run.py` | 否 | node / neighbors / by-source 三条，结果带证据 |
| 6 落盘 | `run.py` | 否 | `graph.json`、`triples.tsv`、`queries.json`、`verify.json`、`result.json` |

词表与阈值在 `rules/criteria.md`。

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
- 本流程没有 PDF，任何页码都不得出现在证据里。

## 6 输出契约

| 字段 | 含义 |
|-|-|
| `case_id` | 取自 `case.json`；缺失用输入目录名 |
| `status` | `success` / `partial` / `blocked` / `refused` |
| `skill_version` | 版本号 |
| `sources` | 解析出的材料来源列表 |
| `stats` | 每源段数、节点数、边数、合并数、查询命中数、耗时 |
| `checks` | G01–G05 逐项 `ok` |
| `evidence` | 单位 `source_id+paragraph_id+quote`；每条节点给出定位 |
| `queries` | 查询名、参数、命中数与行（含证据） |
| `merges` | 合并/不合并的决定与依据 |
| `gaps` | 缺材料、只解析到一篇、查询无结果、G 码问题 |
| `output_contract` | 按平台字段名精确匹配，不做关键词猜测 |
| `output_contract_echo` | 每字段 `full` / `partial` / `pointer` |

平台字段到产物的显式映射：

| platform 字段 | 回显内容 | 状态 |
|-|-|-|
| 阅读/解析结果 | `graph.json` 与节点/边计数 | `full` |
| 证据与边界 | 证据三字段说明 + `gaps` | `full` |
| 运行状态 | `status` | `full` |

未登记的字段名才回落 `pointer` + `partial`。

## 7 限制

- 只认词表里的显式表述；隐式关系（如"优于 baseline"）抽不到，需人工补边。
- 方法—指标边靠同段共现，标 `medium` 置信；不等于因果或对比关系。
- 不做实体消歧到外部词表；同名不同实例靠 `merges` 的 `keep_separate` 记录。
- 段落形状靠宽容解析，字段命名差异过大时会解析不出段落，此时 `gaps` 会说明。
- 只处理两篇；第三篇会被忽略并在 `gaps` 之外的情况需人工确认。

## 8 异常处理

| 情况 | 行为 | 状态 |
|-|-|-|
| 找不到任何 material JSON | 写 `status=blocked` 的 `result.json` 并退出 1 | `blocked` |
| 只解析到一篇 | 照常出图，`gaps` 记"题目要求两篇" | `partial` |
| 某篇 JSON 非法 | 跳过该篇，`gaps` 记原因 | `partial` |
| 抽出的段落为空 | `gaps` 记"未解析出段落（≥20 字）" | `partial` |
| 查询命中 < 3 条 | `gaps` 记 `n/3`，仍写出全部查询 | `partial` |
| G01–G05 有失败 | 逐条进 `gaps`，不静默 | `partial` |

## 9 自查与预算

```bash
python run.py --primary examples/primary-material.json --secondary examples/secondary-material.json --work work --out output --case-id demo
python -c "import json;r=json.load(open('output/result.json'));print(r['status'],r['stats'],r['gaps'])"
python -c "import json;q=json.load(open('output/queries.json'));print([(x['q'],x['n']) for x in q['queries']])"
```

1. `status` 为 `success` 或 `partial`；`partial` 的 `gaps` 每条可执行
2. `queries.json` 里至少 3 条查询，且每条 `n > 0` 或在 `gaps` 说明原因
3. 抽查 3 条 evidence，`source_id` 与 `paragraph_id` 能在材料里找到
4. `merges` 每条都有 `decision` 与 `basis`；跨篇候选按 source 展开
5. `triples.tsv` 行数 = 边数 + 1（表头）

预算：零模型请求。两篇共 4 段时本地处理 < 0.1 s；100 段量级 < 1 s（本地 CPU）。

## 10 题目对齐

| 评分轴 | 机制 | 落在哪里 | 自查 |
|-|-|-|-|
| 抽得准 | 词表只认显式表述；G03 要求证据三字段齐全 | `graph.json` 的 `nodes[].evidence` | 第 9 节第 3 条 |
| 连得对 | G02 拦悬空边；G04 约束类型；关系类型写在边上 | `graph.json` 的 `edges` | 第 9 节第 1 条 |
| 对得齐 | `merges` 记合并依据与不得合并的冲突，候选按 source 展开 | `graph.json` 的 `merges` | 第 9 节第 4 条 |
| 查得到、回得去 | 三条查询真跑，结果逐条带 `paragraph_id` 证据 | `queries.json` | 第 9 节第 2 条 |

## 题目对齐（阅读15）

| 关注点 | 机制 | 落在哪 |
|-|-|-|
| 抽得准 | 节点只取材料里真实出现的实体，附 `source_id` 与 `paragraph_id` | `graph.json` 的 `evidence` |
| 连得对 | 边只建原文有依据的关系，带原引短句 | `graph.json` 的 `edges[].quote` |
| 对得齐 | 跨篇候选合并需给依据；冲突与不同对象列 `merges` 与冲突清单 | `result.json` 的 `merges` |
| 查得到、回得去 | 至少 3 个实际执行查询，结果带支撑证据 | `queries.json` |

## 输入修订要点

- 两篇独立论文：`primary-material.json` 与 `secondary-material.json`；`corpus-manifest.json` 记 DOI、作者、来源哈希与许可证。
- 根 `DOI/title/authors` 属第一篇，不得套到第二篇。
- 位置用组织方 JATS `paragraph_id`，不是 PDF 页码；不得把自建页面称作原 PDF 页。
- 同篇多格式不算跨篇证据；相同方法名不代表同一物理样品、模型实例或数据集。
- 只提供两份 JSON 时，缺 `corpus-manifest.json` 记入 `gaps`，不补造许可信息。
