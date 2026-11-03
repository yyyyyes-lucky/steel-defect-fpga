# 钢材表面缺陷 FPGA 项目：从解压到上板的复刻教程

适用场景是：**更换 Windows 电脑，使用 TUL PYNQ-Z2 开发板和兼容的 PYNQ 板端环境；需要重新构建硬件时使用指定工具链。**

最终运行版本为：v10 硬件 + v9.1 软件 + Q8/cache + 已选掩膜算法；划痕沿用旧算法，不叠加 F1/F3。YOLO 在 Windows 电脑运行，ARM 基线和 FPGA 路径都在开发板执行后处理。模型已放在 `data/models/best.pt`。


参考 bit/hwh 和 2026-10-31 归档的重构建 bit/hwh 均已完成静态及短时动态板测，各有对应结果，详见下方复刻证据。板端运行环境为兼容版本的 PYNQ。

2026-10-31 已板测的 c3078385… 位流保存于 `board/rebuilt_20261031/`，对应构建报告在 `build/reference/reproduction_20261031/`。第 15 节说明新构建后的上板步骤。


## 工程目录与验证入口

本工程包含源码、硬件构建材料、演示程序、验证记录和可复用技能。

| 阅读目的 | 入口 |
| --- | --- |
| 阅读系统设计报告 | [钢材表面缺陷检测系统设计报告](report/steel_surface_defect_design_report.docx) |
| 运行静态/动态演示或从源码复刻 | 继续阅读本 README 的第 1—17 节 |
| 看原有/重构建硬件的静态网页和动态记录 | [两次硬件证据](data/reproduction_20261031/index.html) |
| 查性能、资源、仿真与构建原始依据 | [证据索引](report/evidence_index.md) |
| 查看复刻与优化记录 | [报告材料目录](report/readme.md) |
| 看历史候选为何采用或未采用 | [优化取舍](report/optimization_history.md) |
| 查看完整数据集与模型微调记录 | [数据集说明](data/six_class_steel_surface_defect_dataset/readme.md)、[训练记录](data/models/training/readme.md) |
| 看 AI 协助的工程决策与人工取舍 | [协作记录](report/llm_collaboration.md) |
| 复用方法、提示词、模板和离线工具 | [技能包](skill/readme.md) |

## 本次复刻验证（2026-10-31）

参考硬件和重构建硬件均已完成静态对照及短时动态运行。查看 [两次硬件结果入口](data/reproduction_20261031/index.html) 或 [证据说明](data/reproduction_20261031/readme.md)。新构建硬件静态加速约 3.329 倍，动态 12 帧正常结束；具体范围及时间说明见证据目录。

交付包保留最近两组硬件验证结果：参考硬件 fd1e28f6…，以及本次 `v10_20261031_141207_691` 重构建的 c3078385… 位流。两组各自包含静态对照和动态会话记录；默认运行配置使用参考硬件。

## 1. 先选择自己要做的事情

| 目的 | 需要什么 | 从哪里开始 |
| --- | --- | --- |
| 查看已完成的静态演示结果 | Windows、浏览器 | 第 3 节 |
| 用现有硬件重新测量静态加速 | Windows Python、开发板、兼容 PYNQ 环境 | 第 4 至 8 节 |
| 接摄像头进行新的动态检测 | 上述条件、USB 摄像头、电脑端 Python 依赖 | 第 4 至 7 节，再做第 9 至 13 节 |
| 从源码重新生成硬件并验证 | 上述条件、匹配的 HLS/Vivado 工具及器件支持 | 第 14 至 15 节 |

使用包内已有硬件运行演示，不需要先安装 Vivado。更换电脑但继续使用原板卡和原 SD 卡时，不必仅因电脑更换而重装板端系统。另一块相同型号板卡仍须先准备兼容的 PYNQ 系统和驱动环境。

### 1.1 三种操作位置不要混淆

| 本文标记 | 在哪里操作 | 如何打开 |
| --- | --- | --- |
| Windows PowerShell | 电脑 | 开始菜单搜索 PowerShell |
| 板端 Terminal | 开发板 Linux | 在开发板 Jupyter 中新建 Terminal |
| 板端 Notebook | 开发板 Python 内核 | 在开发板 Jupyter 中打开 `.ipynb`，用 Shift+Enter 运行单元格 |

`127.0.0.1:8092` 是电脑上的演示网页；`开发板IP:8767` 是板端后处理服务。两者不是同一个地址。下文 `192.168.2.99` 只是板端 IP 示例，必须替换为实际地址。不要将 Windows 路径直接填入板端 Terminal。

### 1.2 系统实际执行什么

- 静态验证：已冻结的图片和 YOLO ROI 输入，分别交给同一块板卡上的 ARM 软件路径、FPGA 路径，比较输出和板端后处理耗时。
- 动态检测：Windows 采集 USB 摄像头图像，运行 YOLO，把灰度图和检测框发给开发板；开发板完成后处理后返回结果，电脑网页显示并自动保存。
- FPGA 不运行本项目的完整 YOLO 网络。裂纹检测框和置信度来自电脑上的 YOLO，不能称为整套 YOLO 已在 FPGA 上运行。

## 2. 解压并认识目录

建议使用一个新的、较短的英文目录，例如 `D:\fpga_repro`。以下路径只是示例；没有 D 盘时可以换成其他盘。不要与以前解压的版本合并覆盖。

### 2.1 用资源管理器解压

1. 将 `steel_defect_fpga.zip` 放到 `D:\fpga_repro`。
2. 右键 ZIP，选择“全部解压”。
3. 找到同时包含 `readme.md`、`board`、`src`、`data` 的那一层目录，这一层才是项目根目录。
4. 本教程假定该目录是 `D:\fpga_repro\steel_defect_fpga`。如果实际多了一层同名目录，请进入最里面包含上述文件的目录。

