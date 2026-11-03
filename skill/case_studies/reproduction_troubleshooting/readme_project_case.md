# 本项目案例：跨环境复现问题

通用诊断流程见 [general/reproduction_troubleshooting](../../general/reproduction_troubleshooting/readme.md)。以下问题和修正仅针对本项目使用的工具、Notebook 和交付结构。

## 暴露的问题与修正

- 构建命令曾混入旧版 HLS 启动方式；工程构建步骤按实际 Vivado/Vitis 2026.1 环境整理，并需先确认 IP 生成/导出阶段完成。
- Notebook 会保存执行序号和输出，不能再要求 Notebook 自身文件哈希保持发布时原样；运行入口保留真实依赖、接口与设备错误。
- Python 本体与项目依赖安装是两件事；多个解释器环境下需确认实际启动器使用的解释器。
- 板端 JSON API 和 Windows 演示网页是不同服务，板端 READY 不能代替网页启动确认。
- 静态网页交付需同时检查实际图像资源和元数据，并验证离线/换目录打开；仅确认 HTML 存在不够。

复现证据见 [复刻记录](../../../report/reproduction_record.md)、[结果网页](../../../data/reproduction_20261031/index.html) 和[构建材料](../../../build/reference/reproduction_20261031/)。此处结论限于本项目已有复现，不能据此保证其他电脑、工具安装或板卡环境必然成功。

