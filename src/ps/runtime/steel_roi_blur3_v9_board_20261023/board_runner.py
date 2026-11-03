"""Sequential board acceptance: integrity, primitives, integration, paired timing."""
from datetime import datetime
import json
from pathlib import Path
import struct
import sys
import time
import traceback
import uuid
import xml.etree.ElementTree as ET
import cv2
import numpy as np
import acceptance_common as common
from integrated_runtime import Tail32Runtime
from roi_blur_runtime import RoiBlurRuntime
from blur3_model import blur3

HERE=Path(__file__).resolve().parent
_FAILED=False

def hardware_contract(path):
    tree=ET.parse(str(path))
    modules={n.get("INSTANCE"):n for n in tree.iter("MODULE")}
    addresses={n.get("INSTANCE"):int(n.get("BASEVALUE"),0) for n in tree.iter("MEMRANGE")
               if n.get("MEMTYPE")=="REGISTER"}
    wanted={"axi_dma_0":0x40400000,"axi_dma_sobel":0x40410000,"axi_dma_dark":0x40420000,
            "axi_dma_ellipse":0x40430000,"axi_dma_blur3":0x40440000,
            "gaussian_row_q30_0":0x43c00000,"ellipse7_0":0x43c10000,"blur3_0":0x43c20000}
    if any(addresses.get(k)!=v or k not in modules for k,v in wanted.items()):
        raise RuntimeError("HWH module/address contract differs")
    registers={n.get("NAME"):int(next(p.get("VALUE") for p in n.findall("PROPERTY")
               if p.get("NAME")=="ADDRESS_OFFSET"),0)
               for n in modules["blur3_0"].findall("./ADDRESSBLOCKS/ADDRESSBLOCK/REGISTERS/REGISTER")}
    if any(registers.get(k)!=v for k,v in {"CTRL":0,"height":16,"width":24,"status":32,"status_ctrl":36}.items()):
        raise RuntimeError("Blur3 register contract differs")
    wanted_params={"C_INCLUDE_SG":0,"C_SG_LENGTH_WIDTH":26,
                   "C_M_AXIS_MM2S_TDATA_WIDTH":32,"C_S_AXIS_S2MM_TDATA_WIDTH":32}
    params={n.get("NAME").upper():int(n.get("VALUE"),0)
            for n in modules["axi_dma_blur3"].findall("./PARAMETERS/PARAMETER")
            if n.get("NAME").upper() in wanted_params}
    if params!=wanted_params:raise RuntimeError("Blur3 DMA contract differs")
    return {"addresses":wanted,"registers":registers,"dma_parameters":params}

def initialize_runtime(base_root=None):
    for name in ("acceptance_common","integrated_runtime","gauss_tail",
                 "candidate_response","blur3_driver","blur3_model","roi_blur_runtime"):
        module=sys.modules.get(name)
        if module is not None and Path(module.__file__).resolve().parent!=HERE.resolve():
            raise RuntimeError("Module loaded from another package; restart kernel: "+name)
    result=common.initialize_runtime(base_root)
    result["hardware_contract"]=hardware_contract(HERE/"hardware/steel_roi_blur3.hwh")
    result["v9_bit_sha256"]=common.sha(HERE/"hardware/steel_roi_blur3.bit")
    result["v9_hwh_sha256"]=common.sha(HERE/"hardware/steel_roi_blur3.hwh")
    return result

def guard():
    if _FAILED:raise RuntimeError("Previous failure; reboot board and restart kernel")
    for p in HERE.parent.glob("*/hardware_session_guard.json"):
        previous=json.loads(p.read_bytes())
        if common.boot_id()=="unknown" or previous.get("boot_id")==common.boot_id():
            raise RuntimeError("Unfinished hardware session; reboot before retry: "+str(p))
    return HERE/"hardware_session_guard.json"

def fixtures():
    with (HERE/"fixtures.bin").open("rb") as f:
        count=struct.unpack("<I",f.read(4))[0]
        if count!=120:raise AssertionError("Expected 120 primitive fixtures")
        for index in range(count):
            h,w,n=struct.unpack("<III",f.read(12))
            wire=f.read(n);expected=f.read(h*w)
            if len(wire)!=n or len(expected)!=h*w:raise AssertionError("Truncated fixtures")
            padded=np.frombuffer(wire[:(h+18)*(w+18)],np.uint8).reshape(h+18,w+18)
            raw=padded[9:9+h,9:9+w].copy()
            yield index,raw,np.frombuffer(expected,np.uint8).reshape(h,w)
        if f.read(1):raise AssertionError("Unexpected fixture trailer")

