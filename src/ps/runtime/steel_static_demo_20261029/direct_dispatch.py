"""Generated direct dispatch. Classes 0/3/5 retain frozen branch code.
Source SHA256: 325268431b193fe817acc4d41cb2767c13f909b56ab2acfd601a1b522e5d463c
"""
def process_frame(gray, predictions, *, sobel_frame=None, execution="arm_cpu",
                  local_std_frames=None, broad_provider=None):
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
        std2, std4 = _quality_validate_maps(local_std_frames, gray.shape)
    stage_ms["local_std_sigma4_ms"] = (time.perf_counter()-stage_start)*1000
    stage_start = time.perf_counter()
    prepared = _quality_prepare(gray, classes, broad_provider=broad_provider)
    stage_ms["selected_quality_maps_ms"] = (time.perf_counter()-stage_start)*1000
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
        elif cls in (1, 2, 4):
            candidate, details, masks, reasons, quality_review, selected_method = _quality_branch(
                cls, prepared, bounds, texture, _quality_helpers)
            state = "needs_review" if reasons else "no_automatic_warning"
        elif cls == 3:
            reasons = pitted_reasons(texture)
            state = "needs_review" if reasons else "texture_statistics"
        else:
            mask, candidate = candidate_summary(sobel[y1:y2, x1:x2], 64, 20)
            map_regions(candidate, bounds)
            state = "edge_candidate"
            masks = {"candidate": mask}
        notices = []
        if candidate is not None and candidate["candidate_pixels"] == 0:
            notices.append("empty_candidate_preserve_detection")
        mode, method = BRANCHES[cls]
        processing_time_ms = 0.0  # Set after all record/statistics construction.
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
        records[-1]["provenance"]["texture_maps"] = (
            "fpga_q8_full_frame_then_crop" if broad_provider is not None else "cpu_q8_full_frame_then_crop")
        records[-1]["provenance"]["new_board_access"] = broad_provider is not None
        if cls in (1, 2, 4):
            records[-1]["method"] = selected_method
            records[-1]["quality_review"] = quality_review
            records[-1]["provenance"].update({
                "mask_quality_revision": _quality_revision,
                "mask_execution": "direct_selected_algorithm",
                "broad_response": "fpga_q30" if broad_provider is not None else "cpu_float32",
            })
        processing_time_ms = (time.perf_counter()-roi_start)*1000
        records[-1]["processing_time_ms"] = rounded(processing_time_ms)
        roi_timing_ms.setdefault(str(cls), []).append(processing_time_ms)
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
        "scope": "Direct selected ROI algorithm; shared maps once; excludes input loading, saving and transport.",
    }
