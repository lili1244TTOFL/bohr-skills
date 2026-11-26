# 最小调用示例（示意输出，非正式案例结果）

> 以下输入为公开测试页，输出为示例，用来说明接口与产物形态，不代表组织方固定案例的评分结果。

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

`result.json`（节选）：

```json
{
 "case_id": "demo",
 "status": "success",
 "stats": {"pages": 2, "parsed_pages": 2, "blocks": 32, "tables": 1,
           "formulas": 1, "captions": 2, "columns": [2, 1], "mojibake_ratio": 0.0},
 "checks": {"页覆盖": {"ok": true}, "表格列数一致": {"ok": true},
            "有正文": {"ok": true}, "页眉页脚已剔除": {"ok": true}},
 "gaps": []
}
```

`parsed.md`（节选，左栏读完才读右栏）：

```markdown
<!-- page 1 -->

# Multi-column Reading Order and Structure

A. Author, B. Author

### Abstract
We test column order, headings, tables and formulas in
a two-column layout with running heads.

### 1 Introduction
Two-column papers are common in science. A reader must
follow the left column before the right column, otherwise
sentences from different columns get spliced together.
...

### 2 Method
We cluster lines by baseline, then find a vertical
...

<!-- page 2 -->
## 3 Results

**Table 1 Parse accuracy by layout type**

| Layout | Columns | Accuracy |
| --- | --- | --- |
| single | 1 | 99.1% |
| double | 2 | 97.4% |
```

`blocks.json`（节选，每块可回溯）：

```json
{"page": 2, "role": "table", "text": "[table 3x3]",
 "bbox": [50.0, 652.0, 350.0, 706.0]}
```

## 说明

- 示例只覆盖单栏/双栏、简单网格、单条公式；三栏、跨页续表、扫描件不在示例内。
- 示例输出由本地测试 PDF 产生，未经组织方固定案例验证。

## 命令

```bash
# 解析输入目录里的 PDF
python run.py --input /input --work /app/work --out /app/output

# 直接指定单个 PDF
python run.py --pdf examples/dual.pdf --work w --out o --case-id pdf-A
```

产物：`parsed.md`、`blocks.json`、`sections.json`、`result.json`。