也可以在 **Windows PowerShell** 中解压。和资源管理器方式二选一，不要重复解压到同一个目录：

```powershell
New-Item -ItemType Directory -Path "D:\fpga_repro" -Force
Expand-Archive -LiteralPath "D:\fpga_repro\steel_defect_fpga.zip" -DestinationPath "D:\fpga_repro"
Set-Location "D:\fpga_repro\steel_defect_fpga"
Get-Location
Test-Path .\readme.md
Test-Path .\board\package_runtime.py
Test-Path .\data\models\best.pt
```

最后三项应全部为 `True`。如果为 `False`，先纠正目录，不要继续安装或打包。每次打开新的 PowerShell，都要重新执行 `Set-Location` 进入项目根目录。

### 2.2 目录用途

| 路径 | 内容 |
| --- | --- |
| `src/host` | Windows 摄像头程序、动态网页和环境安装脚本 |
| `src/ps/runtime` | 板端 Python 程序及运行依赖 |
| `src/hls` | Gaussian、ellipse7、blur3 的 HLS 源码 |
| `src/rtl` | RTL 与系统连接 Tcl |
| `sim` | 测试程序、输入和历史仿真记录 |
| `build/build.ps1` | 从源码构建硬件的统一入口 |
| `build/reference` | 历史构建、资源、时序和 DRC 证据 |
| `build/output` | 本机之后生成的板端包、硬件构建和动态结果 |
| `board/hardware` | 已上板验证的参考 bit/hwh |
| `board/run_static.ipynb`、`board/run_dynamic.ipynb` | 打包时使用的板端 notebook 模板 |
| `data/models` | 模型与模型来源记录 |
| `data/static_inputs` | 静态演示的冻结输入 |
| `data/six_class_steel_surface_defect_dataset` | 完整 train/valid/test 分区、演示原图及实际微调清单 |
| `data/provenance` | 文件来源、历史证据和验证范围记录 |
| `data/reproduction_20261031` | 两次硬件的静态与动态验证证据 |
| `data/optimization_evidence` | 历史候选的关键报告，不含全部淘汰工程 |
| `skill` | 可复用工作流、提示词、模板和离线计时工具 |
| `report` | 设计报告、复刻验证、优化取舍、协作过程与证据索引 |

`skill/general/` 收录不绑定题目和板卡的通用工程方法与模板；`skill/case_studies/` 收录本项目实例。`report/` 收录设计报告及复刻、优化和协作证据。

## 3. 不连接板卡，先查看已有静态结果

1. 双击根目录的 `open_static_results.cmd`，在两次硬件结果入口选择参考硬件或重构建硬件的静态网页。
2. 如果没有自动打开，直接从文件夹打开 `data/reproduction_20261031/index.html`，再选择其中一组静态结果。
3. 在网页中选取图片，查看原始输入、ARM 输出、FPGA 输出、局部放大及耗时对比。

这一步不需要 Python、网络或 FPGA。它展示包中已有的测试证据，不会产生一次新测量。

已有结果使用 120 张图片，每类 20 张，来源是 **valid 验证集，而不是 test 测试集**。冻结输入包含 317 个 ROI。两轮测量共 480 次路径调用。

两组板端后处理平均耗时如下：

| 硬件 | ARM 平均 ms | FPGA 路径平均 ms | 加速比 |
| --- | ---: | ---: | ---: |
| 参考硬件 | 2353.347 | 701.569 | 3.354× |
| 重构建硬件 | 2334.491 | 701.157 | 3.329× |

计时包含板端 DMA/cache，排除电脑 YOLO、网络传输、文件保存、审计和预热、overlay 加载。**这些是板端后处理加速比，不是摄像头系统端到端加速比。** 每组特征一致，两个 ROI 共存在 2 个掩膜像素差异，并非所有输出逐像素完全相同。详情见 [参考报告](data/reproduction_20261031/reference/static/static_compare.json)和[重构建报告](data/reproduction_20261031/rebuilt/static/static_compare.json)。



## 4. 在 Windows 准备 Python

### 4.1 安装和确认 Python

动态演示推荐 Python 3.12 64 位，本程序支持 3.10/3.11/3.12。**如果电脑只有 Python 3.13，需要另外安装一个受支持的版本；可以保留 3.13，不用卸载。** `install_environment.cmd` 只安装项目依赖，不负责安装 Python 本体。板端 Python 环境另行管理。

仅生成板端包时，用已有 Python 3.13 也可以，这个操作不加载 YOLO。动态演示仍需安装下面说明的受支持 Python 及依赖。

1. 从 https://www.python.org/downloads/windows/ 获取 Python 3.12 的 Windows 64 位安装程序，不要因为首页推荐更高版本就改装 3.13。可选择提供官方 Windows installer (64-bit) 的 3.12.10：https://www.python.org/downloads/release/python-31210/ 。
2. 安装时启用 Python Launcher，并勾选将 Python 加入 PATH 的选项。
3. 安装完成后关闭旧 PowerShell，重新打开一个。
4. 进入项目根目录，执行：

```powershell
Set-Location "D:\fpga_repro\steel_defect_fpga"
py -3.12 --version
py -3.12 -c "import struct; print(struct.calcsize('P') * 8)"
```

应看到 `Python 3.12.x` 和 `64`。如使用已安装的 3.11，请将本文 Windows 命令中的 `py -3.12` 一致替换成 `py -3.11`。不要把这些命令用于开发板。

