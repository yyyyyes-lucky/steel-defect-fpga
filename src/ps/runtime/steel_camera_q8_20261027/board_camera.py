"""Reuse the existing camera HTTP handler, with a scoped Q8 runtime adapter."""
import hashlib
from http.server import HTTPServer
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import time

from camera_runtime import CameraRuntime, RELEASE

ROOT = Path(__file__).resolve().parent




def imported(name, package):
    directory = (ROOT.parent / package).resolve()
    old = sys.modules.get(name)
    if old is not None and Path(old.__file__).resolve().parent != directory:
        raise RuntimeError('Conflicting ' + name + '; restart notebook kernel')
    sys.path.insert(0, str(directory))
    return importlib.import_module(name)


def initialize_runtime():
    # Load the existing transport under a unique name.
    name = '_accepted_camera_bridge_20261025'
    location = ROOT.parent / 'steel_camera_bridge_v10_20261025/board_service.py'
    legacy = sys.modules.get(name)
    if legacy is None:
        spec = importlib.util.spec_from_file_location(name, str(location))
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
        sys.modules[name] = legacy
    elif Path(legacy.__file__).resolve() != location.resolve():
        raise RuntimeError('Conflicting old camera bridge; restart kernel')
    # Both transport modules implement the same request protocol.
    if Path(sys.modules['protocol'].__file__).resolve() not in (
            ROOT / 'protocol.py', location.parent / 'protocol.py'):
        raise RuntimeError('Conflicting camera protocol; restart kernel')
    q8 = imported('integration_context', 'steel_q8_path_integration_20261025')
    for name in ('generated_paths', 'integration_kernels'):
        if Path(sys.modules[name].__file__).resolve().parent != ROOT.parent / 'steel_q8_path_integration_20261025':
            raise RuntimeError('Conflicting Q8 module; restart kernel')
    decode = imported('candidate_decode', 'steel_dma_f1_probe_20261026').decode_staged
    entry = legacy.load_entry()
    return legacy, q8, decode, entry


def handler_class(legacy):
    class Handler(legacy.Handler):
        def respond(self, code, value):
            value = dict(value, camera_release=RELEASE)
            if code == 200 and 'pipeline_ms' in value:
                value['q8_context_ms'] = self.server.runtime.last_context_ms
                value['pipeline_plus_context_ms'] = value['pipeline_ms'] + value['q8_context_ms']
            super().respond(code, value)
    return Handler


def serve(port=8766, max_frames=30, idle_seconds=300):
    if type(max_frames) is not int or not 1 <= max_frames <= 1000 or not 0 < idle_seconds <= 3600:
        raise ValueError('Invalid service limits')
    legacy, q8, decode, entry = initialize_runtime()
    # Bind before opening a hardware session; an occupied port must not load an overlay.
    with HTTPServer(('0.0.0.0', port), handler_class(legacy)) as server:
        with entry.Runtime(purpose='camera_q8_application') as inner:
            runtime = CameraRuntime(inner, q8, decode)
            inner.save()
            server.runtime = runtime
            server.processed, server.failure, server.last, server.timeout = 0, None, None, 1
            print('CAMERA_SERVICE_READY=True PORT=' + str(server.server_port), flush=True)
            print('CAMERA_RELEASE=' + RELEASE, flush=True)
            deadline = time.monotonic() + idle_seconds
            previous = 0
            while server.processed < max_frames and time.monotonic() < deadline:
                server.handle_request()
                if server.failure is not None:
                    raise RuntimeError('Camera hardware session failed; preserve report') from server.failure
                if server.processed != previous:
                    previous = server.processed
                    deadline = time.monotonic() + idle_seconds
            count = server.processed
            inner.report['camera_stop_reason'] = 'frame_limit' if count == max_frames else 'idle_timeout'
    print('PROCESSED_FRAMES=' + str(count), flush=True)
    print('HARDWARE_SESSION_SAFE=' + str(inner.report['hardware_session_safe']), flush=True)
    print('BOARD_REPORT_FILE=' + str(inner.path), flush=True)
    return inner.report
