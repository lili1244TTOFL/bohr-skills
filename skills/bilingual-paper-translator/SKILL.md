---
name: bilingual-paper-translator
description: 适用于把科研论文（PDF 文字层或 .txt/.md）译成中文并保留可核验性的场景：输入原文与术语表，输出逐段逐句双语对照、术语锁定表、数字/单位/引用/公式漂移核验与需人工确认清单。脚本不调用模型，译文由调用方按 brief 回填；缺译文时只交术语锁定草案并标注未译。
---

# 文献翻译：段句对齐、术语锁定、标记保护、译后核验

## 1 用途

把原文按段、按句切开，锁住术语，把译文按段号回填，再机器核一遍有没有改坏；产出可直接复核的
双语对照，而不是一段通顺大意。

四个关注点都落在机器能判的地方：术语稳（术语表锁定 + 全篇回扫）、段落全（段号与区间一一对应，
不漏段、不重复、不错位）、关系准（逐段比对数字、单位、引用、图表与公式编号、否定与限定）、
可核验（每段每句回指原文区间，译不准的显式标注）。脚本只做能判的事，不调用模型。

## 2 适用条件

| 适合 | 不适合 |
|-|-|
| 有文字层的 `.pdf` / `.txt` / `.md` | 扫描件（无文字层）：先用平台 ocr-tool 转文本 |
| 有或可建一份 Markdown 术语表 | 无术语表且术语密集（一致率无法核验，只交译前底稿） |
| 需要逐段、逐句对照与引用核对 | 只要一段大意或摘要改写 |
| 长文跨页、跨栏、跨章节接续 | 图表与公式为图片且不可 OCR |

## 3 调用（三步，两阶段交接）

```bash
# 1) 首次：切段切句、抽术语与保护标记，出 translation_brief.md（每段上下文+必用译名+必须保留的标记）
python run.py --input /input --work /app/work --out /app/output
# 2) 模型按 brief 逐段翻译，写 targets.txt，每段前加 `### S001` 段号标记
# 3) 回填：重跑，得到最终双语对照与 Q01–Q06 核验
python run.py --input /input --targets targets.txt --work /app/work --out /app/output
```

目录内自动找原文（优先 `case.json` 的 material/paper/source 字段，其次目录内 `.pdf`/`.tex`/`.docx`/`.md`/`.txt`，
**不会**把 `task.md`、`case.json`、`glossary*`、`targets*` 当原文）、`glossary*.md`、`targets*.txt`、
`acknowledged*.json`。退出码：0 正常；1 找不到可切分的原文（`status=blocked`，同时写 `missing_input.md`）。

## 4 流程

| 步 | 做什么 |
|-|-|
| 1 取原文 | case.json 指认优先；PDF 依次试 `pdftotext -layout`、pypdf、pdfminer，保留分页符作页码依据 |
| 2 切段 | 按空行切，保留 `char_start/char_end`；空行过少或单段超 3000 字符改按句群切（≤4 句/≤1200 字符） |
| 3 定角色 | 标题/小标题/摘要/正文/图注/表题/表格/公式/参考文献条目，并记章节与页码 |
| 4 切句 | `S001.01` 编号；`.!?` 后接空白才切，`。！？；` 直接切，缩写与小数点不切 |
| 5 读术语表 | 原文｜译名｜禁用译名｜备注，格式见 `rules/conventions.md` |
| 6 抽标记 | 数字、数字-单位、文献引用（`[1]`、`(Smith et al., 2019)`）、图表编号、公式编号、行内公式 |
| 7 带上下文 | 每段记上段末尾 80 字、下段开头 80 字，供逐段独立翻译 |
| 8 配译文 | `### S001` 段号标记优先，没有标记退回按空行顺序配对 |
| 9 跑核验 | Q01–Q06 逐段比，见 §5 |
| 10 导产物 | `bilingual.md`、`segments.json`、`verify.json`、`result.json`、`translation_brief.md`、`translation_worklist.md`，有问题另出 `retranslate.md` |

## 5 判定口径

