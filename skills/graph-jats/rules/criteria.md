# 判据

词表、阈值与合并规则放这里；`SKILL.md` 正文只留指针。

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

| 情形 | 决定 | 依据 |
|-|-|-|
| 标签归一化后相同（小写、去标点），且出现在两篇 | `merge` | 记归一化结果与来源列表，候选 id 按 `id@source` 展开 |
| 只差规模后缀（`-base`/`-large`/`-small`） | `keep_separate` | 同名不同实例，不得合并 |
| 其他 | 不写记录 | 无跨篇候选 |

不自动做"语义相同"的判断：那需要外部词表，超出范围。

## 5 状态

| 状态 | 条件 |
|-|-|
| `success` | 两篇都解析出段落、G01–G05 全过、3 条查询都有结果、`gaps` 为空 |
| `partial` | 上述任一不满足；`gaps` 逐条说明 |
| `blocked` | 找不到任何 material JSON；仍写 `result.json` |
| `refused` | 材料不是 JSON |

## 6 查询

固定三条，结果与证据一起落盘：

| 查询 | 参数 | 说明 |
|-|-|-|
| `node` | 度数最高的方法标签 | 返回节点、度数、首条证据 |
| `neighbors` | 该方法节点 id | 返回相邻边与各自证据 |
| `by-source` | `secondary` | 返回只由第二篇支撑的节点 |

三条都允许 `n = 0`，但必须在 `gaps` 说明，且不计入 `success`。
