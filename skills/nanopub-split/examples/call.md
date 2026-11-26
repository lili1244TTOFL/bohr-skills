# 最小调用示例（示例，非正式案例结果）

## 输入

- `examples/case/manuscript.md`：6 句正文，含"显著降低""采用""低于""导致"等关系词
- `examples/case/case.json`：`case_id`、`paper`、`authors`、`year`、`doi`、`output_contract`

## 命令

```bash
python run.py --input examples/case --work work --out output --case-id np-demo
```

## 示意输出（示例）

```json
{"ok": true, "status": "success", "assertions": 5, "coverage": 0.714, "wide": 0}
```

产物三份：`output/nanopubs.json`、`output/assertions.tsv`、`output/result.json`。

`assertions.tsv` 一行长这样：

```text
NP-0001  睡眠限制   decreases   工作记忆容量   3   12   26   high
```

数值与条数随正文而变，此处只作示例。