### 4.2 准备好后直接继续

Python 准备好后，静态链路继续第 5 节；动态链路还需完成第 9 节的依赖安装。

## 5. 在电脑上生成板端运行包

在 **Windows PowerShell，项目根目录** 执行：

```powershell
py -3 -B .\board\package_runtime.py
```

预期输出包含 `BOARD_DIRECTORY` 和 `BOARD_ZIP`。默认 ZIP 位置：

```text
D:\fpga_repro\steel_defect_fpga\build\output\board_package\steel_board_runtime.zip
```

同一个输出目录还会生成 `extract_runtime.py`，终端打印 `UPLOAD_WITH_ZIP` 指向它。**把 ZIP 和这个解压脚本一起上传开发板。** 板端运行包把分散的源码、数据和硬件按所需布局装配起来，只需要 Python 标准库；不会运行 YOLO、连接开发板或重新综合硬件。

如果提示输出目录已存在，脚本会保留已有包。项目源码已更新时，旧包不会自动更新；需要部署当前源码时，指定一个尚不存在的新目录：

```powershell
py -3 -B .\board\package_runtime.py --output .\build\output\board_package_02
```

之后上传新目录里的 ZIP。不要把电脑端整个 `src` 目录当成这个板端运行包上传。

## 6. 准备并连接开发板

### 6.1 板端系统前提

1. 使用 **TUL PYNQ-Z2** 开发板，搭载 AMD/Xilinx **Zynq-7020** SoC，工程目标器件为 `xc7z020clg400-1`。本项目两组静态及动态板测均使用该型号开发板。
2. 按板卡说明准备电源、SD 卡和启动方式。包内没有 PYNQ SD 镜像；如原板卡和原 SD 卡可以正常启动，继续使用即可。
3. 将电脑和开发板接入能够互相访问的网络。
4. 按所用系统镜像的说明访问开发板 Jupyter，并使用该镜像对应的账号登录。IP、网页端口和密码由实际网络及镜像决定，本文不假定固定值。

### 6.2 查看 IP 和环境

在 **开发板 Jupyter 新建 Terminal**，执行：

```bash
hostname -I
pwd
python3 -c "import sys, platform, cv2, numpy, pynq; print(sys.version); print(platform.machine()); print('OpenCV', cv2.__version__); print('NumPy', numpy.__version__); print('PYNQ', getattr(pynq, '__version__', 'unknown'))"
```

记录电脑可访问的板端 IPv4 地址。原实测板端环境为 Python 3.10.4、armv7l、OpenCV 4.5.4、NumPy 1.21.5。Notebook 所选内核也必须使用相应环境，Terminal 的 Python 正常不代表 notebook 一定选对内核。

板端启动时不再强制 OpenCV/NumPy 必须等于历史版本；报告会记录实际版本。仍需安装可用的 PYNQ、OpenCV、NumPy，缺少模块或调用不兼容时按具体错误处理。Windows 的 YOLO 依赖不能照搬安装到板端。

## 7. 上传并解压板端包

### 7.1 上传

1. 在开发板 Jupyter 文件页新建文件夹，例如 `steel_repro_01`，并进入该文件夹。
2. 点击 Upload，上传第 5 节同一输出目录内的 `steel_board_runtime.zip` 和 `extract_runtime.py` 两个文件。
3. 经典 Jupyter 可能还需要再点击文件旁的 Upload 确认；看到 ZIP 真正出现在文件列表后继续。
4. 在同一文件夹新建一个 Python notebook，只用来确认目录并解压。

### 7.2 用 notebook 解压

在新 notebook 的第一个单元格粘贴并运行：

```python
from pathlib import Path
print(Path.cwd())
print((Path.cwd() / 'steel_board_runtime.zip').is_file())
print((Path.cwd() / 'extract_runtime.py').is_file())
```

后两行都应为 `True`。第一行是开发板上的实际目录，可以记下来；它不是 Windows 的 `D:\...`。

在第二个单元格运行：

```python
from extract_runtime import extract
board_root = extract('steel_board_runtime.zip')
```

预期打印 `BOARD_DIRECTORY=.../steel_repro_<时间>_<唯一标识>/steel_board_runtime`。回到 Jupyter 文件页并刷新，进入本次打印的新目录，再进入 `steel_board_runtime`，能看到两个入口 notebook 和运行依赖。

修复版解压脚本每次使用独立的新目录，不覆盖旧运行树，不删除已有结果。误重复运行会多生成一个目录，可以选定一个使用，不要混用。平时静态结束后切换动态不需要再解压；只有升级包或确实需要全新运行树时才重新解压。旧脚本的 `Target already exists` 是防覆盖提示，不是 ZIP 损坏，更不要求删除历史结果。

熟悉 Linux 的使用者也可以在板端 Terminal 进入 ZIP 所在目录后使用以下命令，**和 notebook 解压方式二选一**：

```bash
python3 extract_runtime.py steel_board_runtime.zip
```

之后应打开解压包内的 notebook，不是在电脑根目录中直接运行模板。

## 8. 重新运行静态 ARM / FPGA 对照

本节用于在复现板卡上产生新的静态结果。静态输入已经冻结，不需要再运行电脑 YOLO，也不需要接摄像头。

### 8.1 开始测量

1. 确保没有其他 notebook、动态服务或程序占用同一 FPGA 硬件。
2. 在板端 Jupyter 打开解压目录里的 `run_static.ipynb`，选择兼容的 Python 内核。
3. Notebook 只有一个代码单元格，按 Shift+Enter 直接启动；程序自动加载依赖和硬件。
4. 观察输出的 `REPORT_FILE`，记录本次结果位置。应看到 `IMAGES=120`、`MEASURED_CALLS=480`，预热后输出 `WARMUP_DONE`。
5. 等待完成。单元格左侧 `[*]` 表示仍在运行，不等于程序中断。整批测量可能超过十分钟，不要重复点击运行或同时启动动态服务。
6. 完成后记录 `RESULT_ZIP` 指向的文件。

