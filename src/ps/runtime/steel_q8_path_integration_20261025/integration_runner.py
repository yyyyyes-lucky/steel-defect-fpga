"""One existing-v10 session, two warmups and a nine-case ABBA comparison."""
from datetime import datetime
import hashlib
import importlib
import json
import math
from pathlib import Path
import statistics
import sys
import uuid

import numpy as np
from integration_context import components, configuration, restored

ROOT = Path(__file__).resolve().parent
CASE_NAMES = ['class_' + str(i) for i in range(6)] + ['boundary_scale259', 'frame_143', 'empty_predictions']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def initialize_runtime():
    manifest = json.loads((ROOT / 'manifest.json').read_bytes())
    locks = json.loads((ROOT / 'dependency_lock.json').read_bytes())
    for name in ('integration_context', 'integration_kernels', 'generated_paths', 'integration_runner'):
        module = sys.modules.get(name)
        if module is not None and Path(module.__file__).resolve().parent != ROOT:
            raise RuntimeError('Conflicting module; restart this notebook kernel: ' + name)
    daily_root = ROOT.parent / 'steel_runtime_v10_candidate_20261025'
    old = sys.modules.get('runtime_v10')
    if old is not None and Path(old.__file__).resolve().parent != daily_root:
        raise RuntimeError('Conflicting runtime_v10; restart this notebook kernel')
    sys.path.insert(0, str(daily_root))
    daily = importlib.import_module('runtime_v10')
    state = daily.initialize_runtime()
    # These are existing session guards; never clear or bypass them.
    daily.guard()
    state['entry']._guard(ROOT.parent)
    return daily, state, {'candidate_manifest_sha256': sha(ROOT / 'manifest.json'),
                          'candidate_files': manifest['files'], 'dependency_lock': locks,
                          'v10_integrity': state['integrity']}


def check_ownership(session, result):
    _, driver, _ = components(session)
    maps = result['features']
    expected = {'sobel': np.dtype('uint8'), 'std7_q8': np.dtype('uint16'), 'std13_q8': np.dtype('uint16')}
    if set(maps) != set(expected):
        raise AssertionError('Unexpected feature keys')
    for name, dtype in expected.items():
        value = maps[name]
        if value.shape != (512, 512) or value.dtype != dtype or not value.flags.c_contiguous or not value.flags.owndata:
            raise AssertionError('Feature ownership/layout changed: ' + name)
        if any(np.shares_memory(value, buffer) for buffer in driver.buffers):
            raise AssertionError('Returned feature aliases a DMA buffer: ' + name)
    if np.shares_memory(maps['std7_q8'], maps['std13_q8']):
        raise AssertionError('Returned Q8 planes alias each other')


def stage_values(timing):
    values = {'pipeline_ms': timing['end_to_end_static_pipeline_ms'],
              'feature_prepare_dma_copy_ms': timing['feature_timing']['prepare_dma_and_copy_ms'],
              'map_validation_ms': timing['roi_runtime_timing']['full_frame_stages_ms']['local_std_sigma4_ms']}
    if any(not math.isfinite(v) or v < 0 for v in values.values()) or values['pipeline_ms'] <= 0:
        raise AssertionError('Invalid measured stage timing')
    return values


def summarize(rows):
    measured = [r for r in rows if r['phase'] == 'measured']
    if len(measured) != 36:
        raise AssertionError('Expected exactly 36 measured frames')
    expected = [(block, variant, name) for block, variant in enumerate(('baseline', 'candidate', 'candidate', 'baseline')) for name in CASE_NAMES]
    if [(r['block'], r['variant'], r['case']) for r in measured] != expected:
        raise AssertionError('Measured ABBA cases/order differ')
    by_key = {(r['block'], r['case']): r for r in measured}
    totals = [sum(by_key[b, n]['stages']['pipeline_ms'] for n in CASE_NAMES) for b in range(4)]
    old, new = statistics.median([totals[0], totals[3]]), statistics.median([totals[1], totals[2]])
    per_case = []
    for name in CASE_NAMES:
        stages = {}
        for key in ('pipeline_ms', 'feature_prepare_dma_copy_ms', 'map_validation_ms'):
            a = statistics.median([by_key[b, name]['stages'][key] for b in (0, 3)])
            b = statistics.median([by_key[b, name]['stages'][key] for b in (1, 2)])
            stages[key] = {'baseline_ms': a, 'candidate_ms': b, 'saved_ms': a-b,
                           'reduction_pct': (1-b/a)*100 if a else None}
        pairs = [{'order': order, 'baseline_ms': by_key[ob, name]['stages']['pipeline_ms'],
                  'candidate_ms': by_key[nb, name]['stages']['pipeline_ms']}
                 for ob, nb, order in ((0, 1, 'baseline_first'), (3, 2, 'candidate_first'))]
        per_case.append({'case': name, 'stages': stages, 'pairs': pairs})
    return {'sample_frames': 9, 'measured_frames': 36, 'timing_blocks_ms': totals,
            'baseline_nine_case_total_median_ms': old, 'candidate_nine_case_total_median_ms': new,
            'nine_case_total_reduction_pct': (1-new/old)*100,
            'total_reduction_by_order_pct': [(1-totals[1]/totals[0])*100, (1-totals[2]/totals[3])*100],
            'candidate_faster_pairs': sum(p['candidate_ms'] < p['baseline_ms'] for r in per_case for p in r['pairs']),
            'pair_count': 18, 'per_case': per_case,
            'scope': 'Nine-case static integration sample only; not full184, camera, or detector throughput.'}


