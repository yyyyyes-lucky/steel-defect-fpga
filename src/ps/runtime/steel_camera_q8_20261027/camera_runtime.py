"""Application adapter only; reuse qualified Q8 functions and explicit cache."""
import time

RELEASE = 'camera_v10_v91_q8_minmax_staged_explicit_20261027'


class CameraRuntime:
    def __init__(self, runtime, q8, decode_staged):
        self.inner = runtime
        self.q8 = q8
        self.decode_staged = decode_staged
        self.report = runtime.report
        self.path = runtime.path
        self.report['camera_release'] = RELEASE
        self.report['camera_configuration'] = {
            'hardware': 'original_v10', 'software': 'v9.1_combined',
            'q8_validation': 'minmax', 'q8_decode': 'staged',
            'cache': 'explicit', 'f1': False, 'f3': False,
            'class2_ps_reuse_candidate': False,
            'new_camera_adapter_board_accepted': False,
        }

    def process_frame(self, gray, predictions, case):
        begin = time.perf_counter()
        body_ms = 0.0
        with self.q8.configuration(self.inner, 'candidate'):
            _, driver, _ = self.q8.components(self.inner)
            globals_ = driver.process.__func__.__globals__
            previous = globals_['_q8_path_decode']
            try:
                globals_['_q8_path_decode'] = self.decode_staged
                body_begin = time.perf_counter()
                value = self.inner.process_frame(gray, predictions, case=case)
                body_ms = (time.perf_counter() - body_begin) * 1000
            finally:
                globals_['_q8_path_decode'] = previous
        if not self.q8.restored(self.inner):
            raise RuntimeError('Camera Q8 context did not restore')
        context_ms = max(0.0, (time.perf_counter() - begin) * 1000 - body_ms)
        self.last_context_ms = context_ms
        self.report['frames'][-1]['camera_release'] = RELEASE
        self.report['frames'][-1]['q8_context_restored'] = True
        self.report['frames'][-1]['q8_context_ms'] = context_ms
        self.inner.save()
        return value
