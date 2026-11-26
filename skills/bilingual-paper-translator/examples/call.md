# 最小调用示例（示意输出，非正式案例结果）

## 输入

`examples/paper.txt`（2 段英文摘要，含数字、引用、图表号、否定）：

```
Sleep restriction impairs working memory in 60 young adults [1]. We did not observe
a recovery after 2 days of sleep restriction (Fig. 2). The effect size was 0.42
(Smith et al., 2018), and 15% of participants showed no decline.
```

`examples/glossary.md`：

```
sleep restriction	睡眠限制	核心术语
working memory	工作记忆	核心术语
n-back task	n-back 任务	计量工具
```

## 调用

```bash
python run.py --input /input --work /app/work --out /app/output
# status=needs_translation，待译 6 段

# 按 rules/translate_rules.md 逐段翻译，产出 /app/work/targets.jsonl
python run.py --input /input --targets /app/work/targets.jsonl --out /app/output
```

## 示意输出

`result.json`（节选）：

```json
{
 "case_id": "tr-demo",
 "status": "success",
 "stats": {"segments": 6, "translated": 6, "glossary_terms": 3},
 "checks": {"完整性": {"ok": true}, "术语一致": {"ok": true},
            "数字引用公式": {"ok": true}, "否定关系": {"ok": true}},
 "gaps": []
}
```

`bilingual.md`（节选）：

```markdown
## P001　第 1 页

**P001S001**　Sleep restriction impairs working memory in 60 young adults [1].
> 睡眠限制会损害 60 名青年人的工作记忆[1]。
```

反例（删掉数字、改掉术语）时 `verify.json` 报：

```json
{"ok": false, "counts": {"V01": 6, "V06": 3},
 "problems": [{"uid": "P001S001", "code": "V01", "detail": "数字/单位缺失 ['60']"}]}
```

## 说明

示例数据与译文均为演示用，不是组织方固定案例，也不代表评分结果。

## 命令

```bash
# 分段 + 术语约束翻译 + 核验
python run.py --input examples --out output

# 指定术语表与目标语言
python run.py --input examples --glossary examples/glossary.md --target zh --out output
```

产物：`bilingual.md`、`segments.json`、`verify.json`、`result.json`。
