"""Two paired rounds on the user-selected 120 images; existing v10 overlay."""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import importlib
import json
from pathlib import Path
import platform
import sys
import uuid
import zipfile

import cv2
import numpy as np

from check_support import check_result, signature
from comparison import audit
from cpu_baseline import CpuBaseline, local_std_q8
from demo_support import ROOT, batch_summary, evidence, frame_sources, load_inputs, make_html, save, sha
from quality_runtime import CpuRuntime, FpgaRuntime, VERSION
from selected_quality import REVISION


def initialize_runtime():
    manifest = json.loads((ROOT / 'manifest.json').read_bytes())
    for name in ('comparison', 'check_support', 'cpu_baseline', 'quality_runtime',
                 'direct_dispatch', 'selected_branch', 'selected_quality', 'selected_inclusion'):
        module = sys.modules.get(name)
        if module is not None and Path(module.__file__).resolve().parent != ROOT:
            raise RuntimeError('Conflicting module; restart kernel: ' + name)
    inputs, cases = load_inputs()
    directory = ROOT.parent / 'steel_camera_q8_20261027'
    old = sys.modules.get('board_camera')
    if old is not None and Path(old.__file__).resolve().parent != directory:
        raise RuntimeError('Conflicting board_camera; restart kernel')
    sys.path.insert(0, str(directory))
    camera = importlib.import_module('board_camera')
    _, q8, decode, entry = camera.initialize_runtime()
    return manifest, inputs, cases, q8, decode, entry


@contextmanager
def prohibit_hardware(session):
    base = session.runtime.runtime.tail.base
    targets = [(base.features, 'process'), (base.gaussian, 'row_pass'),
               (base.gaussian, 'response'), (session.runtime.runtime.blur, 'blur')]
    missing, previous = object(), []
    def forbidden(*args, **kwargs):
        raise AssertionError('Hardware call attempted in pure ARM path')
    try:
        for target, attr in targets:
            previous.append((target, attr, target.__dict__.get(attr, missing)))
            setattr(target, attr, forbidden)
        yield
    finally:
        for target, attr, value in reversed(previous):
            if value is missing:
                target.__dict__.pop(attr, None)
            else:
                setattr(target, attr, value)


