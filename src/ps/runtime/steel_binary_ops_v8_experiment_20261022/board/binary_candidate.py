"""Private V32 instrument/model path. CCL and all downstream math stay on CPU."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import time

import cv2
import numpy as np

from binary_models import check_kernel, dilate7, ge_mask

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('_binary_private_v32', str(ROOT / 'optimized/roi_runtime_fast_v32.py'))
fast = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fast)
_active = False


@contextmanager
def operators(mode, diagnostics):
    global _active
    if _active or mode not in ('profile', 'model'):
        raise RuntimeError('Unsupported or overlapping private candidate call')
    check_kernel()
    module = fast._runtime
    originals = {name: getattr(module, name) for name in ('hysteresis', 'supported_union')}
    _active = True
    diagnostics.setdefault('calls', [])

    def hysteresis(response, low, high, minimum_area):
        begin = time.perf_counter()
        if mode == 'model':
            low_mask = ge_mask(response, low).astype(np.uint8)
            high_mask = ge_mask(response, high)
        else:
            low_mask = (response >= low).astype(np.uint8)
            high_mask = response >= high
        thresholded = time.perf_counter()
        count, labels, stats, _ = cv2.connectedComponentsWithStats(low_mask, connectivity=8)
        labelled = time.perf_counter()
        keep = np.zeros(count, dtype=np.uint8)
        strong = np.unique(labels[high_mask])
        keep[strong[stats[strong, cv2.CC_STAT_AREA] >= minimum_area]] = 255
        keep[0] = 0
        result = keep[labels]
        end = time.perf_counter()
        diagnostics['calls'].append({'operation': 'hysteresis', 'shape': list(response.shape),
            'input_dtype': str(response.dtype), 'low': low, 'high': high,
            'threshold_pair_ms': (thresholded-begin)*1000,
            'ccl_ms': (labelled-thresholded)*1000, 'keep_and_gather_ms': (end-labelled)*1000,
            'total_ms': (end-begin)*1000})
        return result

    def supported_union(dark, raw):
        begin = time.perf_counter()
        if mode == 'model':
            support = dilate7(raw)
        else:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
            support = cv2.dilate((raw > 0).astype(np.uint8), kernel)
        dilated = time.perf_counter()
        count, labels, stats, _ = cv2.connectedComponentsWithStats((dark > 0).astype(np.uint8), connectivity=8)
        labelled = time.perf_counter()
        keep = np.zeros(count, dtype=np.uint8)
        touched = np.unique(labels[support > 0])
        keep[touched[stats[touched, cv2.CC_STAT_AREA] >= 20]] = 255
        keep[0] = 0
        result = cv2.bitwise_or(raw, keep[labels]), int(np.count_nonzero(keep))
        end = time.perf_counter()
        diagnostics['calls'].append({'operation': 'supported_union', 'shape': list(raw.shape),
            'normalize_and_ellipse_dilate_ms': (dilated-begin)*1000,
            'ccl_ms': (labelled-dilated)*1000, 'overlap_keep_and_union_ms': (end-labelled)*1000,
            'total_ms': (end-begin)*1000})
        if diagnostics.get('capture_inputs'):
            diagnostics.setdefault('raw_inputs', []).append(raw.copy())
        return result

    try:
        module.hysteresis = hysteresis
        module.supported_union = supported_union
        yield
    finally:
        for name, function in originals.items():
            setattr(module, name, function)
        _active = False


def process_frame(gray, predictions, *, mode='model', diagnostics=None, **kwargs):
    diagnostics = {} if diagnostics is None else diagnostics
    with operators(mode, diagnostics):
        return fast.process_frame(gray, predictions, **kwargs)
