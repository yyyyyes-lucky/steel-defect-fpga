"""Portable steel ROI runtime for ARM/PYNQ: stdlib + NumPy + OpenCV only."""
import copy
import math
import time

import cv2
import numpy as np


SCHEMA = "steel_roi/2.0"
CLASS_NAMES = {0: "裂纹", 1: "夹杂", 2: "斑块", 3: "麻面", 4: "氧化皮压入", 5: "划痕"}
BRANCHES = {
    0: ("crack_statistics", "V28/V29 statistics; no Hessian mask"),
    1: ("inclusion_candidate", "V18/V19 dark hysteresis 5/8 min8"),
    2: ("patch_coarse_extent", "V22 raw-supported Otsu; raw 12/20 min20"),
    3: ("pitted_statistics", "V26/V27 multiscale statistics"),
    4: ("scale_shape_candidate", "V24/V25 dark hysteresis 12/20 min20"),
    5: ("scratch_edge_candidate", "V16 Sobel64 min20"),
}


def rounded(value):
    return round(float(value), 6)


def scalar_stats(values):
    values = np.asarray(values, dtype=np.float32)
    return {
        "mean": rounded(values.mean()), "median": rounded(np.median(values)),
        "p10": rounded(np.percentile(values, 10)), "p90": rounded(np.percentile(values, 90)),
        "p95": rounded(np.percentile(values, 95)), "std": rounded(values.std()),
    }


def local_std(gray, sigma):
    pixels = gray.astype(np.float32)
    mean = cv2.GaussianBlur(pixels, (0, 0), sigma)
    mean_square = cv2.GaussianBlur(pixels*pixels, (0, 0), sigma)
    return np.sqrt(np.maximum(mean_square-mean*mean, 0.0))


def sobel_magnitude(gray):
    dx = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3, borderType=cv2.BORDER_CONSTANT)
    dy = cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3, borderType=cv2.BORDER_CONSTANT)
    result = np.clip(np.abs(dx.astype(np.int32))+np.abs(dy.astype(np.int32)), 0, 255).astype(np.float32)
    result[[0, -1], :] = 0
    result[:, [0, -1]] = 0
    return result


def inclusion_dark_response(gray):
    pixels = gray.astype(np.float32)
    smooth = cv2.GaussianBlur(pixels, (0, 0), 1.0)
    background = cv2.GaussianBlur(smooth, (0, 0), 7.0)
    return np.maximum(background-smooth, 0.0)


def broad_dark_response(gray):
    smooth = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.5)
    background = cv2.GaussianBlur(smooth, (0, 0), 25.0)
    return np.maximum(background-smooth, 0.0)


def direction_features(mask):
    points = np.column_stack(np.nonzero(mask > 0))[:, ::-1].astype(np.float32)
    if len(points) < 2:
        return {"dominant_orientation_deg": None, "orientation_coherence": None,
                "major_span_px": None, "minor_span_px": None}
    centered = points-points.mean(axis=0)
    values, vectors = np.linalg.eigh(np.cov(centered, rowvar=False))
    order = np.argsort(values)[::-1]
    values, vectors = values[order], vectors[:, order]
    if values[0] <= 0:
        return {"dominant_orientation_deg": None, "orientation_coherence": None,
                "major_span_px": None, "minor_span_px": None}
    major, minor = vectors[:, 0], vectors[:, 1]
    return {
        "dominant_orientation_deg": rounded(np.degrees(np.arctan2(major[1], major[0])) % 180.0),
        "orientation_coherence": rounded((values[0]-values[1])/(values[0]+values[1]+1e-9)),
        "major_span_px": rounded(np.ptp(centered @ major)+1),
        "minor_span_px": rounded(np.ptp(centered @ minor)+1),
    }


