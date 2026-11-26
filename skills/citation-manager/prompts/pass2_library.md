# 提示词：库整理与来源核验（请求 2）

## 任务

输入文献库。一次输出四件事：关键词、格式错误、重复组、来源核验结论。不回写、不翻译、不改写条目字段。

## 输入

1. `rules/gbt7714.md`
2. `library.tsv`，首行表头，Tab 分隔：

```
id	type	authors	title	container	year	volume	issue	pages	doi	publisher	raw	doi_online
```

`raw` 是原始导出文本；`doi_online` 由脚本给出，取值 `registered` / `not_found` / `offline` / `not_checked_online` / `malformed` / `missing`。

3. `doi_status.json`：逐条 DOI 的核验记录（联网时含 HTTP 状态）。

## 输出

每条一行 JSON：

```json
{"id":"smith2018","kw":["sleep duration","cognitive performance"],"fmt":[],
 "dup":{"group":"G1","same_as":["lib011"],"conf":0.99,"basis":["D1","D2"]},
 "verify":{"doi_status":"registered","verdict":"verified","evidence":"DOI 10.1093/sleep/zsy001 已注册；年/卷/页与条目一致"},
 "near":[],"keep":true}
```

字段：

- `kw`：3–8 个，取自题名，保留原词形；中文不拆词，英文保留大小写与连字符。
- `fmt`：错误码 F01–F20（见第 4 节），无错为 `[]`。
- `dup.group`：`G1`、`G2`…；不重复为 `null`。同组成员互指 `same_as`，`conf` 0–1，`basis` 取 D1–D6。
- `verify.doi_status`：照抄 `doi_online`，不得改写。
- `verify.verdict`：`verified` / `suspect` / `unverifiable`。
- `verify.evidence`：一句话，写清依据（DOI 状态、字段互证、与哪条冲突）。
- `near`：疑似但决定不合并的条目 `[{"id":"...","reason":"..."}]`，无则 `[]`。
- `keep`：`true` 保留；`false` 建议并入组内 `keep:true`。

末行：

```json
{"pass2":{"n":12,"kw":12,"err":3,"dup_groups":2,"verified":9,"suspect":1,"unverifiable":2}}
```

## 规则

1. **DOI 只依据输入**：`registered` → 注册存在；`not_found` → 疑点；`offline` / `not_checked_online` → 只能 `unverifiable`；`malformed` → `suspect`。不得编造检索结果、不得声称"已核实"。
2. 条目字段自相矛盾（年与卷、页码倒序、类型标识与题名冲突、ISSN/ISBN 校验位错）→ `suspect`，evidence 写明哪一处。
3. 提示词外的事实（影响因子、期刊分区、作者单位）一律不写。
4. 合并必须给判据；仅"作者同名"不合并，进 `near` 并写理由。
5. 预印本与正式版判同组，保留正式版；中英文题名判同组，`basis` 加 `D3`。
6. `authors`、`container`、`year`、`doi` 只用于比对，输出里不得改写。

## 判据码

| 码 | 含义 |
|-|-|
| D1 | DOI 相同 |
| D2 | 题名归一化后相同（去标点、大小写、空格） |
| D3 | 中英文题名为互译 |
| D4 | 作者 + 年 + 刊名相同 |
| D5 | 预印本与正式版 |
| D6 | 会议版与期刊版 |

## 格式错误码

| 码 | 含义 | 码 | 含义 |
|-|-|-|-|
| F01 | 缺类型标识 | F11 | 全角标点或缺分隔空格 |
| F02 | 类型标识不合法 | F12 | DOI 格式错 |
| F03 | 缺作者 | F13 | 英文作者写成"名 姓" |
| F04 | 作者分隔符错 | F14 | 学位论文缺保存地/单位 |
| F05 | 作者超 3 位未省略 | F15 | 电子资源缺日期或 URL |
| F06 | 缺题名 | F16 | 会议论文缺 `//` 或论文集名 |
| F07 | 期刊条目缺刊名 | F17 | 条目末标点重复/缺失 |
| F08 | 缺年份 | F18 | 编号重复 |
| F09 | 缺卷或期 | F19 | 年/卷/期/页顺序错 |
| F10 | 缺起止页或页码格式错 | F20 | 页码倒序 |

## 样例

```
{"id":"lim2010","kw":["sleep deprivation","cognitive","meta-analysis"],"fmt":[],"dup":{"group":null,"same_as":[],"conf":0.0,"basis":[]},"verify":{"doi_status":"registered","verdict":"verified","evidence":"DOI 10.1037/a0018883 已注册；年卷页自洽"},"near":[],"keep":true}
{"id":"pre2022","kw":["attention-guided fusion","low-light"],"fmt":[{"code":"F09","note":"预印本无卷期"}],"dup":{"group":"G2","same_as":["pub2023"],"conf":0.92,"basis":["D2","D5"]},"verify":{"doi_status":"registered","verdict":"verified","evidence":"arXiv DOI 已注册，属预印本"},"near":[],"keep":false}
{"id":"notag2019","kw":["类型标识","条目"],"fmt":[{"code":"F01","note":"无类型标识"}],"dup":{"group":null,"same_as":[],"conf":0.0,"basis":[]},"verify":{"doi_status":"missing","verdict":"unverifiable","evidence":"无 DOI，字段不足以核验"},"near":[{"id":"x","reason":"题名相近但作者不同，疑非同一文献"}],"keep":true}
```

## 异常

| 情况 | 处理 |
|-|-|
| `library.tsv` 缺列 | 按表头取值，缺列当空；末行加 `#WARN COLS` |
| 同一 id 出现两次 | 都输出，末行加 `#WARN DUP_ID` |
| 条目全空 | 该条 `fmt:[{"code":"F06"}]`，`verify.verdict=unverifiable` |
| 库超过单次上限 | 按 id 分块，每块一次请求，块首 `#BATCH k/n` |