若需查看结论，可以在本 Notebook 末尾添加一个代码单元格，将下面路径替换成 `REPORT_FILE` 的真实路径后运行。新增单元格或保存输出不会影响下次启动：

```python
import json
from pathlib import Path
report_path = Path('/替换为本次REPORT_FILE打印的完整路径/static_compare.json')
report = json.loads(report_path.read_bytes())
print('completed =', report['completed'])
print('pass =', report['pass'])
print('hardware_session_safe =', report['hardware_session_safe'])
print('errors =', report['errors'])
print('measurements =', len(report['measurements']))
print('report =', report_path)
```

正常完整运行应为 `completed=True`、`pass=True`、`hardware_session_safe=True`、`errors=[]` 和 `measurements=480`。如果中途异常、变量未赋值，以实际报错和已打印报告路径为准，不要继续运行后面的成功检查。

`pass=True` 是程序规定检查通过，不代表掩膜零像素差异，也不是“必定达到某个加速倍数”的承诺。应在结果 JSON 和网页查看实际一致性及耗时。

### 8.2 取回新结果

1. 结果位于板端运行树内 `steel_static_demo_20261029/results/run_<时间>_<标识>/`，以本次 `RESULT_ZIP` 的实际输出为准。
2. 在板端 Jupyter 文件页进入对应目录，找到 `static_demo_results.zip`。
3. 选中文件并下载到 Windows。
4. 在电脑上解压到新的结果目录，打开其中的 `static_demo.html`。
5. 确认页面对应本次运行，并保留同目录的图片、JSON 等依赖；不要只复制一个 HTML。

项目根目录的 `open_static_results.cmd` 打开已归档的两次硬件结果入口。以后新下载的报告仍需自行解压并打开其中的 HTML。

### 8.3 切换到动态演示前

等静态程序正常完成，确认硬件会话安全，再通过 Jupyter 菜单关闭该 notebook 的内核（Shut Down Kernel）。**仅关闭浏览器标签页不等于停止内核。** 然后再执行动态服务步骤。

可以正常保存两份 Notebook。若仍看到 `CHANGED`、`PREFLIGHT` 等旧提示，说明打开的还是旧板端目录；本版没有这些启动门槛。仅更新电脑上的项目目录不会改变已经上传的板端程序。

## 9. 安装 Windows 动态演示环境

本节只在 Windows 执行，不在板端执行。需要联网下载依赖和足够磁盘空间；没有可用 NVIDIA GPU 时可以使用 CPU，推理速度会受电脑性能影响。

先执行 `py --list-paths` 查看注册的 Python，执行 `py -3.12 --version` 确认目标版本。只有 `python --version` 显示 3.13，不代表已经有 3.12。缺少受支持版本时先做第 4.1 节，安装后重开 PowerShell 再继续。

在 **Windows PowerShell，项目根目录** 执行：

```powershell
.\install_environment.cmd
```

安装脚本会检测可用环境，必要时在 `src/host/.venv` 创建虚拟环境。新版会明确说明当前 Python 与支持版本，不再连续显示三段含义不清的 Launcher 报错。首次下载较慢时等待安装完成，不要同时再开一个安装进程。成功提示应包含：

```text
Environment ready. Run start_demo.cmd.
```

安装成功后直接继续第 10 节启动板端服务，再按第 11 节启动 Windows 网页，不需要单独执行电脑预检。CPU 推理可用时不要求必须有 CUDA。

如果受支持 Python 已安装但没有注册到 Launcher，可在安装时显式指定真实路径：

```powershell
.\install_environment.cmd --python "C:\实际Python312安装目录\python.exe"
```

上面的路径需要自行替换，必须指向 Python 3.10/3.11/3.12 64 位。首次安装时不复制旧 `.venv`；安装中网络报错则保留 pip 首处错误后重试，而不是直接执行启动命令。

换电脑时应重新安装环境，不要把原电脑的 `.venv` 文件夹直接复制过去当作便携环境。默认 PyTorch 安装使用 CUDA 12.4 版本；是否实际用到 GPU，要看驱动及运行时检查结果。

## 10. 先启动板端动态服务

在 **开发板 Jupyter** 操作：

1. 确保静态程序及其内核已正常停止。
2. 打开解压目录里的 `run_dynamic.ipynb`，使用兼容内核。
3. Notebook 只有一个代码单元格，按 Shift+Enter 直接启动服务。
4. 看到 `DYNAMIC_SERVICE_READY=True PORT=8767` 后，保持该单元格运行。

此输出只表示**开发板接口**准备好了，不会启动 Windows 网页；下一步在电脑执行第 11 节。缺依赖、端口占用或硬件接口不兼容时，会在启动位置报告具体问题。

服务需要持续等待电脑请求，因此此时一直显示 `[*]` 是正常状态。

默认一次服务最多处理 1000 帧；距最近一次实际处理帧超过 600 秒会结束。健康检查不算实际处理帧，不会重置该空闲计时。长时间调整设备后连接失败时，先看 notebook 是否已正常退出，再重新启动服务。

需要延长正式演示时，先正常结束服务。在解压运行树根目录复制 `dynamic_settings.example.json`，将副本命名为 `dynamic_settings.json`，把副本内容改为：

```json
{"port": 8767, "max_frames": 10000, "idle_seconds": 3600}
```

