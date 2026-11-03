"""Integration-only host tests. DMA is mocked, never board qualification."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import integration_context as context
import integration_runner as runner

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load('host_integration_base', PROJECT / 'steel_binary_ops_v8_experiment_20261022/baseline/steel_defect_runtime.py')
Driver = base._features_module.DualFeatures


class Buffer(np.ndarray):
    def __new__(cls, shape, dtype, events, name):
        value = np.zeros(shape, dtype).view(cls)
        value.events, value.label = events, name
        return value

    def flush(self):
        self.events.append('flush:' + self.label)

    def invalidate(self):
        self.events.append('invalidate:' + self.label)


def fixture():
    driver = Driver.__new__(Driver)
    driver.closed = driver.failed = driver.in_flight = False
    driver.events = []
    driver.status = 2
    driver.bad_length = False
    driver.buffers = [Buffer(shape, dtype, driver.events, name) for shape, dtype, name in (
        ((512, 512), np.uint8, 'sobel_in'), ((512, 512), np.uint8, 'sobel'),
        ((524, 524), np.uint8, 'q8_in'), ((512, 512), np.uint32, 'q8'))]
    driver.sobel_in, driver.sobel_out, driver.q8_in, driver.q8_out = driver.buffers
    class Channel:
        def __init__(self, name, direction):
            self.name, self.direction = name, direction
        def transfer(self, buffer):
            driver.events.append('transfer:%s:%s' % (self.name, self.direction))
        def wait(self):
            driver.events.append('wait:%s:%s' % (self.name, self.direction))
            if self.direction == 'recv':
                image = np.asarray(driver.sobel_in)
                if self.name == 'sobel':
                    driver.sobel_out[:] = image
                else:
                    driver.q8_out[:] = image.astype(np.uint32) | ((255-image).astype(np.uint32) << 16)
    class MMIO:
        def __init__(self, name):
            self.name = name
        def read(self, address):
            if address == 0x58:
                driver.events.append('length:' + self.name)
                return 0 if driver.bad_length else 512*512*(4 if self.name == 'q8' else 1)
            return driver.status
    driver.dmas = {name: SimpleNamespace(sendchannel=Channel(name, 'send'), recvchannel=Channel(name, 'recv'), mmio=MMIO(name)) for name in ('sobel', 'q8')}
    experiment = SimpleNamespace(LOCK=threading.RLock(), base=base)
    session = SimpleNamespace(state={'experiment': experiment}, runtime=SimpleNamespace(runtime=SimpleNamespace(tail=SimpleNamespace(base=SimpleNamespace(features=driver)))))
    return session, driver, base._fast._runtime


def clean_records(records):
    return [{k:v for k,v in row.items() if k != 'processing_time_ms'} for row in records]


class IntegrationTests(unittest.TestCase):
    def test_real_driver_body_decode_after_wait_length_and_invalidate(self):
        session, driver, _ = fixture()
        gray = np.arange(512*512, dtype=np.uint32).reshape(512,512).astype(np.uint8)
        old = driver.process(gray)
        helper = context.decode_owned_planes
        def observed(buffer):
            for event in ('wait:q8:recv', 'length:q8', 'invalidate:q8'):
                self.assertIn(event, driver.events)
            self.assertFalse(driver.in_flight)
            driver.events.append('decode')
            return helper(buffer)
        driver.events.clear()
        with patch.object(context, 'decode_owned_planes', observed), context.configuration(session, 'candidate'):
            new = driver.process(gray)
        self.assertTrue(context.restored(session))
        self.assertEqual(set(old[3]), set(new[3]))
        for a,b in zip(old[:3], new[:3]):
            np.testing.assert_array_equal(a,b)
        self.assertLess(driver.events.index('invalidate:q8'), driver.events.index('decode'))
        held = [a.copy() for a in new[:3]]
        with context.configuration(session, 'candidate'):
            driver.process(np.zeros_like(gray))
        for a,b in zip(held,new[:3]):
            np.testing.assert_array_equal(a,b)
        runner.check_ownership(session, {'features':dict(zip(('sobel','std7_q8','std13_q8'),new[:3]))})

    def test_context_restores_on_exception_and_rejects_nesting(self):
        session, driver, module = fixture()
        original = module.process_frame
        with self.assertRaisesRegex(ValueError, 'body'):
            with context.configuration(session, 'candidate'):
                self.assertIs(module.process_frame.__globals__, original.__globals__)
                with self.assertRaisesRegex(RuntimeError, 'already active'):
                    with context.configuration(session, 'candidate'):
                        pass
                raise ValueError('body')
        self.assertIs(module.process_frame, original)
        self.assertNotIn('process', driver.__dict__)
        self.assertTrue(context.restored(session))

    def test_invalid_input_and_busy_driver_do_not_start_dma(self):
        session, driver, _ = fixture()
        with context.configuration(session, 'candidate'):
            with self.assertRaises(ValueError):
                driver.process(np.zeros((1,1),np.uint8))
        self.assertEqual(driver.events, [])
        driver.in_flight = True
        with self.assertRaisesRegex(RuntimeError, 'busy'):
            with context.configuration(session, 'candidate'):
                pass

    def test_dma_error_timeout_and_length_keep_original_failure_state(self):
        gray = np.zeros((512,512),np.uint8)
        for failure in ('error','timeout','length'):
            session, driver, _ = fixture()
            driver.status = 0x10 if failure == 'error' else 0 if failure == 'timeout' else 2
            driver.bad_length = failure == 'length'
            with patch.object(context, 'decode_owned_planes', side_effect=AssertionError('decode must not run')) as decode:
                with self.assertRaises((RuntimeError,TimeoutError)):
                    with context.configuration(session,'candidate'):
                        driver.process(gray, timeout_seconds=1e-9)
                self.assertEqual(decode.call_count,0)
            self.assertTrue(driver.failed)
            self.assertEqual(driver.in_flight, failure != 'length')
            self.assertTrue(context.restored(session))

    def test_decode_exception_marks_failed_and_restores(self):
        session, driver, _ = fixture()
        with patch.object(context,'decode_owned_planes',side_effect=ValueError('decode failure')):
            with self.assertRaisesRegex(ValueError,'decode failure'):
                with context.configuration(session,'candidate'):
                    driver.process(np.zeros((512,512),np.uint8))
        self.assertTrue(driver.failed)
        self.assertFalse(driver.in_flight)
        self.assertTrue(context.restored(session))

    def test_roi_body_preserves_all_six_branches_and_dynamic_globals(self):
        session, _, module = fixture()
        gray = np.random.RandomState(31).randint(0,256,(512,512)).astype(np.uint8)
        maps = (np.full((512,512),10,np.float32), np.full((512,512),20,np.float32))
        predictions = [{'class_id':c,'detection_index':c,'confidence':0.8,'box_xyxy_pixel':[64,64,128,128]} for c in range(6)]
        kwargs = {'sobel_frame':gray.copy(),'local_std_frames':maps}
        # Existing v9.1 callbacks must still be visible to the generated function.
        with patch.object(sys, 'path', [str(PROJECT/'steel_runtime_v9_1_qualified_20261023')] + sys.path):
            callbacks = load('host_q8_v91_callbacks', PROJECT/'steel_runtime_v9_1_qualified_20261023/candidates.py')
        with patch.object(module,'scalar_stats',callbacks.scalar_histogram), patch.object(module,'orientation_metrics',callbacks.orientation_int16):
            old = module.process_frame(gray,predictions,**kwargs)
            with context.configuration(session,'candidate'):
                new = module.process_frame(gray,predictions,**kwargs)
        self.assertEqual(clean_records(old[0]),clean_records(new[0]))
        for a,b in zip(old[1],new[1]):
            self.assertEqual(set(a),set(b))
            for key in a:
                np.testing.assert_array_equal(a[key],b[key])
        self.assertEqual(set(old[2]['full_frame_stages_ms']),set(new[2]['full_frame_stages_ms']))
        bad = maps[0].copy(); bad[1,1] = np.nan
        with context.configuration(session,'candidate'):
            with self.assertRaisesRegex(ValueError,'finite nonnegative'):
                module.process_frame(gray,predictions,local_std_frames=(bad,maps[1]))
            empty = module.process_frame(gray,[],local_std_frames=(bad,maps[1]))
        self.assertEqual(empty[0],[])
        self.assertTrue(context.restored(session))

    def test_summary_rejects_missing_duplicate_and_wrong_order(self):
        rows = []
        for block,variant in enumerate(('baseline','candidate','candidate','baseline')):
            for name in runner.CASE_NAMES:
                rows.append({'phase':'measured','block':block,'variant':variant,'case':name,
                             'stages':{'pipeline_ms':100 if variant=='baseline' else 80,
                                       'feature_prepare_dma_copy_ms':20,'map_validation_ms':10}})
        summary = runner.summarize(rows)
        self.assertAlmostEqual(summary['nine_case_total_reduction_pct'],20)
        self.assertEqual(summary['candidate_faster_pairs'],18)
        for malformed in (rows[:-1], rows[:-1]+[rows[0]], rows[::-1]):
            with self.assertRaises(AssertionError): runner.summarize(malformed)

    def test_exercise_orders_checks_and_stops_on_retained_output_corruption(self):
        for corrupt in (False,True):
            session, driver, _ = fixture()
            cases = [(name,np.full((512,512),i,np.uint8),[]) for i,name in enumerate(runner.CASE_NAMES)]
            signatures = {name:{'marker':i} for i,(name,_,_) in enumerate(cases)}
            returned = []
            def signature(value): return {'marker':value['marker']}
            def process(gray,predictions,case,reference,check_features):
                self.assertFalse(check_features)
                if corrupt and returned: returned[-1]['marker'] = -1
                value = {'marker':signatures[case]['marker'], 'timing':{
                    'end_to_end_static_pipeline_ms':100,
                    'feature_timing':{'prepare_dma_and_copy_ms':20},
                    'roi_runtime_timing':{'full_frame_stages_ms':{'local_std_sigma4_ms':10}}}}
                returned.append(value)
                session.report['frames'].append({})
                return value
            session.state.update(args=SimpleNamespace(load_cases=lambda phase:cases,signature=signature),
                                 prior=SimpleNamespace(references=lambda:signatures))
            session.process_frame=process; session.report={'frames':[]}; session.save=lambda:None
            report={'frames':[]}
            with patch.object(runner,'check_ownership'), redirect_stdout(io.StringIO()):
                if corrupt:
                    with self.assertRaisesRegex(AssertionError,'overwrote'):
                        runner.exercise(session,report,lambda:None)
                    self.assertEqual(len(returned),2)
                else:
                    runner.exercise(session,report,lambda:None)
                    self.assertEqual(len(report['frames']),38)
                    self.assertEqual(report['retained_output_checks_before_close'],37)
            self.assertTrue(context.restored(session))

    def test_report_success_requires_safe_close_and_saves_failures(self):
        for mode in ('success', 'body_failure', 'close_failure'):
            class Session:
                def __init__(self):
                    self.report={'boot_id':'mock', 'hardware_session_safe':False, 'frames':[]}
                    self.path=Path('mock_session.json')
                    self.state={'integrity':{'locked':True}, 'args':SimpleNamespace(signature=lambda value:'expected')}
                    self.aborted=None
                def __enter__(self): return self
                def __exit__(self, etype, value, traceback):
                    self.aborted=etype is not None
                    if mode=='close_failure': raise RuntimeError('close failed')
                    self.report['hardware_session_safe']=not self.aborted
                    return False
            session=Session()
            daily=SimpleNamespace(Runtime=lambda purpose:session)
            integrity={'v10_integrity':{'locked':True}}
            def exercise(session, report, save):
                if mode=='body_failure': raise AssertionError('body failed')
                report['frames']=[{}]*38
                report['summary']={'nine_case_total_reduction_pct':5}
                return {},'expected'
            with tempfile.TemporaryDirectory() as directory, patch.object(runner,'ROOT',Path(directory)), \
                    patch.object(runner,'initialize_runtime',return_value=(daily,None,integrity)), \
                    patch.object(runner,'exercise',side_effect=exercise), redirect_stdout(io.StringIO()):
                if mode=='success':
                    report,path=runner.run()
                    self.assertTrue(report['pass'] and report['hardware_session_safe'])
                    self.assertEqual(report['checked_frames'],38)
                else:
                    with self.assertRaises((AssertionError,RuntimeError)): runner.run()
                files=list(Path(directory).rglob('q8_path_integration_check.json'))
                self.assertEqual(len(files),1)
                saved=json.loads(files[0].read_bytes())
                self.assertEqual(saved['pass'],mode=='success')
                self.assertEqual(saved['hardware_session_safe'],mode=='success')
                self.assertEqual(bool(saved['errors']),mode!='success')
                self.assertEqual(saved['overlay_loads'],1)
            self.assertEqual(session.aborted,mode=='body_failure')


if __name__ == '__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(IntegrationTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    output=PROJECT/'q8_path_integration_build_20261025/host_qa.json'
    value={'pass':result.wasSuccessful(),'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
           'board_access_performed':False,'hardware_mocked':True,
           'tested_files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(ROOT.iterdir()) if p.is_file()}}
    with output.open('x',encoding='utf-8') as stream: json.dump(value,stream,indent=2)
    sys.exit(0 if result.wasSuccessful() else 1)