| 码 | 判据 | 关注点 |
|-|-|-|
| Q01 | 术语一致率 < 1（源段含术语，译文未见指定译名） | 术语稳 |
| Q02 | 段区间交叠、重复段、非空白覆盖率 < 1、外部译文段数 ≠ 原文段数 | 段落全 |
| Q03 | 逐段数字多重集、否定计数、限定计数不一致 | 关系准 |
| Q04 | 逐段「数字+单位」配对不一致（中英单位先归一） | 关系准 |
| Q05 | 出现术语表第 3 列的禁用译名（被正确译名覆盖的不算） | 术语稳 |
| Q06 | 图表编号、公式编号、行内公式、公式核心表达式未原样保留 | 关系准 |

六条判据都是**完全相等**，没有可放宽的阈值；命中项只能改译文，或按 `acknowledged.json` 逐条写
理由确认（格式见 `rules/conventions.md`，脚本从不自己写这个文件）。

`status`：`success`（每段都有非残留译文且六码无未确认命中）、`partial`（有译文但存在未确认命中、
未译残留或缺段，`status_reason` 写明原因）、`blocked`（找不到可切分原文，另写 `missing_input.md`）。

## 6 输出契约

| 字段 | 产物 | 完整度 |
|-|-|-|
| 阅读/解析结果 | `bilingual.md`（逐段原文、译文、句对齐表、译法依据、标注）加段数句数等计数 | full：角色分布、术语率、草案数、未译段 |
| 证据与边界 | `segments.json` 的段与句 char 区间、页码、上下文；`verify.json.manual_check` | full：段号与句号加字符区间与页码，可回原文截取 |
| 运行状态 | `result.json.运行状态`（status、reason、接管文件） | full：success、partial、blocked 加原因与人工接管位置 |

`case.json` 给了 `output_contract` 就逐字段回显到 `result.json.output_contract`（同名、别名、子串
三级匹配；对不上的写 `present=false` 并进 `gaps`），没给也照样输出上面三项。`result.json` 另带
`change_records`（每步做了什么、替换几处、草案几段、确认几条）与 `manifest`（输入输出文件
bytes 与 sha256、版本、参数、耗时），供重跑复核。

## 7 限制

- 术语表只认 Markdown 表格，`.tsv` 不上交（平台预检拒该扩展名）。
- 术语是子串匹配，不做词形还原：`working memory` 与 `working memories` 视为不同写法。
- 单位只归一常见单位（见 `rules/conventions.md` §5），未收录单位不折算、不猜。
- 否定与限定按词表计数（`unclear`、`unknown` 计入否定；中文 `不同`、`未来`、`不仅` 等为例外），
  仍有误报可能，命中项进 `manual_check` 供核对。
- 不做指代消解：含代词或指示词的段进 `manual_check`，由人或模型确认指代对象。
- 不调用模型：缺译文时只交术语锁定草案（术语已换译名、语序未动），该段标「未译残留」，
  status 不会写成 success。
- 不 OCR：扫描件或文字层缺失时 blocked 并说明缺什么，不拿图像编造译文。
- 参考文献条目与公式按惯例原样保留：不参与术语回扫，也不计未译残留。

## 8 异常处理

| 情况 | 行为 | 状态 |
|-|-|-|
| 输入目录为空或无原文 | 写 `missing_input.md`（找过哪些字段与后缀、要补什么、怎么重跑），退出 1 | `blocked` |
| PDF 抽不到文字层（扫描件） | 同上，gaps 写明需先 OCR 或补文本层 | `blocked` |
| 只有 `.tex` / `.docx` | 说明需先转 `.txt/.md`（detex 或 pandoc），不猜内容 | `blocked` |
| 无外部译文 | 术语表最长优先替换补草案，按 `translation_brief.md` 逐段译；草案段标「未译残留」 | `partial` |
| 无术语表 | 跳过术语回扫，草案与原文同形，另交 `translation_worklist.md` 译前底稿 | `partial` |
| 译文段数不足或段号多余 | 报 Q02，缺段补草案并列进 `retranslate.md` | `partial` |
| 数字、单位、引用、图表、公式漂移 | 报对应 Q 码，列出两侧多重集，逐条进 `retranslate.md` 与 `manual_check` | `partial` |
| 译文与原文同形（未译） | 标「未译残留」，不写成成功 | `partial` |
| 已人工核对命中项 | 读入 `acknowledged.json`（必须写 reason），记进 `change_records` | 依剩余项而定 |

