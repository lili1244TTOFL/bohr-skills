---
name: paper-notes-structured
description: 适用于需要按论文原章节产出可回溯结构化笔记的任务：章节笔记、五字段论文卡片、知识点清单，每条带块 id 与页码。输入一篇论文（PDF 或纯文本），输出 `notes.md`、`notes.json`、`card.json`、`verify.json` 与 `result.json`。不做 OCR、图表语义解释、跨文献合并与检索索引。
---

# 文献总结与结构化笔记

## 1 用途

输入一篇论文，输出能回原文定位的笔记：

| 产物 | 内容 |
|-|-|
| `notes.md` | 论文卡片 + 章节笔记 + 知识点清单 |
| `notes.json` | 结构化笔记：`sections`、`card`、`knowledge` |
| `card.json` | 五字段卡片：研究问题、数据、方法条件、主要发现、局限 |
| `verify.json` | 判据码 N00–N05 的逐条问题清单与计数 |
| `result.json` | 主结果、证据单位、缺口、`case_id`、`output_contract` 回显 |

证据单位是块 id：`P4-12` 指第 4 页第 12 个正文块，块序按该页 kept 角色计。

不做：OCR、图表语义、公式推导、跨文献合并、检索索引、译文、评审式评价。

## 2 适用条件

| 适合 | 不适合 |
|-|-|
| 有标题层级与正文的 PDF | 无文字层的扫描件（无 OCR） |
| 需要按原章节而非通篇摘要 | 需要复现实验的深度解读 |
| 需要同领域论文并排比较的五字段卡片 | 图表数据二次分析 |
| 已有 `case.json` 与 `output_contract` 的案例目录 | 需要跨库检索或引文网络 |

## 3 调用

真实中间文件是 `--work` 目录下的 `notes_auto.json`，每次运行都重写它。

v1 写的 `notes.json` 不是真实路径。`--notes` 指向不存在的文件时按自动模式跑，只留一条 gaps，不报错。

```bash
# 平台形态：自动抽取（notes_status=auto）
python run.py --input /input --work /app/work --out /app/output

# 平台形态：读自己改写后的笔记（notes_status=agent）
python run.py --input /input --notes /app/work/notes_agent.json --work /app/work --out /app/output

# 本地形态
python run.py --pdf examples/paper.md --work work --out output --case-id demo
```

改稿必须另存路径：写回 `notes_auto.json` 会被本次运行先重写，改稿丢失且 `notes_status` 仍报 `agent`。

| 字段 | 位置 | 取值与约束 |
|-|-|-|
| 顶层三键 | 根 | `sections` 数组必填（空则报 N00）、`card` 对象、`knowledge` 数组；缺任一个整份回退自动模式 |
| `sec_id`／`title`／`level`／`page` | `sections[]` | 字符串／原文标题／整数／首页码 |
| `n_paras`／`n_tables` | `sections[]` | 整数，只供 `notes.md` 显示，不参与判据 |
| `candidates` | `sections[]` | 五键 `question`、`data`、`method`、`finding`、`limitation`，各为数组 |
| 候选条目 | `candidates[键]` | `{"id":"P2-3","page":2,"text":"…"}`；`id` 必须是原文块 id |
| 五个字段名 | `card` | 研究问题、数据、方法条件、主要发现、局限；`{"status":"agent","items":[…]}`，无内容 `status` 写 `not_reported` |
| 卡片条目 | `card.<字段>` | `{"text":…,"anchor":"P2-3","page":2,"section":"1 Methods"}` |
| 知识条目 | `knowledge[]` | `{"kind":"finding","section":…,"page":…,"anchor":"P3-1","text":…,"key":…}` |

```text
1. run.py --input /input              → notes_status=auto、status=success；读 notes_auto.json
2. 按 rules/notes_rules.md 改一份 /app/work/notes_agent.json，锚点与数字不动
3. run.py --input /input --notes /app/work/notes_agent.json  → 核验全过则 success
4. 仍 partial 就读 verify.json 的 problems 改锚点或删数字，回 2；超过 3 轮记 partial 并写明原因
```

规则抽取版本（`notes_status=auto`）本身就是要交付的那件产物：切节、字段候选、卡片与知识点都由 `scripts/notes.py` 从原文算出，块 id 可回溯；`--notes` 改写是可选加强，不是产出前提。

## 4 流程

