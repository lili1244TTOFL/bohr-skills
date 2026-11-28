---
name: nanopub-split
description: 适用于把论文段落拆成可判断真假的科研断言并绑定证据的场景：输入正文与来源元数据，输出 nanopubs.json、assertions.tsv 与 result.json，每条断言带最小证据片段与原文行列区间。不做 OCR、不做联网核验。
---

# 断言拆解与证据绑定

## 1 用途

把论文正文拆成一条条**单独可判断真假**的断言，每条绑一个最小证据片段与原文位置，导出成机器可读结构。

| 产物 | 内容 |
|-|-|
| `nanopubs.json` | 断言数组 + 来源元数据 + 统计 |
| `assertions.tsv` | 表格形态：id、主体、关系、客体、行号、字符区间、置信度 |
| `result.json` | 题面三个契约字段平铺到顶层 |

不做：OCR、联网核验、图表内文字抽取、跨论文去重。

## 2 适用条件

| 适合 | 不适合 |
|-|-|
| 有文本层的正文（.md / .txt） | 扫描版 PDF |
| 结论句含显式关系词（提高、采用、导致…） | 全靠推论、无显式关系的段落 |
| 需要逐条核对断言与证据 | 需要读懂图表 |

## 3 调用

```bash
python run.py --input /input --work /app/work --out /app/output
python run.py --input examples/case --work work --out output --case-id np-demo
```

退出码：0 正常（`status` 可能为 `partial`）；1 材料缺失（`blocked`，仍写 `result.json` 与 `missing_input.md`）。

## 4 流程

| 步 | 做什么 |
|-|-|
| 1 读材料 | 找 `manuscript*.md` 或任意 `.md/.txt`；读 `case.json` 取 paper/authors/year/doi |
| 2 切句 | 按 `。！？；` 与换行切分，保留全文字符偏移 |
| 3 判关系 | 命中 `run.py` 的 `RELATIONS` 才成为断言；因果类优先，其余取句内最早命中 |
| 3b 拆长句 | 命中关系但超过长度阈值的句子，先按「；，、and」分句切成 ≤阈值的块，逐块再判关系词；切不出仍标 `review` |
| 4 拆主客 | 转述前缀（作者认为/结果显示/...）进 `attribution`；主体取关系词前并去掉话语标记与系动词；客体取关系词后 |
| 5 绑证据 | 证据取「主体→客体」连续窗口，不取整句；记 `line`、`char_start`、`char_end`，方法记作 `attribution+subject-object-window` |
| 6 导四份 | `nanopubs.json`、`assertions.tsv`、`unresolved.md`、`result.json` |

## 5 判定口径

| 情形 | 判定 |
|-|-|
| 句子无关系词 | 不生成断言，计入 `skipped`，逐句写进 `unresolved.md`；覆盖率随之下降 |
| 断言超过长度阈值（中文 80 字 / 英文 160 字） | 先按分句切分（步 3b）；切完仍超才标 `review=断言过宽，需拆细`，置信度降为 medium |
| 正文找不到 | `status=blocked`，写 `result.json` 与 `missing_input.md` 后退出 1 |
| 来源字段缺失 | 写 `unknown`，不编造，并进 `gaps` |
| 同一句多个关系词 | 因果类优先，其余取句内最早命中；另记 `confidence_basis` |
| 转述前缀 | 单独记 `attribution`，不并进 `subject` |

`status` 取值：`success`（有断言且无过宽）、`partial`（有断言但有过宽或缺口；或断言 0 条但有 `unresolved.md`）、`blocked`（无正文）。

`coverage` = 成断言的**句子数** / 句子总数（一条长句切出多条断言时不会超过 1）。

## 6 输出契约

| 字段 | 内容 |
|-|-|
| `case_id` | 取自 `case.json`，缺失时用输入目录名；写进 `result.json` 顶层 |
| `阅读/解析结果` | 断言条数、覆盖率、逐条 `{id, subject, relation, object}` |
| `证据与边界` | 逐条 `{quote, line, char_start, char_end}`、待复核条数、缺口 |
| `运行状态` | `{status, reason, assertions, n_gaps}` |
| `output_contract` | 三个平台字段装上面的真实内容；`output_contract_echo` 逐键记 `full`/`partial` |
| `evidence.items` | 每条断言的 id 与三处位置 |
| `uncertainty` | 与 `gaps` 相同，列需人工确认的项 |
| `规则命中` | 关系词命中分布、切分记录（原句/切块数/规则）、未命中句子数 |

`nanopubs.json` 里另有 `unresolved`（未成断言的句子原文数组）与同一份 `规则命中`；`unresolved.md` 是人读版，逐句说明为什么没成断言。

`case.json` 里 `output_contract` 的键逐个回显：认得的写真实内容，认不得的置 `null` 并记入 `gaps`，不猜。

