---
name: polish-academic
description: 适用于润色或核验学术语句的场景：输入原稿与改后稿，检出语法/冗余/口语/长句/名词化问题，并核对数字、单位、引用、图号、否定与限定强度是否被改坏。脚本不调用模型，改写由调用方完成。
---

# 学术语言润色：检测、核验、留痕

## 1 用途

对一份学术稿做两件事：把硬问题指出来，把改写有没有改坏查出来。

改写本身交给调用方模型。脚本只做能被机器判定的部分：模式检测、四个守恒项比对、术语一致性回扫。

## 2 适用条件

| 适合 | 不适合 |
|-|-|
| 有文本原稿（`.md` / `.txt`） | 扫描件、图片型 PDF |
| 有或没有改后稿都能跑 | 需要通篇重写并改变论证结构 |
| 中英文学术语句 | 文学创作、口语对话 |

## 3 调用

```bash
# 只出问题清单
python run.py --source paper.md --out output

# 检测 + 核验（推荐）
python run.py --source paper.md --polished polished.md \
  --glossary glossary.md --out output --work work

# 指定 case_id
python run.py --source paper.md --polished polished.md --case-id polish-A --out output
```

三个路径参数都是显式的，不扫描目录猜文件。

退出码：0 正常；1 原文不存在（`status=blocked`）。

## 4 流程

| 步 | 做什么 |
|-|-|
| 1 读原稿 | 按参数读取，缺失即 blocked |
| 2 检测问题 | D01–D10 逐条扫，记录片段、理由、置信度 |
| 3 读改后稿 | 未给则只出清单，状态 `needs_polish` |
| 4 四级守恒 | 数字、单位、引用、图表指向，原文与改后同口径各数一遍 |
| 5 强度守恒 | 加强词与留有余地词，改后任一减少即报 K02 |
| 6 术语回扫 | 原文出现术语时，改后必须用同一译名 |
| 7 导三份 | `polished.md`、`changes.md`、`issues.json`、`result.json` |

## 5 判定口径

| 码 | 判据 | 对应关注点 |
|-|-|-|
| D01–D10 | 语法、被动、模糊加强、名词化、冗余、口语、长句、时态、中式表达、绝对化 | 改得对 |
| K01 | 数字/单位/引用/图表指向数量变化 | 没改坏 |
| K02 | 加强词或留有余地词减少 | 没改坏 |
| T01 | 术语译名与术语表不一致 | 说得像 |

`status`：`success`（有改后稿且无问题）、`partial`（有改后稿但有问题）、`needs_polish`（只出清单）、`blocked`（无原稿）。

## 6 输出契约

| 字段 | 回填内容 | 完整度 |
|-|-|-|
| 目标产物 | `polished.md` 的路径、字符数、句数、是否回填原稿 | full：改后全文，未提供时回填原文 |
| 事实与来源映射 | 逐条 `{码, 类型, 原文片段, 段, 句, 理由, 置信度}` + 术语表条数 + 改后稿计数 | full：逐条可回原稿检索 |
| 版本与边界 | `result.json` 的 `gaps`/`problems`/`drift` | full：缺口与漂移逐项列出 |

`case_id` 取 `--case-id`，其次取原稿同目录 `case.json` 的 `case_id`，再缺用原稿文件名。`output_contract` 也读同一份 `case.json`，逐字段回显。

## 7 限制

- 检测靠正则，写得隐晦的语法问题会漏；命中即报，不保证穷尽。
- 数字比对按数量，不校位置；数字对调但总数不变时检不出。
- 术语判定是子串匹配，不做词形还原。
- 不判断改写后的句子是否更通顺，只判断有没有改坏。
- 不调用模型：不注入改后稿时只输出问题清单。

## 8 异常处理

| 情况 | 行为 | 状态 |
|-|-|-|
| 原稿不存在 | 写 blocked 的 result.json，退出 1 | `blocked` |
| 未给改后稿 | 只出清单，`gaps` 写明 | `needs_polish` |
| 未给术语表 | 跳过术语回扫，`gaps` 写明 | 视改后稿而定 |
| 数字或强度漂移 | 报 K01/K02，列出两侧数量 | `partial` |

## 9 自查与预算

```bash
python run.py --source examples/paper.md --polished examples/polished.md --glossary examples/glossary.md --out o1     # success
python run.py --source examples/paper.md --polished examples/polished_bad.md --glossary examples/glossary.md --out o2  # K02
python run.py --source examples/paper.md --out o3                                                                      # needs_polish
```

1. 三例状态分别为 `success`、`partial`、`needs_polish`。
2. `o2` 的 `checks` 里有 K02，detail 写明加强词 2→0。
3. `changes.md` 表格行数 = 问题数 + 2（表头与分隔）。

预算：零模型请求，毫秒级。

## 10 题目对齐（写作02）

| 关注点 | 机制 | 落在哪 |
|-|-|-|
| 改得对 | D01–D10 模式检测，中英各一套 | `issues.json`、`changes.md` |
| 说得像 | 术语表锁定后回扫改后稿 | T01 |
| 没改坏 | 数字/单位/引用/图号 + 加强/留有余地两侧同口径计数 | K01、K02、`drift` |
| 讲得清 | 每条问题给码、类型、片段、理由、置信度 | `changes.md` |

## 11 输入标识与证据

`case_id` 取 `--case-id`，缺省用原稿文件名，写进 `result.json` 与 `issues.json` 顶层。

证据单位（`evidence`）：`code + span`（问题码 + 原文片段）+ `paragraph` / `sent`（段号与句号）。`changes.md` 每行的「位置」列与 `result.json` 的 `事实与来源映射.逐条` 给出同一组字段，可按片段与位置回原稿检索。字符级偏移不提供（润色问题的边界本身有争议）。

### 检查码全表

| 码 | 判据 |
|-|-|
| D01 | 主谓一致（data/results 用 was） |
| D02 | 一句内两处被动 |
| D03 | 模糊加强词（clearly/very/非常） |
| D04 | 名词化多层 of 结构 |
| D05 | 冗余短语（in order to / due to the fact that） |
| D06 | 口语词（a lot of / big / stuff） |
| D07 | 长句超过 40 词或 80 字 |
| D08 | 同句时态混用 |
| D09 | 中式表达（进行…研究 / 具有重要意义 / 值得一提的是） |
| D10 | 绝对化（always / never / 必然 / 完全） |
| K01 | 数字/单位/引用/图表指向数量变化 |
| K02 | 加强词或留有余地词减少 |
| T01 | 术语译名与术语表不一致 |

`output_contract` 给了就逐字段回显：认得的写实际内容，认不得的置 null 并记入 `gaps`。没给也照样输出「目标产物 / 事实与来源映射 / 版本与边界」三项。
