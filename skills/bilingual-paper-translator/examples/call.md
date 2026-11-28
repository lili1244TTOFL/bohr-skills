# 最小调用示例（示例，非正式案例结果）

## 输入

- `examples/case/paper.txt`：3 段英文摘要，含引用编号 `[1] [2] [3]`、否定句、限定词
- `examples/case/glossary.md`：3 条术语（working memory / n-back task / reaction time）
- `examples/case/targets.txt`：对应译文，空行分隔
- `examples/plain/paper.txt`：只有原文，用来演示缺外部译文与术语表时按段对齐、标未译残留

## 命令

```bash
# 分段 + 术语锁定 + 译后核验（目录内自动找到 glossary.md 与 targets.txt）
python run.py --input examples/case --work work --out output

# 也可显式指定术语表与译文
python run.py --input examples/case --glossary examples/case/glossary.md \
  --targets examples/case/targets.txt --work work --out output

# 只有原文：无外部译文、无术语表时按段对齐，草案段全部标未译残留
python run.py --input examples/plain --work work2 --out output2
```

## 示意输出（示例，取自真实运行）

第一条命令的真实 stdout：

```json
{"ok": true, "status": "partial", "segments": 3, "sentences": 4, "translated": 3, "draft": 0, "residue": 0, "problems": 1, "term_rate": 1.0}
```

第三条命令（只有原文）的真实 stdout：

```json
{"ok": true, "status": "partial", "segments": 3, "sentences": 4, "translated": 0, "draft": 3, "residue": 3, "problems": 0, "term_rate": null}
```

产物：`bilingual.md`（逐段对照）、`verify.json`（术语一致率与四项 profile）、`segments.json`、`result.json`；缺术语表时另交 `translation_worklist.md` 译前底稿（逐段数字/引用/否定/限定清单 + 候选术语）。

第一条 `partial` 的原因是 `verify.json.problems` 里有一条 Q03：原文限定词 2 个、译文 1 个（`suggest` / `may` 译成一句「可能」）。

第三条 `partial` 的原因是 3 段草案与原文同形（`residue=3`），`result.json.运行状态.reason` 写明「3 段译文与原文同形（未译残留），不是可用译文」。按 §5 与 §7，缺译文时 `status` 不会写成 `success`：草案只做段对齐与术语表替换，不是可用译文；要 `success` 必须每段都有非残留译文且 Q01–Q06 无未确认命中。示例仅演示接口，不是组织方固定案例结果。