单条断言的字段：`id`、`identifier`（`np:` + 12 位哈希）、`assertion`、`subject`、`relation`、`object`、`attribution`、`confidence`、`confidence_basis`、`review`、`evidence`（`quote`/`line`/`char_start`/`char_end`/`method`）、`source`。七项齐全才进 `assertions`，缺项写 `gaps`。

## 7 限制

- 关系词表只覆盖常见科研动词；`RELATIONS` 在 `run.py` 里，`rules/relations.md` 是人读副本，改词表要改代码并同步该文件。
- 不做指代消解：主体若是"该结果""上述方法"，会照原文记下来，标为待确认。
- 证据是「主体→客体」窗口，不是子句级；长句切分只在过宽时发生，切完客体仍可能带上并列分句，需人工收窄。
- 长句切分按「；，、and」找切点，不做句法分析；切完仍超阈值才标 `wide` 交人工。
- 不判断断言的真假，只保证它可判断、可回指。

## 8 异常处理

| 情况 | 行为 | 状态 |
|-|-|-|
| 无正文文件 | 写 `result.json` 与 `missing_input.md`，退出 1 | `blocked` |
| 正文无显式关系句 | 断言 0 条，交 `unresolved.md` 逐句说明，`gaps` 记明 | `partial` |
| `case.json` 缺来源字段 | 写 `unknown`，进 `gaps` | `partial` |
| 断言过宽（切分后仍超阈值） | 标 `review`，`uncertainty` 列出 | `partial` |
| 过长句已切分 | 逐条记 `规则命中.切分`，`gaps` 记明多出几条 | 不压状态 |

## 9 自查与预算

```bash
python run.py --input examples/case --work work --out output --case-id np-demo
python -c "import json;r=json.load(open('output/result.json'));print(r['运行状态'],r['阅读/解析结果'])"
```

1. 每条断言的 `evidence.quote` 能在正文里按 `char_start/char_end` 截出。
2. `assertions.tsv` 行数 = 断言数 + 1。
3. `gaps` 与 `uncertainty` 一致；`unresolved.md` 的未命中句数等于 `stats.skipped`。
4. 换一篇正文重跑，覆盖率应随文本变化。
5. 拿一段超长句（无分号、>80 字）跑一遍：应出现 `规则命中.切分`，`wide` 归零。

预算：零模型请求，纯本地处理；单篇 10 KB 正文为毫秒级。

## 10 题目对齐

| 关注点 | 机制 | 落在哪 |
|-|-|-|
| 拆得细 | 一句一断言，只承一个关系；过宽先按分句切，切完仍超才标待拆细 | `assertions[].review`、`规则命中.切分` |
| 对得上 | 证据取主体→客体窗口，另记转述前缀与置信度依据 | `evidence.quote`、`attribution`、`confidence_basis` |
| 追得回 | 断言→证据→paper/authors/year/doi 链路 | `source` 字段 |
| 读得懂 | JSON + TSV 双导出，字段固定；未成断言的句子另出 `unresolved.md` | `nanopubs.json`、`assertions.tsv`、`unresolved.md` |

## 契约字段映射（字段 → 产物 → 完整度）

| 字段 | 产物 | 完整度 |
|-|-|-|
| 阅读/解析结果 | `result.json` 的 `阅读/解析结果`：条数、覆盖率、逐条三元组 | full：来自本次运行的断言数组 |
| 证据与边界 | `result.json` 的 `证据与边界`：逐条 quote 与字符区间、待复核数、gaps | full：每条 quote 可按区间截出 |
| 运行状态 | `result.json` 的 `运行状态`：status + reason | full：success / partial / blocked |

`case.json` 给了 `output_contract` 就逐键回显真实内容；没给时仍输出上面三项。

## 值不值得看，看这两个文件

| 想知道的 | 看哪 |
|-|-|
| 与现成工具比，边界在哪、做不到什么 | `docs/对比与边界.md` |
| 每条结论的出处 | `result.json` 里带出处的那几个字段，见上面的输出契约表 |

## 判断层：哪些交给调用方模型

脚本准备候选与锚点，判断由调用方模型做，脚本再核结果。本节说明本包的判断项。

| 判断项 | 口径 |
|-|-|
| 这句含几条可判断真假的断言 | 并列与转折常含两条以上 |
| 每条的主语与关系是什么 | 关系词必须在原句里能找到 |

脚本给句子、候选关系词、字符区间；模型判拆分与主客；脚本核每条断言的文字是原句子串。

判断结果写进 `result.json` 的 `判断层` 字段：每条含 `item`（判断项）、`value`（结论）、`basis`（依据，必须是原句子串）、`filled_by`（模型名或 caller）。未填写的项标 `pending`，不自行补结论。
