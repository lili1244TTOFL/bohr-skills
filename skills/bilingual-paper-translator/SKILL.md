---
name: bilingual-paper-translator
description: 适用于把科研论文正文译成中文并保留可核验性的场景：输入原文与术语表，输出逐段双语对照、术语一致率与数字/引用/否定/限定漂移检查。脚本不调用模型，翻译由调用方完成；缺外部译文时用术语表规则出术语锁定草案。
---

# 文献翻译：分段、术语锁定、译后核验

## 1 用途

把原文按段切开，锁住术语，把译文按段号回填，然后机器核一遍有没有改坏。

脚本只做能判的事：分段、术语抽取、数字/引用/否定/限定的数量比对、段落配对。翻译由调用方模型完成，两步交接。

## 2 适用条件

| 适合 | 不适合 |
|-|-|
| 有文本层的原文（`.txt` / `.md`） | 扫描件、图片型 PDF |
| 有或可建一份术语表 | 无术语表且领域术语密集（一致率无法核验） |
| 需要逐段对照与回指 | 只想要一段通顺译文 |

## 3 调用

```bash
# 第一步：分段 + 术语锁定 + 核验，一次跑完（目录内自动找 glossary.md 与 targets.txt）
python run.py --input /input --work /app/work --out /app/output

# 也可显式指定术语表与外部译文
python run.py --input /input --glossary glossary.md --targets targets.txt \
  --work /app/work --out /app/output
```

没有外部译文时不会停在待译：用术语表做「术语锁定草案」（原文术语最长优先替换成译名 + 空行段对齐），照样出 `bilingual.md`；连术语表也没有时，草案与原文同形，另交 `translation_worklist.md` 译前底稿（逐段数字/引用/否定/限定清单 + 候选术语）。

退出码：0 正常；1 找不到原文（`status=blocked`，同时写 `missing_input.md` 说明缺什么）。

## 4 流程

| 步 | 做什么 |
|-|-|
| 1 找原文 | 输入目录内首个 `.txt`/`.md`（排除 `glossary*`、`targets*`） |
| 2 分段 | 按空行切，保留 `char_start`/`char_end` |
| 3 读术语表 | Markdown 表格取前两列，键=原文、值=译名 |
| 4 配译文 | `targets.txt` 按段号顺序配对，缺的留空 |
| 5 四项比对 | 数字、引用编号、否定词、限定词，原文与译文各数一遍 |
| 6 术语回扫 | 原文出现术语时，译文必须用同一译名 |
| 7 补草案 | 外部译文缺的段，用术语表最长优先替换 + 段对齐补出草案 |
| 8 导四份 | `bilingual.md`、`verify.json`、`segments.json`、`result.json` |

## 5 判定口径

| 码 | 判据 | 对应关注点 |
|-|-|-|
| Q01 | 术语一致率 < 1 | 术语稳 |
| Q02 | 段数与外部译文段数不等 | 段落全 |
| Q03 | 数字/引用/否定/限定数量不一致 | 关系准 |

`status`：`success`（每段都有译文——外部译文或内置规则草案——且无问题）、`partial`（有译文但 Q01/Q02/Q03 报问题）、`blocked`（找不到原文，另写 `missing_input.md`）。

缺外部译文不是终态：内置规则先出术语锁定草案，`result.json.规则命中` 逐段记下替换了哪些术语、几处；`gaps` 写明草案边界（语序未动、不是模型译文）。

## 6 输出契约

| 字段 | 产物 | 完整度 |
|-|-|-|
| 阅读/解析结果 | `result.json` 的段数/已译数/未译段/术语数 + `bilingual.md` | full：逐段原文与译文 |
| 证据与边界 | `result.json` 的 `anchors`（每段 `seg_id` + 字符区间）与 `gaps` | full：可回原文定位 |
| 运行状态 | `result.json` 的 `status` | full：四态之一 |

`case.json` 给了 `output_contract` 按其回显；没给也照样输出这三项。

## 7 限制

- 只认 Markdown 表格形式的术语表；`.tsv` 不上交（平台预检拒该扩展名），要用就转成 `.md`。
- 术语判定是子串匹配，不做形态还原；同一术语的不同词形可能漏检。
- 数字比对按数量对比，不校位置；数字对调但数量不变时检不出。
- 不做指代消解，代词译法需人看。
- 不调用模型：术语锁定草案只做词面替换，不做语序调整与虚词补全；术语表缺失时草案与原文同形，只交译前底稿。

## 8 异常处理

| 情况 | 行为 | 状态 |
|-|-|-|
| 无原文 | 写 blocked 的 result.json 与 `missing_input.md`，退出 1 | `blocked` |
| 无外部译文 | 术语表最长优先替换 + 段对齐补出每段草案 | `success` |
| 无术语表 | 跳过术语回扫，草案与原文同形，另交 `translation_worklist.md` | `success` |
| 译文段数不足 | 报 Q02，缺的段用草案补上 | `partial` |
| 数字/引用漂移 | 报 Q03，列出原文与译文各自数量 | `partial` |

## 9 自查与预算

```bash
python run.py --input examples/case  --work w1 --out o1   # 带术语表与译文 → Q03 报限定 2→1
python run.py --input examples/plain --work w2 --out o2   # 只有原文 → 3 段草案 + 译前底稿
```

1. 两形态状态分别为 `partial`（`examples/case`：3 段全译，限定词 2→1 触发 Q03）与 `success`（`examples/plain`：缺术语表，3 段草案与原文同形 + `translation_worklist.md`）。
2. 只给术语表不给译文时 `status=success`、`term_rate=1.0`，`bilingual.md` 每段译文里术语已换成译名。
3. `bilingual.md` 的段号与 `segments.json` 一致。
4. `verify.json` 的四个 profile 字段与正文数字对得上。

预算：零模型请求；分段与核验为毫秒级。

## 10 题目对齐（阅读03）

| 关注点 | 机制 | 落在哪 |
|-|-|-|
| 术语稳 | 术语表锁定 + 全篇译名回扫 | Q01、`verify.json.term_consistency` |
| 段落全 | 段号一一对应，段数不等即报 | Q02、`segments.json` |
| 关系准 | 数字/引用/否定/限定四项数量比对 | Q03、`verify.json.profile_*` |
| 可核验 | 每段带 `char_start/char_end` 可回原文 | `bilingual.md` 段头 |

## 11 输入标识与证据

`case_id` 取自 `case.json`，缺失时用输入目录名，写进 `result.json`、`verify.json`、`segments.json` 三处顶层。

`证据与边界`（`evidence`）字段写明证据单位 `seg_id + char_start/char_end`：`anchors` 逐段给出字符区间，可按 `segments.json` 回到原文对应位置，`bilingual.md` 每段标题也标了区间。
