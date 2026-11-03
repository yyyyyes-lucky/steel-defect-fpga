"""Reversible V32 runtime entry with an optional PL broad-dark response."""
from contextlib import contextmanager
import hashlib
import importlib.util
import math
import threading
import time
from pathlib import Path

import numpy as np


VERSION = "runtime_gaussian_final_20261022_v1"
ROOT = Path(__file__).resolve().parent
BOARD_ROOT = ROOT / "board"
RUNTIME_ROOT = ROOT / "runtime"
BITSTREAM = BOARD_ROOT / "steel_triple.bit"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load module: " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_fast = _load_module("_steel_final_v32_20261022", RUNTIME_ROOT / "optimized" / "roi_runtime_fast_v32.py")
_features_module = _load_module("_steel_final_features_20261022", BOARD_ROOT / "dual_features.py")
_gaussian_module = _load_module("_steel_final_gaussian_20261022", BOARD_ROOT / "gaussian_rows.py")
_fixed_module = _load_module("_steel_final_fixed_20261022", BOARD_ROOT / "fixed_dark_response.py")
_MODE_LOCK = threading.RLock()
_HARDWARE_LOCK = threading.RLock()


@contextmanager
def _broad_response(response):
    """Temporarily select a response in this private V32 module, serialized."""
    with _MODE_LOCK:
        original = _fast._runtime.broad_dark_response
        _fast._runtime.broad_dark_response = response
        try:
            yield
        finally:
            _fast._runtime.broad_dark_response = original


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class SteelDefectRuntime:
    """One-frame API. Components may be injected for host-side control-flow tests."""

    def __init__(self, feature_driver, gaussian_driver=None, *, fixed_model=None,
                 allow_arm_fallback=False):
        self.features = feature_driver
        self.gaussian = gaussian_driver
        self.fixed_model = fixed_model or _fixed_module
        self.allow_arm_fallback = bool(allow_arm_fallback)
        self._lock = threading.RLock()
        self.closed = False

    @classmethod
    def open_board(cls, bitstream=BITSTREAM, *, allow_arm_fallback=False):
        bitstream = Path(bitstream).resolve()
        if not bitstream.is_file() or not bitstream.with_suffix(".hwh").is_file():
            raise FileNotFoundError("Matching triple bitstream and HWH are required")
        features = _features_module.DualFeatures(bitstream)
        try:
            gaussian = _gaussian_module.GaussianRows(features.overlay)
        except Exception:
            features.close()
            raise
        runtime = cls(features, gaussian, allow_arm_fallback=allow_arm_fallback)
        runtime.bitstream_sha256 = _sha256(bitstream)
        runtime.hwh_sha256 = _sha256(bitstream.with_suffix(".hwh"))
        return runtime

    def process_frame(self, gray512_uint8, predictions, *, mode="arm_float"):
        if mode not in ("arm_float", "pl_gaussian"):
            raise ValueError("mode must be 'arm_float' or 'pl_gaussian'")
        gray = np.asarray(gray512_uint8)
        if gray.shape != (512, 512) or gray.dtype != np.uint8:
            raise ValueError("Expected 512x512 uint8 grayscale image")
        prediction_list = list(predictions)
        seen = set()
        classes = set()
        for prediction in prediction_list:
            if not isinstance(prediction, dict):
                raise ValueError("Each prediction must be a mapping")
            cls = prediction.get("class_id")
            index = prediction.get("detection_index")
            confidence = prediction.get("confidence")
            if isinstance(cls, bool) or not isinstance(cls, (int, np.integer)) or cls not in range(6):
                raise ValueError("class_id must be integer 0..5")
            if isinstance(index, bool) or not isinstance(index, (int, np.integer)) or index < 0 or index in seen:
                raise ValueError("detection_index must be unique nonnegative integer")
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                raise ValueError("confidence must be finite and within [0,1]")
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("confidence must be finite and within [0,1]")
            try:
                _fast._runtime.roi_bounds(prediction["box_xyxy_pixel"])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Invalid prediction box: %s" % exc) from exc
            seen.add(index)
            classes.add(int(cls))
        wants_pl = mode == "pl_gaussian" and bool(classes.intersection({2, 4}))
        with self._lock, _HARDWARE_LOCK:
            if self.closed:
                raise RuntimeError("Runtime is closed")
            total_start = time.perf_counter()
            sobel, std7_q8, std13_q8, feature_timing = self.features.process(gray)
            import cv2
            std7 = cv2.multiply(std7_q8, 1 / 256.0, dtype=cv2.CV_32F)
            std13 = cv2.multiply(std13_q8, 1 / 256.0, dtype=cv2.CV_32F)
            kwargs = {"sobel_frame": sobel, "local_std_frames": (std7, std13),
                      "execution": "runtime_gaussian_final_20261022"}

            response_time = 0.0
            pl_response_value = None
            arm_response = _fast._runtime.broad_dark_response
            fallback_reason = None
            actual_mode = "arm_float"
            if wants_pl:
                try:
                    def pl_response(image):
                        nonlocal response_time, pl_response_value
                        start = time.perf_counter()
                        response, _details = self.gaussian.response(image, "broad")
                        response_time = (time.perf_counter() - start) * 1000.0
                        pl_response_value = response
                        return response

                    # The fixed-point oracle is deliberately outside all reported runtime intervals.
                    with _broad_response(pl_response):
                        result = _fast.process_frame(gray, prediction_list, **kwargs)
                    actual_mode = "pl_gaussian"
                except Exception as exc:
                    if not self.allow_arm_fallback:
                        raise
                    fallback_reason = "%s: %s" % (type(exc).__name__, exc)
                    with _broad_response(arm_response):
                        result = _fast.process_frame(gray, prediction_list, **kwargs)
            else:
                result = _fast.process_frame(gray, prediction_list, **kwargs)

            records, masks, runtime_timing = result
            pipeline_ms = (time.perf_counter() - total_start) * 1000.0
            oracle = None
            if actual_mode == "pl_gaussian":
                oracle_start = time.perf_counter()
                oracle = self.fixed_model.response(gray, "broad", 30, 20)
                oracle_ms = (time.perf_counter() - oracle_start) * 1000.0
                if oracle.shape != (512, 512) or not np.isfinite(oracle).all():
                    raise RuntimeError("PL oracle produced an invalid response")
            else:
                oracle_ms = 0.0
            report = {
                "version": VERSION, "requested_mode": mode, "actual_mode": actual_mode,
                "fallback_reason": fallback_reason,
                "feature_timing": feature_timing,
                "gaussian_response_ms": response_time,
                "fixed_oracle_ms_excluded_from_pipeline": oracle_ms,
                "roi_runtime_timing": runtime_timing,
                "end_to_end_static_pipeline_ms": pipeline_ms,
                "pl_integer_model_different_pixels": (
                    int(np.count_nonzero(pl_response_value != oracle))
                    if oracle is not None else None),
            }
            return {"records": records, "masks": masks, "timing": report,
                    "features": {"sobel": sobel, "std7_q8": std7_q8,
                                 "std13_q8": std13_q8}}

    def close(self):
        with self._lock:
            if self.closed:
                return
            self.closed = True
            errors = []
            for component in (self.gaussian, self.features):
                if component is not None:
                    try:
                        component.close()
                    except Exception as exc:
                        errors.append(exc)
            if errors:
                raise RuntimeError("One or more hardware drivers failed to close") from errors[0]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
