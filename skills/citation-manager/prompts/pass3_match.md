# 提示词：句—库匹配（请求 3）

## 任务

给每个 `CITE` 句指定库内条目。只能选库内条目，每句 0–3 个。

## 输入

1. `lib_min.tsv`：全库精简清单 `id  type  authors  title  container  year  doi`
2. `refs_orig.tsv`：稿件原有参考文献表 `orig_no  id  title  authors  container  year  doi`（已并入库，`id` 为规范条目）
3. `match_input.tsv`：`uid  section  句子  cands  status`

`cands` 形如 `id|score|src|匹配词`，`; ` 分隔。`src=MARK` 表示该句已有 `[n]` 指向该条目，**必须保留**；`src=SEM` 为语义候选，可弃可换。

`refs_orig.tsv` 用途：确认哪些条目是原稿已有的。原编号只作对照，**输出里不得出现原编号**，只写库 `id`。

## 输出

每句一行：

```
uid|编号列表|置信度|依据词
```

- 编号列表：库 `id`，多个用 `,` 分隔；不给编号写 `NONE`
- 置信度：`H` / `M` / `L`
- 依据词：句中匹配词或原标记，如 `[1]`、`prefrontal`、`量表`
- `NONE|H`：确认不需新增（上文已覆盖、本文自有、结构性）
- `NONE|L`：疑似库内缺条目，交人工补库

末行：

```
#MATCH n=<句数> n_assign=<给出编号的句数> n_none=<NONE 句数> n_new=<需补库句数>
```

## 规则

1. 不改写句子，不改动原文标记。
2. 不引用库外条目；库内没有就 `NONE|L`。
3. 讨论段、结果段的比较句只给被比较方，不给本文工作。
4. 一句多引按语义先后排列。
5. 方法段同一句最多 3 个：软件、量表、数据集、任务范式各算一个。
6. 已有 `[n]` 与 `MARK` 冲突时以 `MARK` 为准，并在依据词写 `[n]`。
7. 候选全为 `SEM` 且语义不匹配时，不硬配，给 `NONE|L`。
8. 原稿已引条目见 `refs_orig.tsv`，复用同一 `id`，不新建副本；库内与稿件条目重复时以库条目为准。

## 异常

| 情况 | 输出 |
|-|-|
| 有 `[n]` 但 `refs` 无对应条目 | `uid|?|L|refs 缺条目 [n]` |
| 句子无候选且明显需引 | `NONE|L` |
| 同一 `[n]` 指向库内多条重复 | 取 `lib_min.tsv` 中题名无尾点、页码含 `-` 的一条，依据词写 `重复项` |
| 输入 `uid` 重复 | 只处理第一条，末行加 `#WARN DUP uid` |

## 样例

```
P004:S003|smith2018|H|[1]
P005:S002|lim2010,nback2018,pupil2021|M|[4-6]
P005:S003|rosenberg1965,wechsler2008|M|量表
P006:S003|psychopy2019,nback2018|H|[9] 与 n-back 任务
P004:S004|NONE|H|上文[1]已覆盖
P009:S003|slowwave2020|H|[11]
#MATCH n=6 n_assign=5 n_none=1 n_new=0
```