再按顺序运行两格。`max_frames` 可设 1 至 10000，`idle_seconds` 可设 30 至 3600；不配置该文件则用默认值。运行配置与受保护代码分开，不需要修改 notebook 代码或清单。配置文件里仅接受上述三个字段。

### 10.1 从电脑检查板端是否可达

回到 **Windows PowerShell**，把示例 IP 改成自己的开发板 IP：

```powershell
Test-NetConnection -ComputerName 192.168.2.99 -Port 8767
Invoke-RestMethod -Uri "http://192.168.2.99:8767/health"
```

预期 `TcpTestSucceeded=True`，健康检查返回 `status` 为 `ready`，并带有版本与硬件标识。失败时先检查板端服务输出、实际 IP、网络是否互通；不要把 `127.0.0.1` 当成开发板 IP。

`http://开发板IP:8767/health` 返回 JSON，板端 `8767/` 根地址不提供网页。真正的演示网页由第 11 节的 Windows 程序提供，地址默认是 `http://127.0.0.1:8092/`。

## 11. 启动电脑动态网页并设置摄像头

### 11.1 启动

1. 将 USB 摄像头接到 Windows 电脑。
2. 关闭可能独占该摄像头的软件，例如系统相机、会议软件或其他采集脚本。
3. 在项目根目录的 Windows PowerShell 中执行：

```powershell
.\start_dynamic.cmd
```

4. 保留终端窗口，用浏览器打开 http://127.0.0.1:8092/ 。
5. 如果 8092 已被其他程序占用，停止本次启动，在同一终端改用：

```powershell
.\start_dynamic.cmd --port 8093
```

此时访问 http://127.0.0.1:8093/ 。打开网页本身不会自动打开摄像头或运行 YOLO。

### 11.2 第一次建议这样填

打开网页“相机 / 检测 / 保存设置”，逐项确认：

| 设置 | 第一次建议 | 如何判断是否合适 |
| --- | --- | --- |
| 开发板地址 | `http://实际板端IP:8767` | 必须是刚启动服务的那块板卡 |
| 模型 | 包内 `data/models/best.pt` | 先使用默认模型，避免同时改模型和环境 |
| 摄像头索引 | 先试 0 | 内置相机占用 0 时，停止采集后改试 1、2 |
| 后端 | 先试 DirectShow | 打不开或参数无效时，可停止后改试 MSMF |
| 分辨率 / 格式 | 1920×1080、MJPG | 无法开启或卡顿时依次降低到 1280×720、640×480 |
| 请求采集帧率 | 先设 30 | 以启动后的实际读回和处理表现为准 |
| 连续检测间隔 | 先设 1 秒 | 稳定后再调，0 表示不额外要求间隔，仍受处理耗时限制 |
| YOLO 置信度 | 先设 0.25 | 结合真实钢板观察漏检与误检，不能只看框多不多 |
| 曝光 | 先保留驱动设置 | 先固定光照，再尝试自动或手动曝光 |
| 对焦 | GC200 旋转镜头手动调焦 | 以钢板纹理清晰为准，网页不提供电动对焦控制 |
| 保存目录 | 选择本机可写、有足够空间的目录 | 换电脑后重新选择，不依赖原电脑盘符 |

界面提供的值是程序向摄像头驱动发出的请求。摄像头不支持的分辨率、帧率、格式或曝光，对应请求可能被拒绝或被驱动替换。**填 60 不代表实际就有 60 FPS。** 摄像头采集速率、连续检测间隔、完成结果帧率是不同概念，检测帧率还受 YOLO、板端、网络和自动保存影响。

### 11.3 按这个顺序做第一次检测

1. 填完参数，点击“应用并保存设置”。应用参数会停止已有采集，需要重新开始。
2. 点击“检查开发板”，确认连接成功及版本匹配。
3. 点击“开始采集”，确认打开的是想用的 USB 摄像头，并查看实际参数。
4. 固定钢板距离和光照，调到纹理与缺陷清晰。将目标放进画面中央处理区域。
5. 点击“单帧检测”，等待处理及自动保存完成。
6. 检查原图、YOLO 和 FPGA 结果对应同一帧；确认“当前帧保存位置”指向实际存在的文件夹。
7. 单帧稳定后再点击“连续检测”。需要讲解时点击“暂停当前帧”，查看缺陷局部及统计。

当前处理链取相机画面中央正方形区域，再转换成 512×512 灰度输入。中央范围外的物体没有进入本次检测。保存的 `original.png` 可以是相机彩色原图；`input.png` 才是进入处理链的灰度图，二者用途不同。

缺陷类别依次为 `crack`、`inclusion`、`patches`、`pittedsurface`、`rolledinscale`、`scratches`。裂纹和麻点分支不提供掩膜；其他分支的掩膜按实际能力展示为候选区域或粗分割，不能解释为每种缺陷都有精确轮廓。未检出时也会保留该完成帧的输入和结果记录。

## 12. 自动保存内容与位置

### 12.1 何时保存

单帧检测、连续检测中，**每个成功完成检测的帧都会自动保存，包括未检出缺陷的完成帧**。不需要逐帧点击保存按钮。

自动保存针对检测完成帧，不是将摄像头预览的所有原始帧全部录下来。处理失败也不能当作成功完成帧。连续模式为了保证对应关系与完整保存，不应按摄像头标称 FPS 推算结果文件数。

### 12.2 默认目录

默认输出根目录为：

```text
D:\fpga_repro\steel_defect_fpga\build\output\dynamic_results\
```

每次会话和每个帧都会有独立目录，例如：

