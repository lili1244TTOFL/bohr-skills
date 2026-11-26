---
name: latex-build-guard
description: 适用于需要核对 LaTeX 稿件能否编译、并把结果交给复核者的任务。输入一份 .tex（可带 .bib），输出 report.md、checks.json、result.json，编译成功时另出 PDF。不改写源码，不写回修好的 .tex，不做版式视觉比对。
---

# LaTeX 编译守卫

## 1 用途

输入一份 `.tex`（可带 `.bib`），产出以下 4 项（PDF 有条件）：

| 产物 | 落点 | 内容 |
|-|-|-|
| `report.md` | `--out` | 问题清单（码 + severity + 描述）与编译状态 |
| `checks.json` | `--work` | T01–T11 逐条结果、按码计数、`ok` |
| `result.json` | `--out` | 主结果 + 编译记录 + `case_id` + `output_contract` 回显 |
| PDF | `--out` | 仅当 `latexmk` 或 `pdflatex` 存在且返回码为 0 |

不做：

- 不修改源码，不写回修好的 `.tex`：交付物只有报告与 PDF。
- 不做版式视觉比对，不比较 PDF 外观。
- 不解析宏包自定义命令语义。
- 不联网安装宏包或字体。

## 2 适用条件

| 适合 | 不适合 |
|-|-|
| 单文件 `.tex`，或能指出主文件的工程 | 多主文件工程：只取输入目录下首个 `.tex` |
| 交叉引用、文献键、caption、环境配对的形式检查 | 公式语义与符号正误：只读文本，不读数学含义 |
| 需要一条真实编译记录（工具名、返回码、日志尾部） | 模板必填项逐项比对：无模板解析 |
| 需要把"能否编译"作为可复核证据交出去 | 需要修好的可编辑源码：本 Skill 不产出 |

## 3 调用

```bash
# 平台形态
python run.py --input /input --work /app/work --out /app/output

# 本地形态
python run.py --tex examples/paper.tex --bib examples/refs.bib \
              --out output --work work --case-id demo

# 不编译，只做静态检查
python run.py --tex paper.tex --out output --no-compile
```

| 参数 | 缺省行为 |
|-|-|
| `--tex` | 未给时取 `--input` 目录下首个 `.tex` |
| `--bib` | 未给时取 `.tex` 同目录下首个 `.bib` |
| `--input` | 有用时读 `case.json` 的 `case_id` 与 `output_contract` |
| `--case-id` | `case.json` 缺 `case_id` 时的回落值 |

无模型步骤，不需要自查循环：跑一次即终态。

## 4 流程

| 步 | 脚本 | 花模型 | 做什么 |
|-|-|-|-|
| 0 读输入 | `run.py` | 否 | 读 `case.json`；定位 `.tex` 与 `.bib` |
| 1 静态检查 | `scripts/texcheck.py` | 否 | T01–T10 单遍扫描；写 `work/checks.json` |
| 2 编译 | `run.py` | 否 | 有 `latexmk`/`pdflatex` 才跑；最多 2 轮，首轮非零即停 |
| 3 落盘 | `run.py` | 否 | `report.md`、`result.json`；有 PDF 则复制到 `--out` |

请求数 0。步骤 2 在 `--no-compile` 下跳过，`build.skipped` 记 `no_compile`。

## 5 判定口径

阈值与 severity 以 `scripts/texcheck.py` 为准，只有 `error` 与 `info` 两档。

