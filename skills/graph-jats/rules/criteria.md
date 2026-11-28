# 判据

`run.py` 是唯一真源，本文件说明它的口径。词表、别名、合并策略、签名表、状态条件
任何一处改动都要同时改 `run.py` 的常量与本节表格；`run.py` 里 `LEX`/`ALIAS`/
`MERGE_KINDS`/`KEEP_CONFLICT`/`SIG`/`CODES` 是这些表的代码形态。

## 1 段落解析口径

- 容忍多种 JSON 形状：`{paragraphs:[...]}`、裸数组、嵌套 `{sections:{body:[...]}}`。
- id 键按优先级取：`paragraph_id` → `paragraphId` → `para_id` → `pid` → `id`。
- 文本键按优先级取：`text` → `content` → `body` → `paragraph` → `value`。
- 只收归一化后 ≥20 字的段落；更短的多半是标题或残片。
- 归一化只做：去 HTML 标签、全角空格与不间断空格转半角、压缩连续空白。不改标点。
- 切句：`。！？!?` 与句点；**句点紧跟 `et al / e.g / i.e / vs / Fig / Eq / Tab / Sec`
  这类缩写时不切句**。切错会让"Devlin et al. reported that …"断成两句，
  后半句丢掉转述线索，转述被误标成作者自述。
- 位置一律是组织方 JATS `paragraph_id`，**不是 PDF 页码**；输入目录里的 PDF 只记
  `gaps`（不解析），不从 PDF 取任何数据。

## 2 实体类型

| 类型 | 怎么建 | 词表/线索 |
|-|-|-|
| paper | 每个 source 一个 | 无（由 source_id 决定） |
| method | 词表命中 | bert, roberta, electra, t5, gpt, lstm, gru, cnn, rnn, transformer, attention, gan, svm, crf, random forest, xgboost, graph neural network, gnn, encoder-decoder, diffusion model, contrastive learning, resnet |
| dataset | 词表命中 | sst-2, sst-5, imdb, mnist, cifar-10, cifar-100, squad, wmt, conll, conll-2003, ms marco, wikitext, librispeech, chexpert, glue, snli, mnli, qqp, stsb, paws, atis, reuters |
| metric | 词表命中 | accuracy, f1, f1-score, auc, precision, recall, bleu, rouge, mse, mae, rmse, perplexity, kappa, mean average precision, mrr, ndcg |
| task | 词表命中 | classification, regression, translation, summarization, question answering, segmentation, detection, retrieval, named entity recognition, parsing, ranking |
| material | 词表命中 | corpus, annotated corpus, text corpus, participants, patients, specimen(s), tissue, cohort |
| researcher | `X et al.` 模式；或 manifest 作者姓氏（长度 ≥4、不在常见词表）**且该名字真的出现在正文里** | `ETAL` 正则 + `author_surnames()` |
| finding | 显式结论句：we find/show/demonstrate/observe that、results show that、findings suggest that、this suggests that、we conclude that；标签直接截原句（≤80 字） | `FINDING_CUES` |

抽取只认显式表述，宁可少抽不错抽。同句内取**最长匹配**：`annotated corpus` 命中后
不再另建 `corpus` 节点，`conll-2003` 不再另建 `conll`。

## 3 别名归一

两级：

1. `norm_key()`：小写、连字符/下划线/全角破折号→空格、去标点、末尾复数归一
   （长度 >4 且不是 `ss` 结尾）。这一级处理大小写、连字符、单复数。
2. `ALIAS`：缩写/简称→规范写法。`gnn`→`graph neural network`、`sst2`→`sst-2`、
   `f1-score`→`f1`、`ms-marco`→`ms marco`、`conll2003`→`conll-2003`、
   `bi-lstm`→`lstm`、`cnns`→`cnn`、`rnns`→`rnn`、`qa`→`question answering`。
   **ALIAS 的键会自动登记进词表**（`run.py` 顶部那段循环），避免"别名表里有条
   永远不触发的死规则"。

归一化只回答"是不是同一个写法"，不回答"是不是同一个实例"。后者见第 6 节。

## 4 关系类型与签名（G06）

方向由签名表 `SIG` 约束：`(源类型, 目标类型)` 不在表内就不许建这条边。