```text
dynamic_results/
  session_<会话标识>/
    frame_YYYY-MM-DD_HH-MM-SS-mmm_000001_<唯一标识>/
      original.png
      input.png
      yolo.png
      fpga.png
      overlay.png
      seeds.png
      masks/
      frame.json
      manifest.json
      index.html
    frame_YYYY-MM-DD_HH-MM-SS-mmm_000001_<唯一标识>.zip
    index.html
```

时间来自 Windows 电脑本地采集时间，精确到毫秒，并附加序号和唯一标识避免冲突。系统时钟应事先设置正确；系统时钟回拨会影响按名称排序时的时间含义。

`original.png` 是原始采集图；`input.png` 是处理输入；`yolo.png` 是检测框结果；`fpga.png`、`overlay.png` 等用于展示后处理。`masks` 仅收录本帧实际生成的掩膜，没有掩膜的类别不会被虚构一张掩膜。JSON 保留该帧数据和来源信息，HTML 与 ZIP 便于整体查看和提交。

页面可改选保存目录。选择后“应用并保存设置”，再开始检测；以页面显示的本帧保存路径为准。转移结果时复制整个帧目录或对应 ZIP，不能只拿 HTML。

### 12.3 保存失败怎么办

如果出现自动保存错误，检查目录权限、磁盘剩余空间、移动硬盘是否断开。程序会保留待保存结果并暂停进一步检测；排除问题后点击“重试保存”，确认保存成功再继续。不要在保存未完成时直接关闭程序。



## 13. 正常停止，避免占用硬件

1. 在电脑网页暂停连续检测、停止采集，等待正在处理的帧及其保存结束。
2. 点击“正常结束板端服务”。
3. 查看板端 notebook，等待服务单元格返回；确认输出 `HARDWARE_SESSION_SAFE=True`，并记录 `BOARD_REPORT_FILE`。
4. 在 Jupyter 中关闭动态 notebook 的内核。
5. 回到电脑启动网页的终端，按 Ctrl+C 正常停止电脑程序。

若出现 DMA 超时或硬件会话不安全，先保存报错和报告，再按板卡流程重启板端系统。不要通过删除 `hardware_session_guard.json` 或强行跳过保护来恢复演示。关闭网页或关闭 notebook 标签页都不能代替正常释放硬件。

## 14. 从源码重新构建硬件

本节是额外的源码复刻路线。先跑通已有硬件的演示，能够帮助区分环境问题和新硬件问题。

### 14.1 工具前提

- 目标器件：`xc7z020clg400-1`，安装时包含对应器件支持。
- 构建脚本要求 Vivado 2026.1。
- HLS 使用 **Vitis 2026.1 的 `vitis-run.bat --mode hls --tcl 脚本路径`**。不是旧入口 `vitis_hls.bat -f`；`vitis-run` 的 `-f` 指向平台说明，不用于执行本项目 HLS Tcl。仅替换可执行文件路径、保留旧参数不正确。
- 需要可用工具授权、足够磁盘空间和内存。新版会检查 Vivado 与 Vitis 两者的实际版本，都必须为 2026.1；本机检查、构建完成范围以修复验证记录为准。

### 14.2 确认工具路径

**最简单的方法不需要手动给变量赋值。** 打开 Windows PowerShell，进入真正的项目根目录，先执行只检查工具的命令：

```powershell
Set-Location "D:\fpga_repro\steel_defect_fpga"
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\build.ps1 -CheckTools
```

脚本会查找各盘常见 `AMDDesignTools\2026.1`、`Xilinx\2026.1` 安装目录及 PATH。成功会打印真实的 `VIVADO_EXE`、`VITIS_RUN_EXE`、版本和 `TOOLS_2026_1_PASS=True`，不会开始综合。找到多个安装位置时不会随便选一个，而是要求指定。

**如果自动查找失败，按下面的方法找实际路径：**

1. 开始菜单找到 Vivado 2026.1，右键“打开文件所在的位置”，再查看快捷方式“属性”的目标。若目标是启动器，沿路径找到实际安装目录。
2. 用资源管理器进入安装目录，找到 `2026.1\Vivado\bin\vivado.bat` 和 `2026.1\Vitis\bin\vitis-run.bat`。注意前一个是 Vivado，后一个是 Vitis。
3. 如果只知道安装在某个父目录，可在那个目录内搜索下面两个文件，不必扫描全部磁盘：

```powershell
Get-ChildItem -LiteralPath "D:\AMDDesignTools" -Filter vivado.bat -Recurse -File
Get-ChildItem -LiteralPath "D:\AMDDesignTools" -Filter vitis-run.bat -Recurse -File
```

把示例父目录换成自己实际的安装位置。若没有找到所需文件，先确认安装了对应组件及版本，不要直接复制示例路径。若两者都在 `D:\AMDDesignTools\2026.1` 下，指定这个共同目录即可：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\build.ps1 -ToolRoot "D:\AMDDesignTools\2026.1" -CheckTools
```

只有在工具分别安装于不同位置时才需要两个变量。变量只是保存路径，不会安装软件，也不会因为赋值成功就证明文件存在：

```powershell
$vivadoExe = "D:\AMDDesignTools\2026.1\Vivado\bin\vivado.bat"
$vitisRunExe = "D:\AMDDesignTools\2026.1\Vitis\bin\vitis-run.bat"
Test-Path -LiteralPath $vivadoExe
Test-Path -LiteralPath $vitisRunExe
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\build.ps1 -VivadoExe $vivadoExe -VitisRunExe $vitisRunExe -CheckTools
```

两个 `Test-Path` 必须都是 `True`，工具检查也必须通过。为 `False` 时先找对路径，不要继续构建。

### 14.3 执行统一构建入口

如果第 14.2 节自动查找已成功，在项目根目录执行以下命令，就会按顺序完成 HLS 和 Vivado 构建：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\build.ps1
```

