"""Direct selected masks, with CPU and existing-v10 feature/Gaussian backends."""
from contextlib import contextmanager
import copy
import math
import time
import types

import cv2
import numpy as np

import direct_dispatch
from selected_quality import REVISION, prepare_maps
from selected_branch import branch

VERSION = 'v10_v91_q8_selected_masks_direct_1'


def validate_inputs(gray, predictions, runtime):
    if not isinstance(gray, np.ndarray) or gray.dtype != np.uint8 or gray.shape != (512, 512):
        raise ValueError('Expected original 512x512 uint8 grayscale image')
    predictions = copy.deepcopy(list(predictions))
    seen = set()
    for p in predictions:
        if not isinstance(p, dict):
            raise ValueError('Prediction must be a mapping')
        cls, index = p.get('class_id'), p.get('detection_index')
        if isinstance(cls, bool) or not isinstance(cls, (int, np.integer)) or cls not in range(6):
            raise ValueError('class_id must be integer 0..5')
        if isinstance(index, bool) or not isinstance(index, (int, np.integer)) or index < 0 or index in seen:
            raise ValueError('detection_index must be unique nonnegative integer')
        confidence = p.get('confidence')
        if not isinstance(confidence, (int, float, np.number)) or not math.isfinite(float(confidence)) or not 0 <= confidence <= 1:
            raise ValueError('Invalid confidence')
        runtime.roi_bounds(p.get('box_xyxy_pixel'))
        seen.add(index)
    return predictions


def bind_dispatch(runtime, validate_maps):
    # Fresh globals snapshot captures v9.1 scalar/orientation helpers after its
    # context enters, and never replaces the frozen module's process_frame.
    globals_ = dict(runtime.__dict__)
    globals_.update(_quality_validate_maps=validate_maps, _quality_prepare=prepare_maps,
                    _quality_branch=branch, _quality_revision=REVISION,
                    _quality_helpers=runtime)
    fn = types.FunctionType(direct_dispatch.process_frame.__code__, globals_, 'process_frame')
    fn.__kwdefaults__ = direct_dispatch.process_frame.__kwdefaults__
    return fn


def validate_maps(maps, shape):
    if len(maps) != 2:
        raise ValueError('Expected two local-std maps')
    for m in maps:
        if m.shape != shape or m.dtype.kind != 'f' or not np.isfinite(m.min()) or not np.isfinite(m.max()) or m.min() < 0:
            raise ValueError('Invalid local-std map')
    return maps


class CpuRuntime:
    def __init__(self, baseline, local_std_q8):
        self.baseline = baseline
        self.runtime = baseline.fast._runtime
        self.local_std_q8 = local_std_q8
        self.dispatch = bind_dispatch(self.runtime, validate_maps)

    def process_frame(self, gray, predictions):
        start = time.perf_counter()
        predictions = validate_inputs(gray, predictions, self.runtime)
        sobel = self.runtime.sobel_magnitude(gray).astype(np.uint8)
        q7, q13 = (self.local_std_q8(gray, size) for size in (7, 13))
        maps = tuple(cv2.multiply(v, 1 / 256., dtype=cv2.CV_32F) for v in (q7, q13))
        feature_ms = (time.perf_counter() - start) * 1000
        records, masks, roi_timing = self.dispatch(gray, predictions, sobel_frame=sobel,
            local_std_frames=maps, execution='pure_arm_v91_selected_masks')
        return {'records': records, 'masks': masks, 'features': {'sobel': sobel, 'std7_q8': q7, 'std13_q8': q13},
                'timing': {'pipeline_ms': (time.perf_counter()-start)*1000,
                           'feature_ms': feature_ms, 'roi_runtime_timing': roi_timing,
                           'version': VERSION, 'backend': 'pure_arm',
                           'scope': 'input validation, CPU features, selected maps and ROI records; no I/O or YOLO'}}


@contextmanager
def configuration(session, q8, decode):
    from combined_runtime import configuration as combined
    with q8.configuration(session, 'candidate'):
        _, driver, _ = q8.components(session)
        globals_ = driver.process.__func__.__globals__
        previous = globals_['_q8_path_decode']
        try:
            globals_['_q8_path_decode'] = decode
            with combined(session.runtime.runtime, 'combined'):
                yield
        finally:
            globals_['_q8_path_decode'] = previous


class FpgaRuntime:
    """Own no hardware; retain the original Runtime context and safety guard."""
    def __init__(self, session, q8, decode):
        self.session, self.q8, self.decode = session, q8, decode

    def process_frame(self, gray, predictions):
        s = self.session
        if s.closed or s.failed:
            raise RuntimeError('Session closed or failed')
        start = time.perf_counter()
        experiment, driver, runtime = self.q8.components(s)
        predictions = validate_inputs(gray, predictions, runtime)  # before DMA
        gaussian = s.runtime.runtime.tail.base.gaussian
        if gaussian.closed or gaussian.failed or getattr(gaussian, 'in_flight', False):
            raise RuntimeError('Gaussian driver unavailable')
        gaussian_calls = []
        try:
            with configuration(s, self.q8, self.decode):
                # Reuse the existing tail32 response and v9.1 row-pass operators.
                response = s.runtime.runtime.tail.process_frame.__func__.__globals__['response']
                def broad(image):
                    value, timing = response(gaussian, image, 'broad')
                    gaussian_calls.append(timing)
                    return value
                sobel, q7, q13, feature_timing = driver.process(gray)
                maps = tuple(cv2.multiply(v, 1 / 256., dtype=cv2.CV_32F) for v in (q7, q13))
                feature_ms = (time.perf_counter() - start) * 1000
                dispatch = bind_dispatch(runtime, validate_maps)
                records, masks, roi_timing = dispatch(gray, predictions, sobel_frame=sobel,
                    local_std_frames=maps, execution='v10_v91_q8_selected_masks', broad_provider=broad)
            if not self.q8.restored(s):
                raise AssertionError('Q8 configuration was not restored')
            expected = int(any(p['class_id'] in (1, 2, 4) for p in predictions))
            if len(gaussian_calls) != expected:
                raise AssertionError('Shared broad response must run exactly once when required')
            return {'records': records, 'masks': masks,
                    'features': {'sobel': sobel, 'std7_q8': q7, 'std13_q8': q13},
                    'timing': {'pipeline_ms': (time.perf_counter()-start)*1000,
                        'feature_ms': feature_ms, 'feature_timing': feature_timing,
                        'roi_runtime_timing': roi_timing, 'gaussian_calls': gaussian_calls,
                        'blur3_calls': [], 'legacy_masks_computed': 0, 'fixed_oracle_calls': 0,
                        'version': VERSION, 'backend': 'v10_fpga',
                        'scope': 'input validation, context, DMA/cache/waits/decode, shared broad response and PS masks; no I/O or YOLO'}}
        except BaseException as exc:
            # Preserve the frozen runtime's failure latch even if a caller catches
            # the exception inside the outer context.
            s.failed = True
            s.report['errors'].append('selected masks: ' + repr(exc))
            s.save()
            raise
