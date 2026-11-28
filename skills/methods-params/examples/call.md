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
{"ok": true, "status": "success", "items": 34, "params": 6, "steps": 3, "refs": {"步骤": 3, "实验设置": 7, "参数": 6, "数据集": 11, "对照": 6, "复现与限制": 6}, "missing": 0, "unsourced": 0, "conflicts": 0, "gaps": 1, "questions": 0}
```

产物：`methods_section.md`（六节：步骤 / 实验设置 / 参数 / 数据集 / 对照 / 复现与限制）、
`params.tsv`（34 条，每条带 `config.yaml:行` 或 `materials.md:行`）、`result.json`（含三字段与 `六要素覆盖`）。

`status=success` 的依据：材料抽取到 34 条带来源的条目（其中参数 6 条），六类要素都有带证据条目。唯一一条 `gaps` 是边界说明——本次没给正文（没有对齐可做）。`对照` 已按 SKILL.md §5 从材料抽到四项：基线对象 `BERT-base`（materials.md:5）、对照条件 `同预算`（materials.md:5）、评价指标 `accuracy`（config.yaml:20）、指标值 `val_acc=0.872`（materials.md:17）。日志时间戳不再被误判成"无来源数值"。实际数值随材料而变，此处仅为接口示意，不是组织方固定案例结果。