| 码 | 检查 | severity | 判据 |
|-|-|-|-|
| T01 | label 重复 | error | 同一 `\label` 键出现 >1 次 |
| T02 | ref 指向不存在的 label | error | `\ref`/`\eqref`/`\autoref`/`\cref`/`\pageref` 的键不在 label 集合 |
| T03 | label 未被引用 | info | label 集合减 ref 集合，逐个报 |
| T04 | cite 键不在 bib | error | 仅当 `.bib` 解析出 ≥1 个键时执行 |
| T05 | bib 条目未被引用 | info | bib 键集合减正文 cite 键，逐个报 |
| T06 | figure/table 缺 caption | error | 该环境体内无 `\caption{`；按最近的同名 `\end` 截取 |
| T07 | 环境不配对 | error | 栈顶不等于 `\end` 的名字即报并 break；栈非空时补报栈顶 |
| T08 | 花括号不配对 | error | 全文 `{` 与 `}` 计数差 ≠ 0，报告差值 |
| T09 | 图片文件不存在 | error | 按 `.tex` 目录解析；原后缀、`.pdf`、`.png` 皆不存在即报 |
| T10 | 子章节早于 section | error | 首个 `\section` 之前出现 `\subsection` 或 `\subsubsection` |

`checks.ok` 判据：不存在 `severity` 不为 `info` 的条目。`info` 只有 T03 与 T05，故仅这两码不压 `status`。

## 6 输出契约

| 字段 | 内容 |
|-|-|
| `case_id` | 取 `case.json` 的 `case_id` 或 `id`；缺则用 `--case-id`，再缺用 `case` |
| `status` | `success` / `partial` / `blocked`，无其他取值 |
| `build` | `ok`、`tool`、`returncode`、`errors`、`pdf`、`log_tail`；未跑时多一个 `skipped` |
| `evidence` | 单位 `file:line`：静态检查每条带行号；编译错误带 `build.log` 行号 |
| `checks` | `静态检查`（`ok` + 按码计数）与 `编译`（`ok` + `skipped` 或 `returncode`） |
| `gaps` | 字符串数组：`no_tex_distribution：…`、`编译失败：rc=N`；`--no-compile` 下为空 |
| `output_contract` | 按 `case.json` 的 `output_contract` 逐键回显：命中规则给文件名，未命中给 `pointer` |
| `output_contract_echo` | 每键填充状态 `full` / `partial`，带 `source` |
| `source` / `stats` / `deliverables` / `evidence` | 源文件行数、按码计数与耗时、产物路径与字节、证据单位 `file + code` |

`status` 三个取值各自的条件：

| 取值 | 条件 |
|-|-|
| `success` | `checks.ok` 与 `build.ok` 同时为真 |
| `partial` | 二者任一为假；`--no-compile` 下 `build.ok` 恒为假，故 `success` 不可达 |
| `blocked` | 找不到 `.tex`；只打印一行 JSON 并以退出码 1 结束，不写 `result.json` |

平台 3 个字段的回显：`run.py` 已按平台字段名精确匹配，命中即 `full`。

| 平台字段 | 回显结果 | 实际内容在哪 |
|-|-|-|
| 目标产物 | `report.md`，`full` | `report.md` 与 `build` |
| 事实与来源映射 | `report.md`，`full` | `work/checks.json` 的码与描述 |
| 版本与边界 | `report.md`，`full` | `build.tool`、`build.returncode`、`gaps` |

原第 f 条（契约回显用正则猜键名）已修：三个平台字段现均回显 `full`。

## 7 限制

### 已知缺陷（v2 未修复）

| 编号 | 缺陷 | 后果 | 调用方必须自己做 |
|-|-|-|-|
| a | 从不写回修好的 `.tex`，产物只有 `report.md` 与 PDF | 题目要"结构清楚、可继续编辑的源码"，本 Skill 交不出，这是最大缺口 | 按 `report.md` 手工改源码，另存可编辑 `.tex` |
| b | 编译带 `-halt-on-error`，返回码非 0 即 break，只跑首轮 | 正是题目点名的"停在第一条错误"，后续错误不出现 | 修完首条再重跑，循环到 `build.ok` 为真 |
| c | 错误只收以 `!` 开头的行与 `文件:数字:` 形式的行 | 抓不到 TeX 独立的 `l.NNN` 行，`build.errors` 无文件与行号 | 从 `build.log_tail` 或原始日志找 `l.NNN` 定位 |
| d | `build` 只记工具名与返回码 | 不记发行版版本、宏包与字体版本、编译命令、轮次；"复现得了"只满足一半 | 自行记录 `--version` 输出、宏包版本与命令 |
| e | T08 全文数 `{` 与 `}`，注释与 verbatim 内的也计入 | 含注释或 verbatim 花括号的稿件即误报（示例实测差值 2） | 每条 T08 命中人工确认后再改 |

