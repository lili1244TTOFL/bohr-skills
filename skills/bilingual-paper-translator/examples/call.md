# 最小调用示例（示例，非正式案例结果）

## 输入

- `examples/case/paper.txt`：3 段英文摘要，含引用编号 `[1] [2] [3]`、否定句、限定词
- `examples/case/glossary.md`：3 条术语（working memory / n-back task / reaction time）
- `examples/case/targets.txt`：对应译文，空行分隔
- `examples/plain/paper.txt`：只有原文，用来演示缺外部译文时内置规则出锁定草案

## 命令

```bash
# 分段 + 术语锁定 + 译后核验（目录内自动找到 glossary.md 与 targets.txt）
python run.py --input examples/case --work work --out output

# 也可显式指定术语表与译文
python run.py --input examples/case --glossary examples/case/glossary.md \
  --targets examples/case/targets.txt --work work --out output

# 只有原文：缺外部译文时用术语表出锁定草案，不再返回待译
python run.py --input examples/plain --work work2 --out output2
```

## 示意输出（示例）

第一条命令的真实 stdout：

```json
{"ok": true, "status": "partial", "segments": 3, "translated": 3, "auto_translated": 0, "term_rate": 1.0}
```

第三条命令（只有原文）的真实 stdout：

```json
{"ok": true, "status": "success", "segments": 3, "translated": 0, "auto_translated": 3, "term_rate": null}
```

产物：`bilingual.md`（逐段对照）、`verify.json`（术语一致率与四项 profile）、`segments.json`、`result.json`；缺术语表时另交 `translation_worklist.md` 译前底稿（逐段数字/引用/否定/限定清单 + 候选术语）。

`partial` 的原因是 `verify.json.problems` 里有一条 Q03：原文限定词 2 个、译文 1 个（`suggest` / `may` 译成一句「可能」）。示例仅演示接口，不是组织方固定案例结果。
