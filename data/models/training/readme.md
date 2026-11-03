# YOLO 微调记录

最终运行权重为上级目录的 [best.pt](../best.pt)。本轮训练使用旧版 best.pt 初始化，进行微调，未恢复旧训练任务的优化器状态（resume=false）。

| 项目 | 实际记录 |
| --- | --- |
| 初始化权重 | initial_weights.pt，旧版检测模型 |
| 训练输入 | 1439 张，见数据集 train_originals.txt |
| 验证输入 | valid 中 179 张 |
| 最大训练轮数 | 60 |
| 自动早停 patience | 15 |
| 最佳权重轮数 | 12 |
| 实际完成轮数 | 27 |
| 输入尺寸 / batch | 512 / 8 |
| 优化器 / 初始学习率 / seed | AdamW / 0.0001 / 42 |

训练日志原文：

> EarlyStopping: Training stopped early as no improvement observed in last 15 epochs. Best results observed at epoch 12, best model saved as best.pt.

第 12 轮验证指标：Precision 0.79977、Recall 0.70043、mAP50 0.77219、mAP50-95 0.43934。上述指标来自本轮验证集，不是独立测试集结果，也不是 FPGA 加速性能指标。

| 文件 | 内容 |
| --- | --- |
| [results.csv](results.csv) | 27 轮训练及验证指标 |
| [training.log](training.log) | 原始训练输出及早停依据 |
| [original_args.yaml](original_args.yaml) | 原始参数归档，保留训练电脑的历史绝对路径 |
| [initial_weights.pt](initial_weights.pt) | 初始化模型，用于追溯本轮微调起点，不是默认演示模型 |
| [模型清单](../model_manifest.json) | 最终权重及初始化权重标识、轮数和输入数量 |
| [可迁移数据配置](../../six_class_steel_surface_defect_dataset/retrain_data.yaml) | 使用相对路径的同一训练清单和验证分区 |

original_args.yaml 用于记录历史参数，其中绝对路径不适合作为另一台电脑的直接运行配置。重新训练可能受软件版本、设备和随机计算差异影响，不能保证产出与已归档权重逐字节一致。
