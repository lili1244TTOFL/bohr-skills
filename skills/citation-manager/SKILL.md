---
name: citation-manager
description: 适用于需要给中英文论文补引用、重排编号、合并重复文献或核对正文与文献表的场景：输入 docx/md 稿件加 BibTeX/RIS 文献库，输出带正常上标编号的可编辑稿与 GB/T 7714—2015 文献表。不做联网核验条目真伪。
---

# 引用管理

## 1 用途

输入一份稿件与一个文献库，输出可继续编辑的引用成品：

- `<原文件名>_cited.docx`：上标 `[n]` 已插入的可编辑稿
- `numbering.tsv` / `refs_rendered.txt`：序号、条目、锚点与重建的文献表
- `report.json`、`result.json`：逐项检查结果与契约回显

不做：

- 不联网核验条目真伪，不解析 DOI 真值（只做形态检查）。
- 不处理著者-出版年制的脚注形式。
- 不改写正文文字，只插标记与重建文献表。

## 2 适用条件

| 适合 | 不适合 |
|-|-|
| `.docx` / `.md` 稿件，含或不含原有 `[n]` 标记 | 无正文的纯文献库整理 |
| `.bib` / `.ris`，可多份 | 非学术文本（公文、新闻、产品文档） |
| 目标规范为 GB/T 7714—2015 顺序编码制，或 APA 7 / IEEE | 需要真实 DOI 核验的场景 |
| 需要去重合并、编号连锁更新 | 手绘图表内容识别 |

## 3 调用

```bash
# 平台形态：读 /input（case.json + task.md + 稿件 + 文献库），写 /app/output
python run.py --input /input --work /app/work --out /app/output

# 本地形态
python run.py --docx manuscript.docx --refs refs.txt --lib library.bib \
              --work work --out output --case-id case01

# 重放已有两次模型输出（不花模型请求）
python run.py --docx manuscript.docx --refs refs.txt --lib library.bib \
              --work work --out output --no-model \
              --pass1 work/pass1_out.txt --match work/pass3_match.txt
```

本 Skill 有两步需要模型判断（句子分流、句—库匹配）。单一自主 Agent 按下面循环执行：

```text
1. run.py --input /input                                 → 得到 work/pass1_input.txt
2. 按 prompts/pass1_sentence_triage.md 逐句分流，写 work/pass1_out.txt
   每行：uid|类别|理由码|句首
3. run.py --input /input --pass1 work/pass1_out.txt      → 得到 work/match_input.tsv
4. 按 prompts/pass3_match.md 逐句匹配，写 work/pass3_match.txt
   每行：uid|编号|置信度|依据
5. run.py --input /input --pass1 … --match …             → 产出成品
6. 第 2/4 步输出不合规时按校验报码重做，最多 3 轮
```

## 4 流程

| 步 | 脚本 | 花模型 | 做什么 |
|-|-|-|-|
| 0 读输入 | `caseinput.py` | 否 | `/input` → `case_id`、`output_contract`、稿件、文献库 |
| 1 建库 | `mklib.py` | 否 | `.bib`/`.ris` → `library.json` + TSV |
| 2 并库查重 | `ingest_refs.py` | 否 | 稿件文献表 + 库 → `library_merged.json` + `orig_map.json` |
| 2b DOI 形态检查 | `doi_check.py` | 否 | 库 → `doi_status.json`（`--online` 才联网） |
| 3 关键词 | `keywords.py` | 否 | 库 → 关键词、索引、查重候选 |
| 4 分句编号 | `pass1_prep.py` | 否 | 正文 → `prep.json` + `pass1_input.txt` |
| 5 句子分流 | `prompts/pass1_sentence_triage.md` | 是 | 编号句 → `uid\|类别\|理由码\|句首` |
| 6 候选检索 | `match_candidates.py` | 否 | prep + 分流 + 库 → `match_input.tsv` |
| 7 句—库匹配 | `prompts/pass3_match.md` | 是 | 句子 + 候选 + 库 → `uid\|编号\|置信度\|依据` |
| 8 编号重排 | `renumber.py` | 否 | 匹配表 + 库 + `orig_map` → `numbering.json` |
| 9 写入 docx | `insert_refs.py` | 否 | 原稿 + numbering → `*_cited.docx` |
| 9b 图表编号 | `captions.py` | 否 | `--renumber-captions` 时重排图表式编号 |
| 10 成品校验 | `check_refs.py` | 否 | 编号、上标、列表、首现顺序、图表与附录引用 |
| 11 文献表复查 | `check_gbt.py` | 否 | GB/T 样式下的条目格式 |

