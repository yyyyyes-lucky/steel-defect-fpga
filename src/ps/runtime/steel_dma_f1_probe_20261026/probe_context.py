"""Keep existing locks and cleanup; do not change any cache maintenance."""
from contextlib import contextmanager
import types
import integration_context as q8
from candidate_decode import decode_staged
from profiled_driver import profiled_feature_process

@contextmanager
def configuration(session, variant, detailed=False):
    if variant not in ('direct','staged'): raise ValueError('Unknown DMA candidate')
    with q8.configuration(session,'candidate'):
        _,driver,_=q8.components(session)
        original=driver.process
        globals_=original.__func__.__globals__
        decode=globals_['_q8_path_decode']
        try:
            if variant=='staged': globals_['_q8_path_decode']=decode_staged
            if detailed:
                function=q8.rebound(profiled_feature_process,original.__func__)
                driver.process=types.MethodType(function,driver)
                driver._dma_f1_detail=None
            yield
        finally:
            driver.process=original
            globals_['_q8_path_decode']=decode
