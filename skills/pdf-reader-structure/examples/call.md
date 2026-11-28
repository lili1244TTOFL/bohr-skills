# 最小调用示例（示意输出，非正式案例结果）

> 以下输入为包内脚本生成的公开测试页，输出为该输入的实际运行结果，用来说明接口与产物形态，
> 不代表组织方固定案例的评分结果。

## 输入

`examples/two_column.pdf`（由 `make_two_column.py` 生成，2 页）：

- 第 1 页：页眉、通栏标题、作者、摘要、左栏 `1 Introduction` + `1.1 Motivation`、右栏 `2 Method` + `2.1 Table extraction`、页脚页码
- 第 2 页：`3 Results`、表 1 的 3×3 网格、公式 `S = A / N (1)`、参考文献

## 调用

```bash
python examples/make_two_column.py examples/two_column.pdf
python run.py --pdf examples/two_column.pdf --out /tmp/out --case-id demo
```

## 示意输出

`result.json`（节选，数值取自本机实跑）：

```json
{
 "case_id": "demo",
 "status": "success",
 "docmeta": {"title": "Multi-column Reading Order and Structure",
             "authors": "A. Author, B. Author", "affiliation": ""},
 "stats": {"pages": 2, "parsed_pages": 2, "blocks": 32, "tables": 1,
           "formulas": 1, "captions": 1, "columns": [2, 1],
           "bbox_estimated": 0, "mojibake_ratio": 0.0},
 "checks": {"页覆盖": {"ok": true, "detail": "2/2"},
            "表格列数一致": {"ok": true, "detail": [3]},
            "有正文": {"ok": true, "detail": 16},
            "页眉页脚已剔除": {"ok": true, "detail": "header=2 footer=0；parsed.md 残留=0"}},
 "gaps": []
}
```

`parsed.md`（节选，左栏读完才读右栏）：

```markdown
<!-- page 1 -->

# Multi-column Reading Order and Structure

A. Author, B. Author
# Abstract

We test column order, headings, tables and formulas in
a two-column layout with running heads.
# 1 Introduction

Two-column papers are common in science. A reader must
follow the left column before the right column, otherwise
sentences from different columns get spliced together.
...
## 1.1 Motivation
...
# 2 Method
...
<!-- page 2 -->

# 3 Results

**Table 1 Parse accuracy by layout type**

| Layout | Columns | Accuracy |
| --- | --- | --- |
| single | 1 | 99.1% |
| double | 2 | 97.4% |

The score is computed from aligned characters:
$$ S = A / N (1) $$

# References

[1] A. Author. Layout parsing. J. Testing, 2024.
```

`blocks.json`（节选，每块带页码与测量框）：

```json
{"page": 1, "role": "title", "bbox": [50.0, 767.0, 382.8, 783.0],
 "text": "Multi-column Reading Order and Structure", "size": 16.0}
```

## 说明

- 示例只覆盖单栏/双栏、3×3 网格、单条公式；三栏、跨页续表、扫描件不在示例内。
- 包内另有四份版式样例（`examples/make_cases.py` 生成到 `_cases/`）：单栏英文、双栏英文、
  4×5 表格页、单栏中文。每份的实际运行结果见 `03_matrix.md`。

## 材料不全时的形态

中文单栏页（`make_cases.py` 生成的 chinese 版式）实跑：

```json
{"ok": true, "status": "success", "tables": 0, "gaps": 1}
gaps: 中文页共 422 字：CJK 无词间空格，标题行分段以字距为准，标题层级需人工核对
```

无文字层的 PDF（用 pypdf 造的两页空白）实跑：

```json
{"ok": true, "status": "partial", "tables": 0, "gaps": 3}
gaps: 第 1 页没有文字层 / 第 2 页没有文字层 / 无文字层页：1,2（需 OCR，本 Skill 未内置 OCR）
```

此时另出 `unreadable.md`，逐页写页码与原因；`parsed.md` 只写真实抽到的文字，缺的页按页留空，不做推测。
输入目录没有 PDF、或 `--pdf` 指的文件不存在时，写 `result.json` + `missing_input.md`，`status=blocked`、退出码 1。

## 命令

```bash
# 解析输入目录里的 PDF
python run.py --input /input --work /app/work --out /app/output

# 直接指定单个 PDF
python run.py --pdf examples/dual.pdf --work w --out o --case-id pdf-A
```

产物：`parsed.md`、`blocks.json`、`sections.json`、`tables/tableN.md`、`result.json`；读不全时另有 `unreadable.md`。
