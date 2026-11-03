# 本项目案例：同输入板端性能比较

通用配对方法见 [general/performance_comparison](../../general/performance_comparison/readme.md)。以下是本项目测量，不能代表相机到网页的整条端到端延迟。

## 静态对照

参考硬件和重构建硬件分别使用 120 张 valid 图片、317 个 ROI、两轮共 480 条 ARM/FPGA 路径测量。整体加速按 ARM 总耗时除以 FPGA 路径总耗时计算：

| 硬件 | ARM 平均 | FPGA 路径平均 | 板端后处理加速 |
| --- | ---: | ---: | ---: |
| 参考硬件 | 2353.347 ms | 701.569 ms | 3.354× |
| 重构建硬件 | 2334.491 ms | 701.157 ms | 3.329× |

两次静态实验的语义输出摘要一致；少量掩膜像素与浮点字段差异保留在原报告中。它们证明的是指定输入下的板端后处理比较，不是 YOLO 或相机系统的端到端加速，也不是掩膜精度评价。

## 历史候选统计

F1 的平均节省约 0.619%，但 8 个独立会话对的 95% 节省区间跨零，未晋级。P2/F3 初筛整体慢约 1.216%，停止该实现。其他候选的对象和范围见[硬件筛选案例](../hardware_selection/readme_project_case.md)。

## 证据

- [两次复刻结果网页](../../../data/reproduction_20261031/index.html)
- [参考硬件原始数据](../../../data/reproduction_20261031/reference/static/static_compare.json)
- [重构建硬件原始数据](../../../data/reproduction_20261031/rebuilt/static/static_compare.json)
- [复刻记录](../../../report/reproduction_record.md)

以上测量不能外推到其他输入分布、硬件或计时边界。

