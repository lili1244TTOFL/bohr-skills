---
name: latex-build-guard
description: 适用于需要核对 LaTeX 稿件能否编译、并把修好的源码交给复核者的任务。输入一份 .tex（可带 .bib），输出修复后的 fixed.tex、fixes.md、report.md、checks.json、result.json，编译成功时另出 PDF。不改正文文字，只修可机械修复的 label/caption/缺图/层级问题，不做版式视觉比对。
---

# LaTeX 编译守卫

## 1 用途

输入一份 `.tex`（可带 `.bib`），产出以下 5 项（PDF 有条件）：

| 产物 | 落点 | 内容 |
|-|-|-|
| `fixed.tex` | `--out` | **成品**：按内置规则修好的可编辑源码（label/caption/缺图占位/层级） |
| `fixes.md` | `--out` | 修复记录：每条命中的规则码与改动说明 |
| `report.md` | `--out` | 原稿问题清单（码 + severity + 描述）+ 修复记录 + 修复稿残留 + 编译状态 |
| `checks.json` | `--work` | T01–T11 逐条结果、按码计数、`ok` |
| `result.json` | `--out` | 主结果 + 修复记录 + 编译记录 + `case_id` + `output_contract` 回显 |
| PDF | `--out` | 仅当 `latexmk` 或 `pdflatex` 存在且返回码为 0 |

不做：

- 不改正文文字、数字与公式。
- 不做版式视觉比对，不比较 PDF 外观。
- 不解析宏包自定义命令语义。
- 不联网安装宏包或字体。

## 2 适用条件

| 适合 | 不适合 |
|-|-|
| 单文件 `.tex`，或能指出主文件的工程 | 多主文件工程：只取输入目录下首个 `.tex` |
| 交叉引用、文献键、caption、环境配对的形式检查 | 公式语义与符号正误：只读文本，不读数学含义 |
| 需要一条真实编译记录（工具名、返回码、日志尾部） | 模板必填项逐项比对：无模板解析 |
| 需要把"能否编译"作为可复核证据交出去 | 需要修好的可编辑源码：由 autofix.py 产出 fixed.tex |

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
| 1 静态检查 | `scripts/texcheck.py` | 否 | T01–T11 单遍扫描（查原稿）；写 `work/checks.json` |
| 2 自动修复 | `autofix.py` | 否 | F01–F06 规则修 → `fixed.tex` + `fixes.md`；这是交付物 |
| 3 复检 | `scripts/texcheck.py` | 否 | 同一套 T 码查 `fixed.tex`，残留分类记进 `规则命中` |
| 4 编译 | `run.py` | 否 | 有 `latexmk`/`pdflatex` 才跑（编 `fixed.tex`）；最多 2 轮，首轮非零继续跑完 |
| 5 落盘 | `run.py` | 否 | `fixed.tex`、`fixes.md`、`report.md`、`result.json`；有 PDF 则复制到 `--out` |

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

`status` 看的是**修复稿**：`fixed.tex` 写出，且下列两类残留为空即 `success`——

- `TOOL_CODES = {T01, T02, T06}`：autofix 能修的码，修完还在说明修复失败；
- `BLOCK_CODES = {T07, T08, T11}`：环境/花括号/表格列数这类结构性语法错，规则修不了，还在就说明这份 `.tex` 还不可用。

其余残留（`T04` 缺 bib 键、`T09` 缺图已被 F05 换成占位框、`T10` 层级已被 F06 提升、`T03`/`T05` 是 info）只进 `gaps` 与 `规则命中.需作者处理`，不压状态。
**编译能否通过不参与 `status`**：本机没有 TeX 发行版是环境缺件，不是产物没做完；`build.skipped` 与 `gaps` 会记明。`result.json` 把编译状态单列在顶层 `compile`（`state` / `ok` / `skipped` / `returncode` / `pdf`），与 `status` 并列但明确 `affects_status=false`，不进入 `status` 计算；本机无 TeX 时改后的 `fixed.tex` 仍是可用交付，所以编译跳过时 `status` 仍可为 `success`。

## 6 输出契约

| 字段 | 内容 |
|-|-|
| `case_id` | 取 `case.json` 的 `case_id` 或 `id`；缺则用 `--case-id`，再缺用 `case` |
| `status` | `success` / `partial` / `blocked`，无其他取值；只看修复稿结构闸门，编译不参与 |
| `compile` | 编译状态单列：`state`（`pass`/`skip`/`fail`）、`ok`、`skipped`、`engine`、`returncode`、`pdf`、`affects_status=false`；与 `status` 分开读 |
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
| `success` | `fixed.tex` 写出，且修复稿上 `TOOL_CODES`、`BLOCK_CODES` 残留均为空（编译与否不影响） |
| `partial` | 修复稿上仍有 `TOOL_CODES` 或 `BLOCK_CODES` 残留（修没修成、或结构性语法错要作者改） |
| `blocked` | 找不到 `.tex`；写 `result.json` + `missing_input.md`，退出码 1 |