def region_properties(mask):
    height, width = mask.shape
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (mask > 0).astype(np.uint8), connectivity=8
    )
    results = []
    for index in range(1, count):
        x1, y1, box_width, box_height, _ = map(int, stats[index])
        x2, y2 = x1+box_width, y1+box_height
        component = (labels[y1:y2, x1:x2] == index).astype(np.uint8)
        area = int(component.sum())
        if not area:
            continue
        contours, hierarchy = cv2.findContours(component, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)[-2:]
        outer = [contour for j, contour in enumerate(contours) if hierarchy[0, j, 3] == -1]
        holes = [contour for j, contour in enumerate(contours) if hierarchy[0, j, 3] != -1]
        contour_area = max(0.0, sum(cv2.contourArea(c) for c in outer)-sum(cv2.contourArea(c) for c in holes))
        perimeter = float(sum(cv2.arcLength(c, True) for c in contours))
        hull_area = float(cv2.contourArea(cv2.convexHull(np.concatenate(outer))))
        direction = direction_features(component)
        major_span = direction["major_span_px"]
        results.append({
            "area_px": area, "box_xyxy_roi": [x1, y1, x2, y2],
            "aspect_ratio": rounded(max(box_width, box_height)/max(1, min(box_width, box_height))),
            "extent": rounded(area/max(1, box_width*box_height)),
            "solidity": rounded(min(1.0, contour_area/hull_area)) if hull_area else None,
            "circularity": rounded(4*np.pi*contour_area/(perimeter*perimeter)) if perimeter else None,
            "equivalent_diameter_px": rounded(np.sqrt(4*area/np.pi)),
            "border_distance_px": int(min(x1, y1, width-x2, height-y2)),
            "candidate_area_per_major_span_px": rounded(area/major_span) if major_span else None,
            **direction,
        })
    results.sort(key=lambda item: item["area_px"], reverse=True)
    return results


def hysteresis(response, low, high, minimum_area):
    low_mask = (response >= low).astype(np.uint8)
    high_mask = response >= high
    count, labels, stats, _ = cv2.connectedComponentsWithStats(low_mask, connectivity=8)
    keep = np.zeros(count, dtype=np.uint8)
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] >= minimum_area and np.any(high_mask[labels == index]):
            keep[index] = 255
    return keep[labels]


def candidate_summary(response, threshold, minimum_area):
    raw = (response >= threshold).astype(np.uint8)*255
    count, labels, stats, _ = cv2.connectedComponentsWithStats(raw, connectivity=8)
    keep = np.zeros(count, dtype=np.uint8)
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] >= minimum_area:
            keep[index] = 255
    mask = keep[labels]
    props = region_properties(mask)
    pixels = int(np.count_nonzero(mask))
    areas = [item["area_px"] for item in props]
    union_box = None if not props else [
        min(item["box_xyxy_roi"][0] for item in props),
        min(item["box_xyxy_roi"][1] for item in props),
        max(item["box_xyxy_roi"][2] for item in props),
        max(item["box_xyxy_roi"][3] for item in props),
    ]
    return mask, {
        "threshold": threshold, "min_area": minimum_area, "candidate_pixels": pixels,
        "candidate_fraction": rounded(pixels/mask.size), "region_count": len(props),
        "largest_region_area_px": max(areas) if areas else 0,
        "mean_region_area_px": rounded(np.mean(areas)) if areas else 0.0,
        "median_region_area_px": rounded(np.median(areas)) if areas else 0.0,
        "largest_region_share": rounded(max(areas)/pixels) if pixels else 0.0,
        "fragmentation_per_10k_px": rounded(len(props)*10000/mask.size),
        "union_box_xyxy_roi": union_box, "overall_direction": direction_features(mask),
        "regions": props,
    }


def mask_stats(mask):
    props = region_properties(mask)
    pixels = int(np.count_nonzero(mask))
    return {
        "candidate_pixels": pixels, "candidate_fraction": rounded(np.mean(mask > 0)),
        "region_count": len(props), "largest_region_area_px": props[0]["area_px"] if props else 0,
        "largest_region_share": rounded(props[0]["area_px"]/pixels) if pixels else 0.0,
    }


def inclusion_stats(mask):
    props = region_properties(mask)
    return {
        "candidate_pixels": int(np.count_nonzero(mask)),
        "candidate_fraction": rounded(np.mean(mask > 0)),
        "region_count": len(props), "largest_region_area_px": props[0]["area_px"] if props else 0,
        "largest_region": props[0] if props else None,
    }


