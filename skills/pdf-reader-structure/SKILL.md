---
name: pdf-reader-structure
description: 适用于需要把有文字层的科研 PDF 拆成可继续处理结构的任务。输入一份 PDF，输出 `parsed.md`、`blocks.json`、`sections.json`、`tables/` 与 `result.json`。不做 OCR，不合并跨页续表，不按字号还原上下标。
---

# 文献文档解析

## 1 用途

回答：输入什么、输出什么、不做什么。

输入一份有文字层的科研 PDF，输出按页组织的结构：

| 产物 | 内容 |
|-|-|
| `parsed.md` | 阅读顺序正文；每页带 `<!-- page N -->` 标记 |
| `blocks.json`、`sections.json` | 块数组（`page` `role` `text` `bbox` `size`）；标题树（`title` `level` `page` `bbox` `children`） |
| `tables/tableN.md` | 表格网格的 Markdown |
| `result.json` | 主结果 + 证据 + 边界 + `case_id` + `output_contract` 回显；`docmeta` 装标题/作者/单位/摘要 |
| `unreadable.md` | 读不出或读不全时另有这份：逐页写页码与原因（无文字层、整份 0 页、无正文块） |

不做：不 OCR、不翻译、不改写原文、不合并跨页续表、不解释图表与公式的科学含义。

## 2 适用条件

回答：哪些输入能用、哪些不能用。

| 适合 | 不适合 |
|-|-|
| 有文字层的期刊论文、预印本、报告 | 扫描件，无文字层；`gaps` 记页号，另出 `unreadable.md` |
| 单栏与双栏版式 | 三栏及以上：栏数由行首聚集决定，不封顶（见第 5 节「分栏与顺序」） |
| 内容流里有 `m/l S` 线段或 `re` 矩形的规则表格 | 图片型表格、跨页续表、合并单元格语义 |
| 字体嵌入且带 ToUnicode | 无 ToUnicode 的字体，缺字只能标注 |
| 英文与中文页（`STSong-Light` 这类预定义 CMap 的中文可解） | 中文标题层级只按编号点数判，行内字距极端时需人工核对 |

## 3 调用

回答：怎么跑起来，参数各做什么。

```bash
# 平台形态
python run.py --input /input --work /app/work --out /app/output

# 本地形态
python run.py --pdf paper.pdf --out output --work work --case-id demo
```

| 参数 | 作用 |
|-|-|
| `--input` | 平台输入目录；按文件头 `%PDF-` 嗅探 PDF，不信扩展名 |
| `--pdf` | 直接指定 PDF；`--input` 里没嗅到 PDF 时作回退 |
| `--case-id` `--work` `--out` | 覆盖 `case_id`；中间产物目录；交付产物目录 |

无模型改写，无 `needs_model` 循环，一次运行即产出。coords 引擎只能直接调脚本：`python scripts/pdfparse.py paper.pdf --engine coords --out work/parse.json`；`run.py` 不暴露该开关，默认走 layout。

## 4 流程

回答：每一步谁执行、花不花模型。

| 步 | 脚本 | 花模型 | 做什么 |
|-|-|-|-|
| 0 读输入 | `run.py` | 否 | 取 `case.json` 的 `case_id` 与 `output_contract`；`rglob` 嗅探 `%PDF-` |
| 1 取行 | `scripts/layout.py` | 否 | `extraction_mode="layout"` 取文本；连续 ≥5 空格切段 |
| 2 表格先行 | `scripts/tables.py` | 否 | 线段 → 网格 → 文字按中心点入格；单元格文字从正文段里剔除 |
| 3 聚栏排序 | `scripts/layout.py` | 否 | 段起始列按 8 列取整计数；副栏需与主栏同行共现 ≥2 次；栏左→右，栏内行号上→下 |
| 4 定框定角色 | `scripts/layout.py` | 否 | 段文本回查 visitor 字符块拿实测 bbox 与字号；正则与字号判 `title` `heading` `caption` `reference` `formula` `page_number`，其余 `body` |
| 5 页眉页脚 | `scripts/pdfparse.py` | 否 | 上下边缘重复文本跨 ≥60% 页则标 `header`/`footer` |
| 6 文档元数据 | `run.py` | 否 | `role=title` 合并成标题，标题后两块取作者/单位，Abstract 到下一个标题之间取摘要 |
| 7 自检落盘 | `run.py` | 否 | 4 条 `checks`、`gaps`、`result.json` |