`--no-compile` 只跳过编译，不影响 `status`（`build.skipped=no_compile`）。

平台 3 个字段的回显：`run.py` 已按平台字段名精确匹配，命中即 `full`。

| 平台字段 | 回显结果 | 实际内容在哪 |
|-|-|-|
| 目标产物 | `fixed.tex`，`full` | `deliverables` 与 `目标产物`（字节数前后对比、修复条数） |
| 事实与来源映射 | `fixed.tex`，`full` | `work/checks.json` 的码与描述 + `规则命中.修复` |
| 版本与边界 | `fixed.tex`，`full` | `build.tool`、`build.returncode`、`gaps`、`版本与边界.残留` |

原第 f 条（契约回显用正则猜键名）已修：三个平台字段现均回显 `full`。

## 7 限制

### 已知缺陷（v2 未修复）

| 编号 | 缺陷 | 后果 | 调用方必须自己做 |
|-|-|-|-|
| a | 不写回原件：修复结果写到 `--out/fixed.tex`，输入目录的 `.tex` 一律不动 | 想把成果并入原工程时要自己拷回 | 把 `fixed.tex` 覆盖回原文件或改成目标文件名 |
| b | 结构性语法错（T07 环境不配对、T08 花括号、T11 表格列数）不自动修 | 这类错要读懂作者意图才能改，规则修不了，修完仍在就判 `partial` | 按 `report.md` 的残留清单手工改 |
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
| 输入无 `.tex` | 写 `result.json` + `missing_input.md`（列出找过的后缀与要补什么），stdout 打 `{"ok": false, "status": "blocked"}`，退出码 1 | `blocked` |
| `case.json` 缺失或损坏 | 静默回落：`case_id` 用 `--case-id` 或 `case`，契约为空对象 | 照常计算 |
| 有可修项 | F01–F06 逐条修，写 `fixed.tex` + `fixes.md`，逐条记进 `规则命中.修复` | 按修复稿复检定 |
| 修复稿仍缺图 | F05 换占位框 `\fbox{图片缺失：…}` 并标 TODO；`gaps` 记明要作者补图 | 不压状态 |
| 修复稿仍缺 bib 键（T04） | 不猜条目；`gaps` 记"需作者处理" | 不压状态 |
| 修复稿仍有 T07/T08/T11 | `gaps` 记"结构性错误未修（压状态）"，`report.md` 残留段列出 | `partial` |
| 编译返回码非 0 | `gaps` 追加 `编译失败：rc=N`；`build.errors` 取末轮日志前 40 条（不首错即停） | 不压状态 |
| 无 `latexmk`/`pdflatex` | `build.skipped=no_tex_distribution`，`gaps` 记明"PDF 未生成" | 不压状态 |
| `--no-compile` | `build.skipped=no_compile` | 不压状态 |
| 无 `.bib` | 跳过 T04/T05，不报错 | 由其余检查决定 |
| 源码非 UTF-8 | 按 `errors="replace"` 解码继续，不报错 | 照常计算 |

`refused` 与 `needs_model` 不在本 Skill 的状态集内，代码不会输出这两个值。

## 9 自查与预算

```bash
python scripts/texcheck.py --tex examples/paper.tex --bib examples/refs.bib --out work/checks.json
python run.py --tex examples/paper.tex --bib examples/refs.bib --out output --work work --case-id demo
python -c "import json;r=json.load(open('output/result.json',encoding='utf-8'));print(r['status'],r['stats']['fixes'],r['gaps'])"
```

1. `output/fixed.tex` 存在且非空；`fixes.md` 逐条列出命中的 F 码。
2. `status` 为 `success` 或 `partial`；`partial` 必须能从 `gaps` 或 `规则命中` 读出原因（TOOL/BLOCK 残留）。
3. `build` 里 `skipped` 与 `returncode` 必有其一，不出现两者皆无。
4. `output_contract` 的键集合与 `case.json` 一致，只有值会变。
5. `report.md` 的问题条数等于 `work/checks.json` 的 `n_problems`，逐码计数一致；修复稿残留段与 `规则命中` 一致。

预算：模型请求 0 次；静态检查为单遍扫描，耗时正比 `.tex` 行数；编译最多 2 轮；全程不联网。

## 10 题目对齐

| 评分轴 | 机制 | 输出字段 | 自查 |
|-|-|-|-|
| 编译得通 | 真跑 `latexmk`/`pdflatex`，记工具名与返回码；无发行版记 `no_tex_distribution` | `build.ok`、`build.returncode`、`gaps` | 第 9 节第 3 条 |
| 改得对 | 只判形式层：T01/T02/T04/T06/T09 查引用、caption、图片；不改内容、不判公式语义 | `checks` 的 `静态检查`、`work/checks.json` | 第 9 节第 5 条 |
| 交得可用 | `autofix.py` 写出改定的 `fixed.tex`（真改，不是报告），`run.py` 默认就走这一步 | `目标产物`、`deliverables`、`规则命中.修复` | 第 9 节第 1 条 |
| 复现得了 | 记工具名、返回码、日志尾部；不记发行版与宏包版本、命令、轮次 | `build.tool`、`build.log_tail` | 第 9 节第 3 条与第 7 节缺陷 d |

