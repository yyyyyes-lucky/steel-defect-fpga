"""Only the new adapter/release wiring. No camera, YOLO or board access."""
from contextlib import contextmanager
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import threading
import unittest
from unittest.mock import patch

import numpy as np
import requests

import board_camera
from camera_runtime import CameraRuntime, RELEASE
from pc_camera import request_frame

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'steel_q8_path_integration_20261025'))
import integration_context as q8
sys.path.insert(0, str(BASE / 'steel_dma_f1_probe_20261026'))
from candidate_decode import decode_staged


def fake_inner(fail=False):
    # Independent globals reproduce real method rebinding and cleanup.
    namespace = {}
    exec('def process(self, gray): pass', namespace)
    Driver = type('Driver', (), {'process': namespace['process'],
                                'failed': False, 'closed': False, 'in_flight': False})
    driver = Driver()
    roi_globals = {}
    exec(compile('def process_frame(gray, predictions): pass',
                 'roi_runtime_portable_v31.py', 'exec'), roi_globals)
    module = NS(process_frame=roi_globals['process_frame'])
    experiment = NS(LOCK=threading.RLock(), base=NS(
        _MODE_LOCK=threading.RLock(), _HARDWARE_LOCK=threading.RLock(),
        _fast=NS(_runtime=module), _features_module=NS(DualFeatures=Driver)))
    inner = NS(state={'experiment': experiment},
               runtime=NS(runtime=NS(tail=NS(base=NS(features=driver)))),
               report={'frames': [], 'integrity': {'new_bit_sha256': 'mock'}},
               path=Path('mock_session.json'), save=lambda: None)
    def process(gray, predictions, case):
        assert driver.process.__func__.__globals__['_q8_path_decode'] is decode_staged
        assert '_q8_path_validate' in module.process_frame.__globals__
        if fail:
            raise RuntimeError('simulated DMA failure')
        inner.report['frames'].append({'case': case})
        return {'records': [dict(p, branch_state='statistics_only') for p in predictions],
                'timing': {'end_to_end_static_pipeline_ms': 2.0}}
    inner.process_frame = process
    return inner


@contextmanager
def server_for(runtime, handler):
    with HTTPServer(('127.0.0.1', 0), handler) as server:
        server.runtime = runtime
        server.processed, server.failure, server.last = 0, None, None
        worker = threading.Thread(target=server.serve_forever,
                                  kwargs={'poll_interval': .01}, daemon=True)
        worker.start()
        with requests.Session() as client:
            client.trust_env = False
            try:
                yield server, client, 'http://127.0.0.1:' + str(server.server_port)
            finally:
                server.shutdown()
                worker.join(timeout=2)


class CameraQ8Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load only the old HTTP module, not initialize_runtime / PYNQ / overlay.
        import importlib.util
        spec = importlib.util.spec_from_file_location('test_legacy_camera', str(
            BASE / 'steel_camera_bridge_v10_20261025/board_service.py'))
        cls.legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.legacy)

    def test_real_q8_context_is_installed_per_frame_and_restored(self):
        inner = fake_inner()
        runtime = CameraRuntime(inner, q8, decode_staged)
        for i in range(2):
            runtime.process_frame(None, [], case=str(i))
            self.assertTrue(q8.restored(inner))
        self.assertTrue(all(f['q8_context_restored'] for f in inner.report['frames']))
        self.assertTrue(all(f['q8_context_ms'] >= 0 for f in inner.report['frames']))
        self.assertEqual(inner.report['camera_configuration']['cache'], 'explicit')

    def test_exception_restores_real_q8_bindings(self):
        inner = fake_inner(fail=True)
        runtime = CameraRuntime(inner, q8, decode_staged)
        with self.assertRaisesRegex(RuntimeError, 'DMA failure'):
            runtime.process_frame(None, [], case='failure')
        self.assertTrue(q8.restored(inner))

    def test_http_health_and_empty_frame_use_new_release(self):
        runtime = CameraRuntime(fake_inner(), q8, decode_staged)
        with server_for(runtime, board_camera.handler_class(self.legacy)) as (server, client, url):
            self.assertEqual(client.get(url + '/health').json()['camera_release'], RELEASE)
            response, _ = request_frame(client, url, np.zeros((512, 512), np.uint8), [])
            self.assertEqual(server.processed, 1)
            self.assertEqual(response['records'], [])
            self.assertAlmostEqual(response['pipeline_plus_context_ms'],
                                   response['pipeline_ms'] + response['q8_context_ms'])

    def test_pc_rejects_old_service_even_when_hardware_matches(self):
        runtime = CameraRuntime(fake_inner(), q8, decode_staged)
        with server_for(runtime, self.legacy.Handler) as (_, client, url):
            with self.assertRaisesRegex(RuntimeError, 'Wrong camera application release'):
                request_frame(client, url, np.zeros((512, 512), np.uint8), [])

    def test_service_frame_limit_closes_single_runtime(self):
        ready = threading.Event()
        addresses, opened, closed, reports, errors = [], [], [], [], []
        inner = fake_inner()
        @contextmanager
        def runtime_factory(purpose):
            opened.append(purpose)
            try:
                yield inner
            finally:
                inner.report['hardware_session_safe'] = True
                closed.append(True)
        class BoundServer(HTTPServer):
            def handle_request(self):
                addresses.append(self.server_port)
                ready.set()
                return super().handle_request()
        def run():
            try:
                reports.append(board_camera.serve(port=0, max_frames=2, idle_seconds=10))
            except BaseException as exc:
                errors.append(exc)
        dependencies = (self.legacy, q8, decode_staged, NS(Runtime=runtime_factory))
        with patch.object(board_camera, 'initialize_runtime', return_value=dependencies), \
                patch.object(board_camera, 'HTTPServer', BoundServer):
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            self.assertTrue(ready.wait(3))
            with requests.Session() as client:
                client.trust_env = False
                url = 'http://127.0.0.1:' + str(addresses[0])
                for _ in range(2):
                    request_frame(client, url, np.zeros((512, 512), np.uint8), [])
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(opened), 1)
        self.assertEqual(closed, [True])
        self.assertEqual(reports[0]['camera_stop_reason'], 'frame_limit')
        self.assertEqual(len(inner.report['frames']), 2)

    def test_occupied_port_does_not_open_runtime(self):
        dependencies = (self.legacy, q8, decode_staged, NS(Runtime=lambda **kw: self.fail('overlay opened')))
        with patch.object(board_camera, 'HTTPServer', side_effect=OSError('address already in use')), \
                patch.object(board_camera, 'initialize_runtime', return_value=dependencies):
            with self.assertRaises(OSError):
                board_camera.serve(port=8766)


if __name__ == '__main__':
    unittest.main(verbosity=2)
