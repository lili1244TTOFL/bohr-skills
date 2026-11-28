---
name: polish-academic
description: 适用于润色、核验或对照评测学术文本：输入原稿与改后稿，逐句给出原文/改文/修改类型/理由，并核对数字、单位、引用、图号、公式、否定与限定强度有没有被改坏，另查术语译名、缩写首现与目标文体越界。脚本不调用模型，改写由调用方完成。
---

# 学术语言润色：逐句留痕、守恒核对、对照评测

## 1 用途

对一份学术稿做四件事：指出现有硬问题、把改写有没有改坏查出来、把每一处改动记成可复核的逐句记录、把「改后稿 vs 基线稿」放在同一把尺子上比。

改写本身交给调用方模型。脚本只做能被机器判定的部分：模式检测、六项守恒、术语与缩写的全篇回扫、文体越界、最小编辑度量。

## 2 适用条件

| 适合 | 不适合 |
|-|-|
| 有文本原稿（`.md` / `.txt` / `.tex`） | 扫描件、图片型 PDF |
| 同语润色、中译英、英译中 | 需要改变论证结构或重写全文 |
| 有或没有改后稿都能跑 | 文学创作、口语对话 |
| 有术语表时做译名与写法核验 | 无术语表时只能跳过译名核验 |

## 3 调用

```bash
# 1 只出问题清单 + 最小词面修复（无改后稿）
python run.py --source examples/draft_cn.md --out out1            # status=needs_polish

# 2 检测 + 逐句核验（推荐）
python run.py --source examples/paper.md --polished examples/polished.md \
  --glossary examples/glossary.md --out out2 --case-id polish-A

# 3 中译英 + 三列术语表
python run.py --source examples/case_cn/source_cn.md \
  --polished examples/case_cn/polished_cn.md \
  --glossary examples/case_cn/glossary_cn.md --out out3

# 4 与基线稿同尺对照
python run.py --source examples/paper.md --polished examples/polished.md \
  --glossary examples/glossary.md --baseline examples/baseline_weak.md --out out4
```

| 参数 | 含义 | 缺省行为 |
|-|-|-|
| `--source` | 原稿，必填 | 不存在即 `blocked`，退出码 1 |
| `--polished` | 改后稿 | 不给则跑内置最小词面修复；改了 0 处判 `needs_polish` |
| `--glossary` | Markdown 两列/三列术语表 | 不给则跳过译名核验并写进 `gaps` |
| `--baseline` | 基线稿，用同一 `evaluate()` 跑对照 | 不给则 `coverage.md` 里该项记 `missing` |
| `--style` | `journal` / `dissertation` / `grant` / `review-response` / `abstract` | 读 `case.json`，再缺按文件名与开头推断并记 `gaps` |
| `--out` | 产物目录 | `output` |
| `--case-id` | 输入标识 | 读同目录 `case.json`，再缺用原稿文件名 |

三个文件参数都是显式路径，不扫描目录猜文件。退出码：0 正常；1 原稿不存在（`blocked`）。

平台形态：原稿与 `case.json` 同目录时自动读入 `case_id`、`output_contract` 与体裁；产物写到 `--out`（平台为 `/app/output`），中间件可写 `--work`。

## 4 流程

| 步 | 做什么 | 落成 |
|-|-|-|
| 1 读原稿 | 按参数读取，识别 BOM/GBK/UTF-8；缺失即 `blocked` | `输入标识` |
| 2 切结构与公式 | 段落、句子（缩写点与公式内不切）、公式区间单独标记 | `段落覆盖` |
| 3 检测问题 | D01–D10 逐条扫，记录片段、句文本、理由、置信度 | `issues.json` |
| 4 读改后稿 | 未给则做最小词面修复，修复 0 处即 `needs_polish` | `polished.md` |
| 5 六项守恒 | 数字、单位、引用、图表指向、公式、否定，两侧同口径先比数量再比集合 | `changes.md` §守恒 |
| 6 强度比对 | 加强词与留有余地词，削弱报 K02、凭空加强报 K07 | `changes.md` §核验 |
| 7 术语与缩写 | 术语表逐条回扫；缩写首现是否保留全称；单位写法是否统一 | `bilingual.md` |
| 8 逐句留痕 | 句级对齐，每处改动给原文、改文、类型、理由、词级变更 | `changes.md` §逐句 |
| 9 最小编辑 | 相似度低于阈值报 M01；有改动但无检测码报 M02 | `coverage.md` |
| 10 导六份 | `polished.md`、`changes.md`、`bilingual.md`、`coverage.md`、`issues.json`、`result.json`（有 `--baseline` 再导 `compare.md`） | 产物目录 |

## 5 判定口径

阈值只许收紧不许放宽：长句 40 词（中文 80 字）、改动判定 0.995、整句重写判定 0.60。