## 5 判定口径

回答：每条判据的具体阈值，以及规则文件与代码不一致时以谁为准。

| 判据项 | 实际规则（以代码为准） | 出处 |
|-|-|-|
| 段切分 | 行内连续 ≥5 个空格或制表符断开成两段（layout 路径） | `layout.py:25` |
| 行合并 | 基线差 ≤ max(1.5, 0.35×字号)（coords 路径） | `pdfparse.py:86-90` |
| 分栏与顺序 | 主栏取出现最多的行首列；副栏需与主栏相距 ≥20 列、占比 ≥15%，且两栏在同一视觉行共现 ≥2 次（防居中标题被当成一栏）；栏左→右、栏内上→下；表格单元格段先剔除再聚栏 | `layout.py:80-119` |
| 标题 | 先判编号标题（`1. Introduction`、`1.1 Motivation`），再判参考文献；长度 ≤90；或字号 ≥ 正文 1.15 倍 | `layout.py:121-152` |
| 标题层级 | 编号点数 +1（`1.`→1 级，`1.1`→2 级）封顶 3；Abstract/References 这类词表标题记 1 级 | `layout.py:153-161` |
| 题注 | 行首匹配 图/表/Figure/Fig/Table + 数字；若数字后紧跟小写词（`Table 1 reports ...`）判正文 | `layout.py:26-28,131` |
| 表格 | ≥2 条水平线且 ≥2 条垂直线；坐标差 <2.0 合并；文字按中心点入格 | `tables.py:57-94` |
| 页眉页脚 | 页高上下 8% 内，同文本重复 ≥max(2, 60%×页数)，长度 <100 | `pdfparse.py:309-340` |
| 公式编号 | 行尾 `(n)` 或 `（n）` 命中即 `formula`；编号独段且与前块同一视觉行则并入前块 | `layout.py:251-260` |
| bbox | 段文本在 visitor 字符块里最长匹配，取实测 x0/x1/y；匹配不上退回推算框并置 `bbox_est=True` | `layout.py:180-201,226-244` |
| 质量标记 | `U+FFFD` 与 `□` 占正文字符 >2% 记一条 `gaps`；整页无字符块且无表格记「第 N 页没有文字层」 | `run.py:136-139,415-416` |

`rules/parse_rules.md` 的判栏一节写的是 coords 引擎的 `find_columns`，默认 layout 引擎不读它；两处不一致时以本节为准。

## 6 输出契约

回答：`result.json` 每个字段装什么，平台的三个字段怎么对上。

| 字段 | 内容 |
|-|-|
| `case_id` | 取 `case.json` 的 `case_id`；缺省用 `--case-id`，再缺省 `case` |
| `status` | `success`（有页解析出来且抽到文字）／`partial`（一页都没解析出或没抽到任何文字层）／`blocked`（输入里没有 PDF 或指定的 PDF 不存在） |
| `source` | `path` `sha256`（前 16 位）`n_pages` `parsed_pages` `encrypted` |
| `docmeta` | `title` `authors` `affiliation` `abstract` `keywords`；取不到的字段是空串，不编 |
| `stats` | `pages` `parsed_pages` `blocks` `body_blocks` `headings` `tables` `formulas` `captions` `references` `headers` `footers` `bbox_estimated` `columns` `chars` `mojibake_ratio` `elapsed_s` |
| `sections` | 嵌套树，节点含 `title` `level` `page` `bbox` `children` |
| `tables` | `no` `page` `n_rows` `n_cols` `path` |
| `formulas` | `page` `no` `text` `bbox` |
| `figures` | `page` `text` `bbox`，取自 `caption` 角色块 |
| `references` | `page` `text`，取自 `reference` 角色块 |
| `deliverables` | `kind` `path` `bytes`；表格项另带 `table_no` `n_rows` `n_cols` |
| `evidence` | `unit`=`page+bbox`，`blocks`=`blocks.json`，`note` 说明实测框与 `bbox_est` 的区别 |
| `gaps` | 字符串数组，每条对应具体页、比例或页集；无文字层页、栏序未验证、中文提示这类版面边界**不压 `status`** |
| `checks` | `页覆盖` `表格列数一致` `有正文` `页眉页脚已剔除`，各带 `ok` 与 `detail` |
| `规则命中` | 块角色计数、每页判定的栏数、表格网格行列、borrow 实测/推算框数、规则说明 |
| `output_contract` | 键取自 `case.json`；值是解析出的真实内容（标题、章节、表、公式、样本框、gaps），不是文件名字符串 |
| `output_contract_echo` | 每键 `{status: full 或 partial, source}` |

