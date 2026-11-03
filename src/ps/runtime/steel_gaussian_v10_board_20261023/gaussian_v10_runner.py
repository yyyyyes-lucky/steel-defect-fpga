"""Separate v10 experiment; all PS math and transport use qualified v9.1 code."""
from datetime import datetime
import hashlib
import importlib
import json
from pathlib import Path
import struct
import sys
import uuid
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
DAILY_NAME = "steel_runtime_v9_1_qualified_20261023"
TIMING_CASES = ("class_0", "class_1", "class_2", "class_4", "frame_20", "frame_21")
_FAILED = False


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()




def initialize_runtime():
    daily = ROOT.parent / DAILY_NAME
    for name in ("runtime_v9_1", "combined_runtime", "candidates", "gaussian_candidate"):
        module = sys.modules.get(name)
        if module is not None and Path(module.__file__).resolve().parent != daily.resolve():
            raise RuntimeError("Conflicting module; restart notebook kernel: " + name)
    sys.path.insert(0, str(daily))
    entry = importlib.import_module("runtime_v9_1")
    integrity = entry.initialize_runtime()
    actual_environment = {"opencv": cv2.__version__, "numpy": np.__version__}
    return {"package": str(ROOT), "manifest_sha256": sha(ROOT / "manifest.json"),
            "new_bit_sha256": sha(ROOT / "hardware/steel_gaussian_v10.bit"),
            "new_hwh_sha256": sha(ROOT / "hardware/steel_gaussian_v10.hwh"),
            "daily_integrity": integrity, "environment": actual_environment}


def dependencies(integrity):
    entry = importlib.import_module("runtime_v9_1")
    board_root, board = entry.dependencies()
    a, experiment = board.common.dependencies(
        Path(integrity["daily_integrity"]["v9_integrity"]["source_package"]))
    return entry, board_root, board, a, experiment


def references():
    daily = ROOT.parent / DAILY_NAME
    refs = {}
    for name in ("v9_combined_acceptance.json", "roi_blur3_smoke.json"):
        prior = json.loads((daily / "qualification" / name).read_bytes())
        if not prior["pass"] or not prior["completed"]:
            raise RuntimeError("Unqualified reference")
        for row in prior["frames"] + prior.get("supplemental", []):
            case, sig = row["case"], row["reference_signature"]
            if case in refs and refs[case] != sig:
                raise AssertionError("Conflicting reference: " + case)
            refs[case] = sig
    return refs


def datasets(a):
    full, smoke = a.load_cases("full"), a.load_cases("smoke")
    assert len(full) == 184 and sum(len(p) for _, _, p in full) == 403
    samples = smoke + [x for x in full if x[0] in ("frame_19", "frame_20", "frame_21")]
    extra = [x for x in smoke if x[0] in ("boundary_scale259", "frame_143", "empty_predictions")]
    assert len(samples) == 12 and len({x[0] for x in samples}) == 12 and len(extra) == 3
    return full, samples, extra


