# Case C: an English manuscript (example fixture — synthetic entries, demo only)

这组夹具用来验两件事：**语言切换**（英文四部分标题是否被识别）与**引用写法**（`\cite{}` 而不是 `[@key]`）。

```bash
python run.py --input cases/caseC_en --work work --out output
```

期望：`输入定位.order` 认出 `Introduction / Related Work / Research Gap / Contributions` 四段
（英文别名）；`citations.json.cited` 里是从 `\cite{}` 解出来的键；`status=success`。