def primitive_test(driver,save,report):
    for index,raw,frozen in fixtures():
        expected=cv2.GaussianBlur(raw,(0,0),3.)
        if not np.array_equal(expected,frozen) or not np.array_equal(blur3(raw),expected):
            raise AssertionError("Board OpenCV/model/frozen oracle disagree")
        actual,timing=driver.blur(raw)
        difference=int(np.count_nonzero(actual!=expected))
        report["primitives"].append(dict(timing,case=index,different_pixels=difference))
        if difference:raise AssertionError("Blur3 primitive mismatch: "+str(index))
        if (index+1)%20==0:
            print("BLUR3_PRIMITIVE %d/120 EXACT=True"%(index+1),flush=True);save()

def validate_result(a,result,gray,predictions,reference,features=False):
    a.check_result(result,predictions)
    if a.signature(result)!=reference:raise AssertionError("Runtime signature mismatch")
    diffs=a.feature_diffs(result,gray) if features else None
    if diffs and any(diffs.values()):raise AssertionError("Feature oracle mismatch")
    return diffs

def run(phase="smoke",base_root=None):
    global _FAILED
    if phase not in ("smoke","full"):raise ValueError("phase must be smoke/full")
    marker=guard()
    integrity=initialize_runtime(base_root)
    root=Path(integrity["source_package"])
    a,experiment=common.dependencies(root)
    full=a.load_cases("full");smoke=a.load_cases("smoke")
    if len(full)!=184 or sum(len(p) for _,_,p in full)!=403:raise AssertionError("Coverage differs")
    prior=json.loads((HERE/"evidence/gauss_tail32_full184.json").read_bytes())
    references={r["case"]:r["reference_signature"] for r in prior["frames"]+prior["supplemental"]}
    timing_names=("class_2","frame_19","frame_20","frame_21")
    extra=[c for c in full if c[0] in timing_names]
    samples=smoke+extra
    if len(samples)!=12:raise AssertionError("Smoke/timing coverage differs")
    out=HERE/"results"/("run_"+datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:8]+"_"+phase)
    out.mkdir(parents=True,exist_ok=False)
    r={"schema":"roi_blur3_system/1.0","phase":phase,"completed":False,"pass":False,
       "hardware_session_safe":False,"formal_release_replaced":False,"promotion_approved":False,
       "integrity":integrity,"opencv":cv2.__version__,"numpy":np.__version__,
       "boot_id":common.boot_id(),"board_clock_as_reported":datetime.now().isoformat(),
       "errors":[],"primitives":[],"old":[],"frames":[],"supplemental":[],"paired_timing":[],
       "performance":{"measured":False},"result_directory":str(out),
       "scope":"Tail32 on both routes; ellipse PS. New bitstream, optional PL sigma3 blur only.",
       "known_gaussian_float_differences_not_reclassified":["frame_143","boundary_scale259"]}
    report_file=out/"roi_blur3_system.json"
    def save():common.save_json(report_file,r)
    current=None;attempted=False
    try:
        cv2.setNumThreads(1)
        common.save_json(marker,{"boot_id":common.boot_id(),"result_directory":str(out)})
        attempted=True;save()
        # Smoke compares two actual bitstreams. Full compares to frozen board
        # signatures plus same-new-overlay PS/PL, avoiding 184 redundant old runs.
        if phase=="smoke":
            current=Tail32Runtime(experiment,root/"hardware/steel_binary_experiment.bit")
            for name,gray,predictions in samples:
                result=current.process_frame(gray,predictions)
                a.check_result(result,predictions)
                sig=a.signature(result)
                if name in references and sig!=references[name]:
                    raise AssertionError("Old runtime differs from qualified report: "+name)
                references[name]=sig
                r["old"].append({"case":name,"reference_signature":sig,"timing":result["timing"]})
                print("OLD "+name+" EXACT=True",flush=True);save()
            current.close();current=None
        current=RoiBlurRuntime(experiment,HERE/"hardware/steel_roi_blur3.bit")
        primitive_test(current.blur,save,r)
        cases=samples if phase=="smoke" else full
        def accept(name,gray,predictions,index):
            order=(False,True) if index%2==0 else (True,False)
            results={mode:current.process_frame(gray,predictions,mode) for mode in order}
            reference=references[name]
            for result in results.values():
                validate_result(a,result,gray,predictions,reference)
            diffs=a.feature_diffs(results[True],gray)
            if any(diffs.values()):raise AssertionError("Feature oracle mismatch")
            return {"case":name,"roi_count":len(predictions),"exact":True,
                    "feature_different_pixels":diffs,"reference_signature":reference,
                    "order":["pl" if m else "ps" for m in order],
                    "timing":{"pl" if m else "ps":result["timing"] for m,result in results.items()}}
        for index,(name,gray,predictions) in enumerate(cases):
            r["frames"].append(accept(name,gray,predictions,index))
            print("NEW %s %d/%d EXACT=True"%(name,index+1,len(cases)),flush=True);save()
        if phase=="full":
            for index,(name,gray,predictions) in enumerate(smoke):
                if name in ("boundary_scale259","frame_143","empty_predictions"):
                    r["supplemental"].append(accept(name,gray,predictions,index));save()
        else:
            # Six pairs for each of four representative class2 frames;
            # order alternates, warmup excluded, signatures checked every time.
            for name,gray,predictions in samples:
                if name not in timing_names:continue
                for mode in (False,True):
                    result=current.process_frame(gray,predictions,mode)
                    validate_result(a,result,gray,predictions,references[name])
                for repeat in range(6):
                    order=(False,True) if repeat%2==0 else (True,False)
                    results={m:current.process_frame(gray,predictions,m) for m in order}
                    for result in results.values():
                        validate_result(a,result,gray,predictions,references[name])
                    ps=results[False]["timing"]["end_to_end_static_pipeline_ms"]
                    pl=results[True]["timing"]["end_to_end_static_pipeline_ms"]
                    r["paired_timing"].append({"case":name,"repeat":repeat,
                        "order":["pl" if m else "ps" for m in order],
                        "ps_ms":ps,"pl_ms":pl,"speedup_ps_over_pl":ps/pl,"exact":True,
                        "timing":{"pl" if m else "ps":v["timing"] for m,v in results.items()}})
                    print("PAIR %s %d/6 PS=%.2fms PL=%.2fms SPEEDUP=%.4fx"%(name,repeat+1,ps,pl,ps/pl),flush=True);save()
            if len(r["paired_timing"])!=24:raise AssertionError("Incomplete paired timing")
            r["performance"]={"measured":True,
                "scope":"Pipeline timer includes PS preparation/cache/DMA/copy/postprocessing; excludes integer oracle, IO and overlay setup.",
                "cases":{name:{"pairs":6,
                    "paired_speedup_median":float(np.median([p["speedup_ps_over_pl"] for p in r["paired_timing"] if p["case"]==name])),
                    "wins":sum(p["pl_ms"]<p["ps_ms"] for p in r["paired_timing"] if p["case"]==name)}
                    for name in timing_names}}
        r["completed"]=True;r["pass"]=True
    except BaseException as exc:
        _FAILED=attempted
        r["errors"].append(type(exc).__name__+": "+str(exc))
        (out/"traceback.txt").write_text(traceback.format_exc(),encoding="utf-8")
        raise
    finally:
        if current is not None:
            try:current.close()
            except BaseException as exc:
                _FAILED=True;r["errors"].append("cleanup: "+repr(exc))
        r["hardware_session_safe"]=not _FAILED
        if _FAILED:r["pass"]=False
        save()
        if r["pass"] and marker.exists():marker.unlink()
        print("BLUR3_SYSTEM_PASS="+str(r["pass"]),flush=True)
        print("REPORT_FILE="+str(report_file),flush=True)
        print("当前 PL 为 v9 实验位流，正式版本文件未改动。",flush=True)
    return r

def rollback(base_root=None):
    guard()
    integrity=initialize_runtime(base_root)
    root=Path(integrity["source_package"])
    a,experiment=common.dependencies(root)
    marker=HERE/"hardware_session_guard.json"
    common.save_json(marker,{"boot_id":common.boot_id(),"action":"rollback"})
    runtime=None
    try:
        runtime=experiment.ExperimentRuntime(root/"baseline/board/steel_triple.bit",enable_ellipse=False)
        name,gray,predictions=next(c for c in a.load_cases("smoke") if c[0]=="class_2")
        result=runtime.process_frame(gray,predictions)
        a.check_result(result,predictions)
        if any(a.feature_diffs(result,gray).values()):raise AssertionError("Rollback features differ")
        runtime.close();runtime=None
        marker.unlink()
        print("ROLLBACK_VERIFIED=True")
    finally:
        if runtime is not None:runtime.close()
