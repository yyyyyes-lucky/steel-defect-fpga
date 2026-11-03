# 证据索引

## 最终工程与入口

| 内容 | 位置 |
| --- | --- |
| 完整复现步骤 | [根 README](../readme.md) |
| Windows 程序、动态网页、依赖 | [src/host](../src/host/) |
| ARM/板端运行模块 | [src/ps/runtime](../src/ps/runtime/) |
| HLS / RTL | [src/hls](../src/hls/)、[src/rtl](../src/rtl/) |
| 硬件统一构建入口 | [build.ps1](../build/build.ps1) |
| 板端装配与运行入口 | [package_runtime.py](../board/package_runtime.py)、[静态 Notebook](../board/run_static.ipynb)、[动态 Notebook](../board/run_dynamic.ipynb) |
| 模型和来源 | [模型目录](../data/models/)、[微调记录](../data/models/training/readme.md) |
| 完整数据集与实际训练清单 | [数据集说明](../data/six_class_steel_surface_defect_dataset/readme.md) |

## 最终验证证据

| 想确认的问题 | 原始材料 |
| --- | --- |
| 原有硬件和重构建硬件静态效果 | [双结果入口](../data/reproduction_20261031/index.html) |
| 每次是否完整完成、480 条测量及输出差异 | [参考 JSON](../data/reproduction_20261031/reference/static/static_compare.json)、[重构建 JSON](../data/reproduction_20261031/rebuilt/static/static_compare.json) |
| 动态是否执行和安全关闭 | [参考会话](../data/reproduction_20261031/reference/dynamic/v10_session.json)、[重构建会话](../data/reproduction_20261031/rebuilt/dynamic/v10_session.json) |
| 电脑与板端是否对应同一帧 | [参考对应表](../data/reproduction_20261031/reference/dynamic/host_board_match.json)、[重构建对应表](../data/reproduction_20261031/rebuilt/dynamic/host_board_match.json) |
| 新 bit/hwh 是否对应本次板测 | [复刻汇总](../data/reproduction_20261031/summary.json)、[构建摘要](../build/reference/reproduction_20261031/build_summary.json)、[新硬件文件](../board/rebuilt_20261031/) |
| 新硬件资源、时序、DRC | [资源](../build/reference/reproduction_20261031/reports/gaussian_v10_utilization.rpt)、[时序](../build/reference/reproduction_20261031/reports/gaussian_v10_timing.rpt)、[DRC](../build/reference/reproduction_20261031/reports/gaussian_v10_drc.rpt) |
| HLS 仿真、综合、IP 导出与系统构建 | [本次阶段日志](../build/reference/reproduction_20261031/logs/)、[阶段记录](../build/reference/reproduction_20261031/receipts/)、[原有仿真结果](../sim/reference/) |

## 设计决策与技能材料

- [硬件候选原始报告](../data/optimization_evidence/readme.md)与[优化取舍](optimization_history.md)。
- [AI 协助的工程决策与实践心得](llm_collaboration.md)及[可复用技能包](../skill/readme.md)。前者说明人工判断、采取/未采取的方案；后者明确区分通用方法和本项目案例，并提供对应入口。
- 历史候选报告仅代表各自实验的输入、版本和计时范围；当前硬件验证结论见[复刻记录](reproduction_record.md)。