def primitives(runtime):
    # These frozen inputs were independently generated using full uint64 MACs.
    # Driver regenerates REFLECT_101 padding from their unpadded centers.
    payload = (ROOT / "fixtures.bin").read_bytes()
    offset = 4
    count = struct.unpack_from("<I", payload)[0]
    if count != 24:
        raise AssertionError("Unexpected fixture count")
    result = []
    from combined_runtime import configuration
    driver = runtime.runtime.tail.base.gaussian
    with configuration(runtime.runtime, "combined"):
        for case in range(count):
            mode, rows, width = struct.unpack_from("<III", payload, offset)
            offset += 12
            taps = (9, 13, 57, 201)[mode]
            if width != 512 + taps - 1 or not 1 <= rows <= 512:
                raise AssertionError("Invalid fixture dimensions")
            padded = np.frombuffer(payload, "<u4", rows * width, offset).reshape(rows, width)
            offset += 4 * rows * width
            expected = np.frombuffer(payload, "<u4", rows * 512, offset).reshape(rows, 512)
            offset += 4 * rows * 512
            center = np.ascontiguousarray(padded[:, taps // 2:taps // 2 + 512])
            actual, timing = driver.row_pass(center, mode)
            diff = int(np.count_nonzero(actual != expected))
            if diff:
                raise AssertionError("Gaussian primitive mismatch: " + str(case))
            result.append({"case": case, "kernel_id": mode, "rows": rows,
                           "integer_different_pixels": diff, "timing": timing})
    if offset != len(payload):
        raise AssertionError("Trailing fixture data")
    return result


def summarize(timing):
    result = {}
    for name in TIMING_CASES:
        rows = [r for r in timing if r["case"] == name]
        lookup = {(r["block"], r["repeat"]): r for r in rows}
        pairs = []
        for old_block, new_block, order in ((0, 1, "old_first"), (3, 2, "new_first")):
            for repeat in range(3):
                old = lookup[old_block, repeat]["timing"]
                new = lookup[new_block, repeat]["timing"]
                a, b = (t["end_to_end_static_pipeline_ms"] for t in (old, new))
                if not np.isfinite([a, b]).all() or min(a, b) <= 0:
                    raise AssertionError("Invalid timing")
                pairs.append({"order": order, "repeat": repeat, "old_ms": a, "new_ms": b,
                              "speedup": a / b, "saved_ms": a - b,
                              "old_gaussian_ms": old["gaussian_response_ms"],
                              "new_gaussian_ms": new["gaussian_response_ms"]})
        result[name] = {
            "pairs": pairs, "wins": sum(x["new_ms"] < x["old_ms"] for x in pairs),
            "speedup_median": float(np.median([x["speedup"] for x in pairs])),
            "saved_ms_median": float(np.median([x["saved_ms"] for x in pairs])),
            "saved_ms_by_order": {order: float(np.median([x["saved_ms"] for x in pairs
                                                        if x["order"] == order]))
                                  for order in ("old_first", "new_first")}}
    return result


def run(phase="smoke"):
    global _FAILED
    if phase not in ("smoke", "full", "rollback"):
        raise ValueError("phase must be smoke, full or rollback")
    if _FAILED:
        raise RuntimeError("Previous failed run; reboot board and restart kernel")
    integrity = initialize_runtime()
    entry, board_root, board, a, experiment = dependencies(integrity)
    entry._guard(ROOT.parent)
    full, samples, extra = datasets(a)
    refs = references()
    if any(name not in refs for name, _, _ in full + samples + extra):
        raise AssertionError("Missing qualified signature")
    by_name = {name: (gray, predictions) for name, gray, predictions in samples}
    out = ROOT / "results" / ("run_" + datetime.now().strftime("%Y%m%d_%H%M%S")
                              + "_" + uuid.uuid4().hex[:8] + "_" + phase)
    out.mkdir(parents=True, exist_ok=False)
    marker = ROOT / "hardware_session_guard.json"
    r = {"schema": "gaussian_v10/1.0", "phase": phase, "completed": False, "pass": False,
         "hardware_session_safe": False, "integrity": integrity, "boot_id": entry._boot_id(),
         "formal_release_replaced": False, "performance_review_required": phase == "smoke",
         "overlay_loads": 0, "frames": [], "primitives": [], "timing_samples": [],
         "errors": [], "result_directory": str(out),
         "scope": "Identical qualified v9.1 PS/transport; only Gaussian RTL differs. CCL is not in this bitstream.",
         "performance_scope": "Static image pipeline including PS/cache/DMA/postprocessing, excluding overlay load, disk IO, oracle and detector. ABBA blocks, not adjacent alternating pairs."}
    path = out / "gaussian_v10_acceptance.json"
    def save():
        entry._save(path, r)
    runtime = None
    armed = False
    try:
        if marker.exists():
            marker.rename(ROOT / ("hardware_session_guard.previous_" + uuid.uuid4().hex + ".json"))
        with marker.open("x", encoding="utf-8") as f:
            json.dump({"boot_id": entry._boot_id(), "result_directory": str(out)}, f)
        armed = True
        cv2.setNumThreads(1)
        save()
        blocks = ("old", "new", "new", "old") if phase == "smoke" else (("new",) if phase == "full" else ("old",))
        for block, hardware in enumerate(blocks):
            bitstream = board_root / "hardware/steel_roi_blur3.bit" if hardware == "old" else ROOT / "hardware/steel_gaussian_v10.bit"
            runtime = entry.CombinedRuntime(board, experiment, bitstream)
            r["overlay_loads"] += 1
            r["last_loaded_hardware"] = hardware
            save()
            def execute(name, gray, predictions, feature_check=False):
                value = runtime.process_frame(gray, predictions, mode="combined")
                a.check_result(value, predictions)
                if a.signature(value) != refs[name]:
                    raise AssertionError("Qualified signature differs: " + name)
                t = value["timing"]
                expected_pl = any(p["class_id"] in (2, 4) for p in predictions)
                if t["actual_mode"] != ("pl_gaussian" if expected_pl else "arm_float") or t["fallback_reason"]:
                    raise AssertionError("Unexpected routing or fallback")
                if expected_pl and t["pl_integer_model_different_pixels"] != 0:
                    raise AssertionError("Integer oracle differs")
                if t["combined_configuration"] != "combined":
                    raise AssertionError("Qualified PS configuration not enabled")
                if len(t["blur3_calls"]) != sum(p["class_id"] == 2 for p in predictions):
                    raise AssertionError("Blur3 route differs")
                if any(x["status"] != 0 for x in t["blur3_calls"]):
                    raise AssertionError("Blur3 status differs")
                diffs = a.feature_diffs(value, gray) if feature_check else None
                if diffs is not None and any(diffs.values()):
                    raise AssertionError("Feature differences")
                return {"case": name, "hardware": hardware, "roi_count": len(predictions),
                        "exact": True, "feature_different_pixels": diffs, "timing": t}

            if hardware == "new" and (phase == "full" or block == 1):
                r["primitives"] = primitives(runtime)
                print("GAUSSIAN_PRIMITIVE_PASS=True CASES=24", flush=True)
                save()
            functional = (samples if block < 2 else []) if phase == "smoke" else (
                full + extra if phase == "full" else [x for x in samples if x[0] == "class_2"])
            for index, (name, gray, predictions) in enumerate(functional, 1):
                r["frames"].append(execute(name, gray, predictions, True))
                save()
                print(hardware.upper(), name, str(index) + "/" + str(len(functional)), "EXACT=True", flush=True)
            if phase == "smoke":
                # One warmup per case after every overlay programming.
                for name in TIMING_CASES:
                    execute(name, *by_name[name])
                for repeat in range(3):
                    order = TIMING_CASES if repeat % 2 == 0 else tuple(reversed(TIMING_CASES))
                    for name in order:
                        row = execute(name, *by_name[name])
                        row.update(block=block, repeat=repeat)
                        r["timing_samples"].append(row)
                    save()
                    print("TIMING", "block=" + str(block), hardware, "repeat=" + str(repeat + 1), flush=True)
            runtime.close()
            runtime = None
        if phase == "smoke":
            r["performance"] = summarize(r["timing_samples"])
            assert len(r["frames"]) == 24 and len(r["timing_samples"]) == 72
        elif phase == "full":
            assert len(r["frames"]) == 187 and sum(x["roi_count"] for x in r["frames"][:184]) == 403
        r.update(completed=True, **{"pass": True}, hardware_session_safe=True)
        save()
        marker.unlink()
    except BaseException as exc:
        _FAILED = armed
        r["completed"] = r["pass"] = r["hardware_session_safe"] = False
        r["errors"].append(type(exc).__name__ + ": " + str(exc))
        if runtime is not None:
            try:
                runtime.close()
            except BaseException as cleanup:
                r["errors"].append("cleanup: " + repr(cleanup))
        save()
        print("REPORT_FILE=" + str(path), flush=True)
        raise
    print("GAUSSIAN_V10_PASS=True", flush=True)
    print("LAST_LOADED_HARDWARE=" + r["last_loaded_hardware"], flush=True)
    print("REPORT_FILE=" + str(path), flush=True)
    return r
