"""Exact C++ stats model vs OpenCV, including masks captured from 184 frames.

All measurements are host functional evidence, never board performance.
Real masks use the frozen Q30/Q20 response oracle and Q8 feature references.
"""
from collections import Counter
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
PYTHON = sys.executable
FIELDS = ("count", "pixels", "largest", "count_ge8", "count_ge20",
          "peak_nodes", "peak_runs", "peak_find_depth", "workspace_bytes")


def run():
    dll = HERE / "ccl_stats.dll"
    subprocess.run(["D:/CodeCache/Tools/mingw64/bin/g++.exe", "-std=c++17", "-O2",
                    "-Wall", "-Wextra", "-Werror", "-shared", "-static-libgcc",
                    "-static-libstdc++", str(HERE / "ccl_stats.cpp"), "-o", str(dll)],
                   check=True)
    library = ctypes.CDLL(str(dll))
    fn = library.ccl_stats
    fn.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint, ctypes.c_uint,
                   ctypes.POINTER(ctypes.c_uint32)]
    fn.restype = ctypes.c_int
    original = cv2.connectedComponentsWithStats
    records = []

    def check(mask, name, reference=None, caller=None):
        mask = np.ascontiguousarray(mask, dtype=np.uint8)
        count, _, stats, _ = original(mask, connectivity=8) if reference is None else reference
        areas = stats[1:, cv2.CC_STAT_AREA]
        expected = [count - 1, int(np.count_nonzero(mask)),
                    int(areas.max()) if len(areas) else 0,
                    int(np.count_nonzero(areas >= 8)), int(np.count_nonzero(areas >= 20))]
        output = (ctypes.c_uint32 * 9)()
        status = fn(mask.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
                    mask.shape[0], mask.shape[1], output)
        actual = list(output)
        if status != 0 or actual[:5] != expected:
            raise AssertionError((name, status, actual, expected))
        assert actual[5] <= 512 and actual[6] <= 256 and actual[7] <= 9
        record = {"name": name, "caller": caller, "shape": list(mask.shape),
                  "mask_sha256": hashlib.sha256(mask.tobytes()).hexdigest(),
                  **dict(zip(FIELDS, actual)), "exact": True}
        records.append(record)

    rng = np.random.default_rng(20261023)
    for h, w in ((1, 1), (1, 512), (512, 1), (7, 13), (511, 509), (512, 512)):
        check(np.zeros((h, w), np.uint8), f"empty_{h}_{w}")
        check(np.full((h, w), 255, np.uint8), f"full_{h}_{w}")
        yy, xx = np.indices((h, w))
        check(((yy + xx) % 2).astype(np.uint8), f"diagonal_checker_{h}_{w}")
        check(((yy % 2 == 0) & (xx % 2 == 0)).astype(np.uint8), f"isolated_{h}_{w}")
        check((xx % 2 == 0).astype(np.uint8), f"vertical_runs_{h}_{w}")
        bridge = (xx % 2 == 0).astype(np.uint8)
        bridge[-1, :] = 1
        check(bridge, f"late_bridge_{h}_{w}")
        for p in (.01, .1, .3, .5, .8, .99):
            check((rng.random((h, w)) < p).astype(np.uint8), f"random_{h}_{w}_{p}")
    for n in range(100):
        h, w = rng.integers(1, 513, 2)
        check((rng.random((h, w)) < rng.uniform(.01, .99)).astype(np.uint8), f"fuzz_{n}")
    dummy = (ctypes.c_uint8 * 1)(0)
    output = (ctypes.c_uint32 * 9)()
    for h, w in ((0, 1), (1, 0), (513, 1), (1, 513)):
        assert fn(dummy, h, w, output) == 1
    adversarial_count = len(records)
    isolated = next(r for r in records if r["name"] == "isolated_512_512")
    assert isolated["count"] == 65536 and isolated["largest"] == 1
    print("CCL_ADVERSARIAL_PASS", adversarial_count, flush=True)

    sys.path.insert(0, str(PROJECT / "steel_roi_blur3_v9_board_20261023"))
    import board_runner as board
    a, e = board.common.dependencies(PROJECT / "steel_binary_ops_v8_experiment_20261022")
    m = e.base._fast._runtime
    # Apply already qualified descriptor math, but all acceleration is emulated.
    sys.path.insert(0, str(PROJECT / "steel_runtime_v9_1_qualified_20261023"))
    from candidates import scalar_histogram, orientation_int16
    full = a.load_cases("full")
    current_name = ""

    def capture(mask, *args, **kwargs):
        assert kwargs.get("connectivity", 8) == 8
        reference = original(mask, *args, **kwargs)
        check(mask, current_name, reference, sys._getframe(1).f_code.co_name)
        return reference

    def fixed_broad(gray):
        return e.base._fixed_module.response(gray, "broad", 30, 20)

    with patch.object(cv2, "connectedComponentsWithStats", capture), \
         patch.object(m, "broad_dark_response", fixed_broad), \
         patch.object(m, "scalar_stats", scalar_histogram), \
         patch.object(m, "orientation_metrics", orientation_int16):
        for index, (name, gray, predictions) in enumerate(full, 1):
            current_name = name
            std = tuple(cv2.multiply(a.q8_reference(gray, k), 1 / 256., dtype=cv2.CV_32F)
                        for k in (7, 13))
            e.base._fast.process_frame(gray, predictions, sobel_frame=a.cpu_sobel(gray),
                                      local_std_frames=std, execution="host_ccl_feasibility")
            if index % 20 == 0:
                print("CCL_REAL_FRAMES", index, "/", len(full), flush=True)
    real = records[adversarial_count:]
    report = {"pass": True, "scope": "host C++ model; no RTL, DMA or board speed claim",
              "real_mask_source": "frozen 184-frame runtime; exact Q30/Q20 broad response and Q8 references; v9.1 descriptor math; CPU blur3 equivalent",
              "full_frames": len(full), "full_rois": sum(len(p) for _, _, p in full),
              "adversarial_cases": adversarial_count, "real_ccl_calls": len(real),
              "callers": dict(Counter(r["caller"] for r in real)),
              "real_unique_masks": len({r["mask_sha256"] for r in real}),
              "global_peak_nodes": max(r["peak_nodes"] for r in records),
              "global_peak_find_depth": max(r["peak_find_depth"] for r in records),
              "source_sha256": hashlib.sha256((HERE / "ccl_stats.cpp").read_bytes()).hexdigest(),
              "records": records}
    (HERE / "ccl_host_validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "records"}, indent=2))


if __name__ == "__main__":
    run()
