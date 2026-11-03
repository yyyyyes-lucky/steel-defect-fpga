"""One sequential board entry: old baseline, new kernel, runtime and timing."""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'board'))
from binary_models import check_kernel, dilate7
from q8_reference import q8_reference
from transport_common import cpu_sobel, predictions_for
from experiment_runtime import ExperimentRuntime, base

OLD_BIT = ROOT / 'baseline/board/steel_triple.bit'
NEW_BIT = ROOT / 'hardware/steel_binary_experiment.bit'
OLD_HASHES = {'bit': '54b3cf3f2d13405f23b6559fc4305f3d36c3f898d7691ef4450765cd7947ddb1',
              'hwh': 'ca2209c49e0ab6255b41171cafed569a03cc3834bd77dd459d555d870f4fbc6e'}
_FAILED_HARDWARE_SESSION = False


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inspect_hardware():
    hwh = NEW_BIT.with_suffix('.hwh')
    tree = ET.parse(str(hwh))
    modules = {node.get('INSTANCE'): node for node in tree.iter('MODULE')}
    required = {'axi_dma_0', 'axi_dma_sobel', 'axi_dma_dark',
                'gaussian_row_q30_0', 'axi_dma_ellipse', 'ellipse7_0'}
    if not required.issubset(modules):
        raise RuntimeError('New HWH missing modules: ' + str(required-set(modules)))
    addresses = {node.get('INSTANCE'): int(node.get('BASEVALUE'), 0) for node in tree.iter('MEMRANGE')
                 if node.get('MEMTYPE') == 'REGISTER'}
    expected_addresses = {'axi_dma_0': 0x40400000, 'axi_dma_sobel': 0x40410000,
                          'axi_dma_dark': 0x40420000, 'axi_dma_ellipse': 0x40430000,
                          'gaussian_row_q30_0': 0x43c00000, 'ellipse7_0': 0x43c10000}
    if any(addresses.get(key) != value for key, value in expected_addresses.items()):
        raise RuntimeError('New HWH address map differs from frozen driver contract')
    registers = {node.get('NAME'): int(next(p.get('VALUE') for p in node.findall('PROPERTY')
                 if p.get('NAME') == 'ADDRESS_OFFSET'), 0)
                 for node in modules['ellipse7_0'].findall('./ADDRESSBLOCKS/ADDRESSBLOCK/REGISTERS/REGISTER')}
    if any(registers.get(key) != value for key, value in
           {'CTRL': 0, 'height': 16, 'width': 24, 'status': 32, 'status_ctrl': 36}.items()):
        raise RuntimeError('Ellipse register offsets mismatch')
    parameters = {node.get('NAME').upper(): int(node.get('VALUE'), 0)
                  for node in modules['axi_dma_ellipse'].findall('./PARAMETERS/PARAMETER')
                  if node.get('NAME').upper() in ('C_INCLUDE_SG', 'C_SG_LENGTH_WIDTH',
                     'C_M_AXIS_MM2S_TDATA_WIDTH', 'C_S_AXIS_S2MM_TDATA_WIDTH')}
    if parameters != {'C_INCLUDE_SG': 0, 'C_SG_LENGTH_WIDTH': 26,
                      'C_M_AXIS_MM2S_TDATA_WIDTH': 32, 'C_S_AXIS_S2MM_TDATA_WIDTH': 32}:
        raise RuntimeError('Ellipse DMA configuration mismatch')
    return {'release_manifest_sha256': sha256(ROOT / 'release_manifest.json'),
            'new_bit_sha256': sha256(NEW_BIT), 'new_hwh_sha256': sha256(hwh),
            'baseline_hashes': {ext: sha256(OLD_BIT.with_suffix('.' + ext)) for ext in ('bit', 'hwh')},
            'file_integrity_enforced': False}


def load_image(path, expected_hash):
    gray = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None or gray.shape != (512, 512) or gray.dtype != np.uint8:
        raise ValueError('Expected 512x512 uint8 grayscale: ' + str(path))
    return np.ascontiguousarray(gray)


