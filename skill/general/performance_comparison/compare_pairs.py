"""Read-only paired timing summary. No board, model, network, or startup gate."""
import argparse
import json
import math
from pathlib import Path
import statistics
import sys


def summarize(document, baseline='cpu', candidate='fpga', rows_key='measurements',
              variant_field='variant', time_field='pipeline_ms', pair_fields=('round', 'case'),
              phase_field=None, phase_value='measured'):
    if baseline == candidate:
        raise ValueError('Baseline and candidate must be different.')
    if not pair_fields or len(set(pair_fields)) != len(pair_fields):
        raise ValueError('Pair fields must be unique and nonempty.')
    if isinstance(document, dict):
        for key in ('completed', 'pass', 'hardware_session_safe'):
            if key in document and document[key] is not True:
                raise ValueError('Report does not declare successful completion: ' + key)
        if document.get('errors'):
            raise ValueError('Report contains errors; do not silently summarize a partial run.')
        rows = document.get(rows_key)
    else:
        rows = document
    if not isinstance(rows, list) or not rows:
        raise ValueError('Expected a nonempty list of measurement records.')
    selected = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError('Record %d is not an object.' % index)
        if phase_field:
            if phase_field not in row:
                raise ValueError('Missing phase field in record %d.' % index)
            if row[phase_field] != phase_value:
                continue
        selected.append(row)
    if not selected:
        raise ValueError('No records selected. Check the phase filter.')
    paired = {baseline: {}, candidate: {}}
    for index, row in enumerate(selected):
        try:
            variant, value = row[variant_field], row[time_field]
            key = tuple(row[field] for field in pair_fields)
        except KeyError as exc:
            raise ValueError('Missing field in record %d: %s' % (index, exc)) from exc
        if variant not in (baseline, candidate):
            raise ValueError('Unexpected variant %r; supply exactly two implementations.' % variant)
        if any(isinstance(v, bool) or not isinstance(v, (str, int)) for v in key):
            raise ValueError('Pair identities must be strings or integers, not bool/containers.')
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('Timing must be a number, not a bool or string.')
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError('Timing must be finite and strictly positive.')
        if key in paired[variant]:
            raise ValueError('Duplicate pair key for %s: %r' % (variant, key))
        paired[variant][key] = value
    if paired[baseline].keys() != paired[candidate].keys() or not paired[baseline]:
        raise ValueError('Missing counterpart or unequal input sets; no speedup reported.')

    def stats(values):
        ordered = sorted(values)
        total = math.fsum(ordered)
        if not math.isfinite(total):
            raise ValueError('Timing total overflow.')
        return {'samples': len(ordered), 'sum': total, 'mean': total/len(ordered),
                'median': statistics.median(ordered),
                'p95_nearest_rank': ordered[math.ceil(len(ordered)*0.95)-1],
                'min': ordered[0], 'max': ordered[-1]}

    totals = {variant: stats(values.values()) for variant, values in paired.items()}
    old, new = totals[baseline]['sum'], totals[candidate]['sum']
    result = {'schema': 'paired_timing_summary/1.0', 'baseline': baseline, 'candidate': candidate,
              'pair_fields': list(pair_fields), 'timing_field': time_field,
              'pairs': len(paired[baseline]), 'statistics': totals,
              'speedup_ratio_of_totals': old/new, 'time_reduction_pct': 100*(old-new)/old,
              'candidate_faster_pairs': sum(paired[candidate][k] < v for k, v in paired[baseline].items()),
              'p95_method': 'nearest rank; may differ from interpolated p95 in source report',
              'scope': 'Only these paired measurements; no confidence interval, accuracy proof or hardware execution claim.'}
    if isinstance(document, dict):
        result['source_timing_scope'] = document.get('timing_scope', 'unspecified')
        result['source_synthetic'] = document.get('synthetic', False)
    if 'round' in pair_fields:
        position = pair_fields.index('round')
        groups = {}
        for key, old_time in paired[baseline].items():
            round_id = key[position]
            group = groups.setdefault(round_id, {'round': round_id, 'pairs': 0, 'baseline_sum': 0.0, 'candidate_sum': 0.0})
            group['pairs'] += 1
            group['baseline_sum'] += old_time
            group['candidate_sum'] += paired[candidate][key]
        result['per_round'] = []
        for group in groups.values():
            group['speedup'] = group['baseline_sum']/group['candidate_sum']
            result['per_round'].append(group)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--baseline', default='cpu')
    parser.add_argument('--candidate', default='fpga')
    parser.add_argument('--rows-key', default='measurements')
    parser.add_argument('--variant-field', default='variant')
    parser.add_argument('--time-field', default='pipeline_ms')
    parser.add_argument('--pair-fields', default='round,case')
    parser.add_argument('--phase-field')
    parser.add_argument('--phase-value', default='measured')
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.input.read_text(encoding='utf-8-sig'))
        result = summarize(document, args.baseline, args.candidate, args.rows_key,
                           args.variant_field, args.time_field,
                           tuple(field.strip() for field in args.pair_fields.split(',') if field.strip()),
                           args.phase_field, args.phase_value)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, OverflowError) as exc:
        print('Cannot summarize: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
