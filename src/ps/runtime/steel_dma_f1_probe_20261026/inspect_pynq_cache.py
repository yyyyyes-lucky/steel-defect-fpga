"""Read installed PYNQ Python implementation only; never allocate or access an overlay.
Usage on the board: python inspect_pynq_cache.py > pynq_cache_contract.json
This captures code for manual review, not a cache-safety certification.
"""
import hashlib
import inspect
import json
import platform
from pathlib import Path

def collect():
    report = {'schema': 'pynq_cache_source_inventory/1.0',
              'python': platform.python_version(), 'hardware_access_performed': False,
              'overlay_loaded': False, 'buffers_allocated': False, 'errors': []}
    try:
        import pynq
        import pynq.lib.dma as dma
        import pynq.buffer as buffer_module
        report['pynq_version'] = getattr(pynq, '__version__', 'unknown')
        report['dma_source_path'] = inspect.getsourcefile(dma)
        report['dma_source_sha256'] = hashlib.sha256(Path(report['dma_source_path']).read_bytes()).hexdigest()
        report['channel_implementations'] = {}
        for name, cls in inspect.getmembers(dma, inspect.isclass):
            if cls.__module__ != dma.__name__ or not hasattr(cls, 'transfer') or not hasattr(cls, 'wait'):
                continue
            report['channel_implementations'][name] = {
                method: inspect.getsource(getattr(cls, method))
                for method in ('__init__', 'transfer', 'wait')}
        report['buffer_cache_methods'] = {
            method: inspect.getsource(getattr(buffer_module.PynqBuffer, method))
            for method in ('flush', 'invalidate')}
        if not report['channel_implementations']:
            raise RuntimeError('No inspectable DMA channel implementation found')
    except Exception as exc:
        report['errors'].append(type(exc).__name__ + ': ' + str(exc))
    report['source_capture_complete'] = not report['errors']
    return report

if __name__ == '__main__':
    print(json.dumps(collect(), indent=2))
