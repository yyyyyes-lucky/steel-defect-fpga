"""F1 PS replacement budget only. Integer payload generation is outside timing.
Does not install the adapter or pretend synthetic statistics came from FPGA.
"""
import ast
import json
import statistics
import time
import types
from pathlib import Path
import cv2
import numpy as np
import f1_adapter

PROJECT=Path(__file__).resolve().parent.parent

def load_scalar():
    file=PROJECT/'steel_runtime_v9_1_qualified_20261023/candidates.py'
    tree=ast.parse(file.read_text(encoding='utf-8'))
    ns={'np':np,'cv2':cv2}
    for name in ('scalar_histogram','orientation_int16'):
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(file),'exec'),ns)
    return ns

def paired(old,new):
    old();new()
    rows=[]
    for order in (('old','new'),('new','old'),('old','new'),('new','old')):
        row={}
        for name in order:
            begin=time.perf_counter();(old if name=='old' else new)()
            row[name]=(time.perf_counter()-begin)*1000
        rows.append(row)
    return {'pairs_ms':rows,'median_saved_ms':statistics.median(r['old']-r['new'] for r in rows)}

def measure(gray,predictions,features,module):
    refs=load_scalar(); scalar=refs['scalar_histogram']; ns=dict(module.__dict__)
    ns['orientation_metrics']=refs['orientation_int16']
    descriptors=types.FunctionType(module.descriptors.__code__,ns)
    adapter=types.FunctionType(f1_adapter.descriptors_from_count.__code__,ns)
    maps=[cv2.multiply(features[k],1/256.0,dtype=cv2.CV_32F) for k in ('std7_q8','std13_q8')]
    rows=[]
    for index,prediction in enumerate(predictions):
        bounds=module.roi_bounds(prediction['box_xyxy_pixel']); x1,y1,x2,y2=bounds
        roi=gray[y1:y2,x1:x2]; sobel=features['sobel'][y1:y2,x1:x2]
        std7,std13=[a[y1:y2,x1:x2] for a in maps]
        counts=np.bincount(roi.ravel(),minlength=256);cdf=np.cumsum(counts,dtype=np.int64)
        positions=(roi.size-1)*np.array([.5,.1,.9,.95],np.float64)
        ranks=np.stack([np.floor(positions),np.ceil(positions)],axis=1).astype(np.int64)
        endpoints=np.searchsorted(cdf,ranks,side='right').astype(np.uint8)
        edges=int(np.count_nonzero(sobel>=64))
        if scalar(roi)!=f1_adapter.scalar_from_ranks(roi,endpoints): raise AssertionError('F1 gray mismatch')
        if descriptors(roi,std7,std13,sobel)!=adapter(roi,std7,std13,sobel,edges): raise AssertionError('F1 descriptor mismatch')
        gray_cost=paired(lambda:scalar(roi),lambda:f1_adapter.scalar_from_ranks(roi,endpoints))
        edge_cost=paired(lambda:round(float(np.mean(sobel>=64)),6),lambda:round(float(edges/sobel.size),6))
        rows.append({'roi':index,'bounds':bounds,'pixels':roi.size,'gray_adapter_exact':True,'descriptor_adapter_exact':True,
                     'gray_replacement_budget':gray_cost,'edge_scan_budget':edge_cost,
                     'endpoint_ranks':ranks.tolist(),'endpoint_values':endpoints.tolist(),'edge_count':edges})
    return {'rows':rows,'sum_median_saved_ms':sum(r['gray_replacement_budget']['median_saved_ms']+r['edge_scan_budget']['median_saved_ms'] for r in rows),
            'scope':'Ordinary ARM PS microdiagnostic; excludes future ROI control writes, result reads, backpressure and kernel costs. Not F1 hardware speedup.',
            'statistics_source':'PS reference model, not PL','new_hardware_access':False}