def run():
    manifest, inputs, cases, q8, decode, entry = initialize_runtime()
    source = frame_sources(inputs, cases)
    prediction_hashes = {r['case']: hashlib.sha256(json.dumps(r['predictions'], sort_keys=True).encode()).hexdigest()
                         for r in inputs['frames']}
    count, rounds = len(cases), 2
    out = ROOT / 'results' / ('run_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
    out.mkdir(parents=True, exist_ok=False)
    path = out / 'static_compare.json'
    report = {'schema': 'selected_static_demo/1.0', 'mode': 'board_measured',
        'completed': False, 'pass': False, 'hardware_session_safe': False, 'errors': [],
        'frames_expected': count, 'rounds': rounds, 'measurements_expected': count*rounds*2,
        'rois_expected': sum(len(c[2]) for c in cases), 'measurements': [], 'audits': [],
        'version': VERSION, 'mask_revision': REVISION, 'source_split': 'valid',
        'inputs_sha256': sha(ROOT/'inputs.json'), 'manifest_sha256': sha(ROOT/'manifest.json'),
        'protocol': 'Two balanced paired rounds; reversed frame and backend order in round two. One warmup image per predicted class and backend. No separate functional sweep.',
        'timing_scope': 'Same-board postprocessing from preloaded 512x512 grayscale and identical frozen YOLO boxes to records/masks. Includes validation, context, feature preparation, DMA, cache, wait, decode, shared Gaussian and PS work. Excludes PC YOLO, network, disk, audit, export, warmup and overlay load. No discarded old masks or per-frame Python fixed oracle.',
        'numeric_scope': 'Q8/Sobel exact. Floating CPU Gaussian and Q30 PL Gaussian can differ at thresholds. All record and mask differences retained. No segmentation ground truth or pixel accuracy claim.',
        'pass_means': 'Complete paired measurements, exact features, deterministic repeats and safe close; does not assert exact masks, equal quality or faster performance.',
        'cpu_configuration': 'Pure ARM, v9.1 scalar/orientation, native exact Q8, selected masks; hardware entry points blocked.',
        'fpga_configuration': 'Original v10 + v9.1 + Q8 staged + explicit cache + selected masks; no F1/F3.',
        'environment': {'python': sys.version, 'machine': platform.machine(),
                        'opencv': cv2.__version__, 'numpy': np.__version__}}
    save(path, report)
    print('REPORT_FILE=' + str(path), flush=True)
    print('IMAGES=%d MEASURED_CALLS=%d' % (count, count*4), flush=True)
    session, frames, fingerprints, previous = None, [], {}, None
    try:
        with entry.Runtime(purpose='selected_120_static_demo') as session:
            cv2.setNumThreads(1)
            cv2.ocl.setUseOpenCL(False)
            report['environment']['opencv_threads'] = cv2.getNumThreads()
            report['overlay_loads'] = 1
            report['session_report_file'] = str(session.path)
            candidates = importlib.import_module('candidates')
            cpu = CpuRuntime(CpuBaseline(session.state['experiment'].base._fast.__file__,
                candidates.scalar_histogram, candidates.orientation_int16), local_std_q8)
            fpga = FpgaRuntime(session, q8, decode)
            def one(variant, item):
                nonlocal previous
                name, gray, predictions = item
                if variant == 'cpu':
                    with prohibit_hardware(session):
                        value = cpu.process_frame(gray, predictions)
                else:
                    value = fpga.process_frame(gray, predictions)
                check_result(value, predictions)
                if hashlib.sha256(gray.tobytes()).hexdigest() != source[name]['gray_array_sha256']:
                    raise AssertionError('Input pixels were modified: ' + name)
                if hashlib.sha256(json.dumps(predictions, sort_keys=True).encode()).hexdigest() != prediction_hashes[name]:
                    raise AssertionError('Frozen detections were modified: ' + name)
                mark = signature(value)
                if (variant, name) in fingerprints and fingerprints[variant, name] != mark:
                    raise AssertionError('Nondeterministic output: ' + variant + '/' + name)
                fingerprints[variant, name] = mark
                if previous is not None and signature(previous[0]) != previous[1]:
                    raise AssertionError('Retained output overwritten')
                previous = value, mark
                return value
            warm = {}
            for cls in range(6):
                case = next((c for c in cases if any(p['class_id'] == cls for p in c[2])), None)
                if case is not None:
                    warm[case[0]] = case
            report['warmup_cases'] = list(warm)
            for case in warm.values():
                for variant in ('cpu', 'fpga'):
                    one(variant, case)
            print('WARMUP_DONE=True', flush=True)
            for repeat in range(rounds):
                ordered = cases if repeat == 0 else list(reversed(cases))
                variants = ('cpu', 'fpga') if repeat == 0 else ('fpga', 'cpu')
                for index, case in enumerate(ordered, 1):
                    pair = {}
                    for position, variant in enumerate(variants):
                        value = one(variant, case)
                        pair[variant] = value
                        report['measurements'].append({'round': repeat, 'case': case[0],
                            'variant': variant, 'position_in_pair': position,
                            'pipeline_ms': value['timing']['pipeline_ms'],
                            'roi_compute_by_class': value['timing']['roi_runtime_timing']['roi_compute_by_class'],
                            'semantic_sha256': signature(value), 'timing': value['timing']})
                    checked = audit(pair['cpu'], pair['fpga'])
                    report['audits'].append(dict(checked, case=case[0], round=repeat))
                    if repeat == 0:
                        frames.append(evidence(case, pair, checked, source[case[0]]))
                    if index % 10 == 0 or index == count:
                        save(path, report)
                        print('STATIC_DEMO ROUND=%d/2 IMAGE=%d/%d CALLS=%d/%d' %
                              (repeat+1, index, count, len(report['measurements']), count*4), flush=True)
            report['summary'] = batch_summary(report['measurements'], inputs['frames'])
            first = [r for r in report['audits'] if r['round'] == 0]
            rois = [a for r in first for a in r['rois']]
            report['agreement'] = {'frames': count, 'frames_exact': sum(r['exact'] for r in first),
                'rois': len(rois), 'rois_exact': sum(r['exact'] for r in rois),
                'mask_rois_different': sum(any(r['mask_different_pixels'].values()) for r in rois),
                'mask_different_pixels': sum(sum(r['mask_different_pixels'].values()) for r in rois),
                'all_semantic_outputs_exact': all(r['exact'] for r in first),
                'features_exact': True, 'branch_states_equal': all(r['branch_state_equal'] for r in rois),
                'review_reasons_equal': all(r['review_reasons_equal'] for r in rois)}
            session.report['static_demo_measurements'] = len(report['measurements'])
        report['hardware_session_safe'] = session.report['hardware_session_safe']
        if not report['hardware_session_safe'] or session.report['errors']:
            raise AssertionError('Session did not close safely')
        if previous is not None and signature(previous[0]) != previous[1]:
            raise AssertionError('Returned arrays changed after close')
        report.update(completed=True, **{'pass': True})
    except BaseException as exc:
        report['errors'].append(type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        if session is not None:
            report['session'] = session.report
            report['hardware_session_safe'] = session.report['hardware_session_safe']
        save(path, report)
        save(ROOT/'latest_static_compare.json', report)
        print('HARDWARE_SESSION_SAFE=' + str(report['hardware_session_safe']), flush=True)
        print('REPORT_FILE=' + str(path), flush=True)
    try:
        make_html(out/'static_demo.html', report, frames)
        save(out/'evidence.json', {'report': report, 'frames': frames})
        archive = out/'static_demo_results.zip'
        with zipfile.ZipFile(str(archive), 'w', zipfile.ZIP_DEFLATED) as z:
            for name in ('static_compare.json', 'evidence.json', 'static_demo.html'):
                z.write(str(out/name), name)
            z.write(str(ROOT/'inputs.json'), 'inputs.json')
        print('STATIC_DEMO_PASS=True', flush=True)
        print('SPEEDUP=' + str(report['summary']['speedup']), flush=True)
        print('DOWNLOAD_ZIP=' + str(archive), flush=True)
    except BaseException as exc:
        report['export_error'] = repr(exc)
        save(path, report)
        save(ROOT/'latest_static_compare.json', report)
        raise
    return report, path


if __name__ == '__main__':
    run()