如果之前通过 `-ToolRoot` 指定安装位置，构建时也带同样的参数：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\build.ps1 -ToolRoot "D:\AMDDesignTools\2026.1"
```

如果选择了分别赋值的方式，则在**同一个 PowerShell 窗口**执行下方命令；关闭或新开窗口后变量不会保留，需先重新赋值：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\build.ps1 -VivadoExe $vivadoExe -VitisRunExe $vitisRunExe
```

以上三种入口按安装情况选一种，不要都运行。执行策略参数只用于本次子进程。不要双击 Tcl，也不要直接跳到 `build_vivado.tcl`；统一脚本会先装配源码、测试输入并生成三种 HLS IP，再进入系统构建。

如项目所在磁盘空间不足，可指定新的输出根目录：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\build.ps1 -ToolRoot "D:\AMDDesignTools\2026.1" -OutputRoot "D:\fpga_build_output"
```

每次新构建建立 `v10_<时间>` 工作目录。输出里的 `RUN=... LOG=...` 会指出当前阶段及日志位置。可在另一个 PowerShell 用 `Get-Content -LiteralPath "实际日志路径" -Tail 30 -Wait` 观察；在观察窗口按 Ctrl+C 只停止日志跟随，不要误关真正构建窗口。

脚本依次准备源文件，进行 Gaussian HLS C 仿真、综合、RTL 协同仿真及导出、扩展 C 仿真，构建 ellipse7 和 blur3 IP，随后执行 Vivado 系统构建、综合、实现及报告检查，再导出 bit/hwh。运行时间取决于电脑和工具，不要因长时间无新输出就重复启动构建；先看当前日志和进程状态。

**每个 HLS 阶段都检查退出码、成功标记、导出 `component.xml` 和 IP 标识。** 缺少 IP 时立即停止在 HLS 阶段，显示具体日志，不会等到 Vivado 再报模糊的 `Blur3 IP missing`。

如果要分阶段检查或避免重复运行已成功 HLS，可先运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\build.ps1 -Stage Hls
```

看到 `ALL_HLS_IPS_READY=True`、`HLS_ONLY_PASS=True` 后，记录 `BUILD_DIRECTORY`，再用实际目录继续：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\build.ps1 -ResumeFrom "D:\实际路径\v10_实际时间" -Stage Full
```

续跑会核对工具版本、输入及已生成输出哈希，匹配才复用 HLS，不会重新跑已通过阶段。未完成、源文件已变化或输出被修改的阶段不能假装通过；旧版脚本生成的失败目录也不能直接用作新版续跑目录。自动复用限于 HLS，已创建的 Vivado 工程保留供诊断，不会被静默删除重建。

### 14.4 查找产物和报告

成功输出应包含 `BUILD_DIRECTORY`、`BIT_FILE`、`HWH_FILE`、`SUMMARY_FILE` 和 `BUILD_AND_EXPORT_PASS`，以及与参考产物逐字节比较的信息。

默认输出布局：

```text
build/output/v10_<时间>/
  hls/
  logs/
    gaussian.log
    gaussian_extended.log
    ellipse7.log
    blur3.log
    vivado_console.log
  receipts/
  vivado/
  hardware/
    steel_gaussian_v10.bit
    steel_gaussian_v10.hwh
  reports/
  vivado_build.log
  build_summary.json
  build_state.json
