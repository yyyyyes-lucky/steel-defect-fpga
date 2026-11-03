"""Offline software candidate. Does not change the sealed runtime or hardware.

Boxes/classes/confidence are preserved. Inclusion masks use two dark-response
scales and noise-aware hysteresis. Other masks only receive review flags.
All thresholds are heuristics; no segmentation quality guarantee is implied.
"""
import copy
import time

import cv2
import numpy as np

REVISION = 'mask_quality_sw_candidate_3'


def prepare_inclusion_maps(gray):
    if gray.dtype != np.uint8 or gray.ndim != 2:
        raise ValueError('Expected a two-dimensional uint8 grayscale image')
    pixels = gray.astype(np.float32)
    smooth = cv2.GaussianBlur(pixels, (0, 0), 1.0)
    narrow = np.maximum(cv2.GaussianBlur(smooth, (0, 0), 7.0) - smooth, 0)
    # Same broad-response definition already used by the patch/scale branches.
    broad_smooth = cv2.GaussianBlur(pixels, (0, 0), 1.5)
    broad = np.maximum(cv2.GaussianBlur(broad_smooth, (0, 0), 25.0) - broad_smooth, 0)
    return {'response': np.maximum(narrow, broad), 'residual': pixels - smooth}


def hysteresis(response, low, high, min_area=8):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (response >= low).astype(np.uint8), connectivity=8)
    keep = np.zeros(count, np.uint8)
    strong = np.unique(labels[response >= high])
    keep[strong[stats[strong, cv2.CC_STAT_AREA] >= min_area]] = 255
    keep[0] = 0
    return keep[labels]


def inclusion_mask(response, residual):
    center = float(np.median(residual))
    noise = float(1.4826 * np.median(np.abs(residual - center)))
    signal = float(np.percentile(response, 99))
    low = max(2.0, 1.5 * noise, .45 * signal)
    high = max(4.0, 3.0 * noise, .70 * signal)
    mask = hysteresis(response, low, high)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (mask > 0).astype(np.uint8), connectivity=8)
    largest = int(stats[1:, cv2.CC_STAT_AREA].max()) if count > 1 else 0
    area_floor = max(8, int(.002 * mask.size), int(.02 * largest))
    keep = (stats[:, cv2.CC_STAT_AREA] >= area_floor).astype(np.uint8) * 255
    keep[0] = 0
    mask = keep[labels]
    return mask, {
        'noise_mad': noise, 'low_threshold': low, 'high_threshold': high,
        'response_p99': signal, 'background_sigmas': [7.0, 25.0],
        'minimum_component_area': area_floor,
    }


def mask_metrics(mask):
    count, _, stats, _ = cv2.connectedComponentsWithStats(
        (mask > 0).astype(np.uint8), connectivity=8)
    pixels = int(np.count_nonzero(mask))
    largest = int(stats[1:, cv2.CC_STAT_AREA].max()) if count > 1 else 0
    return {'pixels': pixels, 'fraction': pixels / mask.size,
            'components': count - 1,
            'largest_component_share': largest / pixels if pixels else 0.0}


def review_mask(class_id, mask):
    metrics = mask_metrics(mask)
    reasons = []
    if not metrics['pixels']:
        reasons.append('empty_mask')
    elif class_id in (1, 2, 4) and metrics['fraction'] < .01:
        reasons.append('sparse_area_candidate')
    elif class_id == 5 and metrics['fraction'] < .001:
        reasons.append('sparse_edge_candidate')
    if class_id in (1, 2, 4) and metrics['fraction'] > .70:
        reasons.append('large_area_candidate')
    if metrics['components'] > 30 and metrics['largest_component_share'] < .25:
        reasons.append('fragmented_candidate')
    return metrics, reasons