def load_cases(phase):
    cases = []
    if phase == 'full':
        frozen = ROOT / 'baseline/frozen'
        manifest = json.loads((frozen / 'full_manifest.json').read_text(encoding='utf-8'))
        for row in manifest['frames']:
            cases.append((row['frame_id'], load_image(frozen / 'full184' / row['input_gray'], row['sha256']), row['predictions']))
        if len(cases) != 184 or sum(len(c[2]) for c in cases) != 403:
            raise ValueError('Full coverage must be 184 frames / 403 ROIs')
    else:
        samples = json.loads((ROOT / 'board/samples.json').read_text(encoding='utf-8'))
        if [s['class_id'] for s in samples] != list(range(6)) + [4]:
            raise ValueError('Smoke class coverage mismatch')
        for sample in samples:
            cases.append((sample.get('case_id', 'class_%d' % sample['class_id']),
                          load_image(ROOT / 'board/images' / sample['file'], sample['sha256']), predictions_for(sample)))
        fixture = ROOT / 'baseline/fixtures'
        row = json.loads((fixture / 'frame_143.json').read_text(encoding='utf-8'))
        gray = load_image(fixture / 'frame_143.png', row['sha256'])
        cases.extend([('frame_143', gray, row['predictions']), ('empty_predictions', gray, [])])
        if len(cases) != 9:
            raise ValueError('Smoke must contain nine cases')
    return cases


def array_signature(a):
    a = np.asarray(a)
    return {'dtype': str(a.dtype), 'shape': list(a.shape),
            'sha256': hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()}


def signature(result):
    records = [{k: v for k, v in row.items() if k not in ('provenance', 'processing_time_ms')}
               for row in result['records']]
    return {'records': records,
            'masks': [{k: array_signature(v) for k, v in row.items()} for row in result['masks']],
            'features': {k: array_signature(v) for k, v in result['features'].items()},
            'actual_mode': result['timing']['actual_mode']}


def feature_diffs(result, gray):
    refs = {'sobel': cpu_sobel(gray), 'std7_q8': q8_reference(gray, 7),
            'std13_q8': q8_reference(gray, 13)}
    differences = {}
    for key, ref in refs.items():
        actual = result['features'][key]
        if actual.shape != ref.shape or actual.dtype != ref.dtype:
            raise AssertionError('Feature shape/dtype mismatch: ' + key)
        differences[key] = int(np.count_nonzero(actual != ref))
    return differences


def check_result(result, predictions):
    expected_mode = 'pl_gaussian' if any(p['class_id'] in (2, 4) for p in predictions) else 'arm_float'
    if len(result['records']) != len(predictions) or len(result['masks']) != len(predictions):
        raise AssertionError('ROI output count mismatch')
    timing = result['timing']
    if timing['actual_mode'] != expected_mode or timing['fallback_reason'] is not None:
        raise AssertionError('Unexpected routing/fallback')
    if expected_mode == 'pl_gaussian' and timing['pl_integer_model_different_pixels'] != 0:
        raise AssertionError('Gaussian integer oracle mismatch')


def summary(values):
    return {'count': len(values), 'median': float(np.median(values)),
            'p95': float(np.percentile(values, 95)), 'samples': [float(v) for v in values]}


def arm_dilate(raw):
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    return cv2.dilate((raw > 0).astype(np.uint8), kernel)


def primitive_test(driver):
    rng = np.random.RandomState(20261022)
    masks = []
    for shape in [(1, 1), (1, 512), (512, 1), (2, 3), (6, 7), (7, 6), (7, 7),
                  (8, 9), (33, 69), (127, 131), (511, 512), (512, 512)]:
        for kind in ('zero', 'full', 'edge', 'random'):
            raw = np.zeros(shape, np.uint8)
            if kind == 'full': raw[:] = 255
            elif kind == 'edge': raw[0, 0] = raw[-1, -1] = 255
            elif kind == 'random': raw[:] = (rng.rand(*shape) > .85).astype(np.uint8)*255
            masks.append(('%dx%d_%s' % (shape[0], shape[1], kind), raw))
    # Alternating dimensions and a zero frame after a full frame detect stale state.
    masks.extend([('recovery_zero_1x1', np.zeros((1, 1), np.uint8)),
                  ('center_7x7', np.pad(np.ones((1, 1), np.uint8), 3)*255)])
    import binary_candidate as candidate
    sample = next(s for s in json.loads((ROOT / 'board/samples.json').read_text(encoding='utf-8')) if s['class_id'] == 2)
    gray = load_image(ROOT / 'board/images' / sample['file'], sample['sha256'])
    diagnostics = {'capture_inputs': True}
    candidate.process_frame(gray, predictions_for(sample), mode='model', diagnostics=diagnostics,
        sobel_frame=cpu_sobel(gray), local_std_frames=tuple(q8_reference(gray, size).astype(np.float32)/256 for size in (7, 13)))
    raws = diagnostics.get('raw_inputs', [])
    if len(raws) != 3:
        raise AssertionError('Expected exactly three real class2 ellipse inputs')
    masks.extend(('class2_real_%d' % i, raw) for i, raw in enumerate(raws))
    rows = []
    for name, raw in masks:
        expected = arm_dilate(raw)
        if not np.array_equal(expected, dilate7(raw)):
            raise AssertionError('Host integer/OpenCV oracle disagreement')
        actual, timing = driver.dilate(raw)
        different = int(np.count_nonzero(actual != expected))
        rows.append(dict(timing, case=name, different_pixels=different))
        if different:
            raise AssertionError('Ellipse pixel mismatch: ' + name)
    timing_rows = []
    for index, raw in enumerate(raws):
        arm_dilate(raw); driver.dilate(raw)  # warm both routes outside timing
        arms, pls = [], []
        for repeat in range(9):
            outputs = {}
            for mode in (('arm', 'pl') if repeat % 2 == 0 else ('pl', 'arm')):
                start = time.perf_counter()
                outputs[mode] = arm_dilate(raw) if mode == 'arm' else driver.dilate(raw)[0]
                elapsed = (time.perf_counter()-start)*1000
                (arms if mode == 'arm' else pls).append(elapsed)
            if not np.array_equal(outputs['arm'], outputs['pl']):
                raise AssertionError('Primitive timing-run mismatch')
        timing_rows.append({'real_input': index, 'shape': list(raw.shape),
            'arm_call_ms': summary(arms), 'pl_call_ms': summary(pls),
            'paired_speedup_arm_over_pl': summary([a/p for a, p in zip(arms, pls)])})
    if len(rows) != 53:
        raise AssertionError('Primitive coverage incomplete')
    return {'pass': True, 'cases': rows, 'case_count': len(rows),
            'paired_timing': timing_rows, 'allocation_setup_ms': driver.setup_ms,
            'timing_scope': 'Reusable buffers. Full call includes validation, normalization, padding, cache, DMA, decode and copy; setup reported separately.'}


