# 判据

词表、阈值与合并规则放这里；`SKILL.md` 正文只留指针。本文件与 `run.py` 的
`LEX`/`ALIAS`/`MERGE_KINDS`/`CONFLICT` 是同一份口径，改一处要同步另一处。

## 1 段落解析口径

- 容忍多种 JSON 形状：`{paragraphs:[...]}`、裸数组、嵌套 `{sections:{body:[...]}}`。
- id 键按优先级取：`paragraph_id` → `paragraphId` → `para_id` → `pid` → `id`。
- 文本键按优先级取：`text` → `content` → `body` → `paragraph` → `value`。
- 只收归一化后 ≥20 字的段落；更短的多半是标题或残片。
- 归一化只做：去 HTML 标签、全角空格与不间断空格转半角、压缩连续空白。不改字号标点。

## 2 抽取词表

只认显式表述。宁可少抽，也不要靠猜补。

| 类型 | 词表（小写匹配，按词边界） |
|-|-|
| method | bert, roberta, transformer, attention, lstm, cnn, gan, svm, random forest, xgboost, graph neural network, gnn, encoder-decoder, diffusion model, contrastive learning |
| dataset | sst-2, imdb, mnist, cifar-10, cifar-100, squad, wmt, conll, ms marco, wikitext, librispeech, chexpert |
| metric | accuracy, f1, auc, precision, recall, bleu, rouge, mse, rmse, perplexity, kappa |
| task | classification, regression, translation, summarization, question answering, segmentation, detection, retrieval |

边界正则：`(?<![a-z])词(?![a-z])`，避免 `gan` 命中 `organ`。

别名归一：`gnn` → `graph neural network`（`ALIAS` 表）。同一对象不因缩写/全称裂成两个节点。

## 3 关系规则

| 关系 | 何时连 | 置信度 |
|-|-|-|
| `uses_method` | 段里出现方法 → paper—method | high |
| `evaluates_on` | 段里出现数据集 → paper—dataset | high |
| `reports_metric` | 段里出现指标 → paper—metric | high |
| `reports_metric`（方法级） | 同一段里**同时**出现方法与指标 → method—metric | medium |
| `about` | 段里出现任务 → paper—task | high |

方法—指标边靠共现，只能说明"这段同时提到"，不代表因果或对比。故标 `medium`。

## 4 合并规则

跨篇候选 = 同类型 + 标签归一化（小写、去标点）相同 + 两篇都显式出现。

| 类型 | 决定 | 依据 / 冲突 |
|-|-|-|
| method、task | `merge` | 记归一化标签与两篇来源；写明"只合并名称，不断言同一模型权重、超参或实验条件" |
| dataset | `keep_separate` | conflict：两篇共用数据集名，但未报告划分/版本/许可，不得视为同一数据集实例 |
| metric | `keep_separate` | conflict：同名指标的计算口径与所用数据集可能不同，不得合并 |
| 只在单篇出现 | 不写记录 | 无跨篇候选 |

保留分离的节点 id 用 `类型:标签@source`，边端点同步改写；合并的节点收成
`类型:标签`，证据并列两篇。不自动做"语义相同"的判断：那需要外部词表，超出范围。

## 5 状态

| 状态 | 条件 |
|-|-|
| `success` | 两篇都解析出段落、G01–G05 全过、≥3 条查询有结果、`gaps` 为空 |
| `partial` | 上述任一不满足；`gaps` 逐条说明（含缺 manifest、缺 DOI） |
| `blocked` | 找不到任何 material JSON；仍写 `result.json` |
| `refused` | 材料不是 JSON |

## 6 查询

固定四条，结果与证据一起落盘：

| 查询 | 参数 | 说明 |
|-|-|-|
| `node` | 度数最高的方法标签 | 返回节点、度数、前两条证据 |
| `neighbors` | 该方法节点 id | 返回相邻边、置信度与各自证据 |
| `by-source` | `secondary` | 返回只由第二篇支撑的节点，每行带 paragraph_id 与 quote |
| `unresolved` | 无 | 返回 `keep_separate` 候选与 `medium` 置信边，即"无法确定的关系" |

四条都允许 `n = 0`，但至少 3 条要有结果，否则在 `gaps` 说明且不计入 `success`。

## 7 输入元数据

| 文件 | 用途 |
|-|-|
| `material.json` | 两篇正文段落的汇总索引：`{source_id, file, paragraphs}`，用于定位材料文件 |
| `corpus-manifest.json` | 每篇 `{source_id, doi, title, authors, license, sha256}`；根 `DOI/title/authors` 只归第一篇 |
| `case.json` | `case_id` 与 `output_contract` |

缺 `corpus-manifest.json` 时写 `gaps`，把每篇 DOI/标题/作者/许可置 `unknown`，不借用另一篇的元数据。