| 关系 | 允许的端点类型 | 何时建 | 方向依据 |
|-|-|-|-|
| `uses_method` | paper/researcher → method | 句中命中方法 | 陈述主体 → 被陈述对象 |
| `evaluates_on` | paper/researcher → dataset | 句中命中数据集 | 同上 |
| `reports_metric` | paper/researcher → metric；method → metric | 句中命中指标 | 同上；方法→指标另一条见共现 |
| `about` | paper/researcher → task | 句中命中任务 | 同上 |
| `mentions_material` | paper/researcher → material | 句中命中材料 | 同上 |
| `cites_researcher` | paper → researcher | 转述句里出现 `X et al.` | 本文（引用者）→ 被引用研究者 |
| `authored_by` | researcher → paper | manifest 作者名出现在自己论文正文里 | 研究者 → 该研究者的论文 |
| `reports_finding` | paper → finding | 显式结论句 | 论文 → 本文结论 |
| `compared_with` | method/dataset/metric 同类 → 同类 | 有 compare/versus/vs 且同句有两个同类实体 | 原文语序（比较本身无方向） |
| `outperforms` | method → method | 有 outperform/better than/higher than/surpass 等 | 比较词前实体 → 比较词后实体 |
| `supports` | method↔method / method↔finding | 有 confirm/support/consistent with | 原文语序 |
| `contradicts` | method → metric / method↔method / finding↔method | 有 however/in contrast/does not/fail 等 | 原文语序 |

关系词命中但同句没有可配对的合法实体时**不建边**，记进
`boundary.rejected_relation_candidates`（带 cue、段落、原因），不静默丢弃。

## 5 证据强度与归属

证据单位固定 `source_id + paragraph_id + quote`；`quote` 取关系词所在句，
不跨句、≤160 字、只在词边界截断（保证是原文子串，G07 校验）。

- `evidence[].stance`：`self`（第一人称）/ `unspecified`（无主语被动，按自述处理）/
  `reported`（et al.、previous work、reported by 等转述线索）。
- `edge.attribution`：两分。`self` = 作者自述（含无主语被动）；`reported_elsewhere` =
  他人转述。转述句若指认到具体研究者，边的**源**换成 `researcher:` 节点，
  `reported_by` 记该节点 id。
- `edge.confidence`：三档。
  - `high`：显式关系词 + 自述（`uses_method` 需 use/fine-tune/train/evaluat 等，
    `evaluates_on` 需 evaluat/experiment/benchmark/test/measure/train 等，
    `reports_metric` 需同句出现数值）。
  - `medium`：仅提及（mention）或转述（reported_elsewhere）。
  - `low`：同句方法×指标共现。
- **G08**：`attribution != self` 的边标 high → 失败；`basis` 为 `cooccurrence`/
  `mention` 的边标 high → 失败；`confidence` 不在三档内 → 失败。

## 6 跨篇合并规则

跨篇候选 = 同类型 + `norm_key` 相同 + 两篇都显式出现。

| 类型 | 决定 | 依据 / 冲突 |
|-|-|-|
| method、task | `merge` | 记规范化键、两篇来源、原文字面写法；写明"只合并名称，不断言同一模型权重、超参或实验条件" |
| dataset | `keep_separate` | 两篇共用数据集名，但未报告划分/版本/许可，不得视为同一数据集实例 |
| metric | `keep_separate` | 同名指标的计算口径与所用数据集可能不同，不得合并 |
| material | `keep_separate` | 通用语料/样本名称不代表两篇用了同一物理样品、同一批受试或同一采集条件 |
| researcher | `keep_separate` | 同名姓氏可能对应不同研究者，未报告机构与全名时不得合并 |
| finding | `keep_separate` | 两篇结论各自绑定自己的样本与数值，合并等于把不同论文的结果合成一个结论 |

合并的节点收成 `类型:标签`，证据并列两篇；保留分离的用 `类型:标签@source`。
同名不同类另记 `conflicts.homonyms`（按类型分节点，不做跨类型合并）。
**不自动做"语义相同"的判断**：那需要外部词表，超出本 Skill 范围。

## 7 数值

`measurements` 每条 = `{metric, value, unit, kind, source_id, paragraph_id, stance, quote}`。