def grid_metrics(response, grid=4):
    height, width = response.shape
    means = []
    for row in range(grid):
        for col in range(grid):
            y1, y2 = round(row*height/grid), round((row+1)*height/grid)
            x1, x2 = round(col*width/grid), round((col+1)*width/grid)
            means.append(float(response[y1:y2, x1:x2].mean()))
    values = np.asarray(means, np.float32)
    mean = float(values.mean())
    return {
        "grid_size": grid, "cell_means": [rounded(value) for value in values],
        "cell_mean_cv": rounded(values.std()/(mean+1e-9)),
        "cell_mean_range": rounded(np.ptp(values)),
        "active_fraction_ge_4": rounded(np.mean(values >= 4)),
    }


def orientation_metrics(gray, threshold=64, bins=12):
    pixels = gray.astype(np.float32)
    gx = cv2.Sobel(pixels, cv2.CV_32F, 1, 0, ksize=3, borderType=cv2.BORDER_CONSTANT)
    gy = cv2.Sobel(pixels, cv2.CV_32F, 0, 1, ksize=3, borderType=cv2.BORDER_CONSTANT)
    selected = np.abs(gx)+np.abs(gy) >= threshold
    selected[[0, -1], :] = False
    selected[:, [0, -1]] = False
    count = int(np.count_nonzero(selected))
    if not count:
        return {"strong_edge_pixel_count": 0, "entropy": None, "coherence": None}
    angles = np.mod(np.arctan2(gy[selected], gx[selected]), np.pi)
    weights = np.hypot(gx[selected], gy[selected]).astype(np.float64)
    hist, _ = np.histogram(angles, bins=bins, range=(0, np.pi), weights=weights)
    probabilities = hist/max(float(hist.sum()), 1e-9)
    nonzero = probabilities > 0
    entropy = -float(np.sum(probabilities[nonzero]*np.log(probabilities[nonzero])))/np.log(bins)
    vector = np.sum(weights*np.exp(2j*angles))/max(float(weights.sum()), 1e-9)
    return {"strong_edge_pixel_count": count, "entropy": rounded(entropy), "coherence": rounded(abs(vector))}


def descriptors(roi, std2, std4, sobel):
    grid = grid_metrics(std2)
    orientation = orientation_metrics(roi)
    mean2, mean4 = float(std2.mean()), float(std4.mean())
    return {
        "local_std_sigma2_mean": rounded(mean2),
        "local_std_sigma2_p90": rounded(np.percentile(std2, 90)),
        "local_std_sigma4_mean": rounded(mean4),
        "local_std_sigma4_p90": rounded(np.percentile(std4, 90)),
        "coarse_to_fine_std_ratio": rounded(mean4/(mean2+1e-9)),
        "edge_density_ge_64": rounded(np.mean(sobel >= 64)),
        "gradient_orientation_entropy": orientation["entropy"],
        "gradient_orientation_coherence": orientation["coherence"],
        "strong_edge_pixel_count": orientation["strong_edge_pixel_count"],
        "grid_cell_mean_cv": grid["cell_mean_cv"],
        "grid_cell_mean_range": grid["cell_mean_range"],
        "grid_active_fraction_ge_4": grid["active_fraction_ge_4"], "grid": grid,
    }


def filter_components(mask, minimum_area=20):
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    result = np.zeros_like(mask, dtype=np.uint8)
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] >= minimum_area:
            result[labels == index] = 255
    return result


