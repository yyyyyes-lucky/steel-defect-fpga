# 本项目案例：PYNQ 数据路径与会话保护

PYNQ 框架通用排查流程见 [general/pynq_dma](../../general/pynq_dma/readme.md)。本案例是一个具体 Zynq/PYNQ 工程，不代表所有 PYNQ 设备具有相同 DMA、缓存或启动语义。

## 项目采用的做法

- 最终路径保留 explicit cache 操作；不能因某次观测到重复维护就推断可以删除所有同步。
- 采用 Q8 staged/minmax 减少重复数据处理。数据转换、缓存维护、传输等待和剩余 PS 工作均需放在系统计时范围内解释。
- 运行时记录 DMA/IP 完成、异常和安全关闭状态；单次成功不替代重复调用与异常恢复检查。

具体实现见 [Q8 路径集成](../../../src/ps/runtime/steel_q8_path_integration_20261025/generated_paths.py) 和 [v10 运行时](../../../src/ps/runtime/steel_runtime_v10_candidate_20261025/runtime_v10.py)。参考与重构建硬件的静态、动态会话材料见[复刻记录](../../../report/reproduction_record.md)。

本案例只验证已记录的接口和软件/硬件组合。换 PYNQ 版本、DMA 类型、驱动或 Overlay 后，应重新查 API/驱动并验证缓存责任、完成条件和恢复方式。

