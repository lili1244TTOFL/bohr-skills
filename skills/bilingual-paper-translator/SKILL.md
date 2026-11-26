---
name: bilingual-paper-translator
description: 适用于需要把有文字层的科研文献译成中文、且每段译文都能回指原文的任务。输入 pdf/docx/txt/md/tex，输出段落级双语对照 bilingual.md、segments.jsonl、glossary.md、verify.json 与 result.json。不做 OCR、不做版式还原、不支持多栏切分、不改写原文。
---

# 文献翻译：段落级双语对照
## 1 用途
这节回答什么：输入什么、产出哪些文件、不做什么。

| 产物 | 内容 |
|-|-|
| `bilingual.md` | 以 pid 与页码为二级标题，逐段原文 + 译文；未译段标 ⟦未译⟧ |
| `segments.jsonl` | 每句一行：`uid`、`pid`、`sid`、`page`、`text`、`protected` |
| `glossary.md` | 三列 `term`/`target`/`note`，源术语 → 固定译名 |
| `verify.json` | V01–V09 逐段问题、计数、`missing`、`duplicates` |
| `result.json` | 状态、来源、证据、检查、`gaps`、`output_contract` 回显 |

不做：不做 OCR；不做版式还原；不做多栏重排；不改写、不合并、不拆分原文段；不猜译名。

## 2 适用条件
这节回答什么：哪些输入能跑，哪些必须换方案。

| 适合 | 不适合 |
|-|-|
| 有文字层的英文或中文文献（pdf/docx/txt/md/tex） | 纯扫描件、图片型表格（无文字层） |
| 单栏排版、段落以空行分隔 | 多栏排版（无栏序判定，见 §7 缺陷 b） |
| 需要全篇统一译名、逐段人工复核的长文 | 需要版式还原的排版交付；机翻后直接发布 |

## 3 调用
这节回答什么：怎么跑一次完整翻译，译文由谁补。

```bash
# 平台形态：第一遍切段与术语，第二遍带译文复核
python run.py --input /input --work /app/work --out /app/output
python run.py --input /input --work /app/work --targets /app/work/targets.jsonl --out /app/output
# 本地形态（examples/ 自带示例输入，输出属示意）
python run.py --src examples/paper.txt --glossary examples/glossary.md --targets examples/targets.example.jsonl --work work --out output --case-id demo
```

模型只做一件事：逐段翻译。其余步骤由脚本完成。调用方是执行本 Skill 的那个 Agent 自己，不是外部服务。

```text
1. 跑第一遍 → status=needs_translation，给出待译段数与 targets.jsonl 路径
2. 自己按 rules/translate_rules.md 逐段翻译，写 /app/work/targets.jsonl
   一行一条 JSON，字段只有两个：{"uid": "P001S002", "text": "译文"}；uid 取自 segments.jsonl
3. 跑第二遍并带 --targets → status=success 或 partial
4. 仍为 needs_translation 则回到 2；它不是交付态，超过 3 轮记 partial，在 gaps 写未译 uid
```

## 4 流程
这节回答什么：每一步归脚本还是归模型。

| 步 | 脚本 | 花模型 | 做什么 |
|-|-|-|-|
| 0 读输入 | `run.py` | 否 | 读 `case.json` 的 `case_id` 与 `output_contract`；嗅探 pdf/docx/txt/md/tex |
| 1 切段 | `scripts/segments.py` | 否 | 空行分段、句末切句；写 `uid`=P001S002、`page`、`protected` |
| 2 术语 | `run.py` | 否 | 用 case 目录的 `glossary.md`/`terms.tsv`/`glossary.txt` 或 `--glossary` |
| 3 翻译 | `rules/translate_rules.md` | 是 | 逐段译，产出 `targets.jsonl` |
| 4 核验 | `scripts/verify.py` | 否 | V01–V09 逐段判，写 `verify.json` |
| 5 落盘 | `run.py` | 否 | `bilingual.md`、`segments.jsonl`、`glossary.md`、`verify.json`、`result.json` |

## 5 判定口径
这节回答什么：每个码的真实阈值，取自 `scripts/verify.py`。

