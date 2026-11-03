"""Strict comparison helpers for the independent transport experiment."""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
MASK_KEYS = {0: set(), 1: {"candidate"}, 2: {"raw_candidate", "coarse_extent"},
             3: set(), 4: {"candidate"}, 5: {"candidate"}}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compare_results(actual, expected):
    records, masks, _ = actual
    refs, expected_masks, _ = expected
    if len(records) != len(refs) or len(masks) != len(expected_masks) or len(records) != len(masks):
        raise ValueError("ROI count mismatch")
    checks = []
    for a, b, am, bm in zip(records, refs, masks, expected_masks):
        keys = (set(a) | set(b)) - {"provenance", "processing_time_ms"}
        fields = [k for k in sorted(keys) if k not in a or k not in b or a[k] != b[k]]
        contract = set(am) == set(bm) == MASK_KEYS[b["class_id"]]
        differences = {name: (int(np.count_nonzero(am[name] != bm[name]))
                             if name in am and name in bm and am[name].shape == bm[name].shape
                             and am[name].dtype == bm[name].dtype else "shape_dtype_or_key_mismatch")
                       for name in sorted(set(am) | set(bm))}
        checks.append({"label_index": b["detection_index"], "state": b["branch_state"],
                       "differing_fields": fields, "mask_differences": differences,
                       "pass": not fields and contract and not any(differences.values())})
    return checks


def checked(actual, expected):
    checks = compare_results(actual, expected)
    if not all(c["pass"] for c in checks):
        raise ValueError("Strict ROI mismatch: " + json.dumps(checks, ensure_ascii=False))
    return checks


def predictions_for(sample):
    result = []
    for index, label in enumerate(sample["labels"]):
        if len(label) != 5 or not np.all(np.isfinite(label)):
            raise ValueError("Invalid label")
        cls, cx, cy, width, height = label
        box = np.array([cx-width/2, cy-height/2, cx+width/2, cy+height/2])
        if cls != sample["class_id"] or not (0 < width <= 1 and 0 < height <= 1) or box.min() < -1e-7 or box.max() > 1+1e-7:
            raise ValueError("Invalid class/box; no silent clipping")
        result.append({"class_id": int(cls), "detection_index": index, "confidence": 1.0,
                       "box_xyxy_pixel": (box*512).tolist()})
    return result


def summarize(values):
    return {"median_ms": float(np.median(values)), "p95_ms": float(np.percentile(values, 95)), "samples_ms": values}


def cpu_sobel(gray):
    gx = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3)
    result = np.minimum(abs(gx.astype(np.int32)) + abs(gy.astype(np.int32)), 255).astype(np.uint8)
    result[[0, -1], :] = 0
    result[:, [0, -1]] = 0
    return result
