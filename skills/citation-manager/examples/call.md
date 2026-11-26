# 最小调用示例（示例，非正式案例结果）

## 输入

- `examples/sample.md`：样例稿（中文，含原引用标记 `[1]`…`[11]`）
- `examples/refs.txt`：样例稿原有文献表
- `examples/lib/core.bib`、`examples/lib/sample_refs.bib`：小文献库（12 条，含与稿件重复的条目与一个重复副本）
- `examples/pass1_out.txt`、`examples/pass3_match.txt`：两次模型请求的示例输出（用于免模型重放）

## 命令

```bash
cd skill
python examples/make_docx.py examples/sample.md /tmp/sample.docx

python run.py --docx /tmp/sample.docx --refs examples/refs.txt \
              --lib examples/lib/core.bib examples/lib/sample_refs.bib \
              --work /tmp/work --out /tmp/out --case-id demo

# 用示例的两次模型输出重放（不花模型请求）
python run.py --docx /tmp/sample.docx --refs examples/refs.txt \
              --lib examples/lib/core.bib examples/lib/sample_refs.bib \
              --work /tmp/work --out /tmp/out --case-id demo \
              --no-model --pass1 examples/pass1_out.txt --match examples/pass3_match.txt
```

## 示意输出（示例）

`/tmp/out/result.json` 摘要：

```json
{"case_id": "demo", "status": "success",
 "numbering": {"mode": "first-appearance", "n_refs": 11},
 "checks": {"5 分流校验": {"ok": true}, "7 匹配校验": {"ok": true},
            "10 成品校验": {"ok": true}},
 "changes": {"n_ops": 14}}
```

`/tmp/out/sample_cited.docx` 正文片段：

```
…已有研究表明，慢性睡眠限制会导致注意力和执行功能下降[1]。
Smith 等[2] 对 1 200 名成年人进行了为期两周的追踪…
```

```
1  lim2010      LIM J, DINGES D F. A meta-analysis of the impact of short-term sleep deprivation …[J]. Psychological Bulletin, 2010, 136(3): 375-389.
2  smith2018    SMITH J, BROWN L. Sleep duration and cognitive performance in adults[J]. Sleep, 2018, 41(3): 1-12.
```

以上为示例数据，不是平台固定案例的结果。