```

把下一段中的 `v10_实际时间` 替换为本次真正输出的目录名；如用了 `-OutputRoot`，也要修改对应根路径：

```powershell
$buildDir = "D:\fpga_repro\steel_defect_fpga\build\output\v10_实际时间"
Test-Path "$buildDir\hardware\steel_gaussian_v10.bit"
Test-Path "$buildDir\hardware\steel_gaussian_v10.hwh"
Get-Content "$buildDir\build_summary.json"
```

只有两个文件存在还不足以判定成功，必须结合脚本退出状态、摘要及报告。时序或 DRC 检查失败时保留日志并处理原因，不能只因为中间目录出现 bit 文件就继续交付。

摘要中的 `completed=true` 表示构建脚本完成；`board_tested_by_this_script=false` 是预期事实，因为构建脚本不连接开发板。新硬件是否可用仍需第 15 节验证。

## 15. 新生成的 bit/hwh 怎样进入运行链

本版不会因为 bit/hwh 的 SHA256 与历史参考不同而拒绝启动。程序仍会读取 HWH，确认 IP、地址、寄存器和 DMA 接口符合驱动要求；构建成功也不等于已通过实物板测。

### 15.1 保留参考文件，再成对替换

先确认第 14 节构建成功。在用于复刻的项目副本中操作；不要直接覆盖参考硬件。保持 `$buildDir` 指向本次构建输出，在同一个 Windows PowerShell 执行：

```powershell
$backupDir = Join-Path $PWD ("build\output\reference_hardware_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
New-Item -ItemType Directory -Path $backupDir -ErrorAction Stop | Out-Null
Copy-Item -LiteralPath .\board\hardware\steel_gaussian_v10.bit -Destination $backupDir
Copy-Item -LiteralPath .\board\hardware\steel_gaussian_v10.hwh -Destination $backupDir
Copy-Item -LiteralPath "$buildDir\hardware\steel_gaussian_v10.bit" -Destination .\board\hardware\steel_gaussian_v10.bit
Copy-Item -LiteralPath "$buildDir\hardware\steel_gaussian_v10.hwh" -Destination .\board\hardware\steel_gaussian_v10.hwh
```

bit 和 hwh 必须来自同一次成功构建。`board/package_runtime.py` 会从 `board/hardware` 取当前文件，不需要手工修改历史哈希、清单或协议常量。

### 15.2 部署并取得本次板测证据

需要部署时，按第 5 节指定一个新的输出目录生成板端包，按第 7 节上传解压，再运行第 8 节静态对照和第 10 至 13 节动态检测。

板端报告与动态响应记录实际硬件文件的哈希，仅作追溯，不与旧哈希比对。保留本次构建报告、静态测量、动态检测和正常结束记录，才能说明新硬件完成了哪些验证。已有加速结果仅对应归档的两组硬件及其测量环境，不能直接作为此后新产物的实测结果。

如果新硬件报接口不兼容或 DMA 错误，保留日志；需要继续演示时，恢复上面备份的一对参考文件并重新部署。DMA 异常后先重启开发板，不删除占用保护文件来强行续跑。

## 16. 常见问题：按症状检查

| 现象 | 应检查和处理 |
| --- | --- |
| 命令找不到文件、`Test-Path=False` | 确认处于真正项目根目录；新 PowerShell 要重新 `Set-Location` |
| `py` 找不到或指定 Python 不存在 | 确认安装 Python Launcher、版本和 64 位架构，安装后重开终端 |
| 板端打包提示输出已存在 | 复用完整已有包，或使用新的 `--output`，不要混合覆盖 |
| Jupyter 看不到解压文件 | 上传 ZIP 和配套 `extract_runtime.py`；按打印的新 `BOARD_DIRECTORY` 查找并刷新文件页 |
| notebook 找不到运行依赖 | 应打开解压 `steel_board_runtime` 里的 notebook，选择正确内核 |
| 板端缺少 `pynq/cv2/numpy` | 准备兼容板端环境；不要运行 Windows 安装脚本来修板端 |
| 静态 notebook 一直 `[*]` | 先看输出进度；全量运行可能超过十分钟，不要重复运行 |
| 动态服务一直 `[*]` | READY 后等待请求是正常状态 |
| 提示硬件被占用或会话不安全 | 确认没有并行 notebook；DMA 异常后按流程重启板端，不删除 guard |
| 电脑无法访问 8767 | 检查实际板端 IP、服务是否 READY、是否空闲退出、网络和防火墙 |
| 健康检查正常但网页提示版本不符 | 确认电脑端、板端来自同一个提交包，不混用旧服务 |
| 只有 Python 3.13，提示环境不支持 | 保留 3.13，另装 3.12 64 位；安装脚本只安装项目依赖，不负责安装 Python 本体 |
| 电脑提示缺少 torch/ultralytics 等 | 在电脑根目录运行 `install_environment.cmd`，成功后运行 `start_dynamic.cmd` |
| 板端 READY 但网页打不开 | 8767 是 API，8092 是 Windows 网页；确认电脑终端实际输出了 `DYNAMIC_WEB=...` |
| 摄像头打不开或打开内置摄像头 | 关闭占用程序，停止采集后调整索引、后端、分辨率和格式 |
| 参数改了画面没有更新 | 点击“应用并保存设置”，然后重新开始采集，检查驱动实际读回 |
| 请求 60 FPS 但结果帧率低 | 请求值不等于驱动实际值；结果帧率还包含处理和保存耗时 |
| 有检测框但没有掩膜 | 裂纹、麻点不提供掩膜；先确认当前类别和算法实际输出 |
| 目标在画面边缘没有检测 | 处理链取中央正方形，先将钢板缺陷移入有效处理区 |
| 连续模式没有保存每个预览帧 | 自动保存的是每个完成检测帧，不是预览录像 |
| 自动保存失败 | 检查空间、权限、磁盘连接，修复后点击“重试保存” |
| 打开静态网页仍是旧结果 | 根目录启动脚本打开原有结果；新结果要下载解压后打开其中 HTML |
| HLS/Vivado 可执行文件不存在 | 先运行 `build.ps1 -CheckTools`，失败时按第 14.2 节查找或指定 `-ToolRoot` |
| HLS 未导出 IP、旧版报 `Blur3 IP missing` | 必须用 2026.1 `vitis-run --mode hls --tcl`；新版逐阶段检查成功标记、`component.xml` 和 IP 标识 |
| HLS 仿真、IP 导出失败 | 查看本次工作目录日志，定位第一处真实错误，不继续手工跳过阶段 |
| Vivado 时序或 DRC 未通过 | 查看本次报告并解决约束、接口或实现问题，不以生成文件存在作为通过 |

## 17. 怎样判断自己完成到了哪一步

| 完成项 | 可得出的结论 | 还不能得出的结论 |
| --- | --- | --- |
| 离线静态网页可打开 | 能查看历史证据 | 已在当前板卡重新测量 |
| 板端静态测量完成且检查通过 | 当前板卡完成本次 ARM/FPGA 对照 | 每种环境都同速、所有掩膜逐像素相同 |
| 动态单帧、连续检测和保存正常 | 当前电脑、摄像头、板端链路可运行 | 实时端到端加速等于静态板端加速 |
| 新源码构建完成 | 本次工具链生成产物并通过脚本规定检查 | 新产物已上板验证 |
| 新产物完成运行链适配及新板测 | 有本次源码到板测的完整证据 | 未测试的平台或环境也自动兼容 |

动态页面的 YOLO、板端和链路耗时各有范围；板端耗时不能直接标成系统端到端耗时。静态对照和实时演示应分别展示证据。不同电脑会影响 YOLO、网络和保存速度，不能要求换电脑后所有时间完全相同。


