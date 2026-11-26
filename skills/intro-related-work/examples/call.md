# 最小调用示例（示例，非正式案例结果）

## 输入

- `examples/draft.md`：四段病稿，含空转套话、逐篇罗列的相关工作、无引用的空白段、无证据的贡献
- `examples/refs.bib`：4 条，其中 1 条未被引用、1 条缺 year

## 命令

```bash
python run.py --input examples --work work --out output --case-id intro-A
```

## 示意输出（示例）

```json
{"ok": true, "status": "partial", "problems": 6,
 "codes": ["C02","C04","C05","C06","C07","C08"]}
```

产物：`output/outline.md`（四段骨架）、`output/citations.json`（引用双向差集）、`output/result.json`（三字段）。

示例仅演示接口，不是组织方固定案例结果。
