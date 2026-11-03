"""Candidate daily entry around the unchanged, qualified v10/v9.1 path."""
from datetime import datetime
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parent
V10_NAME = 'steel_gaussian_v10_board_20261023'
FAILED = False


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()




def initialize_runtime():
    v10 = ROOT.parent / V10_NAME
    old = sys.modules.get('gaussian_v10_runner')
    if old is not None and Path(old.__file__).resolve().parent != v10.resolve():
        raise RuntimeError('Conflicting gaussian_v10_runner; restart kernel')
    sys.path.insert(0, str(v10))
    prior = importlib.import_module('gaussian_v10_runner')
    integrity = prior.initialize_runtime()
    entry, _, board, args, experiment = prior.dependencies(integrity)
    # Check the actual HWH about to be used, independent of historical file hashes.
    integrity['hardware_contract'] = board.hardware_contract(v10 / 'hardware/steel_gaussian_v10.hwh')
    return {'prior': prior, 'entry': entry, 'board': board, 'args': args,
            'experiment': experiment, 'integrity': integrity,
            'bitstream': v10 / 'hardware/steel_gaussian_v10.bit'}


def boot_id():
    path = Path('/proc/sys/kernel/random/boot_id')
    return path.read_text().strip() if path.is_file() else 'unknown'


def guard():
    if FAILED:
        raise RuntimeError('Previous session failed; reboot board and restart kernel')
    boot = boot_id()
    if boot == 'unknown':
        raise RuntimeError('Cannot determine board boot ID')
    # Includes the descriptor integration marker used by v15.
    for marker in ROOT.parent.glob('*/*hardware_session_guard.json'):
        old = json.loads(marker.read_bytes())
        if old.get('boot_id') in (None, 'unknown', boot):
            raise RuntimeError('Active/unfinished hardware session; reboot before retry: ' + str(marker))
    return boot


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


