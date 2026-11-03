"""Publish an isolated experiment only after build and test gates pass."""
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
NAME = "steel_gaussian_v10_board_20261023"
OUT = ROOT.parent / NAME


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def dump(p, value):
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def hwh_check():
    old = ROOT.parent / "steel_roi_blur3_v9_board_20261023/hardware/steel_roi_blur3.hwh"
    new = ROOT / "hardware/steel_gaussian_v10.hwh"
    a, b = (ET.parse(p) for p in (old, new))
    am, bm = ({x.get("INSTANCE"): x for x in t.iter("MODULE")} for t in (a, b))
    if set(am) != set(bm):
        raise AssertionError("Module instance set differs from v9")
    def ranges(t):
        return sorted(tuple(sorted(x.attrib.items())) for x in t.iter("MEMRANGE"))
    if ranges(a) != ranges(b):
        raise AssertionError("Memory address map differs from v9")
    for name in am:
        if name != "gaussian_row_q30_0" and am[name].get("VLNV") != bm[name].get("VLNV"):
            raise AssertionError("Unexpected non-Gaussian IP change: " + name)
    if bm["gaussian_row_q30_0"].get("VLNV") != "steel.local:hls:gaussian_row_q30:2.1":
        raise AssertionError("Wrong candidate Gaussian IP")
    def properties(node):
        return tuple(sorted((p.get("NAME"), p.get("VALUE")) for p in node.findall("PROPERTY")
                            if p.get("NAME") != "DESCRIPTION"))
    def registers(module):
        result = sorted((r.get("NAME"), properties(r),
                         tuple(sorted((f.get("NAME"), properties(f)) for f in r.iter("FIELD"))))
                        for r in module.iter("REGISTER"))
        if len(result) < 7:
            raise AssertionError("Missing register descriptions")
        by_name = {name: dict(props) for name, props, _ in result}
        for name, offset in (("kernel_id", 16), ("rows", 24), ("status", 32)):
            if int(by_name[name]["ADDRESS_OFFSET"], 0) != offset:
                raise AssertionError("Unexpected register offset: " + name)
        return result
    if registers(am["gaussian_row_q30_0"]) != registers(bm["gaussian_row_q30_0"]):
        raise AssertionError("Gaussian register map changed")
    return {"same_instance_names": True, "same_address_map": True,
            "same_gaussian_registers": True, "gaussian_vlnv": bm["gaussian_row_q30_0"].get("VLNV")}


