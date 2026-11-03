"""Temporarily install two generated functions under the existing locks."""
from contextlib import contextmanager
import types

import generated_paths
from integration_kernels import decode_owned_planes, validate_maps_minmax

_MISSING = object()


def rebound(template, original):
    # Keep the original globals: v9.1 changes scalar_stats/orientation and v10
    # changes broad/ROI blur operators dynamically in this same private module.
    result = types.FunctionType(template.__code__, original.__globals__,
                                original.__name__, template.__defaults__)
    result.__kwdefaults__ = template.__kwdefaults__
    return result


def components(session):
    experiment = session.state['experiment']
    driver = session.runtime.runtime.tail.base.features
    return experiment, driver, experiment.base._fast._runtime


@contextmanager
def configuration(session, variant):
    if variant not in ('baseline', 'candidate'):
        raise ValueError('Unknown Q8 path variant')
    experiment, driver, module = components(session)
    with experiment.LOCK, experiment.base._MODE_LOCK, experiment.base._HARDWARE_LOCK:
        if driver.failed or driver.closed or driver.in_flight:
            raise RuntimeError('Feature driver is unavailable or busy')
        if variant == 'baseline':
            yield
            return
        old_attribute = driver.__dict__.get('process', _MISSING)
        old_method, old_roi = driver.process, module.process_frame
        driver_globals = old_method.__func__.__globals__
        roi_globals = old_roi.__globals__
        if '_q8_path_decode' in driver_globals or '_q8_path_validate' in roi_globals:
            raise RuntimeError('Q8 path configuration already active')
        candidate_driver = rebound(generated_paths.feature_process, old_method.__func__)
        candidate_roi = rebound(generated_paths.roi_process, old_roi)
        try:
            driver_globals['_q8_path_decode'] = decode_owned_planes
            roi_globals['_q8_path_validate'] = validate_maps_minmax
            driver.process = types.MethodType(candidate_driver, driver)
            module.process_frame = candidate_roi
            yield
        finally:
            module.process_frame = old_roi
            if old_attribute is _MISSING:
                driver.__dict__.pop('process', None)
            else:
                driver.process = old_attribute
            driver_globals.pop('_q8_path_decode', None)
            roi_globals.pop('_q8_path_validate', None)


def restored(session):
    experiment, driver, module = components(session)
    return (driver.process.__func__ is experiment.base._features_module.DualFeatures.process
            and module.process_frame.__code__.co_filename.endswith('roi_runtime_portable_v31.py')
            and '_q8_path_decode' not in driver.process.__func__.__globals__
            and '_q8_path_validate' not in module.process_frame.__globals__)