| 码 | 检查 | 判定 |
|-|-|-|
| V01 | 数字与单位 | `protected.numbers` 每项去逗号与空格后须在译文出现；纯数字另比对只留数字的形式 |
| V02 | 引用标记 | `[n]`、`(Author, 2020)` 逐项在标点归一后的译文里原样出现 |
| V03 | 公式 | `$…$` 或 `\(…\)`（长度 1–80 字符）逐项原样出现 |
| V04 | 图表编号 | fig/figure/table/eq/equation 加编号（可带 1 位尾字母）原样出现 |
| V05 | 否定关系 | 仅当原文 `isascii()` 为真；原文与译文的否定词有无不一致即报，不比个数 |
| V06 | 术语 | 术语表 `term` 在原文出现（忽略大小写）时，`target` 须是译文子串 |
| V07 | 未定标记 | 译文命中 `[待确认]`、`待核`、`⟦…⟧`、`TODO`、`TBD` → 转人工确认 |
| V08 | 长度比 | len(译文) / max(1, len(原文)) > 2.2 |
| V09 | 空译 | 译文 strip 后为空 |

无码项同样参与 `verify.json.ok`：

| 字段 | 判定 |
|-|-|
| `missing` / `duplicates` / `extra` | 缺译、同一 `uid` 多行、多余 `uid`：任一非空即不合格 |
| `order_preserved` / `ok` | 顺序与 `segments.jsonl` 不一致即 false；problems、`missing`、`duplicates` 全空才 ok |

## 6 输出契约
这节回答什么：`result.json` 每个字段是什么，平台三字段各自映射到哪个产物。

### 6.1 字段
| 字段 | 内容 |
|-|-|
| `case_id` | 取 `case.json` 的 `case_id`；缺失用 `--case-id`（默认 `case`），并在 `gaps` 记一条 |
| `status` | `success` / `partial` / `blocked` / `needs_translation` 之一，见 6.3 |
| `source` | `path`、`n_pages`、`n_paras`、`n_segments`、`lang`（首段含 CJK 记 `zh`，否则 `en`） |
| `deliverables` | 每项 `kind`/`path`/`bytes`；`verify.json` 仅在已提交译文时出现 |
| `evidence` | `unit=segment+page`；每段 `uid` 与 `page` 可回指原文 |
| `checks` | `完整性`、`术语一致`、`数字引用公式`、`否定关系` 四项的 `ok` 与 `detail` |
| `gaps` | 每条可执行，如"缺译 3 段""含未定标记 2 段，需人工确认" |
| `output_contract` | 平台字段名 → 产物的显式映射，见 6.2 |
| `output_contract_echo` | 每字段填充状态：`full` / `partial` / `pointer` |

### 6.2 平台字段 → 产物
平台本课题必填字段为：阅读/解析结果、证据与边界、运行状态。逐字段按下表填，不做关键词猜测。

| 平台字段 | 填什么 | 产物 | echo |
|-|-|-|-|
| 阅读/解析结果 | `bilingual.md` 路径 + 段数 + 语言 + 段落覆盖说明 | `bilingual.md`、`segments.jsonl` | `full` |
| 证据与边界 | `evidence.unit=segment+page` + 每段 `uid`/`page` + 全量 `gaps` | `segments.jsonl`、`verify.json`、`gaps` | `full`；含 `page=0` 的段降为 `partial` |
| 运行状态 | `status` + 6.3 的平台状态词 + `checks` 四项 | `result.json` 本体 | `full` |

映射按字段名逐字匹配：字段名不在上表且未被识别时，才回落为 `pointer` 并标 `partial`。

### 6.3 状态词对齐
| 代码 `status` | 平台状态 | 条件 |
|-|-|-|
| `success` | 成功 | `checks` 四项全 ok 且 `gaps` 为空 |
| `partial` | 部分完成 | 有 `gaps`，且每条 `gaps` 可执行 |
| `blocked` | 无法判断 | 找不到原文或不可解析；不伪造译文 |
| `needs_translation` | 不交付 | 未提交 `targets.jsonl`；按 §3 循环，3 轮后才转 `partial` |
| 无对应码 | 拒答 | 代码不发拒答；输入越界（无文字层、纯扫描件）按 `blocked` 交付并写明原因 |

交付前自查：按 6.2 的表逐字段确认，`output_contract_echo` 里不应出现未解释的 `pointer`。

## 7 限制
这节回答什么：哪些情况不覆盖，以及已核对代码但未修的缺陷。

- 只处理有文字层的文本；PDF 无文字层时 `n_segments=0`，不许当成已翻译。
- 非 PDF 输入页码恒为 1（占位值）；`verify.json` 没有"译法依据"字段。
- 译文流畅度与学术语体由第 3 步模型负责；术语表只读 TSV 三列，不读 `.bib` 或 Excel。

已知缺陷（已核对代码，尚未修）：

