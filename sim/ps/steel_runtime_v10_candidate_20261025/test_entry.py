"""Host-only wrapper tests; no simulated result is a board qualification."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import runtime_v10 as entry


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'entry'
        self.root.mkdir()
        self.stack = ExitStack()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(entry, 'ROOT', self.root))
        self.stack.enter_context(patch.object(entry, 'FAILED', False))
        self.stack.enter_context(patch.object(entry, 'boot_id', return_value='boot-a'))
        self.created = []
        outer = self
        class Hardware:
            def __init__(self, board, experiment, bitstream):
                outer.created.append(self)
                self.closed = False
                self.bitstream = bitstream
                self.calls = []
            def process_frame(self, gray, predictions, mode):
                self.calls.append((gray, mode))
                return {'records': predictions, 'signature': gray,
                        'timing': {'combined_configuration': mode, 'blur3_calls': [{'status': 0} for p in predictions if p['class_id'] == 2],
                                   'end_to_end_static_pipeline_ms': 10}}
            def close(self):
                self.closed = True
        self.state = {'entry': SimpleNamespace(_guard=lambda p: None, CombinedRuntime=Hardware),
                      'board': SimpleNamespace(cv2=SimpleNamespace(setNumThreads=lambda n: None)),
                      'experiment': None, 'bitstream': Path('hardware/steel_gaussian_v10.bit'),
                      'integrity': {'new_bit_sha256': 'mock'},
                      'args': SimpleNamespace(check_result=lambda r,p: None,
                                              signature=lambda r: r['signature'],
                                              feature_diffs=lambda r,g: {'sobel': 0, 'std7_q8': 0, 'std13_q8': 0})}
        self.stack.enter_context(patch.object(entry, 'initialize_runtime', return_value=self.state))

    def test_success_close_reopen_and_v10_identity(self):
        for i in range(2):
            with entry.Runtime() as rt:
                rt.process_frame('fixture', [{'class_id': 2}], reference='fixture', check_features=True)
            saved = json.loads(rt.path.read_bytes())
            self.assertTrue(saved['pass'] and saved['hardware_session_safe'])
            self.assertFalse(saved['formal_release_replaced'])
            self.assertFalse(rt.marker.exists())
            self.assertTrue(self.created[-1].closed)
            self.assertEqual(self.created[-1].bitstream.name, 'steel_gaussian_v10.bit')
        self.assertEqual(len(self.created), 2)

    def test_guard_sees_other_package_and_descriptor_marker(self):
        sibling = self.root.parent / 'descriptor'
        sibling.mkdir()
        marker = sibling / 'descriptor_integration_hardware_session_guard.json'
        marker.write_text(json.dumps({'boot_id': 'boot-a'}))
        with self.assertRaisesRegex(RuntimeError, 'Active/unfinished'):
            entry.Runtime()
        self.assertEqual(len(self.created), 0)
        with patch.object(entry, 'boot_id', return_value='boot-b'):
            with entry.Runtime():
                pass

    def test_failed_output_retains_guard_and_reboot_is_required(self):
        with self.assertRaisesRegex(AssertionError, 'Qualified signature'):
            with entry.Runtime() as rt:
                rt.process_frame('bad', [], reference='good')
        self.assertTrue(rt.marker.exists())
        self.assertTrue(self.created[-1].closed)
        entry.FAILED = False  # Kernel restart alone is insufficient.
        with self.assertRaisesRegex(RuntimeError, 'Active/unfinished'):
            entry.Runtime()
        with patch.object(entry, 'boot_id', return_value='boot-b'):
            with entry.Runtime():
                pass
        self.assertEqual(len(list(self.root.glob('hardware_session_guard.previous_*.json'))), 1)

    def test_programming_and_close_failure_retained(self):
        with patch.object(self.state['entry'], 'CombinedRuntime', side_effect=RuntimeError('programming')):
            with self.assertRaisesRegex(RuntimeError, 'programming'):
                entry.Runtime()
        self.assertTrue((self.root / 'hardware_session_guard.json').exists())
        entry.FAILED = False
        with patch.object(entry, 'boot_id', return_value='boot-b'):
            rt = entry.Runtime()
            with patch.object(rt.runtime, 'close', side_effect=RuntimeError('DMA')):
                with self.assertRaisesRegex(RuntimeError, 'Unsafe cleanup'):
                    rt.close()
            saved = json.loads(rt.path.read_bytes())
            self.assertFalse(saved['pass'] or saved['hardware_session_safe'])
            self.assertTrue(rt.marker.exists())

    def test_feature_failure_and_bad_timing(self):
        with patch.object(self.state['args'], 'feature_diffs', return_value={'sobel': 1}):
            with self.assertRaisesRegex(AssertionError, 'Feature oracle'):
                with entry.Runtime() as rt:
                    rt.process_frame('fixture', [], check_features=True)
        self.assertFalse(json.loads(rt.path.read_bytes())['pass'])

    def test_complete_entry_check_separates_diagnostics(self):
        names = ['class_' + str(i) for i in range(6)] + ['boundary_scale259', 'frame_143', 'empty_predictions']
        cases = [(n, n, [] if n == 'empty_predictions' else [{'class_id': 2}]) for n in names]
        self.state['args'].load_cases = lambda phase: cases
        self.state['prior'] = SimpleNamespace(references=lambda: {n: n for n in names})
        self.state['experiment'] = SimpleNamespace(base=SimpleNamespace(_fast=SimpleNamespace(_runtime=SimpleNamespace(roi_bounds=None))))
        with patch('q8_screen.profile_result', return_value=[]), redirect_stdout(io.StringIO()):
            result = entry.run_entry_check()
        self.assertEqual(sum(len(s['frames']) for s in result['sessions']), 19)
        self.assertEqual(len(self.created), 2)
        self.assertTrue(result['pass'])
        self.assertEqual(self.created[0].calls[9][0], 'empty_predictions')
        with patch('q8_screen.profile_result', side_effect=ValueError('candidate mismatch')), redirect_stdout(io.StringIO()):
            result = entry.run_entry_check()
        self.assertTrue(result['pass'])
        self.assertFalse(result['q8_screen']['completed'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
