"""Pure OpenCV/NumPy backend. No board driver, overlay or DMA dependency."""
import importlib.util
import time
from pathlib import Path

import cv2
import numpy as np


def load_private(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def local_std_q8(gray, size):
    """Native box filters, exact integer sums and RTL rounding; not Gaussian std."""
    if gray.dtype != np.uint8 or gray.shape != (512, 512) or size not in (7, 13):
        raise ValueError('Expected 512x512 uint8 and window 7 or 13')
    total = cv2.boxFilter(gray, cv2.CV_64F, (size, size), normalize=False,
                         borderType=cv2.BORDER_REFLECT_101).astype(np.int64)
    square = cv2.sqrBoxFilter(gray, cv2.CV_64F, (size, size), normalize=False,
                             borderType=cv2.BORDER_REFLECT_101).astype(np.int64)
    count = size * size
    mean = (total * 256 + count // 2) // count
    meansq = (square * 256 + count // 2) // count
    variance = np.maximum(meansq * 256 - mean * mean, 0)
    return np.sqrt(variance).astype(np.uint16)


class CpuBaseline:
    def __init__(self, v32_path, scalar_histogram, orientation_int16):
        self.fast = load_private(Path(v32_path), '_static_compare_cpu_v32')
        self.module = self.fast._runtime
        self.module.scalar_stats = scalar_histogram
        self.module.orientation_metrics = orientation_int16

    def process_frame(self, gray, predictions):
        started = time.perf_counter()
        # No externally supplied maps, saved feature maps or FPGA results are reused.
        sobel = self.module.sobel_magnitude(gray).astype(np.uint8)
        std7 = local_std_q8(gray, 7) if predictions else None
        std13 = local_std_q8(gray, 13) if predictions else None
        maps = None if not predictions else (
            cv2.multiply(std7, 1 / 256.0, dtype=cv2.CV_32F),
            cv2.multiply(std13, 1 / 256.0, dtype=cv2.CV_32F))
        feature_ms = (time.perf_counter() - started) * 1000
        records, masks, timing = self.fast.process_frame(
            gray, predictions, sobel_frame=sobel, local_std_frames=maps,
            execution='pure_arm_v32_v91_q8_box_float_gaussian')
        elapsed = (time.perf_counter() - started) * 1000
        return {'records': records, 'masks': masks,
                'features': {'sobel': sobel, 'std7_q8': std7, 'std13_q8': std13},
                'timing': {'pipeline_ms': elapsed, 'feature_ms': feature_ms,
                           'roi_runtime_timing': timing}}
