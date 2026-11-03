"""Single-run board smoke acceptance for the runtime candidate."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np

from steel_defect_runtime import SteelDefectRuntime, VERSION


ROOT = Path(__file__).resolve().parent
BOARD = ROOT / "board"
FIXTURES = ROOT / "fixtures"
sys.path.insert(0, str(BOARD))
MASK_KEYS = {0: set(), 1: {"candidate"}, 2: {"raw_candidate", "coarse_extent"},
             3: set(), 4: {"candidate"}, 5: {"candidate"}}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sobel_reference(gray):
    gx = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3)
    result = np.minimum(np.abs(gx.astype(np.int32)) + np.abs(gy.astype(np.int32)), 255).astype(np.uint8)
    result[[0, -1], :] = 0
    result[:, [0, -1]] = 0
    return result


def predictions_from_labels(sample):
    result = []
    for index, label in enumerate(sample["labels"]):
        cls, cx, cy, width, height = label
        if int(cls) != sample["class_id"] or not np.isfinite(label).all():
            raise ValueError("Invalid normalized sample label")
        if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < width <= 1 and 0 < height <= 1):
            raise ValueError("Out-of-range normalized sample label")
        result.append({"class_id": int(cls), "detection_index": index, "confidence": 1.0,
                       "box_xyxy_pixel": [(cx-width/2)*512, (cy-height/2)*512,
                                          (cx+width/2)*512, (cy+height/2)*512]})
    return result


def compare_results(actual, expected):
    records, masks = actual["records"], actual["masks"]
    refs, ref_masks = expected["records"], expected["masks"]
    if len(records) != len(refs) or len(masks) != len(ref_masks) or len(records) != len(masks):
        raise ValueError("ROI count mismatch")
    checks = []
    for record, reference, actual_masks, expected_masks in zip(records, refs, masks, ref_masks):
        keys = (set(record) | set(reference)) - {"provenance", "processing_time_ms"}
        fields = [key for key in sorted(keys) if key not in record or key not in reference
                  or record[key] != reference[key]]
        contract = set(actual_masks) == set(expected_masks) == MASK_KEYS[reference["class_id"]]
        differences = {}
        for name in sorted(set(actual_masks) | set(expected_masks)):
            left, right = actual_masks.get(name), expected_masks.get(name)
            differences[name] = (int(np.count_nonzero(left != right))
                                 if left is not None and right is not None and left.shape == right.shape
                                 and left.dtype == right.dtype else "shape_dtype_or_key_mismatch")
        checks.append({"detection_index": reference["detection_index"],
                       "class_id": reference["class_id"], "differing_fields": fields,
                       "mask_differences": differences,
                       "pass": not fields and contract and not any(differences.values())})
    return checks


def _load_cases():
    samples_path = BOARD / "samples.json"
    samples = json.loads(samples_path.read_text(encoding="utf-8"))
    if [item["class_id"] for item in samples] != list(range(6)) + [4]:
        raise ValueError("Expected six class samples followed by boundary_scale259")
    cases = []
    for sample in samples:
        path = BOARD / "images" / sample["file"]
        gray = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
        if gray is None or gray.shape != (512, 512) or gray.dtype != np.uint8:
            raise ValueError("Invalid sample image: " + sample["file"])
        cases.append((sample.get("case_id", "class_%d" % sample["class_id"]),
                      np.ascontiguousarray(gray), predictions_from_labels(sample)))

    frame_manifest = json.loads((FIXTURES / "frame_143.json").read_text(encoding="utf-8"))
    frame_path = FIXTURES / "frame_143.png"
    frame = cv2.imdecode(np.frombuffer(frame_path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
    if frame is None or frame.shape != (512, 512) or frame.dtype != np.uint8:
        raise ValueError("Invalid frame_143 input")
    cases.append(("frame_143", np.ascontiguousarray(frame), frame_manifest["predictions"]))
    cases.append(("empty_predictions", np.ascontiguousarray(frame), []))
    return cases


def run(output_root=None):
    cases = _load_cases()
    output_root = Path(output_root or ROOT / "results")
    output = output_root / datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "runtime_smoke/1.0", "version": VERSION, "completed": False,
              "pass": False, "new_fpga_access": False, "bitstream_built_or_changed": False,
              "fpga_access_attempted": False,
              "scope": "New runtime smoke only; no camera, online detector, FPS, or accuracy claim",
              "expected_known_strict_difference_cases": ["frame_143", "boundary_scale259"],
              "cases": [], "result_directory": str(output.resolve()),
              "bitstream_sha256": sha256(BOARD / "steel_triple.bit"),
              "hwh_sha256": sha256(BOARD / "steel_triple.hwh"),
              "python": __import__("sys").version, "numpy": np.__version__, "opencv": cv2.__version__}
    runtime = None
    try:
        report["fpga_access_attempted"] = True
        runtime = SteelDefectRuntime.open_board(BOARD / "steel_triple.bit", allow_arm_fallback=False)
        report["new_fpga_access"] = True
        for case_id, gray, predictions in cases:
            arm = runtime.process_frame(gray, predictions, mode="arm_float")
            pl = runtime.process_frame(gray, predictions, mode="pl_gaussian")
            feature_diffs = {
                "sobel": int(np.count_nonzero(pl["features"]["sobel"] != sobel_reference(gray))),
                "std7_q8": int(np.count_nonzero(pl["features"]["std7_q8"] != runtime_module_q8(gray, 7))),
                "std13_q8": int(np.count_nonzero(pl["features"]["std13_q8"] != runtime_module_q8(gray, 13))),
            }
            strict = compare_results(pl, arm)
            model_diff = pl["timing"]["pl_integer_model_different_pixels"]
            expected_mode = "pl_gaussian" if any(p["class_id"] in (2, 4) for p in predictions) else "arm_float"
            row = {"case_id": case_id, "roi_count": len(predictions),
                   "requested_mode": pl["timing"]["requested_mode"],
                   "actual_mode": pl["timing"]["actual_mode"],
                   "expected_mode": expected_mode, "feature_differences": feature_diffs,
                   "pl_integer_model_different_pixels": model_diff,
                   "strict_roi_checks": strict,
                   "arm_timing": arm["timing"], "pl_request_timing": pl["timing"]}
            report["cases"].append(row)
            print("SMOKE %s: actual=%s features=%s integer_diff=%s strict=%s" %
                  (case_id, row["actual_mode"], feature_diffs, model_diff,
                   all(item["pass"] for item in strict)), flush=True)

        unexpected = []
        for row in report["cases"]:
            known = row["case_id"] in report["expected_known_strict_difference_cases"]
            if row["actual_mode"] != row["expected_mode"]:
                unexpected.append(row["case_id"] + ":actual_mode")
            if any(row["feature_differences"].values()):
                unexpected.append(row["case_id"] + ":feature_difference")
            if row["expected_mode"] == "pl_gaussian" and row["pl_integer_model_different_pixels"] != 0:
                unexpected.append(row["case_id"] + ":PL_integer_model")
            if not known and any(not item["pass"] for item in row["strict_roi_checks"]):
                unexpected.append(row["case_id"] + ":new_strict_difference")
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
        (output / "runtime_smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
        print("RESULT_DIRECTORY=" + str(output.resolve()), flush=True)
    print("SMOKE_PASS:", report["pass"], flush=True)
    return report


def runtime_module_q8(gray, size):
    return _q8_reference(gray, size)


from q8_reference import q8_reference as _q8_reference


if __name__ == "__main__":
    run()
