"""Accepted mask math; optional existing PL broad response, separately qualified."""
import copy
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from selected_inclusion import inclusion_mask, hysteresis, mask_metrics, review_mask

REVISION = 'mask_quality_all_sw_2_legacy_scratch'


def prepare_maps(gray, classes=(1, 2, 4), broad_provider=None):
    if gray.ndim != 2 or gray.dtype != np.uint8:
        raise ValueError('Expected uint8 grayscale')
    classes = set(classes)
    maps = {}
    if not classes.intersection({1, 2, 4}):
        return maps
    pixels = gray.astype(np.float32)
    smooth = cv2.GaussianBlur(pixels, (0, 0), 1.)
    maps['residual'] = pixels - smooth
    if 1 in classes:
        background = cv2.GaussianBlur(smooth, (0, 0), 7.)
        maps['narrow'] = np.maximum(background - smooth, 0)
    if classes.intersection({1, 2, 4}):
        broad_smooth = cv2.GaussianBlur(pixels, (0, 0), 1.5) if broad_provider is None or 2 in classes else None
        maps['broad'] = (np.maximum(cv2.GaussianBlur(broad_smooth, (0, 0), 25.) - broad_smooth, 0)
                         if broad_provider is None else broad_provider(gray))
        if maps['broad'].shape != gray.shape or maps['broad'].dtype != np.float32:
            raise ValueError('Broad provider must return a float32 full-frame response')
        if not np.isfinite(maps['broad']).all() or maps['broad'].min() < 0:
            raise ValueError('Invalid broad response')
        if 2 in classes:
            maps['patch_smooth'] = np.clip(broad_smooth, 0, 255).astype(np.uint8)
    if 1 in classes:
        maps['response'] = np.maximum(maps['narrow'], maps['broad'])
    return maps


def noise_level(residual):
    return float(1.4826 * np.median(np.abs(residual - np.median(residual))))


def filter_components(mask, minimum=12):
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    keep = (stats[:, cv2.CC_STAT_AREA] >= minimum).astype(np.uint8) * 255
    keep[0] = 0
    return keep[labels]


def dark_seeds(response, residual, *, scale=False):
    noise = noise_level(residual)
    signal = float(np.percentile(response, 99))
    low = max(4. if scale else 3., 1.5 * noise, (.35 if scale else .20) * signal)
    high = max(8. if scale else 6., 3. * noise, (.60 if scale else .40) * signal)
    minimum = 20
    return hysteresis(response, low, high, minimum), {
        'noise_mad': noise, 'response_p99': signal, 'low_threshold': low,
        'high_threshold': high, 'minimum_component_area': minimum,
    }


def patch_mask(smooth, response, residual):
    raw, info = dark_seeds(response, residual)
    threshold, binary = cv2.threshold(smooth, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    dark = filter_components(binary, 20)
    dark_group, bright_group = smooth[binary > 0], smooth[binary == 0]
    dark_mean = float(dark_group.mean()) if dark_group.size else None
    bright_mean = float(bright_group.mean()) if bright_group.size else None
    contrast = bright_mean - dark_mean if dark_mean is not None and bright_mean is not None else 0.
    count, labels, stats, _ = cv2.connectedComponentsWithStats((dark > 0).astype(np.uint8), connectivity=8)
    keep = np.zeros(count, np.uint8)
    supported = np.unique(labels[raw > 0])
    keep[supported] = 255
    keep[0] = 0
    # Avoid turning a uniform/noisy ROI's arbitrary Otsu split into a patch.
    contrast_floor = max(8., 3. * info['noise_mad'])
    if contrast < contrast_floor:
        keep[:] = 0
    extent = keep[labels]
    info.update({'otsu_threshold': float(threshold), 'dark_group_mean': dark_mean,
                 'bright_group_mean': bright_mean, 'gray_group_contrast': contrast,
                 'contrast_floor': contrast_floor, 'supported_components': int(np.count_nonzero(keep)),
                 'support_dilation': 0, 'smooth_sigma': 1.5})
    return {'raw_candidate': raw, 'coarse_extent': extent}, info, dark


def make_masks(cls, maps, bounds):
    x1, y1, x2, y2 = bounds
    crop = np.s_[y1:y2, x1:x2]
    if cls == 1:
        mask, info = inclusion_mask(maps['response'][crop], maps['residual'][crop])
        return {'candidate': mask}, info, None
    if cls == 2:
        return patch_mask(maps['patch_smooth'][crop], maps['broad'][crop], maps['residual'][crop])
    if cls == 4:
        mask, info = dark_seeds(maps['broad'][crop], maps['residual'][crop], scale=True)
        return {'candidate': mask}, info, None
    if cls == 5:
        raise ValueError('Scratch must reuse the original baseline records/masks')
    return {}, {}, None

