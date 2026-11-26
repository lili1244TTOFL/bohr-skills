---
name: intro-related-work
description: 适用于起草或核对论文引言与相关工作的场景：输入 draft.md 与 refs.bib，输出四部分骨架、引用双向核对与问题清单。检查项为纯规则，不调用模型。
---

# 引言与相关工作：骨架 + 引用核验

## 1 用途

把一份病稿（或只言片语的材料）整理成四个部分——研究背景、相关工作、研究空白、贡献——同时把引用与贡献逐条对账。

不做：替你编文献、替你判断哪篇更重要、生成实验内容。

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

退出码：0 正常；1 既无 `draft.md` 也无材料 `.md`（`status=blocked`）。

## 4 流程

| 步 | 做什么 |
|-|-|
| 1 找文件 | `draft*.md`、`*.bib`、其余 `.md` 作材料 |
| 2 切段 | 按标题关键词定位四个部分 |
| 3 抽引用 | `[@key]` 与 `\cite{key}`，支持 `[@a; @b]` 多键 |
| 4 核 bib | 双向差集：引用有而 bib 无 / bib 有而未被引 |
| 5 查六项 | C01–C08（见 §5） |
| 6 导三份 | `outline.md`、`citations.json`、`result.json` |

## 5 判定口径

| 码 | 判据 | 对应关注点 |
|-|-|-|
| C01 | 四部分是否齐全 | 立得住 |
| C02 | 相关工作是否给了分组或比较维度 | 分得清 |
| C03 | 引用键是否都在 bib | 对得上 |
| C04 | bib 是否有条目未被引用 | 对得上 |
| C05 | 研究空白段是否带引用（空白要由已引文献推出） | 说得实 |
| C06 | 贡献是否分条且每条有 Fig./Table/Section 指向 | 对得上 |
| C07 | 是否出现空转套话（“近年来受到广泛关注”一类） | 立得住 |
| C08 | bib 元数据是否含 author/year/title | 对得上 |

`status`：无问题且有病稿为 `success`；有问题为 `partial`；无输入为 `blocked`。

## 6 输出契约

| 字段 | 产物 | 回显方式 |
|-|-|-|
| 目标产物 | `outline.md` | full：四部分骨架，缺段标“待补” |
| 事实与来源映射 | `citations.json` | full：引用键 ↔ bib 双向差集 |
| 版本与边界 | `gaps` 与 `uncertainty` | full：缺口逐条列出 |

`case.json` 给了 `output_contract` 按其回显；没给也照样输出这三项。

## 7 限制

- 只认 `[@key]` 与 `\cite{key}` 两种引用写法，数字序号不认。
- 分组判断靠关键词（“按方法/问题/路线”“分为 N 类”），写得隐晦会漏。
- 贡献的证据指向只认 `Fig./Table/Section/式` 四种标记。
- 不判断他人的工作转述是否忠实于原文，那需要读原文献。

## 8 异常处理

| 情况 | 行为 | 状态 |
|-|-|-|
| 无 draft 无材料 | 写 blocked，退出 1 | `blocked` |
| 无 bib | 引用不核，`gaps` 记“引用无法核验” | `partial` |
| 引用键不在 bib | 报 C03，不猜对应关系 | `partial` |
| 章节标题不规范 | 按关键词定位失败则该段视为缺，报 C01 | `partial` |

## 9 自查与预算

```bash
python run.py --input _caseA --work w --out o   # 期望 6 类问题
python run.py --input _caseB --work w --out o   # 期望 0 问题
```

1. A 报 C02/C04/C05/C06/C07/C08，B 干净。
2. `citations.json` 两个差集与病稿一致。
3. `outline.md` 的缺段标记与 C01 对应。

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