class Runtime:
    """Context-managed v10 session. Caller supplies 512x512 gray and predictions."""
    def __init__(self, purpose='daily'):
        global FAILED
        self.closed = self.failed = False
        self.runtime = None
        state = initialize_runtime()
        boot = guard()
        self.state = state
        state['entry']._guard(ROOT.parent)
        state['board'].cv2.setNumThreads(1)
        self.result_directory = ROOT / 'results' / ('run_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
        self.result_directory.mkdir(parents=True, exist_ok=False)
        self.path = self.result_directory / 'v10_session.json'
        self.marker = ROOT / 'hardware_session_guard.json'
        self.report = {'schema': 'v10_candidate_session/1.0', 'purpose': purpose,
                       'completed': False, 'pass': False, 'hardware_session_safe': False,
                       'formal_release_replaced': False, 'entry_board_qualified': False,
                       'hardware_version': 'v10', 'software_configuration': 'v9.1_combined',
                       'integrity': state['integrity'], 'boot_id': boot, 'errors': [], 'frames': [],
                       'result_directory': str(self.result_directory),
                       'timing_scope': 'Qualified static pipeline; excludes detector, disk, overlay load and oracle. No new speedup claim.'}
        if self.marker.exists():
            self.marker.rename(ROOT / ('hardware_session_guard.previous_' + uuid.uuid4().hex + '.json'))
        with self.marker.open('x', encoding='utf-8') as stream:
            json.dump({'boot_id': boot, 'result_directory': str(self.result_directory)}, stream)
        try:
            self.save()
            self.runtime = state['entry'].CombinedRuntime(state['board'], state['experiment'], state['bitstream'])
        except BaseException as exc:
            self.failed = FAILED = True
            self.report['errors'].append(type(exc).__name__ + ': ' + str(exc))
            self.save()
            raise

    def save(self):
        save(self.path, self.report)

    def process_frame(self, gray, predictions, case='user_frame', reference=None, check_features=False):
        global FAILED
        if self.closed or self.failed:
            raise RuntimeError('Session closed or failed')
        try:
            predictions = list(predictions)
            value = self.runtime.process_frame(gray, predictions, mode='combined')
            args = self.state['args']
            args.check_result(value, predictions)
            timing = value['timing']
            if timing['combined_configuration'] != 'combined':
                raise AssertionError('Wrong software configuration')
            calls = timing['blur3_calls']
            if len(calls) != sum(p['class_id'] == 2 for p in predictions) or any(c['status'] != 0 for c in calls):
                raise AssertionError('Blur3 route/status differs')
            ms = timing['end_to_end_static_pipeline_ms']
            if not math.isfinite(ms) or ms <= 0:
                raise AssertionError('Invalid pipeline timing')
            signature = args.signature(value)
            if reference is not None and signature != reference:
                raise AssertionError('Qualified signature differs: ' + case)
            diffs = args.feature_diffs(value, gray) if check_features else None
            if diffs is not None and any(diffs.values()):
                raise AssertionError('Feature oracle differs')
            self.report['frames'].append({'case': case, 'roi_count': len(predictions),
                                          'exact': True if reference is not None else None,
                                          'reference_checked': reference is not None,
                                          'feature_different_pixels': diffs, 'timing': timing})
            self.save()
            return value
        except BaseException as exc:
            self.failed = FAILED = True
            self.report['errors'].append(type(exc).__name__ + ': ' + str(exc))
            self.save()
            raise

    def close(self, aborted=False):
        global FAILED
        if self.closed:
            return
        self.closed = True
        self.failed = self.failed or aborted
        failure = None
        try:
            if self.runtime is not None:
                self.runtime.close()
        except BaseException as exc:
            failure = exc
            self.failed = True
            self.report['errors'].append('cleanup: ' + repr(exc))
        if aborted:
            self.report['errors'].append('Session body interrupted or raised')
        FAILED = FAILED or self.failed
        self.report.update(completed=not self.failed, hardware_session_safe=not self.failed)
        self.report['pass'] = not self.failed
        self.save()
        if not self.failed:
            self.marker.unlink()
        if failure is not None:
            raise RuntimeError('Unsafe cleanup; reboot board') from failure

    def __enter__(self):
        return self

    def __exit__(self, etype, exc, tb):
        self.close(aborted=etype is not None)
        return False


def run_entry_check():
    """18 mixed frames in one session plus one reopen; no primitive/full rerun."""
    from q8_screen import profile_result, summarize_screen
    diagnostics, diagnostic_errors = [], []
    with Runtime(purpose='entry_check') as rt:
        cases = rt.state['args'].load_cases('smoke')
        refs = rt.state['prior'].references()
        if len(cases) != 9 or len({c[0] for c in cases}) != 9:
            raise AssertionError('Expected nine smoke cases')
        bounds = rt.state['experiment'].base._fast._runtime.roi_bounds
        for repeat in range(2):
            for name, gray, predictions in (cases if repeat == 0 else cases[::-1]):
                result = rt.process_frame(gray, predictions, name, refs[name], check_features=True)
                rt.report['frames'][-1]['round'] = repeat + 1
                rt.save()
                if repeat == 0:
                    # Pure PS diagnostics run after checked hardware processing.
                    # A candidate-model failure cannot silently invalidate baseline checks.
                    try:
                        rois = profile_result(result, predictions, bounds)
                        diagnostics.append({'case': name, 'pipeline_ms': result['timing']['end_to_end_static_pipeline_ms'], 'rois': rois})
                    except Exception as exc:
                        diagnostic_errors.append({'case': name, 'error': repr(exc)})
                print('ENTRY_CHECK', len(rt.report['frames']), '/18', name, 'EXACT=True', flush=True)
    # Exercise the newly introduced session close/reopen path, not old kernels.
    name, gray, predictions = next(c for c in cases if c[0] == 'class_2')
    with Runtime(purpose='reopen_check') as reopened:
        reopened.process_frame(gray, predictions, name, refs[name], check_features=True)
    report = {'schema': 'v10_entry_check/1.0', 'completed': True, 'pass': True,
              'entrypoint_stability_pass': True, 'hardware_session_safe': True,
              'formal_release_replaced': False, 'new_bitstream_built': False,
              'checked_frames': 19, 'overlay_loads': 2, 'last_loaded_hardware': 'v10',
              'scope': 'New wrapper: two mixed rounds and close/reopen. Not long-duration soak or full performance comparison.',
              'sessions': [rt.report, reopened.report],
              'q8_screen': {'completed': not diagnostic_errors, 'errors': diagnostic_errors,
                            'frames': diagnostics, 'summary': summarize_screen(diagnostics)}}
    path = rt.result_directory / 'v10_entry_check.json'
    save(path, report)
    print('ENTRY_CHECK_PASS=True', flush=True)
    print('Q8_SCREEN_COMPLETE=' + str(not diagnostic_errors), flush=True)
    print('REPORT_FILE=' + str(path), flush=True)
    return report


def rollback_to_v9_1():
    """Use the preserved v9.1 entry to reload its v9 bitstream and check a fixture."""
    state = initialize_runtime()
    guard()
    result = state['entry'].run_demo('class_2')
    print('ROLLBACK_TARGET=v9_hardware_v9.1_software')
    return result