| 码 | 判据 |
|-|-|
| D01 | 主谓一致（data/results 用 was） |
| D02 | 一句内两处被动 |
| D03 | 模糊加强词（clearly/very/非常） |
| D04 | 名词化多层 of 结构 |
| D05 | 冗余短语（in order to / due to the fact that） |
| D06 | 口语词（a lot of / big / stuff） |
| D07 | 长句：拉丁文超 40 词，中文超 80 字 |
| D08 | 同句时态混用 |
| D09 | 中式表达（进行了…研究/分析/探讨、具有重要意义、值得一提的是） |
| D10 | 绝对化（always / never / 必然 / 完全） |
| K01 | 数字/单位/引用/图表指向/公式/否定数量变化 |
| K02 | 限定强度被削弱（加强词或留有余地词减少） |
| K03 | 数字或单位数量相同但内容被换掉 |
| K04 | 引用编号数量相同但编号被换（[2]→[3]） |
| K05 | 图表指向数量相同但编号被换（Fig. 2→Fig. 3） |
| K06 | 否定数量相同但否定词被换（not→no） |
| K07 | 结论被悄悄加强（改后新增加强词，原文没有） |
| T01 | 译名不一致：接口语言跨语言时，原文术语应按术语表译名出现 |
| T02 | 术语表内部冲突：同一原文对应多个译名 |
| T03 | 术语原样残留：改后稿同时出现原词与译名，写法混用 |
| A01 | 改后稿首现缺全称（原文首现给了「全称（缩写）」） |
| A02 | 未定义先用：缩写先裸用后定义，或全篇未定义 |
| A03 | 一缩多义：同一缩写对应多个全称 |
| A04 | 同一符号多种写法（mL/ml、°C/℃、μM/uM） |
| S01 | 目标文体越界（缩略形式、宣传词、感叹号等，规则见 `rules/style_profiles.json`） |
| M01 | 疑似整句重写：句相似度低于 0.60 |
| M02 | 无检测码的改动：改了但没有码与理由支撑 |
| M03 | 删句：改后稿没有对应句 |
| M04 | 译句：跨语言改写，逐句对应关系在 `changes.md` |

`status`：`success`（有改后稿且无问题）、`partial`（有改后稿但有 K/T/A/S/M 问题）、`needs_polish`（无改后稿且无可机械修复项）、`blocked`（无原稿）。

## 6 输出契约

| 字段 | 回填内容 | 完整度 |
|-|-|-|
| 目标产物 | `polished.md` 的路径、字符数、句数、段落数、改动句数；改后稿来源（外部/内置词面规则/未改写） | full：改后全文，未提供时回填原文并标注 |
| 事实与来源映射 | 逐条 `{码, 类型, 原文片段, 段, 句, 句文本, 理由, 置信度}` + 逐句修改 `{位置, 原文, 改文, 修改类型, 修改理由, 词级变更}` + 术语对照 + 缩写首现 + 守恒核对 + 规则命中 | full：每条都能回原稿检索 |
| 版本与边界 | `gaps`（材料缺口）、`problems`（K/T/A/S/M）、`人工确认项`、`drift`、`改动摘要` | full：缺口与漂移逐项列出 |
| 输入标识 | 原稿 sha256 前 16 位、编码、字数、段数、句数、术语表条数与冲突、基线、目标文体及来源 | full：对照条件可复核 |
| 段落覆盖 | 每段类型、字数、句数、命中码、已改动句数、处理状态 | full：逐段 |
| 覆盖清单 | 题面处理项 → covered/partial/missing + 证据指针 | full：14 项 |
| 基线对照 | 同一函数、同一术语表、同一阈值下的指标表与结论 | 有 `--baseline` 时 full |

`output_contract` 给了就逐字段回显：认得的写实际内容，认不得的置 null 并记入 `gaps`。没给也照样输出「目标产物 / 事实与来源映射 / 版本与边界」三项。

## 7 限制

- 检测靠正则，写得隐晦的语法问题会漏；命中即报，不保证穷尽。
- 数字比对按 token 比较，不改位置；「15% 与 25% 对调」能查出，但两个相同数字互换位置查不出。
- 术语判定是子串匹配，不做词形还原；同语润色只锁写法，译名一致性只在跨语言时判。
- 缩写识别依赖「全称（缩写）」形式；只写缩写不写全称的稿子只能报 A02。
- 跨语言时强度只比数量，词形需人工核对。
- 不判断改写后的句子是否更通顺，只判断有没有改坏。
- 不调用模型：不注入改后稿时只做词面修复，不重写句子。

## 8 异常处理

