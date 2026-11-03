"""One guarded v10 session, direct selected masks, no ARM comparison reruns."""
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
import importlib
import json
from pathlib import Path
import sys
import time

import cv2
from wire import RELEASE, REVISION, VERSION, MAX_BODY, decode_request, encode_png, json_bytes

ROOT = Path(__file__).resolve().parent


def initialize_runtime():
    package = ROOT.parent/'steel_static_demo_20261029'
    for name in ('benchmark', 'quality_runtime', 'selected_quality', 'check_support', 'demo_support'):
        old = sys.modules.get(name)
        if old is not None and Path(old.__file__).resolve().parent != package:
            raise RuntimeError('Restart kernel: conflicting ' + name)
    sys.path.insert(0, str(package))
    benchmark = importlib.import_module('benchmark')
    # Load runtime dependencies only; the live camera does not use static inputs.
    camera_dir = ROOT.parent / 'steel_camera_q8_20261027'
    old = sys.modules.get('board_camera')
    if old is not None and Path(old.__file__).resolve().parent != camera_dir:
        raise RuntimeError('Conflicting board_camera; restart kernel')
    sys.path.insert(0, str(camera_dir))
    camera = importlib.import_module('board_camera')
    _, q8, decode, entry = camera.initialize_runtime()
    quality = importlib.import_module('quality_runtime')
    if quality.VERSION != VERSION or quality.REVISION != REVISION:
        raise RuntimeError('Wrong mask runtime')
    return q8, decode, entry, quality.FpgaRuntime, benchmark.check_result


def make_response(request, result, bit_sha256=None):
    return {'release': RELEASE, 'version': VERSION, 'mask_revision': REVISION, 'bit_sha256': bit_sha256,
            'frame_id': request['frame_id'], 'image_sha256': request['image_sha256'],
            'records': result['records'], 'masks': [{k: encode_png(a) for k, a in row.items()} for row in result['masks']],
            'pipeline_ms': result['timing']['pipeline_ms'], 'timing': result['timing'],
            'scope': 'Selected v10+ARM postprocessing including context/DMA/cache/waits; excludes PNG/HTTP/disk/YOLO.'}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, *args):
        pass

    def respond(self, code, value):
        data = json_bytes(value)
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path != '/health':
            self.respond(404, {'error': 'Unknown route'})
            return
        self.respond(200, {'release': RELEASE, 'version': VERSION, 'mask_revision': REVISION,
            'bit_sha256': self.server.inner.report['integrity']['new_bit_sha256'], 'status': 'ready' if self.server.failure is None else 'failed',
            'processed': self.server.processed, 'max_frames': self.server.max_frames})

    def do_POST(self):
        if self.path not in ('/process', '/stop'):
            self.respond(404, {'error': 'Unknown route'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if self.headers.get('Transfer-Encoding') or not 0 < size <= MAX_BODY:
                raise ValueError('Invalid request size')
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError('Truncated request')
            request = json.loads(raw)
            if self.path == '/stop':
                if request.get('release') != RELEASE:
                    raise ValueError('Wrong service release')
                self.server.stopping = True
                self.respond(200, {'stopping': True, 'safe_close_pending': True})
                return
            gray = decode_request(request)
        except Exception as exc:
            self.respond(400, {'error': str(exc)})
            return
        if request['frame_id'] in self.server.seen:
            self.respond(409, {'error': 'Frame already processed; do not retry'})
            return
        self.server.seen.add(request['frame_id'])
        try:
            started = time.perf_counter()
            result = self.server.runtime.process_frame(gray, request['predictions'])
            self.server.check_result(result, request['predictions'])
            value = make_response(request, result, self.server.inner.report['integrity']['new_bit_sha256'])
            value['board_work_and_encode_ms'] = (time.perf_counter()-started)*1000
            json_bytes(value)
            self.server.inner.report['frames'].append({'case': request['frame_id'],
                'image_sha256': request['image_sha256'], 'roi_count': len(result['records']),
                'timing': result['timing'], 'reference_checked': False})
            self.server.inner.save()
            self.server.processed += 1
            self.server.last_frame = time.monotonic()
            self.respond(200, value)
        except Exception as exc:
            self.server.failure = exc
            self.respond(500, {'error': repr(exc)})


def serve(port=8767, max_frames=1000, idle_seconds=600):
    if type(max_frames) is not int or not 1 <= max_frames <= 10000 or not 30 <= idle_seconds <= 3600:
        raise ValueError('Invalid service limits')
    q8, decode, entry, runtime_type, check_result = initialize_runtime()
    inner = None
    try:
        # An occupied port must fail before opening any hardware.
        with HTTPServer(('0.0.0.0', port), Handler) as server:
            server.timeout = 1
            server.failure, server.stopping, server.processed = None, False, 0
            server.max_frames, server.seen = max_frames, set()
            with entry.Runtime(purpose='dynamic_selected_masks') as inner:
                cv2.setNumThreads(1)
                cv2.ocl.setUseOpenCL(False)
                server.inner = inner
                server.runtime = runtime_type(inner, q8, decode)
                server.check_result = check_result
                server.last_frame = time.monotonic()
                print('DYNAMIC_SERVICE_READY=True PORT=%d' % port, flush=True)
                while not server.stopping and server.processed < max_frames and time.monotonic()-server.last_frame < idle_seconds:
                    server.handle_request()
                    if server.failure is not None:
                        raise RuntimeError('Dynamic processing failed; inspect session report') from server.failure
                inner.report['stop_reason'] = 'user_stop' if server.stopping else ('frame_limit' if server.processed >= max_frames else 'idle_timeout')
                inner.save()
    finally:
        if inner is not None:
            print('HARDWARE_SESSION_SAFE=' + str(inner.report['hardware_session_safe']), flush=True)
            print('BOARD_REPORT_FILE=' + str(inner.path), flush=True)
    return inner.report