可选步骤：`export.py`（`--export bib,ris` 回写标准化库）、`captions.py`（`--renumber-captions` 重排图表式编号）、`preview.py`（渲染 PDF/PNG 预览，依赖 LibreOffice）、`check_pass2.py`（校验库整理输出）、`pdftext.py`（PDF 取文本，供 `.pdf` 稿件转换）、`styles.py`（生成/检查上标与标题样式）。

规则与提示词：

| 文件 | 用途 |
|-|-|
| `rules/paper_sections.md` | 章节引用表、中英信号词、9 问决策树、理由码 |
| `rules/gbt7714.md` | 类型标识、条目骨架、作者与标点规则 |
| `rules/glossary_zh_en.md` | 中英术语，用于中文句子匹配英文题名 |
| `prompts/pass1_sentence_triage.md` | 第 5 步句子分流的提示词 |
| `prompts/pass3_match.md` | 第 7 步句—库匹配的提示词 |
| `prompts/pass2_library.md` | 可选的库整理提示词（不传 `--lib-triage` 时不使用） |

## 5 判定口径

| 组 | 判据 |
|-|-|
| 分流 | 摘要、关键词、题名不标注；软件、仪器、统计工具不引 |
| 归属 | 本文数据/结论/方法 → `OWN`；他人数据、方法、标准、数据集 → `CITE` |
| 位置 | 已有 `[n]` 原位保留只换号；新增编号挂在句中已命中的成分之后 |
| 顺序 | 按正文**首现顺序**编号，句内按锚点位置排 |
| 合并 | 以库内条目为准；同一文献一个编号，同位置多篇合集 `[1]`/`[1,3]`/`[1-3]` |

错误码分两组，码表由脚本持有，不在此重复：

- 条目格式：检查码定义见 `scripts/check_gbt.py`（error 影响退出码，warn/info 只提示）
- 成品一致性：检查码定义见 `scripts/check_refs.py`（编号连续、上标、首现顺序、图表题注）

## 6 输出契约

`result.json` 字段：

| 字段 | 含义 |
|-|-|
| `case_id` | 取自 `case.json`；缺失用目录名并记 `gaps` |
| `status` | `success` / `partial` / `blocked` / `refused` |
| `deliverables` | 产物路径 → 是否真实生成 |
| `fact_source_map` | 数字、引用、关键论断 → 来源条目或原稿位置 |
| `evidence`（并入 `fact_source_map`） | 单位 `line`：原稿段落号 + 行号；每条引用可回指原文 |
| `version_boundary` | 改动差异、人工确认项、材料缺口 |
| `checks` | 分项检查的 `ok` 与 `detail` |
| `gaps` | 缺口清单，每条可执行 |
| `output_contract` | 按字段名显式回显，不用关键词猜 |
| `output_contract_echo` | 每字段填充状态 `full` / `partial` / `pointer` |

平台字段到产物的显式映射：

| platform 字段 | 回显内容 | 状态 |
|-|-|-|
| 目标产物 | `*_cited.docx` 与 `refs_rendered.txt` | `full` |
| 事实与来源映射 | `numbering.tsv` 的「首现句 + 锚点 + 条目」列 | `full` |
| 版本与边界 | `changelog.md` 与 `gaps`、`uncertainty` | `full` |

`version_boundary` 与 `uncertainty` 同时写顶层字段，便于按名取值。

## 7 限制

- 不联网核验条目真伪；`doi_status.json` 只判形态，不判存在性。
- 旧标记不移位：原句只有一个标记、语义上要拆成两个时，第二个留在原位（`--merge-new-into-mark` 可并回一处）。
- 图片内容不可读，只读题注、题注上下文与表格单元格文本。
- 预览依赖外部 LibreOffice；找不到时跳过并记 `material_gaps`。
- 英文稿件：文献表节标题按中文「参考文献」匹配，纯英文 `References` 需人工确认该节是否被识别。

已知缺陷（v2 未改代码，使用时必须知情）：

- 步骤状态：非必需步骤失败时 `status` 已会降为 `partial`（本次已修）；`required=True` 的步骤失败仍记 `blocked`。判断成品是否可信，除 `status` 外还要看 `report.json` 分项。
- 目标规范以命令行 `--style` 为准；从 `task.md` 自动识别的规范写在内部 `plan["style"]`，平台形态下不被采纳，需显式传 `--style apa7|ieee`。

## 8 异常处理

