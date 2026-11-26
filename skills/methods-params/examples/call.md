# 最小调用示例（示例，非正式案例结果）

## 输入

- `config.yaml`：`lr: 0.001`、`batch_size: 64`、`epochs: 100`、`seed: 42`、`dataset: ImageNet-1k`
- `train.log`：训练日志
- `pipeline.py`：`load_data / build_model / train` 三个公开函数
- `manuscript.md`：正文，写了学习率与批大小，**漏写种子**，并出现 `82.9%` 这个材料里没有的数
- `case.json`：`output_contract` = 目标产物 / 事实与来源映射 / 版本与边界

## 命令

```bash
python run.py --input examples/case --work work --out output --case-id demo
```

## 示意输出（示例）

```json
{"ok": true, "status": "partial", "params": 10, "missing": 3, "unsourced": 2, "gaps": 7}
```

`gaps` 会包含三类：正文漏写 `随机种子=42`、正文数值 `82.9` 无来源、六类要素缺"对照"。

实际数值随材料而变，此处仅为示意。
