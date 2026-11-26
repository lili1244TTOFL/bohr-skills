# 最小调用示例（示意输出，非正式案例结果）

## 输入

`examples/paper.md`：4 节小论文（摘要、方法、结果、局限），含数字、样本量与否定表述。

## 调用

```bash
python run.py --input /input --work /app/work --out /app/output
python run.py --input /input --notes /app/work/notes.json --out /app/output
```

## 示意输出

`result.json`（节选）：

```json
{"case_id":"md-demo","status":"partial","notes_status":"auto",
 "stats":{"sections":4,"knowledge":2,"items":10},
 "checks":{"锚点":{"ok":true},"数字可回溯":{"ok":true},
           "章节对齐":{"ok":true},"字段完整":{"ok":true}},
 "gaps":["笔记为规则抽取版本（status=auto），未做人工改写"]}
```

`notes.md`（节选）：

```markdown
## 2 Results
- finding: We found a 15% decline (p<0.01). `[P1-4]`
```

## 说明

示例为演示用，不是组织方固定案例，也不代表评分结果。