| 情况 | 行为 | 状态 |
|-|-|-|
| 缺稿件或文献库 | 不开始，给出缺失项 | `blocked` |
| 缺稿件原有文献表 | 跳过并库只用库；无映射的标记记 `unmapped_markers` | `partial` |
| 库内找不到对应条目 | 匹配给 `NONE`，文献表不新增，记 `dropped_ids` | `partial` |
| 条目字段缺失 | 照常渲染，交给条目检查组报码，不猜值 | `partial` |
| docx 段落与 prep 文本不一致 | 拒绝写入该段并报 `mismatch` | `partial` |
| 编号冲突或重复 | 成品检查组报码，列入 `gaps` | `partial` |
| 模型输出格式错 | 校验脚本报码，按循环重做该步 | `partial` |

## 9 自查与预算

```bash
python run.py --input /input --work /app/work --out /app/output
python scripts/check_pass3.py --match work/pass3_match.txt
python scripts/check_gbt.py --refs output/refs_rendered.txt --json /tmp/gbt.json
python scripts/check_refs.py --docx output/*_cited.docx --numbering work/numbering.json
python -c "import json;r=json.load(open('output/result.json'));print(r['status'],r['checks'],r['gaps'])"
```

1. `check_pass3.py` 退出码 0（CITE 句不漏不重、编号在库内、置信度合法）
2. `check_refs.py` 退出码 0（编号连续、上标、列表升序、首现顺序）
3. `report.json` 的 error 级条目为 0
4. 抽查 3 条 `numbering.tsv`，「首现句」能在稿件里找到

预算：建库、切句、重排、校验为纯本地处理，无模型请求。模型请求固定 2 次（分流 + 匹配），重试按循环上限 3 轮，最坏 6 次，低于 64 次上限。50 页量级本地处理 < 10 s。

## 10 题目对齐

| 评分轴 | 机制 | 落在哪里 | 自查 |
|-|-|-|-|
| 挂得对 | 9 问决策树 + 句中锚点，不挂段末 | `numbering.tsv` 的「首现句」列 | 第 9 节第 4 条 |
| 编得顺 | 首现顺序重排 + 上标写入 + 图表题注连锁 | `check_refs.py` 分项 | 第 9 节第 2 条 |
| 收得净 | 三级判据合并，每次合并写依据 | `ingest_refs.py` 的 `basis` | 抽查 `library_merged.json` |
| 对得上 | 句—库匹配给置信度与依据 | `pass3_match.txt` 第 3、4 列 | 第 9 节第 1 条 |

## 六类动作覆盖

题目把这项任务拆成六类动作。逐条说明本 Skill 做不做、落在哪。

| 动作 | 本 Skill | 落在哪 |
|-|-|-|
| 引用插入位置 | 做 | `scripts/pass1_prep.py` 定句级锚点，`scripts/insert_refs.py` 按锚点插入 |
| 编号更新 | 做 | `scripts/renumber.py` 重排，`numbering.tsv` 记录首现顺序 |
| 文献去重 | 做 | `scripts/ingest_refs.py` + `scripts/match_candidates.py` 归并同一条目 |
| 格式转换与字段补齐 | 部分 | `scripts/check_gbt.py` 按 GB/T 7714 校验并补字段；不支持 APA/EndNote 导出 |
| 来源核验与一致性 | 部分 | `scripts/doi_check.py` 校验 DOI 可达性，`scripts/check_refs.py` 校验正文—列表一致；不做联网全文比对 |
| 交付可编辑成品 | 做 | `out/*_cited.docx`，并生成渲染预览供人工核对 |

不做的部分会在 `gaps` 与 `uncertainty` 里逐条写明，不静默省略。

## 题目对齐（写作04）

| 关注点 | 本 Skill 的机制 | 落在哪 |
|-|-|-|
| 挂得对 | 逐句判定是否需要来源，引用插到论断句而非段末 | `pass1` 分诊 → `report.json` 的句级位置 |
| 编得顺 | 插入/删除/调序后重排编号，正文标记与文末列表同步 | `scripts/renumber.py`、`changelog.md` |
| 收得净 | 中英文题名、预印本与正式版、多库导出按判据合并并给依据 | `scripts/dedupe`、合并依据写入 changelog |
| 对得上 | 条目真实性、元数据字段、正文论断与所引文献主题匹配 | `scripts/check_refs.py`、`pass3` 匹配结果 |

## 契约字段映射

| 字段 | 来源 |
|-|-|
| 目标产物 | `deliverables`（成品稿件与文献表路径） |
| 事实与来源映射 | `fact_source_map`（每条引用的匹配依据与改动记录） |
| 版本与边界 | `version_boundary`（缺口、未确认项、改动清单） |
