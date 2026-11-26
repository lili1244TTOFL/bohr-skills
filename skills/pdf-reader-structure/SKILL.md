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
| `result.json` | 主结果 + 证据 + 边界 + `case_id` + `output_contract` 回显 |

不做：不 OCR、不翻译、不改写原文、不合并跨页续表、不解释图表与公式的科学含义。

## 2 适用条件

回答：哪些输入能用、哪些不能用。

| 适合 | 不适合 |
|-|-|
| 有文字层的期刊论文、预印本、报告 | 扫描件，无文字层；只在 `gaps` 记页号 |
| 单栏与双栏版式 | 三栏及以上：栏数由起始列聚集决定，不封顶（见第 5 节「分栏与顺序」） |
| 内容流里有 `m/l S` 线段或 `re` 矩形的规则表格 | 图片型表格、跨页续表、合并单元格语义 |
| 字体嵌入且带 ToUnicode | 无 ToUnicode 的字体，缺字只能标注 |

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
| 2 聚栏排序 | `scripts/layout.py` | 否 | 段起始列按 8 列取整计数，得主栏与副栏；栏左→右，栏内行号上→下 |
| 3 角色 | `scripts/layout.py` | 否 | 正则判 `formula` `page_number` `caption` `reference` `heading`，其余 `body` |
| 4 表格 | `scripts/tables.py` | 否 | 内容流线段 → 网格 → 文字按中心点入格 |
| 5 页眉页脚 | `scripts/pdfparse.py` | 否 | 上下边缘重复文本跨 ≥60% 页则标 `header`/`footer` |
| 6 自检落盘 | `run.py` | 否 | 4 条 `checks`、`gaps`、`result.json` |

## 5 判定口径

回答：每条判据的具体阈值，以及规则文件与代码不一致时以谁为准。

| 判据项 | 实际规则（以代码为准） | 出处 |
|-|-|-|
| 段切分 | 行内连续 ≥5 个空格或制表符断开成两段（layout 路径） | `layout.py:21` |
| 行合并 | 基线差 ≤ max(1.5, 0.35×字号)，且水平间隙 ≤ max(4.0, 0.6×字号)（coords 路径） | `pdfparse.py:86-90` |
| 分栏与顺序 | `find_columns` 只在页宽 30%–70% 搜一次，最多 2 栏；layout 路径 = 主栏 + 满足「起始列 ≥ 主栏+20、占比 ≥15%、次数 ≥3」的列，不封顶（2 页示例第 2 页得 3 栏）；栏左→右、栏内上→下，表格单元格起始列会被当成栏 | `pdfparse.py:131-148`、`layout.py:64-93` |
| 标题 | 长度 ≤80 且以 `1.2` 式编号开头；或全大写；或 ≤60 且命中 Abstract/引言/结论词表 | `layout.py:96-112` |
| 标题层级 | 编号点数 +1 后封顶 3；全大写记 1；其余记 2 | `layout.py:115-120` |
| 题注 | 行首匹配 图/表/Figure/Fig/Table + 数字或字母 | `layout.py:22,102` |
| 表格 | ≥2 条水平线且 ≥2 条垂直线；坐标差 <2.0 合并；文字按中心点入格 | `tables.py:57-94` |
| 页眉页脚 | 页高上下 8% 内，同文本重复 ≥max(2, 60%×页数)，长度 <100 | `pdfparse.py:308-339` |
| 公式编号 | 行尾 `(n)` 或 `（n）` 命中即 `formula`，并抽 `formula_no` | `pdfparse.py:229-231` |
| 编号合并 | 编号独占一行、与前块基线差 ≤4、前块角色为 heading/body/formula 才并入 | `pdfparse.py:237-248` |
| 质量标记 | `U+FFFD` 与 `□` 占正文字符 >2% 记一条 `gaps`；整页无字符块且无表格记「第 N 页没有文字层」 | `run.py:92-94,298-299` |

两处与 `rules/parse_rules.md` 不一致，以本节为准：水平间隙阈值是 max(4.0, 0.6×字号)，规则写的是 max(8, 2×字号)；上下标按字号还原没有实现，规则第 7 节那条是目标值。

## 6 输出契约

回答：`result.json` 每个字段装什么，平台的三个字段怎么对上。

| 字段 | 内容 |
|-|-|
| `case_id` | 取 `case.json` 的 `case_id`；缺省用 `--case-id`，再缺省 `case` |
| `status` | `gaps` 为空时 `success`，否则 `partial`；`blocked` 不落进文件 |
| `source` | `path` `sha256`（前 16 位）`n_pages` `parsed_pages` `encrypted` |
| `stats` | `pages` `parsed_pages` `blocks` `body_blocks` `headings` `tables` `formulas` `captions` `references` `headers` `footers` `columns` `chars` `mojibake_ratio` `elapsed_s` |
| `sections` | 嵌套树，节点含 `title` `level` `page` `bbox` `children` |
| `tables` | `no` `page` `n_rows` `n_cols` `path` |
| `formulas` | `page` `no` `text` `bbox` |
| `figures` | `page` `text` `bbox`，取自 `caption` 角色块 |
| `deliverables` | `kind` `path` `bytes`；表格项另带 `table_no` `n_rows` `n_cols` |
| `evidence` | `unit`=`page+bbox`，`blocks`=`blocks.json`，另有 `note` |
| `gaps` | 字符串数组，每条对应具体页、比例或页集 |
| `checks` | `页覆盖` `表格列数一致` `有正文` `页眉页脚已剔除`，各带 `ok` 与 `detail` |
| `output_contract` | 键取自 `case.json`；值为文件名字符串、路径数组或 `{note,pointer}` |
| `output_contract_echo` | 每键 `{status: full 或 partial, source}` |