def otsu_dark(roi):
    smooth = cv2.GaussianBlur(roi, (0, 0), 3.0)
    threshold, dark = cv2.threshold(smooth, 0, 255, cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    dark = filter_components(dark, 20)
    selected, rejected = smooth[dark > 0], smooth[dark == 0]
    dark_mean = float(selected.mean()) if selected.size else None
    bright_mean = float(rejected.mean()) if rejected.size else None
    contrast = None if dark_mean is None or bright_mean is None else bright_mean-dark_mean
    return dark, {
        "otsu_threshold": rounded(threshold),
        "dark_mean": None if dark_mean is None else rounded(dark_mean),
        "bright_mean": None if bright_mean is None else rounded(bright_mean),
        "gray_group_contrast": None if contrast is None else rounded(contrast),
    }


def supported_union(dark, raw):
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    support = cv2.dilate((raw > 0).astype(np.uint8), kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats((dark > 0).astype(np.uint8), connectivity=8)
    supported = np.zeros_like(raw)
    kept = 0
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] >= 20 and np.any(support[labels == index]):
            supported[labels == index] = 255
            kept += 1
    return cv2.bitwise_or(raw, supported), kept


def patch_reasons(raw, dark, extent, contrast, supported_count):
    reasons = []
    if contrast is None or contrast < 8.0:
        reasons.append("low_gray_contrast")
    if dark["candidate_fraction"] < .05 or dark["candidate_fraction"] > .85:
        reasons.append("extreme_dark_fraction")
    if extent["candidate_fraction"] > .70:
        reasons.append("large_supported_extent")
    if raw["candidate_fraction"] < .01:
        reasons.append("weak_raw_support")
    if dark["candidate_pixels"] and not supported_count:
        reasons.append("no_supported_component")
    return reasons


def pitted_reasons(features):
    reasons = []
    if features["edge_density_ge_64"] < .005 and features["local_std_sigma2_mean"] < 2:
        reasons.append("very_weak_texture")
    if features["strong_edge_pixel_count"] < 20:
        reasons.append("orientation_unreliable")
    elif features["gradient_orientation_coherence"] is not None and features["gradient_orientation_coherence"] > .65:
        reasons.append("strong_directionality")
    if features["grid_cell_mean_cv"] > .35:
        reasons.append("spatially_concentrated")
    return reasons


def roi_bounds(box, width=512, height=512, margin=8):
    if len(box) != 4 or not all(math.isfinite(float(value)) for value in box):
        raise ValueError("box_xyxy_pixel must contain four finite coordinates")
    x1, y1, x2, y2 = map(float, box)
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError("Expected a nonempty detection box inside the original image")
    return [max(0, math.floor(x1)-margin), max(0, math.floor(y1)-margin),
            min(width, math.ceil(x2)+margin), min(height, math.ceil(y2)+margin)]


def map_regions(candidate, bounds):
    x1, y1, _, _ = bounds
    def mapped(box):
        return [box[0]+x1, box[1]+y1, box[2]+x1, box[3]+y1]
    for region in candidate.get("regions", []):
        region["box_xyxy_image"] = mapped(region["box_xyxy_roi"])
    if "union_box_xyxy_roi" in candidate:
        box = candidate["union_box_xyxy_roi"]
        candidate["union_box_xyxy_image"] = mapped(box) if box is not None else None
    return candidate


def scale_stats(mask, bounds):
    props = region_properties(mask)
    pixels = int(np.count_nonzero(mask))
    candidate = {
        "candidate_pixels": pixels, "candidate_fraction": rounded(np.mean(mask > 0)),
        "region_count": len(props), "largest_region_area_px": props[0]["area_px"] if props else 0,
        "largest_region_share": rounded(props[0]["area_px"]/pixels) if pixels else 0.0,
        "fragmentation_per_10k_px": rounded(len(props)*10000/mask.size),
        "overall_direction": direction_features(mask), "largest_region": props[0] if props else None,
        "regions": props,
    }
    return map_regions(candidate, bounds)


def safe_orientation(texture):
    reliable = texture["strong_edge_pixel_count"] >= 20
    return {
        "reliable": reliable, "minimum_strong_pixels": 20,
        "entropy": texture["gradient_orientation_entropy"] if reliable else None,
        "coherence": texture["gradient_orientation_coherence"] if reliable else None,
        "scope": "ROI-local 3x3 Sobel; ROI outer one-pixel border excluded; Euclidean weights; 12 bins",
    }


def process_frame(gray, predictions, *, sobel_frame=None, execution="arm_cpu",
                  local_std_frames=None):
    """Process one frame with optional externally computed (sigma2, sigma4) maps."""
    start = time.perf_counter()
    stage_start = start
    stage_ms = {}
    if gray.shape != (512, 512) or gray.dtype != np.uint8:
        raise ValueError("Expected original 512x512 uint8 grayscale image")
    predictions = copy.deepcopy(list(predictions))
    seen = set()
    for prediction in predictions:
        cls, index = prediction["class_id"], prediction["detection_index"]
        if isinstance(cls, bool) or not isinstance(cls, (int, np.integer)) or cls not in BRANCHES:
            raise ValueError("class_id must be integer 0..5")
        if isinstance(index, bool) or not isinstance(index, (int, np.integer)) or index < 0 or index in seen:
            raise ValueError("detection_index must be unique nonnegative integer within a frame")
        seen.add(index)
        if not math.isfinite(float(prediction["confidence"])) or not 0 <= prediction["confidence"] <= 1:
            raise ValueError("confidence must be finite and within [0,1]")
        roi_bounds(prediction["box_xyxy_pixel"])
    stage_ms["input_validation_ms"] = (time.perf_counter()-stage_start)*1000
    stage_start = time.perf_counter()
    if sobel_frame is None:
        sobel = sobel_magnitude(gray)
        sobel_source = "arm_cpu"
    else:
        if sobel_frame.dtype != np.uint8 or sobel_frame.shape != gray.shape:
            raise ValueError("Sobel frame must be 512x512 uint8")
        sobel = sobel_frame
        sobel_source = execution
    stage_ms["sobel_prepare_ms"] = (time.perf_counter()-stage_start)*1000
    classes = {prediction["class_id"] for prediction in predictions}
    stage_start = time.perf_counter()
    std2 = local_std(gray, 2) if predictions and local_std_frames is None else None
    stage_ms["local_std_sigma2_ms"] = (time.perf_counter()-stage_start)*1000
    stage_start = time.perf_counter()
    std4 = local_std(gray, 4) if predictions and local_std_frames is None else None
    if predictions and local_std_frames is not None:
        if len(local_std_frames) != 2:
            raise ValueError("local_std_frames must contain (sigma2_map, sigma4_map)")
        std2, std4 = (np.asarray(local_std_frames[0]), np.asarray(local_std_frames[1]))
        for name, values in (("sigma2", std2), ("sigma4", std4)):
            if values.shape != gray.shape or not np.issubdtype(values.dtype, np.number):
                raise ValueError("local_std_%s map must be numeric and 512x512" % name)
            if not np.isfinite(values).all() or np.any(values < 0):
                raise ValueError("local_std_%s map must contain finite nonnegative values" % name)
    stage_ms["local_std_sigma4_ms"] = (time.perf_counter()-stage_start)*1000
    stage_start = time.perf_counter()
    inclusion_dark = inclusion_dark_response(gray) if 1 in classes else None
    stage_ms["inclusion_dark_response_ms"] = (time.perf_counter()-stage_start)*1000
    stage_start = time.perf_counter()
    broad_dark = broad_dark_response(gray) if classes.intersection({2, 4}) else None
    stage_ms["broad_dark_response_ms"] = (time.perf_counter()-stage_start)*1000
    full_map_ms = (time.perf_counter()-start)*1000
    records, artifacts = [], []
    roi_timing_ms = {str(class_id): [] for class_id in sorted(classes)}
    for prediction in predictions:
        roi_start = time.perf_counter()
        cls = prediction["class_id"]
        bounds = roi_bounds(prediction["box_xyxy_pixel"])
        x1, y1, x2, y2 = bounds
        roi = gray[y1:y2, x1:x2]
        texture = descriptors(roi, std2[y1:y2, x1:x2], std4[y1:y2, x1:x2], sobel[y1:y2, x1:x2])
        candidate = None
        details, masks, reasons = {}, {}, []
        if cls == 0:
            state = "statistics_only"
            if texture["strong_edge_pixel_count"] < 20:
                reasons.append("orientation_unreliable")
        elif cls == 1:
            mask = hysteresis(inclusion_dark[y1:y2, x1:x2], 5, 8, 8)
            candidate = inclusion_stats(mask)
            if texture["local_std_sigma2_mean"] >= 5 or candidate["candidate_fraction"] >= .25 or candidate["region_count"] >= 30:
                state, reasons = "uncertain_texture", ["texture_or_dense_candidate"]
            elif candidate["candidate_fraction"] < .005:
                state, reasons = "uncertain_weak", ["weak_candidate"]
            else:
                state = "usable_candidate"
            masks = {"candidate": mask}
        elif cls == 2:
            raw = hysteresis(broad_dark[y1:y2, x1:x2], 12, 20, 20)
            dark, otsu = otsu_dark(roi)
            extent, supported_count = supported_union(dark, raw)
            raw_values, dark_values, candidate = mask_stats(raw), mask_stats(dark), mask_stats(extent)
            reasons = patch_reasons(raw_values, dark_values, candidate, otsu["gray_group_contrast"], supported_count)
            candidate.update({"supported_otsu_component_count": supported_count,
                              "added_pixels": int(np.count_nonzero((extent > 0) & (raw == 0))),
                              "review_reasons": reasons})
            details = {"raw_candidate": raw_values, "otsu_dark_group": dark_values, "otsu": otsu}
            state = "needs_review" if reasons else "no_automatic_warning"
            masks = {"raw_candidate": raw, "coarse_extent": extent}
        elif cls == 3:
            reasons = pitted_reasons(texture)
            state = "needs_review" if reasons else "texture_statistics"
        elif cls == 4:
            mask = hysteresis(broad_dark[y1:y2, x1:x2], 12, 20, 20)
            candidate = scale_stats(mask, bounds)
            state = "uncertain_fragmented" if candidate["region_count"] > 30 else "shape_candidate"
            if state == "uncertain_fragmented":
                reasons = ["region_count_gt_30"]
            masks = {"candidate": mask}
        else:
            mask, candidate = candidate_summary(sobel[y1:y2, x1:x2], 64, 20)
            map_regions(candidate, bounds)
            state = "edge_candidate"
            masks = {"candidate": mask}
        notices = []
        if candidate is not None and candidate["candidate_pixels"] == 0:
            notices.append("empty_candidate_preserve_detection")
        mode, method = BRANCHES[cls]
        processing_time_ms = (time.perf_counter()-roi_start)*1000
        roi_timing_ms.setdefault(str(cls), []).append(processing_time_ms)
        records.append({
            "schema_version": SCHEMA, "detection_index": prediction["detection_index"],
            "class_id": cls, "class_name": CLASS_NAMES[cls], "confidence": prediction["confidence"],
            "box_xyxy_pixel": prediction["box_xyxy_pixel"], "roi_xyxy_pixel": bounds,
            "coordinate_space": "original_image_pixels_half_open", "roi_width": x2-x1, "roi_height": y2-y1,
            "mode": mode, "method": method, "branch_state": state, "review_reasons": reasons,
            "notices": notices, "state_uses_label": False, "common_gray": scalar_stats(roi),
            "texture_statistics": texture, "interpretable_orientation": safe_orientation(texture),
            "candidate": candidate, "branch_details": details,
            "provenance": {"execution": execution, "texture_maps": "cpu_full_frame_then_crop",
                           "candidate_sobel": sobel_source if cls == 5 else "unused", "new_board_access": execution == "fpga_v2"},
            "processing_time_ms": rounded(processing_time_ms),
        })
        artifacts.append(masks)
    roi_timing_summary = {}
    for class_id, values in roi_timing_ms.items():
        roi_timing_summary[class_id] = {
            "count": len(values),
            "total_ms": rounded(sum(values)),
            "mean_ms": rounded(np.mean(values)) if values else 0.0,
            "max_ms": rounded(max(values)) if values else 0.0,
        }
    return records, artifacts, {
        "full_frame_compute_ms": rounded(full_map_ms),
        "total_compute_ms": rounded((time.perf_counter()-start)*1000),
        "full_frame_stages_ms": {key: rounded(value) for key, value in stage_ms.items()},
        "roi_compute_by_class": roi_timing_summary,
        "scope": "Portable ARM-oriented ROI runtime; excludes input loading, result saving and transport.",
    }