def run(phase='smoke', repetitions=5):
    global _FAILED_HARDWARE_SESSION
    if _FAILED_HARDWARE_SESSION:
        raise RuntimeError('Previous hardware test failed: save report, reboot board and restart this notebook')
    if phase not in ('smoke', 'full') or repetitions < 3:
        raise ValueError('phase smoke/full; repetitions must be at least 3')
    output = ROOT / 'results' / (datetime.now().strftime('run_%Y%m%d_%H%M%S_%f') + '_' + phase)
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'ellipse_system_acceptance/1.0', 'phase': phase,
        'completed': False, 'pass': False, 'fpga_access_attempted': False,
        'formal_release_replaced': False, 'promotion_approved': False,
        'known_gaussian_float_differences_not_reclassified': ['frame_143', 'boundary_scale259'],
        'scope': 'Static ROI runtime; no detector/camera/FPS/accuracy claim. New ellipse must be exact versus preserved PL-Gaussian runtime.',
        'python': sys.version, 'numpy': np.__version__, 'opencv': cv2.__version__,
        'old_frames': [], 'old_repeated_timing': [], 'new_frames': [], 'timing': [], 'errors': [],
        'result_directory': str(output.resolve())}
    current = None
    def save():
        (output / 'ellipse_system_acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        cv2.setNumThreads(1)
        report['integrity'] = inspect_hardware()
        cases = load_cases(phase)
        selected = [c for c in cases if any(p['class_id'] == 2 for p in c[2])][:3]
        if not selected:
            raise AssertionError('No class2 runtime timing cases')
        report['frame_count_expected'] = len(cases)
        report['roi_count_expected'] = sum(len(c[2]) for c in cases)
        report['fpga_access_attempted'] = True
        current = ExperimentRuntime(OLD_BIT, enable_ellipse=False)
        report['old_overlay_loaded'] = True
        references = {}
        for index, (name, gray, predictions) in enumerate(cases):
            result = current.process_frame(gray, predictions)
            check_result(result, predictions)
            references[name] = signature(result)
            report['old_frames'].append({'case': name, 'signature': references[name], 'timing': result['timing']})
            print('OLD %s %d/%d' % (name, index+1, len(cases)), flush=True)
            save()
        old_timing = {}
        for name, gray, predictions in selected:
            values = []
            for repeat in range(repetitions):
                result = current.process_frame(gray, predictions)
                check_result(result, predictions)
                if signature(result) != references[name]:
                    raise AssertionError('Baseline repeated output mismatch: ' + name)
                values.append(result['timing']['end_to_end_static_pipeline_ms'])
            old_timing[name] = summary(values)
            report['old_repeated_timing'].append({'case': name, 'pipeline_ms': old_timing[name]})
            print('OLD_TIMING %s median=%.2fms' % (name, np.median(values)), flush=True); save()
        current.close(); current = None
        current = ExperimentRuntime(NEW_BIT, enable_ellipse=True)
        report['new_overlay_loaded'] = True
        report['new_setup_ms'] = current.setup_ms
        report['primitive'] = primitive_test(current.ellipse)
        print('ELLIPSE_PRIMITIVE_PASS=True CASES=53', flush=True); save()
        for index, (name, gray, predictions) in enumerate(cases):
            results = {}
            for enabled in ((False, True) if index % 2 == 0 else (True, False)):
                results[enabled] = current.process_frame(gray, predictions, use_ellipse=enabled)
                check_result(results[enabled], predictions)
            without, with_ellipse = results[False], results[True]
            old_equal = signature(without) == references[name]
            new_equal = signature(with_ellipse) == signature(without)
            differences = feature_diffs(with_ellipse, gray)
            expected_calls = sum(p['class_id'] == 2 for p in predictions)
            actual_calls = len(with_ellipse['timing']['ellipse_calls'])
            row = {'case': name, 'roi_count': len(predictions), 'old_vs_new_noellipse_exact': old_equal,
                'ellipse_vs_noellipse_exact': new_equal, 'feature_different_pixels': differences,
                'expected_ellipse_calls': expected_calls, 'actual_ellipse_calls': actual_calls,
                'noellipse_timing': without['timing'], 'ellipse_timing': with_ellipse['timing']}
            report['new_frames'].append(row); save()
            if not old_equal or not new_equal or any(differences.values()) or actual_calls != expected_calls:
                raise AssertionError('System regression failure: ' + name)
            print('NEW %s %d/%d EXACT=True ELLIPSE_CALLS=%d' % (name, index+1, len(cases), actual_calls), flush=True)
        for name, gray, predictions in selected:
            disabled_times, enabled_times, ratios = [], [], []
            for repeat in range(repetitions):
                pair = {}
                for enabled in ((False, True) if repeat % 2 == 0 else (True, False)):
                    pair[enabled] = current.process_frame(gray, predictions, use_ellipse=enabled)
                    check_result(pair[enabled], predictions)
                if signature(pair[False]) != signature(pair[True]) or signature(pair[False]) != references[name]:
                    raise AssertionError('Repeated timing output mismatch: ' + name)
                a = pair[False]['timing']['end_to_end_static_pipeline_ms']
                p = pair[True]['timing']['end_to_end_static_pipeline_ms']
                disabled_times.append(a); enabled_times.append(p); ratios.append(a/p)
            report['timing'].append({'case': name, 'new_bit_arm_ellipse_pipeline_ms': summary(disabled_times),
                'new_bit_pl_ellipse_pipeline_ms': summary(enabled_times), 'paired_speedup_arm_over_pl': summary(ratios),
                'preserved_old_bit_pipeline_ms': old_timing[name],
                'old_bit_over_new_pl_ratio_of_medians': old_timing[name]['median']/float(np.median(enabled_times)),
                'cross_bit_comparison_is_separate_phases_not_interleaved': True,
                'scope': 'Same new bitstream paired comparison changes only ellipse routing. Old-bit repeated timing is from the earlier phase, not paired. Fixed oracle excluded by baseline runtime.'})
            print('PIPELINE %s ARM_ELLIPSE=%.2fms PL_ELLIPSE=%.2fms SPEEDUP=%.3fx' %
                  (name, np.median(disabled_times), np.median(enabled_times), np.median(ratios)), flush=True); save()
        report['completed'] = True
        report['pass'] = (len(report['old_frames']) == len(cases) == len(report['new_frames'])
                          and report['primitive']['pass'] and bool(report['timing']))
        report['performance_requires_review'] = True
        report['next_step'] = 'Review correctness and repeated end-to-end timing; full acceptance required before release promotion.'
    except BaseException as exc:
        _FAILED_HARDWARE_SESSION = report['fpga_access_attempted']
        report['errors'].append('%s: %s' % (type(exc).__name__, exc))
        raise
    finally:
        if current is not None:
            try:
                current.close()
            except BaseException as exc:
                _FAILED_HARDWARE_SESSION = True
                report['pass'] = False
                report['errors'].append('close: %s: %s' % (type(exc).__name__, exc))
        save()
        print('SYSTEM_ACCEPTANCE_PASS=%s' % report['pass'], flush=True)
        print('RESULT_DIRECTORY=' + str(output.resolve()), flush=True)
        print('正式版本文件未改动；当前 PL 可能仍是实验位流。恢复请使用单独的回退单元格。', flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['smoke', 'full'], default='smoke')
    parser.add_argument('--repetitions', type=int, default=5)
    args = parser.parse_args()
    result = run(args.phase, args.repetitions)
    if not result['pass']:
        raise SystemExit(1)