| 步 | 脚本 | 花模型 | 做什么 |
|-|-|-|-|
| 0 读输入 | `run.py` | 否 | 取 `case.json` 的 `case_id`、`output_contract`；rglob 找第一个源文件 |
| 1 解析 | `scripts/pdfparse.py` | 否 | PDF 分页、块角色、页码；`layout.py`、`tables.py` 为其库模块 |
| 2 切节 | `scripts/notes.py` | 否 | 按 heading 块切节，节内块 id 形如 `P{页}-{序号}` |
| 3 抽候选与卡片 | `scripts/notes.py` | 否 | 五字段正则取首句截 240 字；每字段留前 2 条，finding 与 method 各 1 条按 key 去重 |
| 4 改写 | `rules/notes_rules.md` | 是 | 按节分批改写候选文本，锚点、数字、章节顺序不动 |
| 5 核验 | `scripts/verify_notes.py` | 否 | N00–N05 |
| 6 落盘 | `run.py` | 否 | 上述 5 个产物，另留 `parse.json`、`notes_auto.json` |

## 5 判定口径

| 码 | 检查 | 判定（实现口径） |
|-|-|-|
| N00 | 有章节笔记 | `sections` 为空即报 1 条；核验必不过 |
| N01 | 锚点存在 | `anchor` 须匹配 `P<页>-<序号>`，序号在该页 kept 块内；否则每条报 1 次 |
| N02 | 数字可回溯 | 条目文本每个数字须出现在锚点块原文；去千分位逗号后再比一次；每条只报首例 |
| N03 | 章节对齐 | 原文 heading 命中笔记 `title` 的条数 ≥ max(1, heading 数 × 0.8)，即 ≥80%；分母含同名重复，分子只算文本全同；原文无 heading 时直接过 |
| N04 | 字段完整 | 五个中文键须存在，且各有 `items` 或 `status=not_reported`；一条一报 |
| N05 | 条目重复 | `knowledge[].key` 精确相同的报 1 次 |

## 6 输出契约

`result.json` 每条路径都会写；找不到源文件的 `blocked` 也写，并另写 `missing_input.md`。

| 字段 | 取值 |
|-|-|
| `case_id` | `case.json` 的 `case_id` 或 `id`；都缺用 `--case-id`，再缺用 `case` |
| `status` | `success`（核验 0 问题且笔记非空，`auto`/`agent` 均可）、`partial`（有产物但判据未过）、`blocked`（找不到源文件） |
| `notes_status` | `auto`（规则抽取）或 `agent`（读了 `--notes` 文件） |
| `source` | `path`、`n_pages`、`n_sections` |
| `stats` | `sections`、`knowledge`、`items`（带锚点条目数）、`card_filled`（已填字段名数组）、`elapsed_s` |
| `deliverables` | 4 个产物的 `kind`、`path`、`bytes` |
| `evidence` | 固定 `{"unit":"block id + page"}` 与块 id 释义 |
| `checks` | `有章节`、`锚点`、`数字可回溯`、`章节对齐`、`字段完整`、`知识点不重复` 六项，各带 `ok` 与 `detail` |
| `gaps` | 无文字层页、核验未过（附 counts）、规则抽取版本的披露与边界、改稿缺顶层键 |
| `规则命中` | 切了几节、五类字段各命中几条、知识点数、卡片已填字段 |
| `output_contract` | 按 case 字段逐个写命中值或 pointer 对象 |
| `output_contract_echo` | 每字段 `{status, source}`，`status` 取 `full` 或 `partial` |

`partial` 定义：产物已落盘，但至少一条判据未过或存在字段缺失；此时 `gaps` 必非空。

| 状态 | 触发 | 接手 |
|-|-|-|
| `success` | 核验 0 问题且 `sections`、`knowledge`/`card` 非空（`--notes` 有无都算） | 交付 |
| `partial` | 有产物但 N00–N05 任一未过、或字段缺失 | 交付 + 按 `gaps` 补 |
| `blocked` | 找不到源文件：写 `result.json` + `missing_input.md`，退出码 1 | 人补输入 |
| `needs_model` / `refused` | 未实现，不会出现 | — |

| 本题 case 字段 | `output_contract` 里回填的值 | echo |
|-|-|-|
| 阅读/解析结果 | 节数、章节（`sec_id`/`title`/`page`）、知识点数、带锚点条目数、卡片已填字段 | `full` |
| 证据与边界 | 证据单位、锚点明细（来源/块 id/页/节）、锚点总数、缺口 | `full` |
| 运行状态 | `status`、`notes_status`、六项检查的 `ok`、未过检查 | `full` |

`run.py` 按平台字段名精确匹配 `阅读/解析结果`、`证据与边界`、`运行状态`，命中即回填本次运行算出的真实内容（不是文件名字符串）；只有未登记的字段名才回落到 `pointer` 对象。

