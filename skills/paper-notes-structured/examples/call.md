# 最小调用示例（示意输出，非正式案例结果）

## 输入

`examples/paper.md`：4 节小论文（摘要、方法、结果、局限），含数字、样本量与否定表述。

## 调用

```bash
python run.py --pdf examples/paper.md --work work --out output --case-id md-demo

# 平台形态（/input 下带 case.json）
python run.py --input /input --work /app/work --out /app/output
python run.py --input /input --notes /app/work/notes_agent.json --work /app/work --out /app/output
```

第一条命令的真实 stdout：

```json
{"ok": true, "status": "success", "sections": 4, "knowledge": 2, "gaps": ["笔记由内置规则生成：标题块切节 → 五类字段信号挑候选句 → 卡片与知识点；块 id 锚点不变，未做人工改写"]}
```

`output/result.json`（节选，字段与本次运行一致）：

```json
{"case_id":"md-demo","status":"success","notes_status":"auto",
 "stats":{"sections":4,"knowledge":2,"items":10,
          "card_filled":["研究问题","数据","方法条件","主要发现"]},
 "checks":{"有章节":{"ok":true},"锚点":{"ok":true},"数字可回溯":{"ok":true},
           "章节对齐":{"ok":true},"字段完整":{"ok":true},"知识点不重复":{"ok":true}},
 "gaps":["笔记由内置规则生成：标题块切节 → 五类字段信号挑候选句 → 卡片与知识点；块 id 锚点不变，未做人工改写"]}
```

`output/notes.md`（节选）：

```markdown
## 论文卡片

- **主要发现**（## 2 Results，p.1）：We found a 15% decline (p<0.01).  `[P1-2]`
- **局限**：not_reported

## 章节笔记

### ## 2 Results（p.1，1 段）
- finding: We found a 15% decline (p<0.01). `[P1-2]`
```

自动抽取版本核验六项均为 ok，`status=success`，产物就是要交付的那件笔记；`notes_status=auto` 只说明来源是内置规则（切节+字段信号+卡片归并），`gaps` 写明这条边界。带 `--notes` 改写稿时状态同样按核验结果判。示例为演示用，不是组织方固定案例，也不代表评分结果。
