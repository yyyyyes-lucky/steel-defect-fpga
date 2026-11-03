# 优化筛选与取舍记录

最终配置：v10 + v9.1 combined + Q8 staged/minmax + explicit cache + 已选掩膜算法。下表汇总各候选方案的历史实验结果及选型依据。原始报告中的 `pass` 首先表示其测试定义通过，不等于性能晋级。

| 方案 | 验证达到的层级 | 结论及最终取舍 | 证据 |
| --- | --- | --- | --- |
| v11 CCL 统计 | 全量 187 系统用例、代表配对计时 | class2 代表约 1.02495×，其他场景有快有慢；未以旧全量功能报告证明稳定全量收益，不纳入最终配置 | [full](../data/optimization_evidence/v11_ccl_full.json)、[smoke](../data/optimization_evidence/v11_ccl_smoke.json) |
| v12 阶段筛选 | 旧硬件上的分段测量 | class2 selection chain 约 145.18 ms，占当次约 16.92%；是优化预算，不是新硬件实测收益 | [阶段报告](../data/optimization_evidence/v12_segment_screen.json) |
| v13 class2 融合 | 184 帧/403 ROI 的 PS/PL 全量重复对照 | 中位总量加速约 1.00566×；同 v13 Overlay 内比较，不能写成实际 v10/v13 直接对决。BRAM 136.5/140（97.50%），最终未采用 | [全量](../data/optimization_evidence/v13_full_compare.json)、[资源](../data/optimization_evidence/v13_utilization.rpt) |
| v14 描述子归约 | 原语、184 帧/403 ROI 输出检查 | 核输出正确，局部 DMA 调用计时不能证明系统加速；与后续 v15 集成分开看 | [full](../data/optimization_evidence/v14_descriptor_full.json) |
| v15 描述子集成 | 184 帧/403 ROI，字段与分支检查 | 全量总耗时 166493.770 → 176480.734 ms，增加约 5.998%；保留最终原路径 | [full](../data/optimization_evidence/v15_integration_full.json) |
| v16 夹杂 Gaussian 复用 | 全 184 帧输出比较，36 帧受影响 | 受影响帧总耗时 31001.552 → 31232.622 ms，增加约 0.745%；夹杂对应路径保留 PS。不能写成全部 184 帧慢 0.745% | [full](../data/optimization_evidence/v16_inclusion_full.json) |
| Q8 staged/minmax | 184 帧/403 ROI 的候选正确性与保留输出检查 | 最终采用；此报告仅候选全量计时，不独立提供配对加速比 | [full184](../data/optimization_evidence/q8_full184.json) |
| F1 边缘计数融合 | 560 帧，其中 336 计时、224 预热，8 个独立会话对 | 主口径 430.125 → 427.463 ms，节省 0.619%；会话对 95% 节省区间 [-0.316837, 5.651206] ms 跨零，5/8 对更快，未满足原定晋级条件 | [原始](../data/optimization_evidence/f1_version_select.json)、[分析](../data/optimization_evidence/f1_f3_analysis.json) |
| P2/F3 | 新核检查与 7 用例、28 计时帧初筛 | 431.499 → 436.745 ms，慢约 1.216%；目标 class2 慢约 6.899%，两种顺序收益方向不同，停止该实现，不组合 F1+F3 | [初筛](../data/optimization_evidence/p2_f3_check.json)、[分析](../data/optimization_evidence/f1_f3_analysis.json) |

F3 的 selector 混合调用段包含准备/flush、launch/DMA/core/wait、invalidate/decode；其中等待段不能全叫纯 DMA 搬运。v15/v16 退化也不能无依据全归因于 DMA，剩余 PS、打包/解包和调度都可能参与。

以上是各实验内部的比较，不能跨实验比较绝对时间，不能叠加百分比，也不能与新模型/新掩膜下的最终静态演示混成同一性能表。功能实现成功但整体收益不成立，是工程取舍证据，不意味着该算法在所有实现下都不适合硬化。

最终参考硬件 BRAM 37.5/140（26.79%）；本次新构建采用相同架构，资源及板测见 [复刻记录](reproduction_record.md)。新旧两次静态约 3.354×/3.329× 是相对同板纯 ARM 的后处理加速，与本表的历史候选对照对象不同。
