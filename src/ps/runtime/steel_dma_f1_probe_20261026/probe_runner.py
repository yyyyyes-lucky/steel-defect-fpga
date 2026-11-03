"""Bounded new comparison and F1 cost screen on existing v10; never a full184 replay."""
import importlib
import hashlib
import inspect
import json
import math
import statistics
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent
Q8=ROOT.parent/'steel_q8_path_integration_20261025'
CASES=['class_0','class_2','class_5','empty_predictions']

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,value):
    temp=p.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2),encoding='utf-8');temp.replace(p)

def initialize_runtime():
    for name in ('integration_runner','integration_context','integration_kernels','generated_paths'):
        old=sys.modules.get(name)
        if old is not None and Path(old.__file__).resolve().parent!=Q8: raise RuntimeError('Conflicting Q8 module: restart kernel')
    for name in ('probe_context','profiled_driver','candidate_decode','f1_adapter','f1_budget'):
        old=sys.modules.get(name)
        if old is not None and Path(old.__file__).resolve().parent!=ROOT: raise RuntimeError('Conflicting probe module: restart kernel')
    sys.path.insert(0,str(Q8))
    integration=importlib.import_module('integration_runner')
    daily,state,integrity=integration.initialize_runtime()
    from inspect_pynq_cache import collect
    cache=collect()  # informational; source inspection alone does not authorize cache removal
    return daily,state,integration,{'probe_manifest_sha256':sha(ROOT/'manifest.json'),'q8':integrity,'pynq_source':cache}

def summarize(rows):
    measured=[r for r in rows if r['phase']=='measured']
    expected=[(b,v,n) for b,v in enumerate(('direct','staged','staged','direct')) for n in CASES]
    if [(r['block'],r['variant'],r['case']) for r in measured]!=expected: raise AssertionError('New comparison coverage')
    totals=[sum(r['pipeline_ms'] for r in measured if r['block']==b) for b in range(4)]
    direct=statistics.mean([totals[0],totals[3]]);staged=statistics.mean([totals[1],totals[2]])
    diagnostics=[r for r in rows if r['phase']=='instrumentation_check']
    perturbation=[]
    for block in range(2):
        pair=[r for r in diagnostics if r['block']==block]
        if len(pair)!=2:raise AssertionError('Missing timer perturbation pair')
        plain=next(r for r in pair if 'feature_detail_ms' not in r)
        profiled=next(r for r in pair if 'feature_detail_ms' in r)
        perturbation.append({'block':block,'plain_ms':plain['pipeline_ms'],'profiled_ms':profiled['pipeline_ms'],
                             'delta_pct':100*(profiled['pipeline_ms']/plain['pipeline_ms']-1)})
    return {'instrumentation_pairs':perturbation,'instrumentation_review_required':any(abs(r['delta_pct'])>5 for r in perturbation),
            'measured_frames':len(measured),'case_names':CASES,'block_totals_ms':totals,
            'direct_total_mean_ms':direct,'staged_total_mean_ms':staged,
            'four_case_reduction_pct':100*(1-staged/direct),
            'scope':'Four-case current-Q8-versus-staged comparison only; no full184 or camera gain claim.'}

def run():
    daily,state,integration,integrity=initialize_runtime()
    from probe_context import configuration
    from integration_context import components,restored
    from f1_budget import measure
    cases=state['args'].load_cases('smoke');refs=state['prior'].references()
    by_name={c[0]:c for c in cases}
    if any(n not in by_name or n not in refs for n in CASES):raise RuntimeError('Missing cases')
    directory=ROOT/'results'/('run_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]);directory.mkdir(parents=True)
    path=directory/'dma_f1_check.json'
    report={'schema':'dma_f1_probe/1.0','completed':False,'pass':False,'hardware_session_safe':False,
            'frames':[],'errors':[],'integrity':integrity,'cache_policy_changed':False,'overlay_loads':0,
            'new_bitstream_built':False,'f1_hardware_used':False,'formal_release_replaced':False,'f1_ps_budget':[]}
    session=None;previous=None;previous_signature=None
    def one(case,variant,phase,block,detailed=False,budget=False):
        nonlocal previous,previous_signature
        name,gray,predictions=case
        with configuration(session,variant,detailed):
            result=session.process_frame(gray,predictions,case=name,reference=refs[name],check_features=False)
        if not restored(session):raise AssertionError('Configuration not restored')
        integration.check_ownership(session,result)
        if previous is not None and state['args'].signature(previous)!=previous_signature: raise AssertionError('Retained output overwritten')
        _,driver,module=components(session)
        timing=result['timing']; pipeline=timing['end_to_end_static_pipeline_ms']
        row={'case':name,'variant':variant,'phase':phase,'block':block,'roi_count':len(predictions),'exact':True,'ownership_pass':True,
             'previous_retained':True if previous is not None else None,'pipeline_ms':pipeline,'feature_timing':timing['feature_timing']}
        if detailed:
            row['feature_detail_ms']=dict(driver._dma_f1_detail)
            if any(not math.isfinite(x) or x<0 for x in row['feature_detail_ms'].values()):raise AssertionError('Invalid detail timing')
        if 'active_driver' not in report:
            report['active_driver']={'channels':{n:type(d.sendchannel).__module__+'.'+type(d.sendchannel).__name__ for n,d in driver.dmas.items()},
             'buffers':[{'shape':list(b.shape),'dtype':str(b.dtype),'coherent':str(getattr(b,'coherent','unknown')),'cacheable':str(getattr(b,'cacheable','unknown'))} for b in driver.buffers]}
        report['frames'].append(row)
        if budget:
            report['f1_ps_budget'].append({'case':name,'diagnostic':measure(gray,predictions,result['features'],module)})
        previous=result;previous_signature=state['args'].signature(result)
        save(path,report);print('DMA_F1',len(report['frames']),phase,name,variant,'EXACT=True',flush=True)
    try:
        with daily.Runtime(purpose='dma_f1_new_path_probe') as session:
            if session.state['integrity']!=integrity['q8']['v10_integrity']: raise AssertionError('Hardware identity mismatch')
            report.update(overlay_loads=1,boot_id=session.report['boot_id'])
            for variant in ('direct','staged'):one(by_name['class_2'],variant,'warmup',-1)
            for block,variant in enumerate(('direct','staged','staged','direct')):
                for name in CASES: one(by_name[name],variant,'measured',block)
            # Existing returned raw features are used for new F1 PS budget, not a new stats DMA.
            # Two paired diagnostic orders quantify timer disturbance on the same case.
            for block,order in enumerate(((False,True),(True,False))):
                for detailed in order:one(by_name['class_0'],'direct','instrumentation_check',block,detailed=detailed)
            for case in cases:one(case,'direct','diagnostic',-1,detailed=True,budget=True)
            report['summary']=summarize(report['frames'])
            if len(report['frames'])!=31:raise AssertionError('Expected 31 bounded frames')
        if state['args'].signature(previous)!=previous_signature:raise AssertionError('Output changed after close')
        if not session.report['hardware_session_safe']:raise AssertionError('Unsafe cleanup')
        report.update(completed=True,hardware_session_safe=True,retained_after_close=True)
        report['pass']=True
    except BaseException as exc:
        report['errors'].append(type(exc).__name__+': '+str(exc));raise
    finally:
        if session is not None:report['session']=session.report
        save(path,report);print('REPORT_FILE='+str(path),flush=True)
    print('DMA_F1_PASS=True',flush=True)
    return report,path
