"""Host-only regressions for direct notebooks, extraction, setup and build errors."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


extractor = module('steel_test_extract', REPO / 'board/extract_runtime.py')
environment = module('steel_test_environment', REPO / 'src/host/environment.py')


class DirectNotebookTests(unittest.TestCase):
    def test_static_saved_edited_notebook_runs_again_without_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'steel_static_demo_20261029').mkdir()
            nb = json.loads((REPO / 'board/run_static.ipynb').read_bytes())
            cell = nb['cells'][0]
            cell.update(execution_count=9, outputs=[{'output_type': 'stream', 'name': 'stdout', 'text': ['previous run']}])
            cell['metadata']['trusted'] = True
            cell['source'].append('print("local note")\n')
            path = root / 'run_static.ipynb'
            path.write_text(json.dumps(nb), encoding='utf-8')
            run = Mock(return_value=({}, root/'results/static_compare.json'))
            import sys
            with patch.object(Path, 'cwd', return_value=root), \
                 patch.dict(sys.modules, {'benchmark': types.SimpleNamespace(run=run)}), \
                 patch.object(sys, 'path', list(sys.path)), contextlib.redirect_stdout(io.StringIO()):
                for _ in range(2):
                    exec(''.join(json.loads(path.read_bytes())['cells'][0]['source']), {})
            self.assertEqual(run.call_count, 2)
            self.assertFalse((root/'delivery_manifest.json').exists())

    def test_missing_runtime_directory_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as temp:
            nb = json.loads((REPO/'board/run_static.ipynb').read_bytes())
            with patch.object(Path, 'cwd', return_value=Path(temp)), self.assertRaisesRegex(FileNotFoundError, 'extracted steel_board_runtime'):
                exec(''.join(nb['cells'][0]['source']), {})


class ExtractionTests(unittest.TestCase):
    def test_repeat_extraction_preserves_previous_results(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'steel_board_runtime.zip'
            with ZipFile(archive, 'w') as zipped:
                for name in ('run_static.ipynb', 'run_dynamic.ipynb'):
                    zipped.writestr('steel_board_runtime/' + name, 'fixture')
            with contextlib.redirect_stdout(io.StringIO()):
                first = extractor.extract(archive)
                (first / 'my_result.json').write_text('preserve this')
                second = extractor.extract(archive)
            self.assertNotEqual(first, second)
            self.assertEqual((first / 'my_result.json').read_text(), 'preserve this')

    def test_unsafe_zip_path_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / 'bad.zip'
            with ZipFile(archive, 'w') as zipped:
                zipped.writestr('steel_board_runtime/../../escape.py', 'bad')
            with self.assertRaisesRegex(ValueError, 'Unsafe ZIP'):
                extractor.extract(archive)


class EnvironmentTests(unittest.TestCase):
    def test_check_only_does_not_start_web(self):
        with patch.object(environment, 'ready_python', return_value=('python.exe', {'version': [3, 12, 9], 'bits': 64})), \
             patch.object(environment.subprocess, 'call') as launch, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(environment.main(['--check']), 0)
        self.assertIn('DEMO_ENV_READY=True', output.getvalue())
        launch.assert_not_called()

    def test_missing_environment_is_actionable_and_never_launches_web(self):
        with patch.object(environment, 'ready_python', return_value=(None, None)), \
             patch.object(environment.subprocess, 'call') as launch, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = environment.main(['--check'])
        self.assertEqual(result, 2)
        self.assertIn('3.13', output.getvalue())
        self.assertIn('3.12', output.getvalue())
        launch.assert_not_called()


class DynamicConfigurationTests(unittest.TestCase):
    def run_service_cell(self, root):
        notebook = json.loads((REPO / 'board/run_dynamic.ipynb').read_bytes())
        service = Mock(return_value={'safe': True})
        (root / 'steel_static_demo_20261029').mkdir(exist_ok=True)
        (root / 'steel_dynamic_demo_20261028').mkdir(exist_ok=True)
        import sys
        # Previously saved metadata, output and extra notes cannot block startup.
        notebook['cells'][0]['execution_count'] = 12
        notebook['cells'][0]['outputs'] = [{'output_type': 'stream', 'name': 'stdout', 'text': ['saved']}]
        notebook['cells'][0]['source'].append('print("local settings note")\n')
        with patch.dict(sys.modules, {'board_service': types.SimpleNamespace(serve=service)}), \
             patch.object(Path, 'cwd', return_value=root), patch.object(sys, 'path', list(sys.path)), \
             contextlib.redirect_stdout(io.StringIO()):
            exec(''.join(notebook['cells'][0]['source']), {})
        return service

    def test_defaults_need_no_notebook_edit(self):
        with tempfile.TemporaryDirectory() as temp:
            service = self.run_service_cell(Path(temp))
        service.assert_called_once_with(port=8767, max_frames=1000, idle_seconds=600)

    def test_external_settings_override_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'dynamic_settings.json').write_text(json.dumps({'max_frames': 10000, 'idle_seconds': 3600}))
            service = self.run_service_cell(root)
        service.assert_called_once_with(port=8767, max_frames=10000, idle_seconds=3600)

    def test_invalid_settings_block_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'dynamic_settings.json').write_text(json.dumps({'max_frames': -1}))
            with self.assertRaisesRegex(ValueError, 'max_frames'):
                self.run_service_cell(root)


@unittest.skipUnless(os.name == 'nt', 'PowerShell build driver is Windows-only')
class BuildDriverTests(unittest.TestCase):
    def test_missing_hls_export_blocks_vivado_even_after_zero_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vivado = root / 'vivado.bat'
            vitis = root / 'vitis-run.bat'
            dispatched = root / 'vivado_dispatched.txt'
            vivado.write_text('@echo off\nif "%~1"=="-version" goto version\n'
                              'echo unexpected>"' + str(dispatched) + '"\nexit /b 0\n'
                              ':version\necho vivado v2026.1\nexit /b 0\n')
            vitis.write_text('@echo off\nif "%~1"=="--version" goto version\n'
                             'echo STEEL_STAGE_PASS=gaussian\nexit /b 0\n'
                             ':version\necho vitis-run v2026.1\nexit /b 0\n')
            result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(REPO / 'build/build.ps1'), '-VivadoExe', str(vivado),
                                     '-VitisRunExe', str(vitis), '-OutputRoot', str(root / 'build')],
                                    capture_output=True, timeout=60)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'HLS export missing', result.stdout + result.stderr)
            self.assertFalse(dispatched.exists())

if __name__ == '__main__':
    unittest.main(verbosity=2)
