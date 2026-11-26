# 最小调用示例（示例，非正式案例结果）

## 输入

- `examples/paper.txt`：3 段英文摘要，含引用编号 `[1] [2] [3]`、否定句、限定词
- `examples/glossary.md`：3 条术语（working memory / n-back task / reaction time）
- `examples/targets.txt`：对应译文，空行分隔

## 命令

```bash
# 只分段
python run.py --input examples --work work --out output

# 分段 + 术语锁定 + 译后核验
python run.py --input examples --glossary examples/glossary.md \
  --targets examples/targets.txt --work work --out output
```

## 示意输出（示例）

```json
{"ok": true, "status": "partial", "segments": 3, "translated": 3, "term_rate": 1.0}
```

产物：`bilingual.md`（逐段对照）、`verify.json`（术语一致率与四项 profile）、`segments.json`、`result.json`。

`partial` 表示四项比对里有数量不一致，具体见 `verify.json.problems`。示例仅演示接口，不是组织方固定案例结果。
