# 第一步：读材料，出键清单（由模型做）

## 为什么要有这一步

脚本只会按键名匹配。不同论文的配置键千差万别：`lr` / `learning_rate` / `optim.lr`；
对照可能写在 `baseline:`，也可能只出现在日志的一行里，甚至只在正文一句话里。

**所以先让模型把材料读一遍，列出真实存在的键，再交给脚本抽。** 不要靠内置规则猜。

## 要做的事

打开 `/input` 下的全部文本材料（配置、日志、代码、正文），逐个看，产出 `schema.json`：

```json
{
  "materials": [
    {"file": "config.yaml", "kind": "配置", "keys": ["lr", "batch_size", "baseline"]},
    {"file": "train.log", "kind": "日志", "keys": ["seed", "epoch"]},
    {"file": "pipeline.py", "kind": "代码", "keys": ["load_data", "train"]}
  ],
  "keys": [
    {"要素": "参数", "名称": "学习率", "值": "0.001", "证据": "config.yaml:3"},
    {"要素": "对照", "名称": "对照对象", "值": "ResNet-50", "证据": "config.yaml:11"},
    {"要素": "对照", "名称": "对照条件", "值": "同预算、同数据划分", "证据": "manuscript.md:12"}
  ],
  "notes": ["正文第 5 段的 82.9% 在材料里找不到来源"]
}
```

## 三条要求

1. **证据必须真实**：`file:line` 要能在材料里对上。对不上就不写。
2. **抽不到就留空**，并在 `notes` 里写"未找到 X"，不要编一个合理的值。
3. **六类要素逐类看一遍**：步骤、实验设置、参数、数据集、对照、复现与限制。
   特别是"对照"——它最常以自由文本出现，而不是键值。

## 交付给下一步

写完 `schema.json` 放在工作目录（`/app/work/schema.json`），再运行：

```bash
python run.py --input /input --work /app/work --out /app/output
```

脚本会读 `schema.json`，把里面的条目并入抽取结果。**没有 schema.json 时脚本退回内置规则**，
并在 `uncertainty` 里注明"只用了内置规则，可能漏键"。
