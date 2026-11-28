---
name: intro-related-work
description: 适用于起草或核对论文引言与相关工作的场景：输入 draft.md 与 refs.bib，输出四部分骨架、引用双向核对与问题清单。检查项为纯规则，不调用模型。
---

# 引言与相关工作：骨架 + 引用核验 + 改稿

## 1 用途

把一份病稿（或只言片语的材料）整理成四个部分——研究背景、相关工作、研究空白、贡献——同时把引用与贡献逐条对账，并给出一份改好的稿：

- `outline.md`：四部分骨架，缺段标"待补"
- `revised.md`：改稿——相关工作按原文维度词重组成小节、缺引论断补引并标出补了哪条、贡献条目补 Fig./Table/Section 指向（只挂原文真实出现过的标记）
- `citations.json`：引用键与 bib 的双向差集

不做：替你编文献、替你判断哪篇更重要、生成实验内容、改写原句文字。

## 2 适用条件

| 适合 | 不适合 |
|-|-|
| 有 `draft.md` 或材料 `.md` | 只有 PDF、没有文本层 |
| 有 `refs.bib` 可核引用键 | 引用是纯手写序号、无键 |
| 目标是把已有内容组织好并查漏 | 从零选题、需要检索新文献 |

## 3 调用

```bash
python run.py --input /input --work /app/work --out /app/output
python run.py --input _caseA --work w --out o --case-id intro-A
```

退出码：0 正常；1 既无 `draft.md` 也无材料 `.md`（`status=blocked`，同时写 `missing_input.md`）。

## 4 流程

| 步 | 做什么 |
|-|-|
| 1 找文件 | `draft*.md`、`*.bib`、其余 `.md` 作材料 |
| 2 切段 | 按标题关键词定位四个部分 |
| 3 抽引用 | `[@key]` 与 `\cite{key}`，支持 `[@a; @b]` 多键 |
| 4 核 bib | 双向差集：引用有而 bib 无 / bib 有而未被引 |
| 5 查六项 | C01–C08（见 §5） |
| 6 出骨架与引用核验 | `outline.md`、`citations.json`、`result.json` |
| 7 改稿 | `compose.py` → `revised.md`（去套话 + 分组 + 补引 + 证据指向） |
| 8 复检改稿 | 同一套 C01–C08 对改稿正文再跑一遍，状态看改稿 |

`compose.py` 可单独跑：`python compose.py --draft draft.md --bib refs.bib --out revised.md --json facts.json`。

## 5 判定口径

| 码 | 判据 | 对应关注点 |
|-|-|-|
| C01 | 四部分是否齐全 | 立得住 |
| C02 | 相关工作是否给了分组或比较维度 | 分得清 |
| C03 | 引用键是否都在 bib | 对得上 |
| C04 | bib 是否有条目未被引用 | 对得上 |
| C05 | 研究空白段是否带引用（空白要由已引文献推出） | 说得实 |
| C06 | 贡献是否分条且每条有 Fig./Table/Section 指向 | 对得上 |
| C07 | 是否出现空转套话（"近年来受到广泛关注"一类） | 立得住 |
| C08 | bib 元数据是否含 author/year/title | 对得上 |

`status`：**改稿写出且 C01/C02/C03/C05/C07 在改稿正文上全过 → `success`**；改稿仍有这几码 → `partial`；既无 `draft.md` 也无材料 → `blocked`。
C04/C06/C08 的残留属于"要作者补材料/补编号"，只进 `gaps` 与 `版本与边界.改稿残留`，不压状态（工具不编编号、不编条目字段）。

检出与修复分开：`run.py` 只报码，`compose.py` 负责改（C07 去套话、C02 相关工作分组、C05 空白补引、C06 贡献证据指向），
改成什么样看 `revised.md` 的 §A 分组依据 / §B 补引记录 / §C 证据指向 / §D 人工确认项 / §F 去套话记录。
`result.json` 的 `checks` 是对输入稿的诊断，`改稿残留` 才是对交付物的判定。

## 6 输出契约

