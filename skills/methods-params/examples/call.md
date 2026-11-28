# 最小调用示例（示例，非正式案例结果）

## 输入

`examples/case/`（3 个文件）：

- `case.json`：`case_id=methods-demo`，`output_contract` = 目标产物 / 事实与来源映射 / 版本与边界
- `config.yaml`：`lr: 3e-4`、`batch_size: 64`、`epochs: 12`、`seed: 42`、`hardware: 4xA100`、
  `dataset: PubMedQA`、`version: 2.1`、`split: 8:1:1`
- `materials.md`：材料说明三段（硬件与训练参数、数据集与划分、对照基线），
  末尾两行训练日志

## 命令

```bash
python run.py --input examples/case --work work --out output
```

（`--case-id` 可覆盖 `case.json` 里的 `case_id`。）

## 示意输出（示例）

上面这条命令的真实 stdout：

```json
{"ok": true, "status": "success", "params": 8, "missing": 0, "unsourced": 0, "gaps": 2}
```

产物：`methods_section.md`（六节：步骤 / 实验设置 / 参数 / 数据集 / 对照 / 复现与限制）、
`params.tsv`（8 条，每条带 `config.yaml:行`）、`result.json`（含三字段与 `六要素覆盖`）。

`status=success` 的依据：材料抽取到 8 条带来源的参数，五个可做要素（步骤 / 实验设置 /
参数 / 数据集 / 复现与限制）都已落位；两条 `gaps` 是边界说明——本次没给正文（没有对齐可做）、
「对照」按 SKILL.md §5 本包不抽。日志时间戳不再被误判成"无来源数值"。实际数值随材料而变，
此处仅为接口示意，不是组织方固定案例结果。
