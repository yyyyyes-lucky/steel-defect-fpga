"""Host-only tests: no PYNQ access and no board-performance evidence."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import tempfile
import json
import warnings
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'board'))
import ellipse7_driver as driver_module
from ellipse7_driver import Ellipse7
from binary_models import dilate7
import experiment_runtime as exp
import run_board_acceptance as acceptance


class Buffer(np.ndarray):
    def __new__(cls, shape, dtype):
        obj = np.zeros(shape, dtype).view(cls)
        obj.freed = False
        return obj
    def flush(self): pass
    def invalidate(self): pass
    def freebuffer(self): self.freed = True


class FakeHardware:
    def __init__(self, failure=None):
        self.failure = failure
        self.height = self.width = None
        self.started = self.done = self.halted = False
        self.target = None
        self.wait_calls = 0
        self.axi_dma_ellipse = SimpleNamespace(mmio=SimpleNamespace(read=self.dma_read, write=self.dma_write),
            recvchannel=SimpleNamespace(transfer=self.receive, wait=self.wait),
            sendchannel=SimpleNamespace(transfer=self.send, wait=self.wait))
        self.ellipse7_0 = SimpleNamespace(mmio=SimpleNamespace(read=self.core_read, write=self.core_write))
    def receive(self, target): self.target = target
    def send(self, source):
        if self.failure == 'send_interrupt': raise KeyboardInterrupt()
        self.target[:] = dilate7(np.asarray(source[3:-3, 3:-3]).astype(np.uint8))
        if self.failure == 'nonbinary': self.target.flat[0] = 2
        self.done = self.failure not in ('timeout', 'reset_timeout')
    def wait(self):
        assert self.done, 'Unbounded wait attempted before observed idle/done'
        self.wait_calls += 1
    def core_read(self, addr):
        if addr == 0: return (4 if not self.started else 0) | (6 if self.done else 0)
        if addr == 0x20: return 8 if self.failure == 'protocol' else 0
        return 0
    def core_write(self, addr, value):
        if addr == 0x10: self.height = value
        elif addr == 0x18: self.width = value
        elif addr == 0: self.started, self.done, self.halted = True, False, False
    def dma_read(self, addr):
        if addr == 0: return 4 if self.failure == 'reset_timeout' else 0
        if addr in (0x04, 0x34):
            if self.halted: return 1
            if self.failure == 'dma_error': return 0x10
            return 2 if self.done else 0
        if addr == 0x58: return self.target.nbytes - (4 if self.failure == 'length' else 0)
        return 0
    def dma_write(self, addr, value):
        if addr == 0 and value == 4 and self.failure != 'reset_timeout': self.halted = True


class DriverTests(unittest.TestCase):
    def test_valid_and_size_changes(self):
        hw = FakeHardware()
        with Ellipse7(hw, Buffer) as d:
            for shape in ((512, 512), (1, 1), (1, 512), (512, 1), (33, 69)):
                raw = np.zeros(shape, np.uint8); raw[0, 0] = 255
                actual, timing = d.dilate(raw)
                np.testing.assert_array_equal(actual, acceptance.arm_dilate(raw))
                self.assertEqual(actual.dtype, np.uint8)
                self.assertGreaterEqual(timing['call_ms'], timing['launch_dma_core_wait_ms'])
            self.assertEqual(hw.wait_calls, 10)
        self.assertTrue(all(b.freed for b in d.buffers))

    def test_bad_inputs_never_start_dma(self):
        hw = FakeHardware()
        with Ellipse7(hw, Buffer) as d:
            for raw in (np.zeros((0, 1), np.uint8), np.zeros((513, 1), np.uint8),
                        np.zeros((1, 1), np.float32), np.zeros(5, np.uint8)):
                with self.assertRaises(ValueError): d.dilate(raw)
            for t in (0, -1, float('nan'), float('inf')):
                with self.assertRaises(ValueError): d.dilate(np.zeros((1, 1), np.uint8), t)
            self.assertFalse(hw.started)
            self.assertFalse(d.failed)

    def test_errors_disable_driver(self):
        for failure in ('dma_error', 'protocol', 'length', 'nonbinary', 'timeout', 'send_interrupt'):
            with self.subTest(failure=failure):
                hw = FakeHardware(failure)
                d = Ellipse7(hw, Buffer)
                with self.assertRaises((RuntimeError, TimeoutError, KeyboardInterrupt)):
                    d.dilate(np.ones((7, 7), np.uint8), timeout_seconds=.002)
                with self.assertRaises(RuntimeError): d.dilate(np.ones((1, 1), np.uint8))
                d.close()
                self.assertTrue(all(b.freed for b in d.buffers))
                if failure in ('timeout', 'dma_error', 'send_interrupt'): self.assertEqual(hw.wait_calls, 0)

    def test_failed_reset_retains_memory(self):
        d = Ellipse7(FakeHardware('reset_timeout'), Buffer)
        with self.assertRaises(TimeoutError): d.dilate(np.zeros((1, 1), np.uint8), .001)
        before = len(driver_module._RETAINED_BUFFERS)
        with warnings.catch_warnings(record=True) as caught:
            with patch.object(driver_module.time, 'monotonic', side_effect=[0, 2]): d.close()
        self.assertFalse(d.recovery_safe)
        self.assertFalse(any(b.freed for b in d.buffers))
        self.assertEqual(len(driver_module._RETAINED_BUFFERS), before+2)
        self.assertTrue(caught)


class ModelEllipse:
    setup_ms = 0
    def dilate(self, raw):
        return dilate7(raw), {'call_ms': 0, 'scope': 'HOST_SOFTWARE_ONLY'}


class RuntimeTests(unittest.TestCase):
    def test_full184_private_injection_software_only(self):
        from transport_common import compare_results
        roi_count = call_count = 0
        for name, gray, predictions in acceptance.load_cases('full'):
            kwargs = {'sobel_frame': acceptance.cpu_sobel(gray),
                'local_std_frames': tuple(cv2.multiply(acceptance.q8_reference(gray, size), 1/256., dtype=cv2.CV_32F) for size in (7, 13))}
            expected = exp.base._fast.process_frame(gray, predictions, **kwargs)
            calls = []
            with exp.ellipse_operator(ModelEllipse(), calls):
                actual = exp.base._fast.process_frame(gray, predictions, **kwargs)
            self.assertTrue(all(c['pass'] for c in compare_results(actual, expected)), name)
            self.assertEqual(len(calls), sum(p['class_id'] == 2 for p in predictions), name)
            roi_count += len(predictions)
            call_count += len(calls)
        self.assertEqual(roi_count, 403)
        self.assertGreater(call_count, 0)

    def test_packager_rejects_incomplete_build(self):
        import build_board_package as packager
        with tempfile.TemporaryDirectory(prefix='ellipse_package_gate_') as directory:
            root = Path(directory)
            (root / 'vivado_build.log').write_text('build in progress', encoding='utf-8')
            with patch.object(packager, 'ROOT', root):
                with self.assertRaisesRegex(RuntimeError, 'Actual Vivado build/export'):
                    packager.build()
            self.assertFalse((root / 'release_manifest.json').exists())

    def test_unified_runner_reports_success_and_failure(self):
        case = next(c for c in acceptance.load_cases('smoke') if c[0] == 'class_2')
        _, gray, predictions = case
        features = {'sobel': acceptance.cpu_sobel(gray), 'std7_q8': acceptance.q8_reference(gray, 7),
                    'std13_q8': acceptance.q8_reference(gray, 13)}
        record_count = len(predictions)
        class MockRuntime:
            fail = False
            setup_ms = 0
            ellipse = ModelEllipse()
            def __init__(self, *args, **kwargs): pass
            def close(self): pass
            def process_frame(self, gray, predictions, use_ellipse=False):
                if self.fail and use_ellipse: raise RuntimeError('forced hardware failure')
                return {'records': [{} for _ in range(record_count)],
                    'masks': [{} for _ in range(record_count)], 'features': features,
                    'timing': {'actual_mode': 'pl_gaussian', 'fallback_reason': None,
                        'pl_integer_model_different_pixels': 0, 'end_to_end_static_pipeline_ms': 1.,
                        'ellipse_calls': [{} for _ in range(record_count)] if use_ellipse else []}}
        with tempfile.TemporaryDirectory(prefix='ellipse_host_test_') as directory:
            with patch.object(acceptance, 'ROOT', Path(directory)), \
                 patch.object(acceptance, 'inspect_hardware', return_value={'scope': 'MOCK'}), \
                 patch.object(acceptance, 'load_cases', return_value=[case]), \
                 patch.object(acceptance, 'primitive_test', return_value={'pass': True, 'scope': 'MOCK'}), \
                 patch.object(acceptance, 'ExperimentRuntime', MockRuntime), \
                 patch('builtins.print'):
                result = acceptance.run('smoke', 3)
                self.assertTrue(result['pass'])
                self.assertTrue(result['completed'])
                self.assertEqual(len(result['new_frames']), 1)
                self.assertFalse(result['promotion_approved'])
                MockRuntime.fail = True
                with self.assertRaisesRegex(RuntimeError, 'forced hardware'):
                    acceptance.run('smoke', 3)
                reports = [json.loads(p.read_text(encoding='utf-8')) for p in Path(directory).glob('results/*/*.json')]
                self.assertEqual(len(reports), 2)
                self.assertEqual(sum(r['pass'] for r in reports), 1)
                self.assertTrue(any(r['errors'] for r in reports))
                with self.assertRaisesRegex(RuntimeError, 'Previous hardware test failed'):
                    acceptance.run('smoke', 3)
        acceptance._FAILED_HARDWARE_SESSION = False

    def test_runtime_entry_injection_with_shared_features(self):
        case = next(c for c in acceptance.load_cases('smoke') if c[0] == 'class_2')
        _, gray, predictions = case
        arrays = (acceptance.cpu_sobel(gray), acceptance.q8_reference(gray, 7), acceptance.q8_reference(gray, 13))
        features = SimpleNamespace(process=lambda image: arrays + ({'scope': 'HOST_MOCK'},), close=lambda: None)
        gaussian_calls = []
        def response(image, branch):
            gaussian_calls.append(branch)
            return np.zeros((512, 512), np.float32), {'scope': 'HOST_MOCK'}
        gaussian = SimpleNamespace(response=response, close=lambda: None)
        fixed = SimpleNamespace(response=lambda *args: np.zeros((512, 512), np.float32))
        runtime = exp.ExperimentRuntime.__new__(exp.ExperimentRuntime)
        runtime.base = exp.base.SteelDefectRuntime(features, gaussian, fixed_model=fixed)
        runtime.ellipse = ModelEllipse()
        runtime.closed = False
        expected = runtime.process_frame(gray, predictions, use_ellipse=False)
        actual = runtime.process_frame(gray, predictions, use_ellipse=True)
        self.assertEqual(acceptance.signature(actual), acceptance.signature(expected))
        self.assertEqual(gaussian_calls, ['broad', 'broad'])
        self.assertEqual(len(actual['timing']['ellipse_calls']), 3)
        self.assertEqual(actual['timing']['pl_integer_model_different_pixels'], 0)
        self.assertEqual(expected['timing']['ellipse_calls'], [])
        self.assertEqual(actual['timing']['actual_mode'], 'pl_gaussian')
        self.assertGreater(actual['timing']['end_to_end_static_pipeline_ms'], 0)

    def test_frozen_coverage_and_hashes(self):
        self.assertEqual(len(acceptance.load_cases('smoke')), 9)
        full = acceptance.load_cases('full')
        self.assertEqual(len(full), 184)
        self.assertEqual(sum(len(c[2]) for c in full), 403)
        for suffix, expected in acceptance.OLD_HASHES.items():
            self.assertEqual(acceptance.sha256(acceptance.OLD_BIT.with_suffix('.'+suffix)), expected)

    def test_primitive_harness_with_software_double(self):
        result = acceptance.primitive_test(ModelEllipse())
        self.assertEqual(result['case_count'], 53)
        self.assertTrue(result['pass'])
        # Timings in this host test are not performance evidence and are not exported.

    def test_private_operator_matches_unchanged_runtime(self):
        original = exp.base._fast._runtime.supported_union
        for name, gray, predictions in acceptance.load_cases('smoke'):
            kwargs = {'sobel_frame': acceptance.cpu_sobel(gray),
                'local_std_frames': tuple(cv2.multiply(acceptance.q8_reference(gray, size), 1/256., dtype=cv2.CV_32F) for size in (7, 13))}
            expected = exp.base._fast.process_frame(gray, predictions, **kwargs)
            calls = []
            with exp.ellipse_operator(ModelEllipse(), calls):
                actual = exp.base._fast.process_frame(gray, predictions, **kwargs)
            from transport_common import compare_results
            self.assertTrue(all(row['pass'] for row in compare_results(actual, expected)), name)
            self.assertEqual(len(calls), sum(p['class_id'] == 2 for p in predictions))
            self.assertIs(exp.base._fast._runtime.supported_union, original)
        with self.assertRaisesRegex(RuntimeError, 'forced'):
            with exp.ellipse_operator(ModelEllipse(), []): raise RuntimeError('forced')
        self.assertIs(exp.base._fast._runtime.supported_union, original)


if __name__ == '__main__':
    cv2.setNumThreads(1)
    unittest.main(verbosity=2)