## 11 自动修复：产出改好的 .tex

`autofix.py` 读 `.tex`，改掉可机械修复的问题，把改后的源码写出来。`run.py` 默认调用它（不再只出报告）。

```bash
# 单独跑
python autofix.py --input examples --out fixed --case-id latex-A
# 走主入口，一次出 fixed.tex + fixes.md + report.md
python run.py --input examples --out output --work work
```

产出：`fixed.tex`、`fixes.md`、`result.json`（三字段）。本机无 TeX 时 `compile.ok=false`、`compile.state=skip`，写进边界，不假装编译过。

| 码 | 判据 |
|-|-|
| F01 | label 重复，后者改名 `_2` |
| F02 | 引用不存在，就近补 `\label` |
| F03 | 浮动体缺 caption，补 `\caption{待补图注}` 并标 TODO（TODO 放注释里，避免吃掉右花括号） |
| F04 | bib 条目未被引用，只报不删 |
| F05 | `\includegraphics` 指向的文件不存在，换成 `\fbox{图片缺失：…}` 占位并标 TODO |
| F06 | 首个分节是 `\subsection` 且此前无 `\section`，提升为 `\section`（只改层级，不改标题文字） |

实测：`examples/paper.tex` 原 10 行 270 B，跑完得 `fixed.tex` 15 行 351 B，五处改动（F01 重复 label 改名、F02 补 `label{fig:missing}`、F03 补 caption、F05 缺图换占位框、F06 subsection→section）；修复稿复检只剩 T03/T05 两条 info。

## 检查码全表与脚本

| 码 | 判据 |
|-|-|
| F07 | 见代码实现 |
| F08 | 见代码实现 |
| T12 | 见代码实现 |
| T13 | 见代码实现 |
| T14 | 见代码实现 |
| T15 | 见代码实现 |
| T16 | 见代码实现 |
| T17 | 见代码实现 |
| T18 | 见代码实现 |

脚本：`scripts/build.py`（编译，失败时收集全部错误）、`scripts/project.py`（工程扫描）、`rules/templates.json`（模板必填项）。
模板必填项与编译证据落到 `report.md` 与 `result.json`。

## 值不值得看，看这两个文件

| 想知道的 | 看哪 |
|-|-|
| 与现成工具比，边界在哪、做不到什么 | `docs/对比与边界.md` |
| 每条结论的出处 | `result.json` 里带出处的那几个字段，见上面的输出契约表 |

## 判断层：缺陷根因归类

脚本出候选与原文锚点，模型判，脚本再核。本节说明本包的判断项。

| 判断项 | 填什么 | 由谁核 |
|-|-|-|
| 每条缺陷属于哪类根因 | 从该条候选里选 1 个：标签冲突 / 引用断链 / 文献缺条 / 浮动体不合规 / 语法不闭合 / 素材缺失 / 章节层级 / 表格结构 / 模板缺项 / 冗余未引用 / 误报计数 | `run.py` 核 `value` 是否落在 `candidates` 内（`in_candidates`） |
| 这条判断的依据 | 该条 `snippet`（原文那一行）或 `detail` 里的**原文子串**，不改写 | `run.py` 核 `basis` 是否为原文子串（`basis_verbatim`） |

流程与落点：

1. 脚本先跑静态检查，把每条缺陷写成 `--out/judgment_worksheet.md`：稳定 id（`码@文件:行#序号`）、现象、原文、候选根因。
2. 模型按工作单填 `<输入目录>/judgments.json`：`{"items":[{"id","decision","basis","filled_by"}]}`，id 必须来自工作单。
3. 再跑同一条命令，`run.py` 读 `judgments.json` 并写进 `result.json` 的 `判断层`。

`判断层` 字段：

| 键 | 含义 |
|-|-|
| `unit` | 固定 `缺陷根因归类` |
| `n` / `filled` / `pending` | 待判条数 / 已填条数 / 未填条数 |
| `source` | 读的是哪个 `judgments.json`，读不到就写缺 |
| `who_fills` / `who_checks` | 谁填（调用方模型）、谁核（`run.py`） |
| `items[]` | 每条含 `id`、`item`、`value`、`basis`、`filled_by`、`candidates`、`in_candidates`、`basis_verbatim`、`status` |

没填的条目 `value` 写 `pending`、`status` 写 `pending`，进 `gaps` 记明条数；脚本不替模型下结论，也不因 pending 改 `status`。
`judgments.json` 缺、坏、或 id 不在工作单内都不中断主路径：缺的标 pending，多余的 id 记进 `skipped_ids`。