| 编号 | 缺陷 | 后果与处置 |
|-|-|-|
| a | `scripts/segments.py` 的 `page_of()` 对跨页段落返回 0 | `page=0` 只能读作"未知"；不得称其为原 PDF 页码，证据边界降 `partial` |
| b | 无栏序、页眉页脚、表头判定 | 不支持跨栏；多栏 PDF 段序不可信，记 `gaps` |
| c | 未给术语表时 `glossary.md` 译名列为空 | V06 静默空转（`glossary_terms` 为 0 即空转）；须显式给术语表，否则"术语稳"无产物 |
| d | V05 被 `src.isascii()` 门控 | 含 CJK 的原文段不做否定核对，须人工看 |

## 8 异常处理
这节回答什么：每种坏输入留给谁，状态怎么写。

| 情况 | 行为 | 状态 |
|-|-|-|
| 找不到原文文件 | `run.py` 打印 blocked 并退出 1，不写 `result.json`；须自己补一份 `status=blocked` 的 `result.json`，写 `case_id` 与 `gaps` | `blocked` |
| 原文为空或切不出段 | `n_segments=0`，`gaps` 记"原文为空或无法切段" | `partial` |
| 缺 `case.json` 或缺术语表 | 用 `--case-id`，`output_contract` 为空；生成候选表、译名留空；`gaps` 自己补记 | `partial` |
| 译文缺段或含未定标记 | `missing` 非空则 `checks["完整性"].ok` 为 false；V07 计入 `gaps` | `partial` |
| 未提交 `targets.jsonl` | 停在待译，`next_step` 给路径 | `needs_translation` |
| 无文字层或纯扫描 | 不 OCR，`n_segments=0`，`gaps` 写明缺文字层 | `blocked` |

## 9 自查与预算
这节回答什么：交付前跑什么命令，请求数上限怎么算。

```bash
python scripts/verify.py --segments output/segments.jsonl --targets work/targets.jsonl --glossary output/glossary.md --out work/recheck.json
python -c "import json;r=json.load(open('output/result.json',encoding='utf-8'));print(r['status'],r['checks'],r['gaps'])"
python -c "import json;r=json.load(open('output/result.json',encoding='utf-8'));print(r['output_contract'],r['output_contract_echo'])"
```

平台形态把 output/ 换成 /app/output、work/ 换成 /app/work，targets 用第 2 步写出的 work/targets.jsonl。逐条自查：

| 步 | 判据 |
|-|-|
| 1 | `status` 为 `success` 或 `partial`；`needs_translation` 不是交付态 |
| 2 | `checks` 四项 `ok` 全为 true |
| 3 | `verify.json.counts` 为空，`missing` 与 `duplicates` 为空 |
| 4 | `output_contract_echo` 各字段均为 `full`，无 `pointer` |
| 5 | `bilingual.md` 每段都有 `uid` 与页码；`page=0` 的段在 `gaps` 出现 |
| 6 | 抽查 3 段：`uid` → 原文 → 译文，`protected` 片段逐字出现 |

预算：切段、核验、落盘是本地计算，不消耗模型请求。模型请求只发生在第 3 步逐段翻译。请求数 = `ceil(段数 / 批量)`，批量取 8–12 段/批。段数 ≤ 512 时 `ceil(512/8)=64`，等于 64 次上限；段数 > 512 时只覆盖前 512 段，其余写进 `gaps` 并交 `partial`。单案例墙钟上限 900 s；6 段示例本地三段合计 < 1 s（CPU，无模型请求）。

## 10 题目对齐
这节回答什么：四个评分轴各挂哪个机制、哪个字段、哪条自查。

| 评分轴 | 机制 | 输出字段 | 自查步 |
|-|-|-|-|
| 术语稳 | 术语表锁定译名，V06 逐段核对；空表即空转（§7 c） | `checks["术语一致"]`、`verify.json.counts` | 第 9 节第 3 条 |
| 段落全 | 空行分段加句末切句，`uid` 一一对应；缺译、重复、错序三项计数 | `checks["完整性"]`、`verify.json.missing` | 第 9 节第 2 条 |
| 关系准 | V01 数字单位、V02 引用、V03 公式、V04 图表号、V05 否定有无 | `checks["数字引用公式"]`、`checks["否定关系"]` | 第 9 节第 3 条 |
| 可核验 | 每段 `uid` 加 `page`；`page=0` 标未知；V07 未定标记进 `gaps` | `evidence`、`gaps` | 第 9 节第 5、6 条 |
