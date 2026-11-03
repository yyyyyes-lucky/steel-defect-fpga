# 历史候选的关键证据

收录硬件候选优化的原始比较报告与资源、时序记录，用于说明方案验证范围及最终取舍。这些记录对应各自的历史实验，不属于当前演示运行依赖。

| 文件 | 内容与边界 |
| --- | --- |
| [v11_ccl_full.json](v11_ccl_full.json)、[v11_ccl_smoke.json](v11_ccl_smoke.json) | 8 连通统计核的系统功能与代表配对计时；并非完整几何/标签硬化 |
| [v12_segment_screen.json](v12_segment_screen.json) | 阶段计时与理想预算；没有新增硬件收益证明 |
| [v13_full_compare.json](v13_full_compare.json) | 同 v13 Overlay 中 PS/PL class2 融合全 184 帧对照 |
| [v13_utilization.rpt](v13_utilization.rpt)、[v13_timing.rpt](v13_timing.rpt) | v13 系统资源与实现时序 |
| [v14_descriptor_full.json](v14_descriptor_full.json) | 描述子原语与 full184/403 ROI 输出；只有局部调用计时 |
| [v15_integration_full.json](v15_integration_full.json) | 部分描述子接入系统后整条路径退化 |
| [v16_inclusion_full.json](v16_inclusion_full.json) | 复用已有 Gaussian 到夹杂分支，关注 36 个受影响帧 |
| [f1_version_select.json](f1_version_select.json) | 8 个独立会话对、336 计时帧的 F1 定选 |
| [p2_f3_check.json](p2_f3_check.json) | 7 用例、28 计时帧的 F3 初筛，不是 full184 |
| [f1_f3_analysis.json](f1_f3_analysis.json) | 原配对数据离线汇总、置信区间及不晋级决策 |
| [q8_full184.json](q8_full184.json) | Q8 候选全量正确性；该报告未重跑基线，不能单独推加速比 |

不同实验输入、软件版本、计时边界不同，不横向排列绝对耗时选“最快版本”，不把其百分比叠加到最终 120 张演示。结论表见 [optimization_history.md](../../report/optimization_history.md)。