## 9 自查与预算

```bash
python run.py --input examples/case    --work w1 --out o1   # partial：S003 限定 2→1（Q03）
python run.py --input examples/plain   --work w2 --out o2   # partial：3 段未译残留 + 译前底稿
python run.py --input examples/full    --work w3 --out o3   # success：19 段/39 句，0 问题，术语率 1.0
python run.py --input examples/defects --work w4 --out o4   # partial：Q01–Q06 六码全触发
```

1. `bilingual.md` 的段号、句号与 `segments.json` 一致，字符区间可回原文截取。
2. `verify.json.problems` 与 `result.json.checks` 是同一份；`per_segment.ok` 与逐段命中项对得上。
3. `result.json.manifest.outputs` 的 sha256 与实际产物一致；重跑同一输入的段数、句数、标记数不变。
4. `examples/defects` 必须六码全触发，用来证明检出能力没被放水；它不是对照成绩。
5. 预算：零模型请求、毫秒级；产物是纯文本，`examples/full` 全部产物 < 100 KB。

## 10 题目对齐

| 关注点 | 机制 | 落在哪 |
|-|-|-|
| 术语稳 | 术语表锁定（含禁用译名）加全篇回扫，按段记是否用了指定译名 | Q01、Q05，`verify.json.term_consistency`，`bilingual.md` 术语表节 |
| 段落全 | 段号与字符区间一一对应，覆盖率、重复段、交叠、跨页段都可查 | Q02，`verify.json.continuity`，`segments.json` |
| 关系准 | 逐段多重集比数字、单位、引用、图表编号、公式，逐段比否定与限定计数 | Q03、Q04、Q06，`verify.json.per_segment` |
| 可核验 | 每段每句带区间与页码，逐段给译法依据、标注与待确认清单 | `bilingual.md`，`verify.json.manual_check`，`result.json.证据与边界` |

使用增益的官方对照由平台用固定案例生成；本 Skill 提供逐段锚点、可比计数与 `examples/defects`
自检样本（证明六码确实会触发），不冒充对照成绩。

## 11 输入标识与证据

`case_id` 取自 `case.json`，缺失时用输入目录名，写进 `result.json`、`verify.json`、`segments.json`
三处顶层。证据单位（`evidence`）是「段号 `S001` 加句号 `S001.01` 加字符区间加页码」：`anchors`
逐段给出 `char_start/char_end` 与句级区间，可按区间回原文截取段落与句子；页码在原文含分页符时
给出，否则为 `null` 不猜。参考文献条目、公式段、图表题注、表头在段角色里单列，便于核对完整性。

## 12 维护

版本 `run.py` 3.0.0、规则 `rules/conventions.md` v3.0.0；术语表可持续追加，判定线与单位归一表集中
在规则文件里，改判据要同步 §5 表与 `run.py`（机检会比对 doc 与 code 的码表）。

## 值不值得看，看这两个文件

| 想知道的 | 看哪 |
|-|-|
| 与现成工具比，边界在哪、做不到什么 | `docs/对比与边界.md` |
| 每条结论的出处 | `result.json` 里带出处的那几个字段，见上面的输出契约表 |

## 判断层：哪些交给调用方模型

脚本准备候选与锚点，判断由调用方模型做，脚本再核结果。本节说明本包的判断项。

| 判断项 | 口径 |
|-|-|
| 这段译文怎么写 | 术语按表锁定，句式按目标语习惯 |
| 这两个术语是不是同一概念 | 原文不同写法指向同一概念时统一 |

脚本切段、锁术语、数守恒；模型写译文；脚本核段号对齐、引用编号与否定词未丢。

判断结果写进 `result.json` 的 `判断层` 字段：每条含 `item`（判断项）、`value`（结论）、`basis`（依据，必须是原句子串）、`filled_by`（模型名或 caller）。未填写的项标 `pending`，不自行补结论。
