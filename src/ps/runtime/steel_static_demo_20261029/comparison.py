"""Output differences and paired summaries, independent of board hardware."""
import math
import statistics

import numpy as np


def stats(values):
    values = [float(v) for v in values]
    if not values or any(not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError('Timings must be finite and positive')
    return {'count': len(values), 'sum': sum(values), 'mean': statistics.mean(values),
            'median': statistics.median(values),
            'p95': sorted(values)[math.ceil(.95 * len(values)) - 1],
            'min': min(values), 'max': max(values)}


def differences(a, b, path=''):
    """Return every changed leaf; never remove arithmetic-sensitive fields."""
    if isinstance(a, dict) and isinstance(b, dict):
        result = []
        for key in sorted(set(a) | set(b)):
            next_path = (path + '.' if path else '') + str(key)
            if key not in a or key not in b:
                result.append({'field': next_path, 'cpu': a.get(key), 'fpga': b.get(key)})
            else:
                result.extend(differences(a[key], b[key], next_path))
        return result
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return [{'field': path, 'cpu': a, 'fpga': b}]
        return [d for i, (x, y) in enumerate(zip(a, b))
                for d in differences(x, y, path + '[%d]' % i)]
    return [] if a == b else [{'field': path, 'cpu': a, 'fpga': b}]


def audit(cpu, fpga):
    if len(cpu['records']) != len(fpga['records']):
        raise AssertionError('Output count differs')
    if any(len(value['records']) != len(value['masks']) for value in (cpu, fpga)):
        raise AssertionError('Record/mask count differs')
    rows = []
    for a, b, am, bm in zip(cpu['records'], fpga['records'], cpu['masks'], fpga['masks']):
        if (a['class_id'], a['detection_index']) != (b['class_id'], b['detection_index']):
            raise AssertionError('Output identities differ')
        stripped = lambda r: {k: v for k, v in r.items() if k not in ('provenance', 'processing_time_ms')}
        changed = differences(stripped(a), stripped(b))
        masks = {}
        for key in sorted(set(am) | set(bm)):
            if key not in am or key not in bm or am[key].shape != bm[key].shape or am[key].dtype != bm[key].dtype:
                raise AssertionError('Mask contract differs')
            masks[key] = int(np.count_nonzero(am[key] != bm[key]))
        rows.append({'detection_index': a['detection_index'], 'class_id': a['class_id'],
                     'exact': not changed and not any(masks.values()),
                     'branch_state_equal': a['branch_state'] == b['branch_state'],
                     'review_reasons_equal': a['review_reasons'] == b['review_reasons'],
                     'changed_fields': changed, 'mask_different_pixels': masks})
    features = {}
    for key, a in cpu['features'].items():
        if a is not None:
            b = fpga['features'][key]
            if a.shape != b.shape or a.dtype != b.dtype:
                raise AssertionError('Feature contract differs')
            features[key] = int(np.count_nonzero(a != b))
    if any(features.values()):
        raise AssertionError('CPU native Q8/Sobel differs from qualified FPGA: ' + str(features))
    return {'rois': rows, 'feature_different_pixels': features,
            'exact': all(row['exact'] for row in rows),
            'branch_states_equal': all(row['branch_state_equal'] for row in rows),
            'review_reasons_equal': all(row['review_reasons_equal'] for row in rows)}


def summarize(rows, expected_cases, rounds):
    lookup = {(r['round'], r['case'], r['variant']): r for r in rows}
    if len(rows) != len(expected_cases) * rounds * 2 or len(lookup) != len(rows):
        raise AssertionError('Missing or duplicated measurements')
    for repeat in range(rounds):
        for case in expected_cases:
            for variant in ('cpu', 'fpga'):
                if (repeat, case, variant) not in lookup:
                    raise AssertionError('Missing case/round/variant')
    timing = {v: stats(r['pipeline_ms'] for r in rows if r['variant'] == v) for v in ('cpu', 'fpga')}
    per_round = []
    for repeat in range(rounds):
        totals = {v: sum(lookup[repeat, c, v]['pipeline_ms'] for c in expected_cases) for v in timing}
        per_round.append(dict(totals, speedup=totals['cpu'] / totals['fpga']))
    per_case = []
    for case in expected_cases:
        totals = {v: sum(lookup[n, case, v]['pipeline_ms'] for n in range(rounds)) / rounds for v in timing}
        per_case.append(dict(totals, case=case, speedup=totals['cpu'] / totals['fpga']))
    per_class = {}
    for c in range(6):
        totals = {v: sum(r['roi_compute_by_class'].get(str(c), {}).get('total_ms', 0)
                         for r in rows if r['variant'] == v) / rounds for v in timing}
        per_class[str(c)] = dict(totals, speedup=totals['cpu'] / totals['fpga'] if totals['fpga'] else None)
    ratio = timing['cpu']['sum'] / timing['fpga']['sum']
    return {'timings_ms': timing, 'speedup': ratio, 'time_reduction_pct': (1 - 1 / ratio) * 100,
            'per_round': per_round, 'per_case': per_case,
            'fpga_faster_cases': sum(r['speedup'] > 1 for r in per_case),
            'per_class_roi_only_ms': per_class,
            'per_class_scope': 'ROI CPU work only, excludes shared full-frame features; not class pipeline speedup',
            'stability_scope': 'Two balanced rounds are limited repeat evidence, not a long-duration stability claim.'}
