"""Daily v9.1 software entry: qualified combined PS optimizations on v9 hardware."""
from datetime import datetime
import hashlib
import importlib
import json
from pathlib import Path
import sys
import uuid
from combined_runtime import CombinedRuntime

ROOT=Path(__file__).resolve().parent
VERSION="steel_runtime_v9_1_qualified_20261023"
BOARD_NAME="steel_roi_blur3_v9_board_20261023"
_FAILED=False

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def dependencies(board_root=None):
    root=(Path(board_root) if board_root else ROOT.parent/BOARD_NAME).resolve()
    names=("board_runner","roi_blur_runtime","blur3_driver","blur3_model",
           "acceptance_common","integrated_runtime","candidate_response","gauss_tail")
    for name in names:
        module=sys.modules.get(name)
        if module is not None and Path(module.__file__).resolve().parent!=root:
            raise RuntimeError("Conflicting package module; restart kernel: "+name)
    sys.path.insert(0,str(root))
    board=importlib.import_module("board_runner")
    return root,board

def initialize_runtime(board_root=None):
    for name in ("combined_runtime","candidates","gaussian_candidate"):
        if Path(sys.modules[name].__file__).resolve().parent!=ROOT.resolve():
            raise RuntimeError("Conflicting v9.1 module; restart kernel: "+name)
    root,board=dependencies(board_root)
    integrity=board.initialize_runtime()
    return {"version":VERSION,"entry_manifest_sha256":sha(ROOT/"manifest.json"),
            "entry_package":str(ROOT),"v9_integrity":integrity,
            "algorithm_acceptance":"Combined full184/403 plus 3 supplemental passed; representative 60/60 pairs faster",
            "software_version":"v9.1","hardware_version":"v9",
            "entrypoint_board_stability":"pending until this entry is exercised on board"}

def _save(path,data):
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(path)

def _boot_id():
    p=Path("/proc/sys/kernel/random/boot_id")
    return p.read_text().strip() if p.is_file() else "unknown"

def _guard(parent):
    if _FAILED:raise RuntimeError("Previous session failed; reboot board and restart kernel")
    boot=_boot_id()
    for marker in Path(parent).glob("*/hardware_session_guard.json"):
        previous=json.loads(marker.read_bytes())
        if boot=="unknown" or previous.get("boot_id")==boot:
            raise RuntimeError("Active/unfinished hardware session; reboot before retry: "+str(marker))

class Runtime:
    """Use with Runtime() as runtime; process_frame keeps all qualified checks."""
    def __init__(self,board_root=None,purpose="daily"):
        global _FAILED
        self.closed=self.failed=False
        self.runtime=None
        self.integrity=initialize_runtime(board_root)
        root,self.board=dependencies(board_root)
        _guard(ROOT.parent)
        if root.parent!=ROOT.parent:_guard(root.parent)
        self.a,self.experiment=self.board.common.dependencies(
            Path(self.integrity["v9_integrity"]["source_package"]))
        self.board.cv2.setNumThreads(1)
        self.result_directory=ROOT/"results"/("run_"+datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:8])
        self.result_directory.mkdir(parents=True,exist_ok=False)
        self.marker=ROOT/"hardware_session_guard.json"
        self.report={"schema":"steel_v9_1_session/1.0","version":VERSION,"purpose":purpose,
                     "completed":False,"pass":False,"hardware_session_safe":False,
                     "integrity":self.integrity,"boot_id":_boot_id(),
                     "formal_release_replaced":False,"new_bitstream_built":False,
                     "hardware_version":"v9","configuration":"combined","errors":[],"frames":[],
                     "result_directory":str(self.result_directory),
                     "timing_scope":"Existing pipeline timer; excludes model inference, disk IO and integer oracle. No new performance claim."}
        # An exclusive marker prevents two copies of this entry from opening together.
        if self.marker.exists():
            old=json.loads(self.marker.read_bytes())
            boot=_boot_id()
            if boot=="unknown" or old.get("boot_id")==boot:
                raise RuntimeError("Active/unfinished session; reboot board")
            self.marker.rename(ROOT/("hardware_session_guard.previous_"+uuid.uuid4().hex+".json"))
        with self.marker.open("x",encoding="utf-8") as f:
            json.dump({"boot_id":_boot_id(),"result_directory":str(self.result_directory)},f)
        try:
            self.save()
            self.runtime=CombinedRuntime(self.board,self.experiment,root/"hardware/steel_roi_blur3.bit")
        except BaseException as exc:
            self.failed=_FAILED=True
            self.report["errors"].append(type(exc).__name__+": "+str(exc))
            self.save()
            raise

    def save(self):
        _save(self.result_directory/"v9_1_session.json",self.report)

    def process_frame(self,gray,predictions,case="user_frame",reference=None):
        global _FAILED
        if self.closed or self.failed:raise RuntimeError("Session closed or failed")
        try:
            predictions=list(predictions)
            result=self.runtime.process_frame(gray,predictions,mode="combined")
            self.a.check_result(result,predictions)
            signature=self.a.signature(result)
            if reference is not None and signature!=reference:
                raise AssertionError("Qualified output differs: "+case)
            row={"case":case,"roi_count":len(predictions),"reference_checked":reference is not None,
                 "exact":True if reference is not None else None,
                 "signature":signature,"timing":result["timing"]}
            self.report["frames"].append(row)
            self.save()
            return result
        except BaseException as exc:
            self.failed=_FAILED=True
            self.report["errors"].append(type(exc).__name__+": "+str(exc))
            self.save()
            raise

    def close(self,aborted=False):
        global _FAILED
        if self.closed:return
        self.closed=True
        self.failed=self.failed or aborted
        close_error=None
        if self.runtime is not None:
            try:self.runtime.close()
            except BaseException as exc:
                close_error=exc;self.failed=True
                self.report["errors"].append("cleanup: "+repr(exc))
        if aborted:self.report["errors"].append("Session body interrupted or raised an exception")
        _FAILED=_FAILED or self.failed
        self.report["completed"]=not self.failed
        self.report["pass"]=not self.failed
        self.report["hardware_session_safe"]=not self.failed
        self.save()
        if not self.failed and self.marker.exists():self.marker.unlink()
        if close_error is not None:raise RuntimeError("Unsafe cleanup; reboot board") from close_error

    def __enter__(self):return self
    def __exit__(self,etype,exc,tb):
        self.close(aborted=etype is not None)
        return False

