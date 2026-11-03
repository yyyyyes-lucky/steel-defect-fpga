"""Two-pass Q8 rank model and PS-only screening; no new accelerator is used."""
import math
import statistics
import time
import numpy as np


def radix_p90(values):
    values = np.asarray(values)
    if values.dtype != np.uint16 or values.ndim != 2 or not values.size or values.size > 262144:
        raise ValueError('Expected nonempty uint16 ROI up to 262144 pixels')
    flat = values.ravel()
    position = (flat.size - 1) * .9
    ranks = [math.floor(position), math.ceil(position)]
    coarse = np.bincount(flat >> 8, minlength=256)
    cumulative = np.cumsum(coarse)
    buckets = [int(np.searchsorted(cumulative, rank, side='right')) for rank in ranks]
    fine = {b: np.bincount((flat[(flat >> 8) == b] & 255), minlength=256) for b in set(buckets)}
    selected = []
    for rank, bucket in zip(ranks, buckets):
        before = int(cumulative[bucket - 1]) if bucket else 0
        low = int(np.searchsorted(np.cumsum(fine[bucket]), rank - before, side='right'))
        selected.append((bucket * 256 + low) / 256.0)
    a, b = selected
    weight = position - ranks[0]
    # NumPy 1.21 linear interpolation, with its upper-half arithmetic branch.
    return b - (b - a) * (1 - weight) if weight >= .5 else a + (b - a) * weight


def profile_result(result, predictions, roi_bounds, repeats=3):
    """Use already returned Q8 maps; never run another FPGA frame or patch runtime."""
    rows = []
    for index, prediction in enumerate(predictions):
        x1, y1, x2, y2 = roi_bounds(prediction['box_xyxy_pixel'])
        planes = [result['features'][key][y1:y2, x1:x2] for key in ('std7_q8', 'std13_q8')]
        floats = [p.astype(np.float32) / 256 for p in planes]
        expected = [round(float(np.percentile(p, 90)), 6) for p in floats]
        actual = [round(radix_p90(p), 6) for p in planes]
        if actual != expected:
            raise AssertionError('Q8 p90 differs from installed NumPy: ROI ' + str(index))
        ps_times, pack_times = [], []
        # Warm each PS operation once, then alternate order; no hardware calls.
        def percentile():
            return [np.percentile(p, 90) for p in floats]
        def pack():
            return planes[0].astype(np.uint32) | (planes[1].astype(np.uint32) << 16)
        percentile(); pack()
        for repeat in range(repeats):
            operations = [(percentile, ps_times), (pack, pack_times)]
            if repeat % 2:
                operations.reverse()
            for operation, output in operations:
                begin = time.perf_counter()
                operation()
                output.append((time.perf_counter() - begin) * 1000)
        n = int(planes[0].size)
        ps_ms, pack_ms = statistics.median(ps_times), statistics.median(pack_times)
        rows.append({'roi_index': index, 'class_id': prediction['class_id'], 'pixels': n,
                     'p90_reference': expected, 'p90_model': actual, 'exact': True,
                     'ps_p90_pair_ms': ps_ms, 'pack_pair_ms': pack_ms,
                     'two_pass_payload_bytes': 8 * n,
                     # II=1 is an optimistic design assumption, NOT synthesis evidence.
                     'optimistic_scan_ms_100mhz_ii1': 2 * n / 100000.0,
                     'optimistic_remaining_dma_control_budget_ms': ps_ms - pack_ms - 2 * n / 100000.0})
    return rows


def summarize_screen(frames):
    rows = [row for frame in frames for row in frame['rois']]
    ps = sum(row['ps_p90_pair_ms'] for row in rows)
    packing = sum(row['pack_pair_ms'] for row in rows)
    scan = sum(row['optimistic_scan_ms_100mhz_ii1'] for row in rows)
    pipeline = sum(frame['pipeline_ms'] for frame in frames)
    return {'frames': len(frames), 'rois': len(rows), 'ps_p90_pair_ms': ps,
            'packing_ms': packing, 'optimistic_scan_ms': scan,
            'optimistic_remaining_dma_control_budget_ms': ps - packing - scan,
            'optimistic_elimination_share_percent': 100 * ps / pipeline if pipeline else 0,
            'scope': 'Representative PS diagnostics outside pipeline timing; no measured FPGA candidate speedup.',
            'decision': 'Reject standalone offload if budget <=0; positive budget only permits design review, not promotion.',
            'new_hardware_built': False, 'new_hardware_tested': False}