def main():
    if OUT.exists() or OUT.with_suffix(".zip").exists():
        raise RuntimeError("Do not overwrite a published package")
    log = (ROOT / "vivado_build.log").read_text(encoding="utf-8", errors="replace")
    if "GAUSSIAN_V10_BUILD_AND_EXPORT_PASS" not in log:
        raise RuntimeError("Vivado build/export not complete")
    m = re.search(r"GAUSSIAN_V10_WNS=([\d.+-]+) GAUSSIAN_V10_WHS=([\d.+-]+)", log)
    if not m or min(map(float, m.groups())) < 0:
        raise RuntimeError("Setup/hold timing not passed")
    hls = ROOT / "gaussian_csa/continuous_project/solution1"
    cosim = (hls / "sim/report/gaussian_row_q30_cosim.rpt").read_text()
    if not re.search(r"Verilog\s*\|\s*Pass", cosim):
        raise RuntimeError("RTL cosim not passed")
    csim = (hls / "csim/report/gaussian_row_q30_csim.log").read_text(errors="replace")
    if "ALL INTEGER OUTPUTS AND FRAME PROTOCOL CHECKS PASSED" not in csim:
        raise RuntimeError("C simulation incomplete")
    extended = ROOT / "gaussian_csa/extended_project/solution1/csim/report/gaussian_row_q30_csim.log"
    if "EXTENDED_CSIM_PASS cases=14" not in extended.read_text(errors="replace"):
        raise RuntimeError("Extended C simulation incomplete")
    host = subprocess.run([sys.executable, str(ROOT / "test_board_runner.py")],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if host.returncode:
        raise RuntimeError(host.stdout + host.stderr)
    interface = hwh_check()
    ccl = json.loads((ROOT / "ccl/ccl_host_validation.json").read_bytes())
    if not ccl["pass"] or ccl["full_frames"] != 184 or ccl["real_ccl_calls"] != 1028:
        raise RuntimeError("CCL evidence incomplete")
    OUT.mkdir()
    copy(ROOT / "board_src/gaussian_v10_runner.py", OUT / "gaussian_v10_runner.py")
    copy(ROOT / "gaussian_csa/fixtures.bin", OUT / "fixtures.bin")
    for name in ("steel_gaussian_v10.bit", "steel_gaussian_v10.hwh"):
        copy(ROOT / "hardware" / name, OUT / "hardware" / name)
    for p in (ROOT / "reports").glob("*.rpt"):
        copy(p, OUT / "reports" / p.name)
    for rel in ("syn/report/gaussian_row_q30_csynth.rpt", "sim/report/gaussian_row_q30_cosim.rpt",
                "csim/report/gaussian_row_q30_csim.log"):
        copy(hls / rel, OUT / "reports" / Path(rel).name)
    copy(extended, OUT / "reports/extended_csim.log")
    for name in ("gaussian_row_q30.cpp", "gaussian_row_q30.hpp", "coefficients_q30.hpp",
                 "test_gaussian_row.cpp", "test_extended.cpp", "run_hls.tcl", "run_extended.tcl"):
        copy(ROOT / "gaussian_csa" / name, OUT / "source" / name)
    for name in ("build_vivado.tcl", "test_board_runner.py", "build_board_package.py", "feasibility_zh.md"):
        copy(ROOT / name, OUT / "source" / name)
    copy(ROOT / "ccl/ccl_host_validation.json", OUT / "evidence/ccl_host_validation.json")
    copy(ROOT / "ccl/ccl_stats.cpp", OUT / "source/ccl_stats.cpp")
    copy(ROOT / "ccl/validate_ccl.py", OUT / "source/validate_ccl.py")
    evidence = {"hardware_built": True, "board_tested": False, "promoted": False,
                "wns_ns": float(m[1]), "whs_ns": float(m[2]), "interface_check": interface,
                "rtl_cosim_pass": True, "frozen_csim_cases": 24, "extended_csim_cases": 14,
                "host_test_exit_code": host.returncode, "host_test_output": host.stdout + host.stderr,
                "bit_sha256": sha(OUT / "hardware/steel_gaussian_v10.bit"),
                "hwh_sha256": sha(OUT / "hardware/steel_gaussian_v10.hwh"),
                "ccl_in_bitstream": False}
    dump(OUT / "evidence/build_evidence.json", evidence)
    cells = []
    def md(s):
        cells.append({"cell_type": "markdown", "metadata": {}, "source": s.splitlines(True)})
    def code(s):
        cells.append({"cell_type": "code", "metadata": {}, "source": s.splitlines(True),
                      "outputs": [], "execution_count": None})
    md("# Gaussian v10 独立硬件实验\n\n"
       "保留同级的 v8、v9 板测包和 v9.1 日常包。文件名全部英文，说明使用中文。\n"
       "先重启 Notebook 内核；不要同时运行其他访问 FPGA 的 Notebook。\n"
       "先只运行前两格。任何错误或手动中断：保存报告，重启开发板并重启内核，不要直接重跑。\n"
       "本位流只升级 Gaussian；连通域目前只有主机模型，没有集成进来。\n")
    code("from pathlib import Path\nimport sys\n"
         "PACKAGE = Path('/home/xilinx/jupyter_notebooks/q8_dual_test/" + NAME + "')\n"
         "if not PACKAGE.is_dir():\n    raise FileNotFoundError('请先解压测试包：' + str(PACKAGE))\n"
         "sys.path.insert(0, str(PACKAGE))\nimport gaussian_v10_runner as test\n"
         "if Path(test.__file__).resolve().parent != PACKAGE.resolve():\n"
         "    raise RuntimeError('模块来自旧目录，请重启内核')\n"
         "print(test.initialize_runtime())\nprint('PACKAGE_READY=True')\n")
    md("## 第二格：正确性和新旧性能比较\n\n"
       "程序依次加载旧—新—新—旧位流。24 组核测试、12 个用例的新旧系统检查、"
       "6 个代表用例各 6 组比较；另有预热。进度之间可能等待整数参考计算，请勿中断。\n"
       "总计时包括 PS、DMA、复制和后处理；不包括 YOLO、文件读取和位流加载。\n"
       "正常结束板上为旧 v9 位流。下载 REPORT_FILE 指向的 JSON 发回分析。\n"
       "PASS 表示正确性通过，不代表性能自动合格。\n")
    code("smoke = test.run('smoke')\nprint('SMOKE_PASS=', smoke['pass'])\n"
         "for name, item in smoke['performance'].items():\n"
         "    print(name, 'speedup=', round(item['speedup_median'], 3), 'wins=', item['wins'], '/6')\n")
    md("## 第三格：待分析冒烟结果后再运行全量\n\n"
       "覆盖 184 帧/403 ROI 加 3 个补充用例。正常结束板上为新实验位流；正式包不改动。\n")
    code("RUN_FULL184 = False\nif RUN_FULL184:\n    full = test.run('full')\n"
         "    print('FULL184_PASS=', full['pass'])\n"
         "else:\n    print('先发回 smoke JSON，确认后再将 RUN_FULL184 改成 True')\n")
    md("## 第四格：正常结束后可选恢复 v9\n\n"
       "冒烟已自动以旧 v9 结束，无需再运行。全量成功后需要恢复旧硬件时启用。\n"
       "若出现错误/中断，先重启开发板，不能用此格代替复位。\n")
    code("RESTORE_V9 = False\nif RESTORE_V9:\n    rollback = test.run('rollback')\n"
         "    print('V9_RESTORED=', rollback['pass'])\nelse:\n    print('未额外切换位流')\n")
    for i, cell in enumerate(cells):
        cell["id"] = "gaussian-v10-" + str(i)
        if cell["cell_type"] == "code":
            ast.parse("".join(cell["source"]))
    notebook = {"cells": cells, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.10"}}, "nbformat": 4, "nbformat_minor": 5}
    dump(OUT / "run_gaussian_v10.ipynb", notebook)
    (OUT / "reference").mkdir()
    copy(OUT / "run_gaussian_v10.ipynb", OUT / "reference/run_gaussian_v10.template.ipynb")
    instructions = (
        "# v10 中文测试步骤\n\n"
        "1. 板上保留同级目录：steel_binary_ops_v8_experiment_20261022、"
        "steel_roi_blur3_v9_board_20261023、steel_runtime_v9_1_qualified_20261023。\n"
        "2. 上传本 ZIP 到 /home/xilinx/jupyter_notebooks/q8_dual_test/。\n"
        "3. 在 Jupyter Terminal 执行：\n\n"
        "~~~bash\ncd /home/xilinx/jupyter_notebooks/q8_dual_test\nunzip " + NAME + ".zip\n~~~\n\n"
        "4. 打开 " + NAME + "/run_gaussian_v10.ipynb，重启内核。\n"
        "5. 依次运行前两个代码单元格；看到 PACKAGE_READY=True 后再运行第二格。\n"
        "6. 等 GAUSSIAN_V10_PASS=True 和 SMOKE_PASS=True；下载 REPORT_FILE 指向的 "
        "gaussian_v10_acceptance.json 发回。也请发回各用例 speedup/wins 输出。\n"
        "7. 先不要启用第三格全量；确认性能有收益后再运行。不要点全部运行。\n\n"
        "冒烟正常结束当前 PL 为旧 v9；全量正常结束为新实验 v10。旧文件始终保留。\n"
        "若中断或报错，请保存报告，重启开发板并重启内核，不要直接重跑或回退。\n"
        "文件名保持英文，不改名；manifest 校验不包含工作 notebook，但包括模板和可执行代码。\n\n"
        "此次变更是已有 Gaussian 硬件结构优化，并未添加连通域核。"
        "已有 Sobel、std7/std13、ROI sigma3 平滑与默认 PS 椭圆路径保持原功能。\n"
        "构建证据在 evidence/build_evidence.json；候选评估在 source/feasibility_zh.md。\n")
    (OUT / "readme_zh.md").write_text(instructions, encoding="utf-8")
    for p in OUT.glob("*.py"):
        ast.parse(p.read_text(encoding="utf-8"), filename=p.name)
    files = {p.relative_to(OUT).as_posix(): sha(p) for p in sorted(OUT.rglob("*"))
             if p.is_file() and p.name != "run_gaussian_v10.ipynb"}
    dump(OUT / "manifest.json", {"schema": "gaussian_v10_package/1.0", "files": files,
                                "board_tested_at_publication": False,
                                "daily_manifest_sha256": "3f5cd482d4bbb015c6340c24a405d2be01ee925f52715b926ad4aa2eccb931a4"})
    with zipfile.ZipFile(OUT.with_suffix(".zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(OUT.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(OUT.parent).as_posix())
    with zipfile.ZipFile(OUT.with_suffix(".zip")) as z:
        if z.testzip() is not None:
            raise AssertionError("ZIP corruption")
        assert all(name.isascii() for name in z.namelist())
        for rel, digest in files.items():
            if hashlib.sha256(z.read(NAME + "/" + rel)).hexdigest() != digest:
                raise AssertionError("ZIP manifest differs: " + rel)
    result = {"package": str(OUT), "zip": str(OUT.with_suffix(".zip")),
              "zip_sha256": sha(OUT.with_suffix(".zip")), "manifest_sha256": sha(OUT / "manifest.json"),
              "verified_files": len(files), "evidence": evidence}
    dump(ROOT / "package_build_result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