def references():
    r=json.loads((ROOT/"qualification/roi_blur3_smoke.json").read_bytes())
    return {x["case"]:x["reference_signature"] for x in r["frames"]}

def run_demo(case="class_2"):
    with Runtime(purpose="demo") as rt:
        choices={name:(gray,predictions) for name,gray,predictions in rt.a.load_cases("smoke")}
        if case not in choices:raise ValueError("Unknown demo case: "+case)
        gray,predictions=choices[case]
        result=rt.process_frame(gray,predictions,case,references()[case])
    print("DEMO_PASS=True")
    print("ROI_COUNT="+str(len(result["records"])))
    print("BLUR3_CALLS="+str(len(result["timing"]["blur3_calls"])))
    print("REPORT_FILE="+str(rt.result_directory/"v9_1_session.json"))
    return result

def run_stability(rounds=5):
    if isinstance(rounds,bool) or not isinstance(rounds,int) or not 2<=rounds<=100:
        raise ValueError("rounds must be an integer from 2 to 100")
    refs=references()
    with Runtime(purpose="stability") as rt:
        cases=rt.a.load_cases("smoke")
        if len(cases)!=9:raise AssertionError("Expected nine smoke cases")
        # One overlay and one allocation across all rounds; alternate order to
        # expose stale buffers/routing across categories and empty predictions.
        rt.report["stability"]={"requested_rounds":rounds,"expected_frames":rounds*9,
                               "overlay_loads":1,"continuous_session":True,
                               "scope":"Finite mixed-case repeat test, not a long-duration soak"}
        for repeat in range(rounds):
            ordered=cases if repeat%2==0 else cases[::-1]
            for name,gray,predictions in ordered:
                result=rt.process_frame(gray,predictions,name,refs[name])
                row=rt.report["frames"][-1];row["round"]=repeat+1
                differences=rt.a.feature_diffs(result,gray)
                row["feature_different_pixels"]=differences
                if any(differences.values()):raise AssertionError("Feature oracle differs")
                rt.save()
                print("STABILITY %d/%d %s EXACT=True"%(len(rt.report["frames"]),rounds*9,name),flush=True)
    target=rt.result_directory/"v9_1_stability.json"
    rt.report["entrypoint_stability_pass"]=rt.report["pass"]
    _save(target,rt.report)
    print("STABILITY_PASS="+str(rt.report["pass"]))
    print("REPORT_FILE="+str(target))
    return rt.report

def rollback():
    initialize_runtime()
    _guard(ROOT.parent)
    _,board=dependencies()
    board.rollback()