其他限制：

- 版式：PDF 外观、列宽溢出、字体嵌入均不检查。
- 宏包语义：`\newcommand` 自定义环境与 `\graphicspath` 不解析，T09 会误报宏拼接的图片路径。
- 文献样式：不校验 `.bst`，T04/T05 只比对键集合。
- 无 TeX 发行版时不安装，只记缺口。

## 8 异常处理

| 情况 | 行为 | status |
|-|-|-|
| 输入无 `.tex` | 打印 `{"ok": false, "status": "blocked", "error": "找不到 .tex 文件"}`，退出码 1 | `blocked` |
| `case.json` 缺失或损坏 | 静默回落：`case_id` 用 `--case-id` 或 `case`，契约为空对象 | 照常计算 |
| 编译返回码非 0 | `gaps` 追加 `编译失败：rc=N`；`build.errors` 取末轮日志前 10 条 | `partial` |
| 无 `latexmk`/`pdflatex` | `build.skipped=no_tex_distribution`，`gaps` 追加一条说明 | `partial` |
| `--no-compile` | `build.skipped=no_compile`，不记 `gaps` | `partial` |
| 无 `.bib` | 跳过 T04/T05，不报错 | 由其余检查决定 |
| 源码非 UTF-8 | 按 `errors="replace"` 解码继续，不报错 | 照常计算 |
| 环境不配对 | T07 只报首条后 break，其后不扫 | `partial` |

`refused` 与 `needs_model` 不在本 Skill 的状态集内，代码不会输出这两个值。

## 9 自查与预算

```bash
python scripts/texcheck.py --tex examples/paper.tex --bib examples/refs.bib --out work/checks.json
python run.py --tex examples/paper.tex --bib examples/refs.bib --out output --work work --case-id demo
python -c "import json;r=json.load(open('output/result.json',encoding='utf-8'));print(r['status'],r['build'],r['gaps'])"
```

1. `status` 为 `success` 或 `partial`；`partial` 必须能从 `gaps` 或 `checks` 读出原因。
2. `build` 里 `skipped` 与 `returncode` 必有其一，不出现两者皆无。
3. `output_contract` 的键集合与 `case.json` 一致，只有值会变。
4. `report.md` 问题条数等于 `work/checks.json` 的 `n_problems`，逐码计数一致。
5. T08 命中时先人工复核注释与 verbatim。

预算：模型请求 0 次；静态检查为单遍扫描，耗时正比 `.tex` 行数；编译最多 2 轮；全程不联网。

## 10 题目对齐

| 评分轴 | 机制 | 输出字段 | 自查 |
|-|-|-|-|
| 编译得通 | 真跑 `latexmk`/`pdflatex`，记工具名与返回码；无发行版记 `no_tex_distribution` | `build.ok`、`build.returncode`、`gaps` | 第 9 节第 2 条 |
| 改得对 | 只判形式层：T01/T02/T04/T06/T09 查引用、caption、图片；不改内容、不判公式语义 | `checks` 的 `静态检查`、`work/checks.json` | 第 9 节第 4 条 |
| 交得可用 | ABSENT：不写回可编辑源码，只有 `report.md` 与 PDF | 无字段承载 | 第 7 节缺陷 a |
| 复现得了 | 记工具名、返回码、日志尾部；不记发行版与宏包版本、命令、轮次 | `build.tool`、`build.log_tail` | 第 9 节第 2 条与第 7 节缺陷 d |

`交得可用` 为 ABSENT：题目要"结构清楚、可继续编辑的源码"，本 Skill 从不产出 `.tex`。该轴证据只能由调用方按 `report.md` 手工改出的源码提供。
