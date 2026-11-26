# 笔记改写规则

本文件给调用方模型用：在 `notes.json` 的候选基础上改写，锚点与块 id 保持不变。

## 输入

`notes.json`：

```json
{"sections":[{"sec_id":"S02","title":"1 Introduction","level":1,"page":2,
              "candidates":{"question":[{"id":"P2-3","page":2,"text":"..."}]}}],
 "card":{"研究问题":{"status":"auto","items":[{"text":"...","anchor":"P2-3","page":2}]}},
 "knowledge":[{"kind":"finding","section":"1 Introduction","page":3,"anchor":"P3-1","text":"..."}]}
```

## 改写要求

1. **章节骨架不动**：`sections[].title`、`sec_id`、顺序与原文一致；只改候选文本。
2. **每条必带锚点**：`anchor` 必须是原文块 id，不得新造。
3. **数字只用原文的**：笔记里出现的数字必须能在锚点原文找到；不要换算或补造。
4. **五字段齐全**：研究问题、数据、方法条件、主要发现、局限；没有就写
   `{"status":"not_reported","items":[]}`，不要用推测填空。
5. **一条一义**：知识点拆到单一概念、变量、方法或结论，不同条目不重复。
6. **保留限定条件**：样本量、条件、适用范围、否定表述必须写进条目，不得省略。
7. **不写评价**：不写"重要""有价值""创新性强"等判断，只写论文自述的内容。

## 输出

改后的 `notes.json`，结构与输入一致；`run.py --notes` 会重新核验。

## 自查

1. `verify.json.ok` 为 true
2. 每条笔记的 anchor 都在 `parse.json` 的块内
3. 卡片五字段无空值（要么有 items，要么 not_reported）
4. 知识点 key 无重复
