# 最小调用示例（示例，非正式案例结果）

## 输入

- `examples/paper.md`：英文摘要 4 句，含冗余短语、口语词、模糊加强、主谓一致错误
- `examples/polished.md`：规范改写，保留 `clearly very` 与全部数字引用
- `examples/glossary.md`：Markdown 两列术语表

## 命令

```bash
python run.py --source examples/paper.md --out output

python run.py --source examples/paper.md --polished examples/polished.md \
  --glossary examples/glossary.md --out output --case-id polish-A
```

## 示意输出（示例）

```json
{"ok": true, "status": "success", "issues": 7, "polished": true, "problems": 0}
```

产物：`polished.md`（改后全文）、`changes.md`（逐条码/类型/片段/理由/置信度）、`issues.json`（检测明细）、`result.json`（三字段）。

示例仅演示接口，不是组织方固定案例结果。