def exercise(session, report, save_report):
    args = session.state['args']
    cases = args.load_cases('smoke')
    if [name for name, _, _ in cases] != CASE_NAMES:
        raise AssertionError('Unexpected integration fixtures/order')
    refs = session.state['prior'].references()
    if any(name not in refs for name in CASE_NAMES):
        raise AssertionError('Missing qualified signature')
    report['reference_signature_sha256'] = {name: digest_json(refs[name]) for name in CASE_NAMES}
    report['input_array_sha256'] = {name: hashlib.sha256(gray.tobytes()).hexdigest() for name, gray, _ in cases}
    previous = None
    previous_reference = None
    def one(case, variant, phase, block):
        nonlocal previous, previous_reference
        name, gray, predictions = case
        with configuration(session, variant):
            result = session.process_frame(gray, predictions, case=name, reference=refs[name], check_features=False)
        if not restored(session):
            raise AssertionError('Temporary configuration did not restore')
        # process_frame already checks the qualified signature, including every
        # raw feature and mask hash. No old Q8/Sobel oracle is rerun here.
        check_ownership(session, result)
        lifetime_checked = previous is not None
        if lifetime_checked and args.signature(previous) != previous_reference:
            raise AssertionError('A subsequent frame overwrote retained outputs')
        report['frames'].append({'phase': phase, 'block': block, 'variant': variant, 'case': name,
                                 'roi_count': len(predictions), 'qualified_signature_exact': True,
                                 'raw_features_and_masks_exact': True, 'output_ownership_pass': True,
                                 'previous_frame_preserved': True if lifetime_checked else None,
                                 'configuration_restored': True, 'stages': stage_values(result['timing']),
                                 'timing': result['timing']})
        session.report['frames'][-1].update(q8_path_variant=variant, integration_phase=phase, integration_block=block)
        session.save()
        previous, previous_reference = result, refs[name]
        save_report()
        print('Q8_PATH %d/38 %s %s %s EXACT=True' % (len(report['frames']), phase, variant, name), flush=True)
    warm = next(case for case in cases if case[0] == 'class_2')
    for variant in ('baseline', 'candidate'):
        one(warm, variant, 'warmup', -1)
    for block, variant in enumerate(('baseline', 'candidate', 'candidate', 'baseline')):
        for case in cases:
            one(case, variant, 'measured', block)
    report['summary'] = summarize(report['frames'])
    report['retained_output_checks_before_close'] = sum(r['previous_frame_preserved'] is True for r in report['frames'])
    return previous, previous_reference


def run():
    daily, _, integrity = initialize_runtime()
    out = ROOT / 'results' / ('run_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
    out.mkdir(parents=True, exist_ok=False)
    path = out / 'q8_path_integration_check.json'
    report = {'schema': 'q8_path_integration/1.0', 'completed': False, 'pass': False,
              'hardware_session_safe': False, 'formal_release_replaced': False, 'new_bitstream_built': False,
              'integrity': integrity, 'overlay_loads': 0, 'frames': [], 'errors': [],
              'timing_scope': 'Existing static pipeline timer; excludes overlay load, oracle, disk, detector, network and checks.',
              'pass_means': 'Functional integrity and safe close; timing benefit must be reviewed separately.'}
    save_report = lambda: save(path, report)
    save_report()
    session = None
    try:
        with daily.Runtime(purpose='q8_path_integration') as session:
            report.update(overlay_loads=1, boot_id=session.report['boot_id'], session_report_file=str(session.path))
            # Verify the session programmed the same locked v10 hardware.
            if session.state['integrity'] != integrity['v10_integrity']:
                raise AssertionError('Session integrity differs from initialize_runtime')
            last, reference = exercise(session, report, save_report)
        # All arrays must survive freeing the receive buffers as well.
        if session.state['args'].signature(last) != reference:
            raise AssertionError('Closing DMA buffers changed returned outputs')
        report.update(completed=True, **{'pass': True}, hardware_session_safe=session.report['hardware_session_safe'],
                      retained_output_after_close_pass=True, checked_frames=len(report['frames']))
        if not report['hardware_session_safe']:
            raise AssertionError('Session cleanup unsafe')
    except BaseException as error:
        report.update(completed=False, **{'pass': False})
        report['errors'].append(type(error).__name__ + ': ' + str(error))
        raise
    finally:
        if session is not None:
            report['session'] = session.report
        save_report()
        print('REPORT_FILE=' + str(path), flush=True)
    print('Q8_PATH_INTEGRATION_PASS=True', flush=True)
    print('HARDWARE_SESSION_SAFE=True', flush=True)
    print('NINE_CASE_REDUCTION_PCT=' + str(report['summary']['nine_case_total_reduction_pct']), flush=True)
    return report, path
