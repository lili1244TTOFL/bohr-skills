# 受控关系词表

只认显式关系词，命中才生成断言。本文件是人读副本；**代码里的 `RELATIONS`（`run.py`）
才是执行口径**，改词表要改代码并同步这里。

同一句命中多个关系词时：因果类（`causes`）优先，其余取句内最早命中。

| pattern | relation | 说明 |
|-|-|-|
| 显著提高\|显著提升\|显著增加\|显著改善\|显著增强 | increases | 方向为增 |
| 显著降低\|显著减少\|显著下降\|显著削弱 | decreases | 方向为减 |
| 提高\|提升\|增加\|改善\|增强（可带"了"） | increases | 一般增量 |
| 降低\|减少\|下降\|削弱\|抑制（可带"了"） | decreases | 一般减量 |
| 优于\|高于\|好于\|领先于 | outperforms | 比较关系，主体优 |
| 低于\|劣于\|不如 | underperforms | 比较关系，主体劣 |
| 采用\|使用\|基于\|利用；use/employ/adopt（带 s/d/ed/ing） | uses | 方法或工具 |
| 测量\|评估\|检验\|验证；measur/assess/evaluat/verif/quantif | measures | 测量动作 |
| 导致\|引起\|造成；cause/lead to/result in | causes | 因果 |
| 相关\|关联；correlat/associat … with | correlates_with | 相关，不含因果 |
| 支持；support(s/ed/ing) | supports | 证据对结论的支撑 |
| exhibit/display/show + significantly + higher/lower/greater/smaller；be + 比较级 | compares | 组间比较 |
| 表明\|显示\|说明\|发现\|指出；show/indicate/suggest/demonstrate/reveal/find/found | shows | 陈述支撑 |
| 存在\|具有\|属于\|构成 | has_property | 属性 |