- 只在指标句里取，取与该指标最近的一个数（±40/60 字窗口）。
- `kind`：`delta`（前有 by/gain/improve/increase/higher/… 且带 points 或 %) / `absolute`。
- **不做跨篇汇总、平均、相加**。`results` 查询按指标名分组、逐篇分列，
  并在行内写明"不同样本、不同划分不可比"。两篇的条件与数值不得合成一个结论。

## 8 查询

定义在 `rules/queries.json`，`run.py` 逐条执行，改模板不用改代码。每条返回
`rows`（支撑证据）与 `subgraph`（结果子图：节点 id 列表 + 边列表）。

| 查询 | 返回 |
|-|-|
| `node` | 度数最高的非论文节点、度数、前两条证据 |
| `neighbors` | 该节点的相邻边（类型、置信度、归属、适用条件、证据）+ 深度 1 子图 |
| `by-source` | 只由第二个来源支撑的实体，每行带 paragraph_id 与 quote |
| `cross-paper` | 每条跨篇候选的 decision、依据、冲突、各自证据 |
| `results` | 指标名 → 各篇各自的数值、段落、短引 |
| `unresolved` | `keep_separate` 冲突 + 非 high 置信边 |

允许某条 `n = 0`，但至少 3 条要有结果，否则记 `gaps`。

## 9 增量更新（`--append`）

- 只处理基图里没有的 source；已在基图里的 source 跳过并记 `gaps`。
- 既有节点 id、节点编号、边原样保留（G09 校验旧 id 集合 ⊆ 新 id 集合）。
- 新节点与旧节点同类型同 `norm_key` 时：method/task → 合并进旧 id
  （`action: merge_into_existing`）；其余 → 保留分离（`action: keep_separate`），
  写 `conflict`，旧 id 与新 id 都由 `existing_ids` / `new_id` 列出。
- id 里的 `@来源` 后缀记录**首次出现**来源，不代表唯一来源；增量模式不重命名既有 id。
- 落盘 `incremental.json`（previous / kept_ids / id_preservation / added_nodes /
  merged_into_existing / kept_separate / conflicts / policy），并照常导出
  `graph.json` 与可读的 `graph.md`。

## 10 状态

| 状态 | 条件 |
|-|-|
| `success` | 节点与边都非空、G01–G09 全过、schema 校验通过 |
| `partial` | 上述任一不满足；`gaps` 逐条说明 |
| `blocked` | 找不到任何 material JSON；仍写 `result.json` + `missing_input.md` |

`gaps` 里还会记：缺 manifest、缺 DOI、只解析到一篇、PDF 未解析、查询不足 3 条、
实体/关系类型未在本批材料出现（未补造）、消融开关关闭。这些是材料边界与模式说明，
不压状态；压状态的只有 G 码失败与 schema 失败。

## 11 输入元数据

| 文件 | 用途 |
|-|-|
| `material.json` | 两篇正文段落的汇总索引：`{source_id, file, paragraphs}`，用于定位材料文件 |
| `corpus-manifest.json` | 每篇 `{source_id, doi, title, authors, license, sha256}`；根 `DOI/title/authors` 只归第一篇 |
| `case.json` | `case_id` 与 `output_contract` |

缺 `corpus-manifest.json` 时写 `gaps`，把每篇 DOI/标题/作者/许可置 `unknown`，
不借用另一篇的元数据。

## 12 维护信息

- 版本：`2.0.0`（`run.py` 的 `VERSION` 与 `environment/resources.yaml` 的 `version` 同步）。
- 改词表 → 改 `run.py` 的 `LEX`，同步第 2 节表格。
- 改别名 → 改 `ALIAS`；键会自动进词表，不用手改第 2 节。
- 改关系 → 改 `REL_ALLOWED` / `SIG`，同步第 4 节、`rules/graph.schema.json` 的
  `relation_signatures`、`SKILL.md` 第 5 节。
- 改合并策略 → 改 `MERGE_KINDS` / `KEEP_CONFLICT`，同步第 6 节。
- 改查询 → 改 `rules/queries.json`，同步第 8 节与 `SKILL.md` 第 6 节。
- 改 G 码 → 改 `CODES`，同步 `SKILL.md` 第 5 节（`skill_lint.py` 会比对两处码表，
  代码有、文档没有就判 V01 失败）。
- 改状态条件 → 同步第 10 节与 `SKILL.md` 第 8 节。
