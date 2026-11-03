"""Full frozen-set acceptance and timing for the final runtime candidate."""
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import cv2
import numpy as np

from run_smoke import compare_results, sha256, sobel_reference
from steel_defect_runtime import SteelDefectRuntime, VERSION


ROOT = Path(__file__).resolve().parent
FROZEN = ROOT / "frozen"
REPETITIONS = 5


def load_v31():
    path = ROOT / "runtime" / "roi_runtime_portable_v31.py"
    spec = importlib.util.spec_from_file_location("_full_acceptance_v31", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_cases():
    path = FROZEN / "full_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("frame_count") != 184 or manifest.get("roi_count") != 403:
        raise ValueError("Frozen manifest is not 184 frames / 403 ROIs")
    cases = []
    total_rois = 0
    for frame in manifest["frames"]:
        image_path = FROZEN / "full184" / frame["input_gray"]
        gray = cv2.imdecode(np.frombuffer(image_path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
        if gray is None or gray.shape != (512, 512) or gray.dtype != np.uint8:
            raise ValueError("Invalid frozen frame: " + frame["frame_id"])
        predictions = frame["predictions"]
        total_rois += len(predictions)
        cases.append({"frame_id": frame["frame_id"], "image_name": frame.get("image_name"),
                      "gray": np.ascontiguousarray(gray), "predictions": predictions,
                      "sha256": frame["sha256"]})
    if len(cases) != 184 or total_rois != 403:
        raise ValueError("Frozen manifest coverage mismatch")
    return manifest, cases


def map_differences(result, gray, q8):
    features = result["features"]
    return {"sobel": int(np.count_nonzero(features["sobel"] != sobel_reference(gray))),
            "std7_q8": int(np.count_nonzero(features["std7_q8"] != q8.q8_reference(gray, 7))),
            "std13_q8": int(np.count_nonzero(features["std13_q8"] != q8.q8_reference(gray, 13)))}


def _summary(values):
    values = [float(v) for v in values]
    return {"count": len(values), "median_ms": float(np.median(values)),
            "p95_ms": float(np.percentile(values, 95)), "samples_ms": values}


def _class_counts(cases):
    counts = {str(cls): 0 for cls in range(6)}
    for case in cases:
        for prediction in case["predictions"]:
            counts[str(prediction["class_id"])] += 1
    return counts


def _targeted_benchmark(cases, runtime):
    broad = [case for case in cases if any(p["class_id"] in (2, 4) for p in case["predictions"])]
    selected = []
    for cls in (2, 4):
        match = next(case for case in broad if any(p["class_id"] == cls for p in case["predictions"]))
        selected.append(match)
    selected_ids = {case["frame_id"] for case in selected}
    for case in broad:
        if case["frame_id"] not in selected_ids and case["frame_id"] != "frame_143":
            selected.append(case)
            selected_ids.add(case["frame_id"])
            break
    frame143 = next(case for case in broad if case["frame_id"] == "frame_143")
    if frame143["frame_id"] not in selected_ids:
        selected.append(frame143)
    timings = []
    for case_index, case in enumerate(selected):
        arms, pls, ratios = [], [], []
        for repeat in range(REPETITIONS):
            order = ("arm_float", "pl_gaussian") if (repeat + case_index) % 2 == 0 else ("pl_gaussian", "arm_float")
            results = {}
            for mode in order:
                result = runtime.process_frame(case["gray"], case["predictions"], mode=mode)
                results[mode] = result
                value = result["timing"]["end_to_end_static_pipeline_ms"]
                (arms if mode == "arm_float" else pls).append(value)
            ratios.append(arms[-1] / pls[-1])
        timings.append({"frame_id": case["frame_id"], "roi_count": len(case["predictions"]),
                        "arm_float_ms": _summary(arms), "pl_gaussian_ms": _summary(pls),
                        "paired_speedup_arm_over_pl": _summary(ratios),
                        "repetitions": REPETITIONS,
                        "order": "alternating per repetition, offset by case index"})
        print("TIMING %s ARM=%.2fms PL=%.2fms paired=%.3fx" %
              (case["frame_id"], np.median(arms), np.median(pls), np.median(ratios)), flush=True)
    return timings


def run(output_root=None):
    manifest, cases = load_cases()
    output_root = Path(output_root or ROOT / "results")
    output = output_root / datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "runtime_full_acceptance/1.0", "version": VERSION,
              "completed": False, "pass": False, "strict_all_pass": None,
              "new_fpga_access": False, "fpga_access_attempted": False,
              "bitstream_built_or_changed": False,
              "scope": "Frozen 184-frame/403-ROI new-entry acceptance; static inputs only",
              "frame_count_expected": 184, "roi_count_expected": 403,
              "class_counts": _class_counts(cases), "manifest_sha256": sha256(FROZEN / "full_manifest.json"),
              "board_asset_sha256": {}, "features_different_pixels": {"sobel": 0, "std7_q8": 0, "std13_q8": 0},
              "pl_integer_model": {"broad_frames": 0, "pixels": 0, "different_pixels": 0},
              "known_strict_difference_cases": ["frame_143"], "frames": [],
              "result_directory": str(output.resolve()), "numpy": np.__version__, "opencv": cv2.__version__}
    for name in ("steel_triple.bit", "steel_triple.hwh"):
        report["board_asset_sha256"][name] = sha256(ROOT / "board" / name)
    runtime = None
    v31 = load_v31()
    q8_spec = importlib.util.spec_from_file_location("_full_acceptance_q8", ROOT / "board" / "q8_reference.py")
    q8 = importlib.util.module_from_spec(q8_spec)
    q8_spec.loader.exec_module(q8)
    all_strict = True
    unexpected = []
    arms_ms, pls_ms, paired_ratios = [], [], []
    try:
        cv2.setNumThreads(1)
        report["fpga_access_attempted"] = True
        runtime = SteelDefectRuntime.open_board(ROOT / "board" / "steel_triple.bit", allow_arm_fallback=False)
        report["new_fpga_access"] = True
        for frame_index, case in enumerate(cases):
            modes = ("arm_float", "pl_gaussian") if frame_index % 2 == 0 else ("pl_gaussian", "arm_float")
            outputs = {}
            for mode in modes:
                outputs[mode] = runtime.process_frame(case["gray"], case["predictions"], mode=mode)
            arm, pl = outputs["arm_float"], outputs["pl_gaussian"]
            arm_ms = arm["timing"]["end_to_end_static_pipeline_ms"]
            pl_ms = pl["timing"]["end_to_end_static_pipeline_ms"]
            arms_ms.append(arm_ms)
            pls_ms.append(pl_ms)
            if any(p["class_id"] in (2, 4) for p in case["predictions"]):
                paired_ratios.append(arm_ms / pl_ms)
                report["pl_integer_model"]["broad_frames"] += 1
                report["pl_integer_model"]["pixels"] += 512 * 512
                report["pl_integer_model"]["different_pixels"] += int(pl["timing"]["pl_integer_model_different_pixels"])

            diffs = map_differences(pl, case["gray"], q8)
            for name, count in diffs.items():
                report["features_different_pixels"][name] += count
            pl_strict = compare_results(pl, arm)
            v31_expected = v31.process_frame(
                case["gray"], case["predictions"], sobel_frame=pl["features"]["sobel"],
                local_std_frames=(cv2.multiply(pl["features"]["std7_q8"], 1 / 256.0, dtype=cv2.CV_32F),
                                  cv2.multiply(pl["features"]["std13_q8"], 1 / 256.0, dtype=cv2.CV_32F)),
                execution="runtime_full_acceptance_shared_fpga_features")
            v31_out = {"records": v31_expected[0], "masks": v31_expected[1]}
            v31_arm_strict = compare_results(v31_out, arm)
            v31_pl_strict = compare_results(v31_out, pl)
            row = {"frame_id": case["frame_id"], "image_name": case["image_name"],
                   "input_sha256": case["sha256"], "roi_count": len(case["predictions"]),
                   "class_counts": {str(cls): sum(p["class_id"] == cls for p in case["predictions"])
                                    for cls in range(6)},
                   "feature_differences": diffs,
                   "pl_integer_model_different_pixels": pl["timing"]["pl_integer_model_different_pixels"],
                   "strict_v32_arm_vs_pl": pl_strict,
                   "strict_v31_vs_arm": v31_arm_strict, "strict_v31_vs_pl": v31_pl_strict,
                   "arm_timing": arm["timing"], "pl_timing": pl["timing"],
                   "execution_order": list(modes)}
            report["frames"].append(row)
            known = case["frame_id"] in report["known_strict_difference_cases"]
            if any(diffs.values()):
                unexpected.append(case["frame_id"] + ":feature_difference")
            if any(p["class_id"] in (2, 4) for p in case["predictions"]):
                if row["pl_integer_model_different_pixels"] != 0:
                    unexpected.append(case["frame_id"] + ":PL_integer_model")
            if not known and any(not item["pass"] for item in pl_strict + v31_pl_strict):
                unexpected.append(case["frame_id"] + ":new_PL_strict_difference")
            if any(not item["pass"] for item in v31_arm_strict):
                unexpected.append(case["frame_id"] + ":V31_vs_V32_difference")
            all_strict = all_strict and all(item["pass"] for item in pl_strict)
            print("FULL %s %d/%d ROI=%d ARM=%.2fms PL=%.2fms strict=%s" %
                  (case["frame_id"], frame_index + 1, len(cases), len(case["predictions"]),
                   arm_ms, pl_ms, all(item["pass"] for item in pl_strict)), flush=True)

        report["functional_timing_one_pass"] = {
            "arm_float_all_frames_ms": _summary(arms_ms), "pl_request_all_frames_ms": _summary(pls_ms),
            "paired_speedup_sigma25_frames": _summary(paired_ratios),
            "note": "One ARM/PL pair per frame; functional-run timing is descriptive, not the repeated timing result."}
        report["targeted_paired_timing"] = _targeted_benchmark(cases, runtime)
        report["strict_all_pass"] = all_strict
        report["unexpected_failures"] = unexpected
        report["completed"] = True
        report["pass"] = not unexpected
    except Exception as exc:
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        raise
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception as exc:
                report["driver_close_error"] = "%s: %s" % (type(exc).__name__, exc)
                report["pass"] = False
        (output / "runtime_full_acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                            encoding="utf-8")
        print("RESULT_DIRECTORY=" + str(output.resolve()), flush=True)
    print("FULL_ACCEPTANCE_PASS:", report["pass"], "strict_all_pass:", report["strict_all_pass"], flush=True)
    return report


if __name__ == "__main__":
    run()