## 7 限制

- 无文字层页只记缺口：该页无块，笔记跳过，不补 OCR。
- 章节依赖 heading 识别；无 heading 的论文退化成单节 `（题名与摘要）`。
- `.docx` 只按 UTF-8 文本读，不做真解析。
- 表格只记行列文本，不解释含义。
- 加密状态只留在 `parse.json` 的 `encrypted`，不进 `result.json`，也不进 `gaps`。
- 卡片与知识点只读 `--notes` 文件的横切结构，不做跨论文对齐。

### 已知缺陷

- N03 的覆盖与顺序检查：`in_order` 原先算了不用，现已并入 `n03`（`verify_notes.py:73-75`）；章节倒序会判失败，`checks.章节对齐` 随之为 false。
- 自动模式只抽信号句，不写概括：`candidates()` 取首句截 240 字，`notes.md` 的章节笔记是原句搬运。想更近成稿再走 `--notes` 改写；规则版本本身按 `success` 交付，边界写在 `gaps` 里。
- 无索引或查询产物：只有 `knowledge[].key` 这类去重键，没有倒排表与查询命令，"用得上"只部分满足。
- `notes_status=agent` 不等于真改写：把改稿写在 `notes_auto.json` 上会被本次运行先覆盖，仍报 `agent`。
- 纯文本输入的 heading 保留行首 `#`，`title` 因此带标记，与 PDF 路径的标题形态不一致。

## 8 异常处理

| 情况 | 行为 | 状态 |
|-|-|-|
| 找不到源文件 | 写 `result.json` 与 `missing_input.md`（列出找过的后缀与要补什么），stdout 打 `{"ok":false,"status":"blocked"}`，退出码 1 | `blocked` |
| `--notes` 路径不存在 | 静默退自动模式，`gaps` 记规则抽取版本一条 | 按核验结果 `success` / `partial` |
| 改稿缺 `sections`、`card`、`knowledge` | 记缺口「缺 X，回退到自动抽取」，整份回退 | 按核验结果 `success` / `partial` |
| 改稿三键齐但 `sections` 为空 | 记 N00，核验不过 | `partial`（`agent`） |
| 锚点越界或数字不在原文 | 记 N01 或 N02，该条目不自动改数 | `partial`（`agent`） |
| 无文字层页 | `gaps` 记页号，该页无块 | `partial` |
| 无 heading | 单节 `（题名与摘要）`，N03 因分母为 0 直接过 | `success`（`auto`，单节） |

## 9 自查与预算

在 Skill 目录下按顺序跑：

```bash
python run.py --pdf examples/paper.md --work work --out output --case-id md-demo
python scripts/verify_notes.py --parse work/parse.json --notes output/notes.json --out work/verify.json
python -c "import json;r=json.load(open('output/result.json',encoding='utf-8'));print(r['status'],r['notes_status'],r['checks'],r['gaps'])"
```

1. `status` 为 `success` 或 `partial`；`partial` 的 `gaps` 非空。不给 `--notes` 时，规则抽取版本核验全过即 `success`。
2. `checks` 四项全 `ok`；另看 `verify.json.counts` 是否还有 N00、N05。
3. `output_contract` 键数等于 case 字段数；echo 全为 `partial` 时在 `gaps` 注明 pointer。
4. 抽查 3 条 `knowledge[]`：`anchor` 的页与序号在 `parse.json` 中存在，`text` 里的数字在原文出现。

预算：脚本无网络、无模型请求；1 页 md 示例 `elapsed_s`=0.02（本地 CPU）。改稿请求数 = ⌈节数 / 10⌉，112 节即 12 次，平台上限 64 次 / 900 s；重跑超 3 轮记 `partial` 并写明未过判据。

## 10 题目对齐

| 评分轴 | 机制 | 落在哪里 | 自查 | 覆盖 |
|-|-|-|-|-|
| 对得上 | 按原文 heading 切节；N03 查标题命中率 ≥80% | `sections[]`、`checks.章节对齐` | 第 9 节第 2 条 | 部分：不校验章节顺序 |
| 留得住 | 五字段卡片；缺项显式 `not_reported`，不猜值 | `card`、`stats.card_filled` | 第 9 节第 2 条 | 完整 |
| 找得回 | 每条带 `anchor`；N01 查块存在，N02 查数字回原文 | `evidence`、`knowledge[].anchor` | 第 9 节第 4 条 | 完整 |
| 用得上 | `knowledge[].key` 去重条目可重跑、可比较 | `notes.md`、`notes.json` | 第 9 节第 1 条 | 部分：无索引、无查询 |
