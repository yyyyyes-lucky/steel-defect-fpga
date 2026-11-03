"""Private experiment: unchanged baseline runtime plus optional PL ellipse.

No threshold, CCL, component filtering, Gaussian rule or class policy is changed.
The original release and its bitstream remain untouched.
"""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
import threading
import time
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'board'))
from ellipse7_driver import Ellipse7

spec = importlib.util.spec_from_file_location('_ellipse_experiment_base', ROOT / 'baseline/steel_defect_runtime.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
LOCK = threading.RLock()


@contextmanager
def ellipse_operator(driver, calls):
    module = base._fast._runtime
    original = module.supported_union

    def supported_union(dark, raw):
        started = time.perf_counter()
        support, timing = driver.dilate(raw)
        count, labels, stats, _ = cv2.connectedComponentsWithStats((dark > 0).astype(np.uint8), connectivity=8)
        keep = np.zeros(count, dtype=np.uint8)
        touched = np.unique(labels[support > 0])
        keep[touched[stats[touched, cv2.CC_STAT_AREA] >= 20]] = 255
        keep[0] = 0
        result = cv2.bitwise_or(raw, keep[labels]), int(np.count_nonzero(keep))
        timing['supported_union_total_ms'] = (time.perf_counter()-started)*1000
        calls.append(timing)
        return result

    module.supported_union = supported_union
    try:
        yield
    finally:
        module.supported_union = original


class ExperimentRuntime:
    def __init__(self, bitstream, enable_ellipse):
        started = time.perf_counter()
        self.base = base.SteelDefectRuntime.open_board(bitstream, allow_arm_fallback=False)
        self.ellipse = None
        self.closed = False
        try:
            if enable_ellipse:
                self.ellipse = Ellipse7(self.base.features.overlay)
        except BaseException:
            self.base.close()
            raise
        self.setup_ms = (time.perf_counter()-started)*1000

    def process_frame(self, gray, predictions, use_ellipse=False, gaussian_mode='pl_gaussian'):
        if self.closed:
            raise RuntimeError('Experiment runtime closed')
        if use_ellipse and self.ellipse is None:
            raise RuntimeError('No ellipse driver on this runtime')
        calls = []
        with LOCK, base._MODE_LOCK, base._HARDWARE_LOCK:
            if use_ellipse:
                with ellipse_operator(self.ellipse, calls):
                    result = self.base.process_frame(gray, predictions, mode=gaussian_mode)
            else:
                result = self.base.process_frame(gray, predictions, mode=gaussian_mode)
        result['timing']['ellipse_calls'] = calls
        result['timing']['ellipse_requested'] = bool(use_ellipse)
        result['timing']['experiment_version'] = 'ellipse7_system_v8_20261022'
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.ellipse is not None:
                self.ellipse.close()
                if not self.ellipse.recovery_safe:
                    raise RuntimeError('Unsafe DMA recovery: reboot board; do not reload overlay')
        finally:
            self.base.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