| 情况 | 行为 | 状态 |
|-|-|-|
| 原稿不存在 | 写 `blocked` 的 `result.json`，退出码 1 | `blocked` |
| 改后稿不存在 | 记入 `gaps`，改用内置最小词面修复 | 视修复结果而定 |
| 未给改后稿且无可机械修复项 | 只出清单，`polished.md` 回填原文并标注未改写 | `needs_polish` |
| 未给术语表 | 跳过译名核验，`gaps` 写明 | 视改后稿而定 |
| 术语表内部冲突 | 取首条译名继续，报 T02 并列入人工确认 | `partial` |
| 术语表条目零命中 | 覆盖清单里「术语表建全」降为 `partial` | 不变 |
| 原稿非 UTF-8 | 依次试 UTF-8/BOM/GB18030 解码，编码写进 `输入标识`，仍乱码则记 `gaps` | 继续 |
| 公式与引用混杂 | 公式区间不参与改写、不切句；公式数量变化报 K01 | 继续 |
| 目标文体不明 | 按文件名与开头推断并记 `gaps`；推断不出则跳过文体检查 | 继续 |
| 长文本 / 分章节 | 逐段逐句处理，`段落覆盖` 每段一行；重复运行结果稳定（`scripts/selftest.py` 的确定性用例） | 继续 |
| 数字或强度漂移 | 报 K01/K02/K03–K07，列出两侧数量或集合 | `partial` |

材料缺失、无法判断与运行失败分别落在 `gaps`、`uncertainty`、`status` 三处，不合并成一句话。

## 9 自查

```bash
python scripts/selftest.py            # 12 个用例：状态、码、公式保护、编码、确定性
python run.py --source examples/paper.md --polished examples/polished.md \
  --glossary examples/glossary.md --out o1                       # success
python run.py --source examples/paper.md --polished examples/polished_bad.md \
  --glossary examples/glossary.md --out o2                       # partial，K02
python run.py --source examples/draft_cn.md --out o3             # needs_polish
```

1. 三例状态分别为 `success`、`partial`、`needs_polish`。
2. `o2` 的 `checks` 里有 K02，detail 写明加强词减少 `{'clearly': 1, 'very': 1}`。
3. `o1` 的 `changes.md` §逐句 3 行 = 改动句 3 句；`coverage.md` 覆盖项 14 条。
4. `o3` 的 `polished.md` 与原稿逐字相同，`目标产物.内容` 写明未改写。

自查表与验收口径见 `rules/rubric.md`。预算：零模型请求，单次毫秒级。

## 10 题目对齐（写作02）

| 关注点 | 机制 | 落在哪 |
|-|-|-|
| 改得对 | D01–D10 模式检测，中英各一套 | `issues.json`、`changes.md` |
| 说得像 | 术语表锁定后回扫改后稿（T01–T03）；缩写首现与单位写法（A01–A04）；目标文体越界（S01） | `bilingual.md`、`changes.md` |
| 没改坏 | 六项守恒先比数量再比集合（K01、K03–K06）；强度削弱与凭空加强分开报（K02、K07） | `changes.md` §守恒 |
| 讲得清 | 逐句原文/改文/类型/理由/置信度/词级变更（M02）；整句重写度量（M01） | `changes.md` §逐句 |
| 可对照 | 同一 `evaluate()` 跑基线与改后稿，条件写进 `基线对照.条件` | `compare.md` |

## 11 输入标识与证据

`case_id` 取 `--case-id`，其次读原稿同目录 `case.json` 的 `case_id`/`id`，再缺用原稿文件名；写进 `result.json` 与 `issues.json` 顶层。输入标识另含原稿 sha256 前 16 位、编码、字数、段数、句数，供对照时确认「同一份原文」。

证据单位（`evidence`）：`code + span`（问题码 + 原文片段）+ `paragraph` / `sent`（段号与句号）+ 句文本。`changes.md` 每行「位置」列与 `result.json` 的 `事实与来源映射.逐条`、`.逐句修改` 给出同一组字段，可按片段与位置回原稿检索。字符级偏移不提供（润色问题的边界本身有争议）。

## 12 维护与复用

| 文件 | 作用 |
|-|-|
| `run.py` | 唯一入口；检测、守恒、术语、缩写、文体、最小编辑、产物渲染 |
| `scripts/selftest.py` | 12 个用例的自检脚本，改动后先跑它 |
| `rules/style_profiles.json` | 五种文体的禁用表达与理由；加一条不用改 `run.py` |
| `rules/glossary_governance.md` | 术语表格式、冲突裁决、跨章节漂移的处理流程 |
| `rules/rubric.md` | 自查评分表：五维度逐项判据与验收命令 |
| `examples/` | 英文摘要、中文摘要、中译英、弱基线四组示意输入（标明为示例） |

版本 4.0.0：相对 3.x 增加逐句修改说明、逐段对照、覆盖清单、缩写与文体检查、集合级守恒、基线对照；`needs_polish` 改为「未做任何改写」才置。只依赖 Python 3.10+ 标准库，无网络、无模型调用。