平台字段映射：`run.py` 已按平台字段名精确匹配，命中即 `full`。

| 平台字段 | 回显内容 | 状态 |
|-|-|-|
| 阅读/解析结果 | `parsed.md` | `full` |
| 证据与边界 | `blocks.json` | `full` |
| 运行状态 | `{status, n_pages, n_gaps}` | `full` |

实测三个中文键均 `full`。`blocks.json` 不携带字号：每块 `size` 恒为 `null`，layout 路径整体不产出字号，`work/parse_raw.json` 也没有。

## 7 限制

回答：没覆盖什么，已知但未修的缺陷有哪些。

- 无 OCR：扫描页只记 `gaps`，不调外部识别。
- 图片型表格与跨页续表不合并；合并单元格按最小网格输出，不推断跨行跨列；不做上下标还原，不转 LaTeX。
- 题注只按行首正则（正文中以 `Table 1` 开头的句子会进 `figures`，2 页示例出现 1 条）；公式编号被栏序拆开后不合并（示例的 `(1)` 未并入 `S = A / N`）。

### 已知缺陷（未修）

| # | 缺陷 | 证据 | 使用时的含义 |
|-|-|-|-|
| 1 | bbox 是推算值：x = 页宽 × 字符列 / 100，y = 页高 − 12 × 行号，不是测量框 | `layout.py:140-141` | 「page+bbox」只作指示，不作精确定位；示例 header 的 y1 为 852，已超页高 842 |
| 2 | 落不进任何单元格的表格文字被丢弃：`assign` 返回未归属列表，两处调用都没接 | `tables.py:78-94`、`pdfparse.py:206` | 这类丢字不进 `gaps`，缺口看起来为空 |
| 3 | 无 PDF 时**已改为**先写 `status=blocked` 的 `result.json`，再退出 1（原缺陷已修） | `run.py:240-258` | 调用方直接读 `status`，不必靠文件缺失推断 |
| 4 | `success` 只在干净输入上可达：任一页单栏且 ≥20 段，或正文含任一汉字，都追加 `gaps` | `run.py:277-282` | 中文 PDF 与长单栏页的正常输出就是 `partial` |
| 5 | `页眉页脚已剔除` **已改为实测量**：检出数 >0 才 `ok`（原硬编码已修） | `run.py:312-320` | 检出 0 表示未识别，不等于已剔除 |
| 6 | 多个 PDF 只读按路径排序后的第一个 | `run.py:233` | 多篇输入必须逐篇调用，不能一次传两个 |

## 8 异常处理

回答：每种异常落到什么状态、调用方怎么接。

| 情况 | 行为 | 状态 |
|-|-|-|
| 输入里没有 PDF | stdout 打 `{"ok": false, "status": "blocked"}` 后返回 1，不产生 `result.json` | 由调用方判 `blocked` |
| PDF 损坏，pypdf 抛 `PdfStreamError` | 未捕获，非零退出，同样不产生 `result.json`（实测返回 1） | 由调用方判 `blocked` |
| PDF 加密 | 试空口令；失败时 `gaps` 记「PDF 加密，已尝试空口令解密」 | `partial` |
| 无文字层页 | 该页写占位 `_（本页无可提取文字层）_`，`gaps` 记页号 | `partial` |
| 字体缺字 | `gaps` 记乱码比例，不改写字符 | `partial` |
| 表格线段不足 2×2 | 不当表格，文字留在正文 | 不记 `gaps` |
| 单栏页 ≥20 段或正文含汉字 | 追加栏序未验证、标题需人工核对的 `gaps` | `partial` |

## 9 自查与预算

回答：跑完怎么验，代价多大。

```bash
python run.py --pdf paper.pdf --out out --work work --case-id demo
python -c "import json;r=json.load(open('out/result.json',encoding='utf-8'));print(r['status'],r['gaps'],r['checks'])"
```

1. `result.json` 存在；缺失按第 7 节第 3 条计 `blocked`。
2. `status` 为 `success` 或 `partial`；`partial` 的 `gaps` 每条能对上具体页或比例。
3. `checks["页覆盖"].ok` 为 true，即 `parsed_pages` 等于 `n_pages`。
4. `checks["表格列数一致"].ok` 为 true，即每张表各行等宽。
5. `stats["mojibake_ratio"]` ≤ 0.02。
6. `output_contract` 的键数等于 `case.json` 的键数。
7. 抽 3 条 `blocks[].bbox` 与原文对照，只作指示不作定位。

预算：2 页示例 0.1 s（本地 CPU，实测 `elapsed_s` 0.1）；按 0.05 s/页外推，50 页 <5 s（本地 CPU，无模型）；模型请求 0 次；单次运行受平台 900 s 上限。

## 10 题目对齐

回答：题目的四个轴各由什么机制接住。

| 评分轴 | 机制 | 输出字段 | 自查步 |
|-|-|-|-|
| 提得全 | layout 模式逐段取文，按页分块，跨页重复的页眉页脚剔出正文 | `parsed.md`、`stats`、`checks["页覆盖"]` | 第 9 节第 3 条 |
| 排得对 | 段起始列聚栏，栏左→右、栏内上→下；单栏长页写 `gaps` | `blocks.json` 的 `col`、`gaps` | 第 9 节第 2 条 |
| 拆得清 | 标题层级、表格 `n_rows`×`n_cols` 网格、公式 `formula_no` | `sections`、`tables`、`formulas` | 第 9 节第 4 条 |
| 核得回 | 每块给 `page` 与 `bbox`，缺口进 `gaps`；bbox 为推算值，属部分完成 | `evidence`、`blocks.json` | 第 9 节第 7 条（partial） |