| 字段 | 产物 | 回显方式 |
|-|-|-|
| 目标产物 | `outline.md`、`revised.md`、`citations.json` | full：产物路径 + 字节数 + sha256 |
| 事实与来源映射 | 每条引用键 → 章节/原句/bib 字段是否齐；补引与证据指向逐条给依据 | full：逐条可回溯到 `draft.md` 与 `refs.bib` |
| 版本与边界 | 分组改动、补引条目、证据指向、人工确认项、缺口 | full：改了什么、没改什么、为什么没改 |

`case.json` 给了 `output_contract` 按其字段名回显（认得的字段回真实内容，认不得的写指针并标 `partial`）；没给也照样输出这三项。

## 7 限制

- 只认 `[@key]` 与 `\cite{key}` 两种引用写法，数字序号不认。
- 分组判断靠关键词（“按方法/问题/路线”“分为 N 类”“基于/采用 X”），写得隐晦会漏。
- 原文没有维度词时不编维度名：只按原文动词（提出/验证/对比…）归一组，并在 `revised.md` §D 写明。
- 去套话只删套话分句；某句删完只剩套话就整句删掉，逐条记在 §F。套话句带的引用不会丢，抬成「相关研究 [@…]」保留。
- 补引只用 bib 里已有的键：先看词面重合，再看本稿别处已引的文献；都不命中就不补，记入 §D。
- 贡献的证据指向只认 `Fig./Table/Section` 三种标记，且只挑原文真实出现过的，不生成新编号。
- 不判断他人的工作转述是否忠实于原文，那需要读原文献。

## 8 异常处理

| 情况 | 行为 | 状态 |
|-|-|-|
| 无 draft 无材料 | 写 `result.json` + `missing_input.md`，退出 1 | `blocked` |
| 无 draft、有材料 | 用材料 `.md` 当底稿改稿，`gaps` 记明 | `success`（改稿过判据） |
| 无 bib | 引用不核，`gaps` 记"引用无法核验" | `partial` |
| 引用键不在 bib | 报 C03，不猜对应关系 | `partial` |
| 章节标题不规范 | 按关键词定位失败则该段视为缺，报 C01 | `partial` |
| 改稿仍缺 Fig./Table 编号 | 不编编号，写"待作者补"，进 `gaps` | 不压状态 |

## 9 自查与预算

```bash
python run.py --input examples --work w --out o   # 期望 status=success
```

1. `revised.md` 的 §F 去套话记录有原文命中的套话分句与改后文本；研究背景里套话已被替换。
2. `revised.md` 的「相关工作按方法路线分为 N 组」与 §A 分组表行数一致。
3. `result.json` 的 `checks`（输入稿诊断）与 `改稿残留`（交付物判定）分开；后者只剩 C04/C06/C08 这类待作者补项。
4. `citations.json` 两个差集与病稿一致。
5. `outline.md` 的缺段标记与 C01 对应。

预算：零模型请求，毫秒级。

## 10 题目对齐（写作08）

| 关注点 | 机制 | 落在哪 |
|-|-|-|
| 立得住 | 套话检出 + 背景须带引用 | C07、C05 |
| 分得清 | 相关工作要有分组或比较维度 | C02 |
| 说得实 | 空白段无引用即判未推导 | C05 |
| 对得上 | 引用双向差集 + 贡献证据指向 + 元数据 | C03、C04、C06、C08 |

## 11 输入标识与证据

`case_id` 取自 `case.json`，缺失时用输入目录名，写进 `result.json` 顶层与 `citations.json`。

证据单位：**引用键 + 行内标记**。每条问题给码与可定位描述（如"引用键 li2020 不在 bib"、"贡献第 2 条无 Fig./Table/Section 标记"），人能凭码与键回到 `draft.md` 与 `refs.bib` 核对。

`result.json` 带 `evidence` 字段，形如 `{"unit": "cite_key+line_marker", "items": [...]}`，逐条列出问题码与对应引用键，用于回指原文。

## 检查码全表

| 码 | 判据 |
|-|-|
| C09 | 见 `run.py` 实现与 `changes.md` 对应条目 |
| C10 | 见 `run.py` 实现与 `changes.md` 对应条目 |
| C11 | 见 `run.py` 实现与 `changes.md` 对应条目 |
| C12 | 见 `run.py` 实现与 `changes.md` 对应条目 |
| L10 | 见 `run.py` 实现与 `changes.md` 对应条目 |