平台字段映射：`run.py` 按平台字段名精确匹配，命中即装真实内容。

| 平台字段 | 回显内容 | 状态 |
|-|-|-|
| 阅读/解析结果 | `{pages, title, authors, affiliation, abstract_chars, headings[], tables[], formulas[], references}` | `full` |
| 证据与边界 | `{unit, measured_blocks, estimated_blocks, sample[3], checks, gaps}` | `full`（有实测框时） |
| 运行状态 | `{status, reason, n_pages, n_gaps}` | `full` |

## 7 限制

回答：没覆盖什么，已知但未修的缺陷有哪些。

- 无 OCR：扫描页只记 `gaps`，不调外部识别。
- 图片型表格与跨页续表不合并；合并单元格按最小网格输出，不推断跨行跨列；不做上下标还原，不转 LaTeX。
- 表格线围成的区域内、落不进任何单元格的文字仍会被丢弃（`assign` 的未归属列表没有接进 `gaps`）。
- 中文标题层级只按编号点数判；行内字距极端时标题行分段可能不准，`gaps` 会写一条。

### 已知缺陷（未修）

| # | 缺陷 | 证据 | 使用时的含义 |
|-|-|-|-|
| 1 | bbox 分两种：`bbox_est=True` 的块仍是推算框（页宽 × 字符列 / 100） | `layout.py:235-244` | 这类块只作指示；未标 `bbox_est` 的块是 visitor 实测坐标 |
| 2 | 落不进任何单元格的表格文字被丢弃：`assign` 返回未归属列表，两处调用都没接 | `tables.py:78-94`、`pdfparse.py:282` | 这类丢字不进 `gaps`，缺口看起来为空 |
| 3 | 无 PDF 或 `--pdf` 指向的文件不存在时，写 `status=blocked` 的 `result.json` 与 `missing_input.md`，再退出 1 | `run.py:311-345` | 调用方直接读 `status`，不必靠文件缺失推断 |
| 4 | 整宽单栏页不触发栏序提示；只有正文宽度 < 页宽 45% 的单栏页才写 `gaps` | `run.py:382-397` | 窄栏页可能被判成单栏，需要人工看一眼 |
| 5 | `页眉页脚已剔除` 是实测量：检出数 >0 且 `parsed.md` 无残留才 `ok` | `run.py:420-431` | 检出 0 表示未识别，不等于已剔除 |
| 6 | 多个 PDF 只读按路径排序后的第一个 | `run.py:333` | 多篇输入必须逐篇调用，不能一次传两个 |
| 7 | 只有一页的 PDF 检不出页眉页脚（重复判据需要 ≥2 页） | `pdfparse.py:309-312` | 单页输入的 `checks` 该项为 false |

## 8 异常处理

回答：每种异常落到什么状态、调用方怎么接。

| 情况 | 行为 | 状态 |
|-|-|-|
| 输入里没有 PDF | 写 `result.json` + `missing_input.md`（列出找过的判据与要补什么）后返回 1 | `blocked` |
| `--pdf` 指的文件不存在 | 同上，另写 `missing_input.md`；不再抛栈 | `blocked` |
| PDF 有页树但 0 页 | `gaps` 记「未解析出任何页面」，另写 `unreadable.md` | `partial` |
| PDF 损坏，pypdf 抛 `PdfStreamError` | 未捕获，非零退出，不产生 `result.json` | 由调用方判 `blocked` |
| PDF 加密 | 试空口令；失败时 `gaps` 记「PDF 加密，已尝试空口令解密」 | 不压状态 |
| 无文字层页 | 该页写占位 `_（本页无可提取文字层）_`，`gaps` 记页号，另写 `unreadable.md` 逐页说明 | 有文字则 `success`，整份无文字则 `partial` |
| 字体缺字 | `gaps` 记乱码比例，不改写字符 | 不压状态 |
| 表格线段不足 2×2 | 不当表格，文字留在正文 | 不记 `gaps` |
| 中文正文 | `gaps` 记中文页字数，提示标题层级需人工核对 | 不压状态 |

