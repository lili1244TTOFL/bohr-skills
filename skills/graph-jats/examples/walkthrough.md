# 标注经验：这套图谱里什么算证据，什么不算

示例材料（`examples/primary-material.json`、`examples/secondary-material.json`）是造的，
不是真论文；下面每条都写清楚"机器为什么这么判定"，换到真实论文时按同一套口径复核。
配套产物：`examples/reference-graph.json`（示例参考图谱）。

## 1 一条边凭什么算 high

判据是两条同时成立：**句子里有显式关系词**，且**这句是作者自述**（第一人称或无主语的
被动陈述）。只看"提到了这个方法"不算。

| 原句 | 判成什么 | 为什么 |
|-|-|-|
| `We fine-tune BERT on SST-2 and report accuracy of 0.91 …` | `paper:primary -uses_method-> method:bert` high / explicit | 有第一人称 `We` + 显式动作 `fine-tune`；`0.91` 同时进 measurements |
| `A graph neural network baseline is used for the retrieval task on MS MARCO.` | `paper:secondary -uses_method-> method:graph-neural-network@secondary` high | 无主语被动句按自述处理，且有 `used` |
| `Devlin et al. reported that BERT outperforms the CNN baseline …` | `researcher:devlin@primary -uses_method-> method:bert` medium / reported_elsewhere | 转述句：主体是 Devlin 不是本文；所以边源换成研究者节点，置信度压到 medium |
| `The transformer encoder is trained with a contrastive learning objective on Wikitext.` | `paper:primary -evaluates_on-> dataset:wikitext@primary` high | `trained on` 视为使用该数据集；但 `mentions_material` 这类只算提及的边固定 medium |

`G08` 是这条口径的机械保障：`attribution != self` 的边、`basis` 为 `cooccurrence` 或
`mention` 的边，只要标了 high 就直接判失败。所以"把转述当直接证据"在这套产物里过不了机检。

## 2 哪些关系机器建不出来，为什么

- `We find that attention accounts for most of the gain on SST-2 classification.`
  → 只建 `paper -reports_finding-> finding`，**不建** `attention -supports-> 某结论`。
  理由：句子里只有一个方法实体和一个指标，没有第二个可配对的实体，"支持"找不到对象。
  这一条会进 `boundary.rejected_relation_candidates`，带 cue 与原因，不静默丢。
- `However, the graph neural network does not improve F1 on MS MARCO in our replication.`
  → `method:graph-neural-network@secondary -contradicts-> metric:f1@secondary`。
  方向是"原文语序：先出现的实体 → 后出现的实体"。这是**本实现的约定**，题面没规定
  矛盾关系的方向，所以 `direction_basis` 里写死这句话，评审能看出来是约定不是事实。
- `We compare BERT with the graph neural network baseline under the same tokenizer.`
  → `compared_with` 有方向字段，但方向没有语义（A 和 B 比较是对称的）。
  所以 `direction_basis` 明写"比较本身无方向"，不要把它读成 A 优于 B。
  真正表示优劣的是 `outperforms`，它按比较词前后切分。

## 3 哪些同名词必须分开

| 候选 | 决定 | 依据 |
|-|-|-|
| `sst-2` 出现在两篇 | `keep_separate` | 两篇都没有报告划分、版本、许可；同名不等于同一实例 |
| `annotated corpus` 出现在两篇 | `keep_separate` | 通用语料名不代表同一批文本、同一采集条件 |
| `bert` 出现在两篇 | `merge` | 方法/任务按名称合并；`basis` 里明写"不断言同一模型权重、超参或实验条件" |
| `devlin` 在两篇都被引用 | `keep_separate` | 同名姓氏可能是不同人；未报告机构与全名时不合并 |

`merge` 的节点证据并列两篇，`keep_separate` 的节点 id 带 `@来源` 后缀。
"不能强迫每个别名都合并"这句在这里的落点是：合并只发生在 `method`/`task`，
其余类型一律分开并写 `conflict`；`cross-paper` 查询把每条判定与冲突一起返回。

## 4 数值为什么不合并

`0.91`（primary，accuracy，绝对值）与 `2.1`（primary，accuracy，差值）分两条记，
各自带 `paragraph_id` 与短引；`results` 查询按指标名分组，每组逐篇列出，不做平均、
不做相加、不跨篇放进同一句结论。两篇的样本、划分、口径都没报告，合并数值就是造数据。

## 5 换真实论文时要人工复核的三处

1. **作者姓氏**：`author_surnames()` 取作者串每个片段的最后一个词（长度 ≥4 且不在常见词表里），
   只在正文真的出现这个名字时才建 `researcher` 节点。中文姓名、复姓、机构署名会取错，
   需要人工看一眼 `nodes[].surface_forms`。
2. **结论句**：`finding` 节点只在出现 `we find that` 这类显式结论词时建，标签直接截取原句。
   摘要里的结论句常常没有这些词，会漏。
3. **同句共现**：方法×指标共现边固定 `low`，只说明"这句同时提到"。
   真要断言"该方法在这个指标上更好"，得靠 `outperforms`，而它需要显式比较词。
