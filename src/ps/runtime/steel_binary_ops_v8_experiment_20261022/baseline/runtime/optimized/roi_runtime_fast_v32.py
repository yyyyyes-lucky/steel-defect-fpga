"""Isolated candidate: preserve V31 outputs, avoid unused/repeated work.

Load a private copy of the frozen V31 module so the reference module used by
other code is never patched. Gaussian responses, orientation math, thresholds,
branch states, record schema and returned masks keep their original rules.
"""
import importlib.util
from pathlib import Path

import cv2
import numpy as np


_source = Path(__file__).resolve().parent.parent / "roi_runtime_portable_v31.py"
_spec = importlib.util.spec_from_file_location("_steel_roi_v32_private", str(_source))
_runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_runtime)
rounded = _runtime.rounded
CLASS_NAMES = _runtime.CLASS_NAMES


def scalar_stats(values):
    values = np.asarray(values, dtype=np.float32)
    # Keep the original median/reductions and percentile interpolation; request
    # the three percentiles together instead of partitioning three times.
    p10, p90, p95 = np.percentile(values, [10, 90, 95])
    return {"mean": rounded(values.mean()), "median": rounded(np.median(values)),
            "p10": rounded(p10), "p90": rounded(p90), "p95": rounded(p95),
            "std": rounded(values.std())}


def mask_stats(mask):
    # This API returns only component counts/areas. Contours, covariance,
    # eigenvectors and projected spans from region_properties are unused.
    count, _, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    pixels = int(np.count_nonzero(mask))
    largest = int(stats[1:, cv2.CC_STAT_AREA].max()) if count > 1 else 0
    return {"candidate_pixels": pixels, "candidate_fraction": rounded(np.mean(mask > 0)),
            "region_count": count - 1, "largest_region_area_px": largest,
            "largest_region_share": rounded(largest / pixels) if pixels else 0.0}


def inclusion_stats(mask):
    # V31 returns geometry for the largest component only. Preserve the stable
    # first-label tie rule, then compute geometry only for that component.
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    largest_region = None
    largest_area = 0
    if count > 1:
        index = int(np.argmax(stats[1:, cv2.CC_STAT_AREA])) + 1
        largest_area = int(stats[index, cv2.CC_STAT_AREA])
        largest_region = _runtime.region_properties((labels == index).astype(np.uint8))[0]
    return {"candidate_pixels": int(np.count_nonzero(mask)),
            "candidate_fraction": rounded(np.mean(mask > 0)), "region_count": count - 1,
            "largest_region_area_px": largest_area, "largest_region": largest_region}


def hysteresis(response, low, high, minimum_area):
    low_mask = (response >= low).astype(np.uint8)
    high_mask = response >= high
    count, labels, stats, _ = cv2.connectedComponentsWithStats(low_mask, connectivity=8)
    keep = np.zeros(count, dtype=np.uint8)
    strong = np.unique(labels[high_mask])
    keep[strong[stats[strong, cv2.CC_STAT_AREA] >= minimum_area]] = 255
    keep[0] = 0
    return keep[labels]


def filter_components(mask, minimum_area=20):
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    keep = np.where(stats[:, cv2.CC_STAT_AREA] >= minimum_area, 255, 0).astype(np.uint8)
    keep[0] = 0
    return keep[labels]


def supported_union(dark, raw):
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    support = cv2.dilate((raw > 0).astype(np.uint8), kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats((dark > 0).astype(np.uint8), connectivity=8)
    keep = np.zeros(count, dtype=np.uint8)
    touched = np.unique(labels[support > 0])
    keep[touched[stats[touched, cv2.CC_STAT_AREA] >= 20]] = 255
    keep[0] = 0
    return cv2.bitwise_or(raw, keep[labels]), int(np.count_nonzero(keep))


for _name in ("scalar_stats", "mask_stats", "inclusion_stats", "hysteresis", "filter_components", "supported_union"):
    setattr(_runtime, _name, globals()[_name])


def process_frame(gray, predictions, *, sobel_frame=None, execution="arm_cpu", local_std_frames=None):
    return _runtime.process_frame(gray, predictions, sobel_frame=sobel_frame,
                                   execution=execution, local_std_frames=local_std_frames)