## 9 自查与预算

回答：跑完怎么验，代价多大。

```bash
python run.py --input ../_cases/two_column --work w --out o
python -c "import json;r=json.load(open('o/result.json',encoding='utf-8'));print(r['status'],r['gaps'],r['checks'])"
```

1. `result.json` 存在；缺失按第 7 节第 3 条计 `blocked`。
2. `status` 为 `success` 或 `partial`：`success` = 有页解析出来且抽到文字；`partial` 时看 `unreadable.md` 与 `gaps` 说明哪几页读不出。
3. `checks["页覆盖"].ok` 为 true，即 `parsed_pages` 等于 `n_pages`。
4. `checks["表格列数一致"].ok` 为 true，即每张表各行等宽。
5. `stats["mojibake_ratio"]` ≤ 0.02。
6. `output_contract` 的键数等于 `case.json` 的键数，且值里能看到标题/章节/样本框。
7. 抽 3 条 `blocks[].bbox` 与原文对照：未标 `bbox_est` 的应当落在正文列内；标了的按推算框对待。

包内四份版式样例（`examples/make_cases.py` 生成）：

| 样例 | 版式 | 实测要点 |
|-|-|-|
| `_cases/single_column` | 单栏英文，2 页 | 编号 `1.` 标题 5 个、页眉页脚 2+2、参考文献 3 |
| `_cases/two_column` | 双栏英文，2 页 | 左栏先于右栏、公式 `S = A / N (1)` 合并、题注 1 |
| `_cases/table_grid` | 单栏表格，2 页 | 4×5 网格 1 张、表题在表前、单元格文字不进正文 |
| `_cases/chinese` | 单栏中文，2 页 | 标题/作者/单位/摘要可出，`gaps` 记中文页字数 |

预算：2 页样例 0.1 s（本地 CPU，实测 `elapsed_s` 0.1）；按 0.05 s/页外推，50 页 <5 s（本地 CPU，无模型）；模型请求 0 次；单次运行受平台 900 s 上限。

## 10 题目对齐

回答：题目的四个轴各由什么机制接住。

| 评分轴 | 机制 | 输出字段 | 自查步 |
|-|-|-|-|
| 提得全 | layout 模式逐段取文，按页分块，跨页重复的页眉页脚剔出正文 | `parsed.md`、`stats`、`checks["页覆盖"]` | 第 9 节第 3 条 |
| 排得对 | 表格单元格先剔除再聚栏，栏左→右、栏内上→下 | `blocks.json` 的 `col`、`stats["columns"]` | 第 9 节第 2 条 |
| 拆得清 | 标题层级、标题/作者/单位/摘要、表格网格、公式编号 | `docmeta`、`sections`、`tables`、`formulas` | 第 9 节第 4 条 |
| 核得回 | 每块给 `page` 与实测 bbox；实测不到的块标 `bbox_est` | `evidence`、`blocks.json` | 第 9 节第 7 条 |

## 值不值得看，看这两个文件

| 想知道的 | 看哪 |
|-|-|
| 与现成工具比，边界在哪、做不到什么 | `docs/对比与边界.md` |
| 每条结论的出处 | `result.json` 里带出处的那几个字段，见上面的输出契约表 |

## 判断层：块归属章节

归到哪一节由调用方模型判；`caption`/`table`/`formula` 不进判断项，按题面「图表与公式不要求理解科学含义」由脚本按阅读顺序定（`exempt` 列明）。

| 判断项 | 填什么 | 由谁核 |
|-|-|-|
| 块属于哪一节 | `judgments.json` 的 `section`，取该块候选里的标题 | `run.py`：`in_candidates` |
| 依据 | `basis`：该块原文的逐字片段 | `run.py`：`basis_verbatim` |

先跑一次出 `--out/block_section_worklist.md` 判断单；模型写 `judgments.json`（缺省读输入目录或 PDF 同目录，也可 `--judgments` 指路径）；再跑一次，`判断层` 落进 `result.json`，全量块→节写 `--out/block_sections.json`。键与填写格式见 `rules/parse_rules.md` §10。没填的块标 `pending` 并记 `gaps`，脚本默认归属只作候选。