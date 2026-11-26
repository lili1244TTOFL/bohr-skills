---
name: nanopub-split
description: 适用于把论文段落拆成可判断真假的科研断言并绑定证据的场景：输入正文与来源元数据，输出 nanopubs.json、assertions.tsv 与 result.json，每条断言带最小证据片段与原文行列区间。不做 OCR、不做联网核验。
---

# 断言拆解与证据绑定

## 1 用途

把论文正文拆成一条条**单独可判断真假**的断言，每条绑一个最小证据片段与原文位置，导出成机器可读结构。

| 产物 | 内容 |
|-|-|
| `nanopubs.json` | 断言数组 + 来源元数据 + 统计 |
| `assertions.tsv` | 表格形态：id、主体、关系、客体、行号、字符区间、置信度 |
| `result.json` | 题面三个契约字段平铺到顶层 |

不做：OCR、联网核验、图表内文字抽取、跨论文去重。

## 2 适用条件

| 适合 | 不适合 |
|-|-|
| 有文本层的正文（.md / .txt） | 扫描版 PDF |
| 结论句含显式关系词（提高、采用、导致…） | 全靠推论、无显式关系的段落 |
| 需要逐条核对断言与证据 | 需要读懂图表 |

## 3 调用

```bash
python run.py --input /input --work /app/work --out /app/output
python run.py --input examples/case --work work --out output --case-id np-demo
```

退出码：0 正常（`status` 可能为 `partial`）；1 材料缺失（`blocked`，仍写 `result.json`）。

## 4 流程

| 步 | 做什么 |
|-|-|
| 1 读材料 | 找 `manuscript*.md` 或任意 `.md/.txt`；读 `case.json` 取 paper/authors/year/doi |
| 2 切句 | 按 `。！？；` 与换行切分，保留全文字符偏移 |
| 3 判关系 | 命中 `rules/relations.md` 的关系词才成为断言 |
| 4 拆主客 | 关系词之前作主体，之后到句末作客体 |
| 5 绑证据 | 记 `line`、`char_start`、`char_end`，方法记作 `sentence-window` |
| 6 导三份 | `nanopubs.json`、`assertions.tsv`、`result.json` |

## 5 判定口径

| 情形 | 判定 |
|-|-|
| 句子无关系词 | 不生成断言，计入 `skipped`，覆盖率随之下降 |
| 断言超过 80 字 | 标 `review=断言过宽，需拆细`，置信度降为 medium |
| 正文找不到 | `status=blocked`，写 `result.json` 后退出 1 |
| 来源字段缺失 | 写 `unknown`，不编造，并进 `gaps` |

`status` 取值：`success`（有断言且无过宽）、`partial`（有断言但有过宽或缺口）、`blocked`（无正文）。

## 6 输出契约

| 字段 | 内容 |
|-|-|
| `case_id` | 取自 `case.json`，缺失时用输入目录名；写进 `result.json` 顶层 |
| `阅读/解析结果` | 断言条数、文件名、覆盖率 |
| `证据与边界` | 证据单位 `line+char_start+char_end`、待复核条数、缺口 |
| `运行状态` | 同 `status` |
| `evidence.items` | 每条断言的 id 与三处位置 |
| `uncertainty` | 与 `gaps` 相同，列需人工确认的项 |

`output_contract` 里的字段逐个回显到 `result.json` 顶层：认得的写实际内容，认不得的置 `null` 并记入 `gaps`，不猜。

## 7 限制

- 关系词表只覆盖常见科研动词，换领域要改 `rules/relations.md`。
- 不做指代消解：主体若是"该结果""上述方法"，会照原文记下来，标为待确认。
- 句子级证据，不做子句级切分；一条长句里的并列关系会被并成一条。
- 不判断断言的真假，只保证它可判断、可回指。

## 8 异常处理

| 情况 | 行为 | 状态 |
|-|-|-|
| 无正文文件 | 写 blocked 的 result.json，退出 1 | `blocked` |
| 正文无显式关系句 | 断言 0 条，`gaps` 说明 | `blocked` |
| `case.json` 缺来源字段 | 写 `unknown`，进 `gaps` | `partial` |
| 断言过宽 | 标 `review`，`uncertainty` 列出 | `partial` |

## 9 自查与预算

```bash
python run.py --input examples/case --work work --out output --case-id np-demo
python -c "import json;r=json.load(open('output/result.json'));print(r['运行状态'],r['阅读/解析结果'])"
```

1. 每条断言的 `evidence.quote` 能在正文里按 `char_start/char_end` 截出。
2. `assertions.tsv` 行数 = 断言数 + 1。
3. `gaps` 与 `uncertainty` 一致。
4. 换一篇正文重跑，覆盖率应随文本变化。

预算：零模型请求，纯本地处理；单篇 10 KB 正文为毫秒级。

## 10 题目对齐

| 关注点 | 机制 | 落在哪 |
|-|-|-|
| 拆得细 | 一句一断言，只承一个关系；超过 80 字标待拆细 | `assertions[].review` |
| 对得上 | 每条带最小证据片段与行列区间 | `evidence.quote/line/char_start/char_end` |
| 追得回 | 断言→证据→paper/authors/year/doi 链路 | `source` 字段 |
| 读得懂 | JSON + TSV 双导出，字段固定 | `nanopubs.json`、`assertions.tsv` |

## 契约字段映射（字段 → 产物 → 完整度）

| 字段 | 产物 | 完整度 |
|-|-|-|
| 阅读/解析结果 | `nanopubs.json` 的 `assertions` 数组 | full：断言原文、主体、关系、客体 |
| 证据与边界 | 每条断言的 `evidence`（quote + line + char 区间） | full：可回原文定位 |
| 运行状态 | `result.json` 顶层 `status` | full：success / partial / blocked |

`case.json` 给了 `output_contract` 按其回显；没给也照样输出这三项。
