import contextlib,importlib.util,io,json,tempfile,types,unittest,sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parent
Q8=ROOT.parent/'steel_q8_path_integration_20261025'
sys.path.append(str(Q8))
import test_integration as fixtures
import integration_context as q8
import integration_runner as integration
from probe_context import configuration
import probe_runner as runner
from f1_budget import load_scalar
from f1_adapter import scalar_from_ranks,descriptors_from_count

class ProbeTests(unittest.TestCase):
 def test_all_paths_cache_order_outputs_and_restore(self):
  image=np.arange(512*512,dtype=np.uint32).reshape(512,512).astype(np.uint8)
  for variant in ('direct','staged'):
   for detailed in (False,True):
    session,driver,module=fixtures.fixture()
    with configuration(session,variant,detailed): result=driver.process(image)
    self.assertTrue(q8.restored(session))
    np.testing.assert_array_equal(result[0],image)
    np.testing.assert_array_equal(result[1],image)
    np.testing.assert_array_equal(result[2],255-image)
    for name in ('sobel','q8'):
     events=driver.events
     self.assertEqual(events.count('flush:'+name+'_in'),1)
     self.assertEqual(events.count('invalidate:'+name),1)
     self.assertLess(events.index('wait:'+name+':recv'),events.index('length:'+name))
     self.assertLess(events.index('length:'+name),events.index('invalidate:'+name))
    if detailed:
     self.assertEqual(len(driver._dma_f1_detail),18)
     self.assertTrue(all(v>=0 for v in driver._dma_f1_detail.values()))
     self.assertLessEqual(sum(driver._dma_f1_detail.values()),result[3]['prepare_dma_and_copy_ms']+.01)
    held=[a.copy() for a in result[:3]]
    with configuration(session,variant,detailed): driver.process(np.zeros_like(image))
    for a,b in zip(held,result[:3]):np.testing.assert_array_equal(a,b)
 def test_error_and_timeout_do_not_decode_or_hide_failure(self):
  for error in ('length','dma','timeout'):
   session,driver,_=fixtures.fixture()
   if error=='length':driver.bad_length=True
   if error=='dma':driver.status=0x72
   if error=='timeout':driver.status=0
   with self.assertRaises((RuntimeError,TimeoutError)):
    with configuration(session,'staged',True):driver.process(np.zeros((512,512),np.uint8),timeout_seconds=.00001)
   self.assertTrue(driver.failed);self.assertTrue(q8.restored(session))
   self.assertNotIn('invalidate:q8',driver.events)
 def test_gray_rank_adapter_matches_current_float_semantics(self):
  scalar=load_scalar()['scalar_histogram'];rng=np.random.RandomState(42)
  for shape in ((1,1),(3,7),(128,129),(512,512)):
   for a in (np.zeros(shape,np.uint8),np.full(shape,255,np.uint8),rng.randint(0,256,shape).astype(np.uint8)):
    c=np.cumsum(np.bincount(a.ravel(),minlength=256));pos=(a.size-1)*np.array([.5,.1,.9,.95])
    ends=np.searchsorted(c,np.stack([np.floor(pos),np.ceil(pos)],axis=1).astype(np.int64),side='right')
    self.assertEqual(scalar(a),scalar_from_ranks(a,ends))
 def test_descriptor_has_no_edge_array_scan(self):
  import inspect,ast
  tree=ast.parse(inspect.getsource(descriptors_from_count))
  calls=[ast.unparse(n) for n in ast.walk(tree) if isinstance(n,ast.Call)]
  self.assertFalse(any('sobel >=' in x for x in calls))
  self.assertFalse(any(x.startswith('descriptors(') for x in calls))
 def test_bounded_runner_and_failure_report(self):
  names=integration.CASE_NAMES
  for fail_at in (None,3):
   session,driver,module=fixtures.fixture();records=[]
   cases=[(n,np.full((512,512),i,np.uint8),[]) for i,n in enumerate(names)]
   args=types.SimpleNamespace(load_cases=lambda _:cases,signature=lambda v:tuple(a.tobytes() for a in v['features'].values()))
   state={'args':args,'prior':types.SimpleNamespace(references=lambda:{n:None for n in names}),'integrity':{}}
   class Runtime:
    def __init__(self,**kw):
     self.state=dict(session.state);self.state.update(state);self.runtime=session.runtime
     self.report={'boot_id':'mock','hardware_session_safe':False,'frames':[]}
    def __enter__(self):return self
    def __exit__(self,typ,exc,tb):
     self.report['hardware_session_safe']=typ is None
     for b in driver.buffers:b.fill(0)
    def process_frame(self,gray,predictions,**kw):
     if len(records)+1==fail_at:raise RuntimeError('Injected failure')
     a,b,c,t=driver.process(gray);records.append(1)
     return {'features':{'sobel':a,'std7_q8':b,'std13_q8':c},'timing':{'end_to_end_static_pipeline_ms':100.0,'feature_timing':t}}
   with tempfile.TemporaryDirectory() as directory:
    with patch.object(runner,'ROOT',Path(directory)),patch.object(runner,'initialize_runtime',return_value=(types.SimpleNamespace(Runtime=Runtime),state,integration,{'q8':{'v10_integrity':{}}})),patch('f1_budget.measure',return_value={'mock':True}) as measure,contextlib.redirect_stdout(io.StringIO()):
     if fail_at:
      with self.assertRaisesRegex(RuntimeError,'Injected'):runner.run()
     else:
      report,path=runner.run();self.assertTrue(report['pass']);self.assertEqual(len(report['frames']),31);self.assertEqual(measure.call_count,9)
    paths=list(Path(directory).rglob('dma_f1_check.json'));self.assertEqual(len(paths),1)
    saved=json.loads(paths[0].read_text())
    self.assertEqual(saved['completed'],fail_at is None)
    if fail_at:self.assertTrue(saved['errors'])
    self.assertTrue(q8.restored(session))

if __name__=='__main__':unittest.main(verbosity=2)
