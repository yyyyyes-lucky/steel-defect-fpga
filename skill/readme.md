# 技能包

本目录明确分开两类材料：

- [`general/`](general/)：通用工程方法和模板。它们不绑定本项目的题目、数据集、版本、固定路径或板卡型号，可以迁移到其他工程；使用时必须按目标环境重新确认接口和参数。
- [`case_studies/`](case_studies/)：本项目案例。它们记录本项目的版本、测量、图像检查和采用/停止决定，只用于说明通用方法如何落地。

## 通用技能入口

| 技能 | 用途 | 入口 |
| --- | --- | --- |
| 硬件候选筛选 | 用系统级替换预算筛选 FPGA/GPU 等候选 | [general/hardware_selection](general/hardware_selection/readme.md) |
| PYNQ DMA 与缓存 | 按 PYNQ API、Overlay 和实际协议排查数据路径 | [general/pynq_dma](general/pynq_dma/readme.md) |
| 成对性能比较 | 用同输入、同边界和配对数据比较实现 | [general/performance_comparison](general/performance_comparison/readme.md) |
| 图像算法验证 | 检查检测框、候选区域、掩膜和人工复核 | [general/algorithm_validation](general/algorithm_validation/readme.md) |
| 复现问题定位 | 按执行位置、版本、文件布局和首个错误排查 | [general/reproduction_troubleshooting](general/reproduction_troubleshooting/readme.md) |

## 本项目案例入口

| 案例 | 内容 |
| --- | --- |
| [硬件候选筛选](case_studies/hardware_selection/readme_project_case.md) | 候选版本和系统级取舍 |
| [PYNQ 数据路径](case_studies/pynq_dma/readme_project_case.md) | 本项目缓存、Q8 路径和会话保护 |
| [性能比较](case_studies/performance_comparison/readme_project_case.md) | 两组硬件静态对照和历史统计 |
| [图像算法验证](case_studies/algorithm_validation/readme_project_case.md) | 裂纹框、划痕分支和候选掩膜复核 |
| [复现排错](case_studies/reproduction_troubleshooting/readme_project_case.md) | 工具、Notebook、服务和网页问题 |

## 使用顺序

先读对应的 `general/` 文档，了解输入、步骤、证据要求和限制；需要参考本项目决策时，再读对应的 `case_studies/`。模板里的示例值只是字段示意，不能当成其他工程的默认参数。

性能比较脚本位于 [general/performance_comparison/compare_pairs.py](general/performance_comparison/compare_pairs.py)，只离线读取 JSON，不连接板卡、不加载模型，也不是演示入口的预检。
