# 材料说明

本研究在 4 张 A100 上训练，学习率 3e-4，批大小 64，训练 12 个 epoch。
数据集为 PubMedQA v2.1，按 8:1:1 划分，随机种子 42，共 211269 条样本。
对照基线为 BERT-base，基线版本为 v1.2，同预算训练，评价指标为 accuracy，
重复 3 次取平均。验证集用于早停与模型选择，测试集仅用于最终评测。

实验流程：

1. 数据准备：读取 PubMedQA v2.1 原始标注（输入：原始 JSON 标注；输出：train/val/test 三个子集）
2. 微调编码器：按配置文件训练 12 个 epoch（输入：train 子集；输出：checkpoint；判断条件：验证集 accuracy 连续 3 轮不升则早停）
3. 评测：在 test 子集上计算 accuracy（输入：checkpoint 与 test 子集；输出：test_acc）

运行日志：

- 2026-01-01 10:00 start training
- 2026-01-01 18:22 epoch 12 done, val_acc=0.872
