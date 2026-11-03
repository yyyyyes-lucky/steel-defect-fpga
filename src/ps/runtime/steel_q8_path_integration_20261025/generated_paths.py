"""Generated from locked source; only decode and map validation differ."""

def feature_process(self, gray, timeout_seconds=10.0):
    if self.closed or self.failed:
        raise RuntimeError("Driver closed or failed; recover before another frame")
    gray = np.asarray(gray)
    if gray.shape != (512, 512) or gray.dtype != np.uint8:
        raise ValueError("Input must be 512x512 uint8")
    if not np.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive and finite")
    begin = time.perf_counter()
    self.sobel_in[:] = gray
    self.q8_in[:] = np.pad(gray, ((6, 6), (6, 6)), mode="reflect")
    self.sobel_in.flush()
    self.q8_in.flush()
    start = time.perf_counter()
    try:
        self.in_flight = True
        self.dmas["sobel"].recvchannel.transfer(self.sobel_out)
        self.dmas["q8"].recvchannel.transfer(self.q8_out)
        self.dmas["sobel"].sendchannel.transfer(self.sobel_in)
        self.dmas["q8"].sendchannel.transfer(self.q8_in)
        deadline = time.monotonic() + timeout_seconds
        finished = {}
        while len(finished) != 2:
            status = {}
            for name, dma in self.dmas.items():
                send = dma.mmio.read(0x04)
                recv = dma.mmio.read(0x34)
                status[name] = [send, recv]
                if (send | recv) & 0x70:
                    raise RuntimeError("%s DMA error: MM2S=0x%08x S2MM=0x%08x" % (name, send, recv))
                if (send & 2) and (recv & 2) and name not in finished:
                    finished[name] = (time.perf_counter() - start) * 1000
            if len(finished) == 2:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Dual DMA timeout; status=%s" % status)
            time.sleep(0.0001)
        for dma in self.dmas.values():
            dma.sendchannel.wait()
            dma.recvchannel.wait()
        self.in_flight = False
        for name, expected in [("sobel", 512 * 512), ("q8", 512 * 512 * 4)]:
            actual = self.dmas[name].mmio.read(0x58)
            if actual != expected:
                raise RuntimeError("%s receive length %d, expected %d" % (name, actual, expected))
        self.sobel_out.invalidate()
        self.q8_out.invalidate()
        sobel = np.asarray(self.sobel_out).copy()
        std7, std13 = _q8_path_decode(self.q8_out)
        end = time.perf_counter()
        return sobel, std7, std13, {
            "prepare_and_flush_ms": (start - begin) * 1000,
            "dma_completion_observed_ms": finished,
            "prepare_dma_and_copy_ms": (end - begin) * 1000,
        }
    except Exception:
        self.failed = True
        raise


def roi_process(gray, predictions, *, sobel_frame=None, execution="arm_cpu",
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
        std2, std4 = _q8_path_validate(local_std_frames, gray.shape)
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
