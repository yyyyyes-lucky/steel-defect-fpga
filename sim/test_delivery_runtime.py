"""Exercise real startup imports and retained contracts without loading FPGA hardware."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[1]


class AssemblyTests(unittest.TestCase):
    def test_assembly_copies_current_sources_without_hash_gates(self):
        spec = importlib.util.spec_from_file_location('pack_runtime', REPO/'board/package_runtime.py')
        pack = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pack)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'board').mkdir()
            (root/'board/extract_runtime.py').write_text('# extractor')
            (root/'changed.py').write_text('value = "current source"\n')
            (root/'board/runtime_map.json').write_text(json.dumps({'files': {
                'changed.py': {'source': 'changed.py', 'sha256': 'obsolete-record'}}}))
            # Exercise assembly without producing a board ZIP, as requested.
            stream = MagicMock()
            stream.__enter__.return_value.testzip.return_value = None
            with patch.object(pack.zipfile, 'ZipFile', return_value=stream):
                target, archive = pack.assemble(root/'output', repo=root)
            self.assertEqual((target/'changed.py').read_bytes(), (root/'changed.py').read_bytes())
            self.assertFalse(archive.exists())


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        mapping = json.loads((REPO/'board/runtime_map.json').read_bytes())
        for name, row in mapping['files'].items():
            target = cls.root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO/row['source'], target)
        # Simulate saved/edited sources with historical manifests deliberately untouched.
        for relative in (
            'steel_runtime_v10_candidate_20261025/runtime_v10.py',
            'steel_runtime_v9_1_qualified_20261023/runtime_v9_1.py',
            'steel_camera_q8_20261027/board_camera.py'):
            with (cls.root/relative).open('a', encoding='utf-8') as stream:
                stream.write('\n# Local edit after extraction.\n')

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def execute(self, code):
        prefix = ('from pathlib import Path\nimport sys\n'
                  'root=Path(sys.argv[1])\n'
                  'sys.path.insert(0,str(root/"steel_dynamic_demo_20261028"))\n')
        result = subprocess.run([sys.executable, '-B', '-c', prefix+code, str(self.root)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout+'\n'+result.stderr)

    def test_real_import_chain_ignores_changed_files_and_old_environment_versions(self):
        self.execute('''
import board_service
import cv2, numpy
cv2.__version__ = 'different-supported-build'
numpy.__version__ = 'different-supported-build'
q8, decode, entry, runtime_type, check = board_service.initialize_runtime()
state = entry.initialize_runtime()
assert state['integrity']['environment']['opencv'] == 'different-supported-build'
assert state['integrity']['hardware_contract']['addresses']['axi_dma_0'] == 0x40400000
assert 'pynq' not in sys.modules
''')

    def test_dynamic_startup_does_not_load_static_dataset(self):
        self.execute('''
import board_service
sys.path.insert(0,str(root/'steel_static_demo_20261029'))
import benchmark
def forbidden():
    raise AssertionError('Live startup tried to read static dataset')
benchmark.load_inputs = forbidden
board_service.initialize_runtime()
assert 'pynq' not in sys.modules
''')

    def test_static_initializer_still_loads_120_inputs(self):
        self.execute('''
sys.path.insert(0,str(root/'steel_static_demo_20261029'))
import benchmark
manifest, inputs, cases, q8, decode, entry = benchmark.initialize_runtime()
assert len(cases) == 120
assert sum(len(predictions) for _,_,predictions in cases) == 317
assert 'pynq' not in sys.modules
''')

    def test_rebuilt_identity_is_recorded_without_a_reference_hash_gate(self):
        # This is only an import/metadata test: altered bytes are never programmed.
        bit = self.root/'steel_gaussian_v10_board_20261023/hardware/steel_gaussian_v10.bit'
        old = bit.read_bytes()
        try:
            bit.write_bytes(old+b'metadata-test-only')
            expected = hashlib.sha256(bit.read_bytes()).hexdigest()
            self.execute('''
import board_service
_,_,entry,_,_ = board_service.initialize_runtime()
state = entry.initialize_runtime()
assert state['integrity']['new_bit_sha256'] == %r
assert 'pynq' not in sys.modules
''' % expected)
        finally:
            bit.write_bytes(old)

    def test_incompatible_hwh_is_still_rejected(self):
        hwh = self.root/'steel_gaussian_v10_board_20261023/hardware/steel_gaussian_v10.hwh'
        old = hwh.read_bytes()
        try:
            self.assertIn(b'axi_dma_blur3', old)
            hwh.write_bytes(old.replace(b'axi_dma_blur3', b'wrong_dma_blur3'))
            self.execute('''
import board_service
_,_,entry,_,_ = board_service.initialize_runtime()
try:
    entry.initialize_runtime()
except RuntimeError as exc:
    assert 'HWH module/address contract' in str(exc), str(exc)
else:
    raise AssertionError('Incompatible HWH was accepted')
''')
        finally:
            hwh.write_bytes(old)

    def test_response_accepts_new_bit_identity_and_rejects_wrong_frame(self):
        self.execute('''
import numpy as np
import board_service
import wire
request=wire.request_payload('a'*32,np.zeros((512,512),dtype=np.uint8),[])
value=board_service.make_response(request,{'records':[],'masks':[],'timing':{'pipeline_ms':1.0}},'new-build-identity')
assert value['bit_sha256']=='new-build-identity'
assert wire.decode_response(value,request)==[]
value['frame_id']='b'*32
try:
    wire.decode_response(value,request)
except ValueError as exc:
    assert 'frame_id' in str(exc)
else:
    raise AssertionError('Wrong frame accepted')
''')

    def test_windows_client_accepts_new_hardware_identity(self):
        self.execute('''
sys.path.insert(0, %r)
from unittest.mock import Mock
import numpy as np
import live, wire
client=live.Pipeline.__new__(live.Pipeline)
client.config={'board':'http://board:8767'}
client.session=Mock()
response=Mock()
response.raise_for_status=Mock()
health={'status':'ready','release':wire.RELEASE,'version':wire.VERSION,
        'mask_revision':wire.REVISION,'bit_sha256':'different-build'}
client.session.get.return_value.__enter__=Mock(return_value=response)
client.session.get.return_value.__exit__=Mock(return_value=False)
live.limited_json=lambda response:health
assert client.check_board()['bit_sha256']=='different-build'
request=wire.request_payload('a'*32,np.zeros((512,512),dtype=np.uint8),[])
value=dict(health,frame_id=request['frame_id'],image_sha256=request['image_sha256'],
           records=[],masks=[],pipeline_ms=1.0)
assert wire.decode_response(value,request)==[]
''' % str(REPO/'src/host'))

    def test_static_export_preserves_viewer_metadata(self):
        self.execute('''
import hashlib, json, re
import numpy as np
sys.path.insert(0,str(root/'steel_static_demo_20261029'))
from demo_support import frame_sources, evidence, make_html
gray=np.full((512,512),127,dtype=np.uint8)
row={'case':'demo_001','image_name':'crazing_example.jpg',
     'source_class_id':0,'source_split':'valid','gray_array_sha256':'old'}
case=('demo_001',gray,[])
sources=frame_sources({'frames':[row]},[case])
frame=evidence(case,{'cpu':{'records':[],'masks':[]}},None,sources[case[0]])
target=root/'metadata_export.html'
make_html(target,{'mode':'host_preview','frames_expected':1},[frame])
text=target.read_text(encoding='utf-8')
payload=json.loads(re.search(r'<script[^>]*id="evidence"[^>]*>(.*?)</script>',text,re.S).group(1))
source=payload['frames'][0]['source']
assert source['image_name']=='crazing_example.jpg'
assert source['source_class_id']==0 and source['source_split']=='valid'
assert source['gray_array_sha256']==hashlib.sha256(gray.tobytes()).hexdigest()
assert row['gray_array_sha256']=='old'
''')


if __name__ == '__main__':
    unittest.main(verbosity=2)
