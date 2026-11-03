"""Focused tests for the new entry's lifecycle; kernel/runtime math unchanged."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import runtime_v9_1 as entry

class EntryTests(unittest.TestCase):
    def environment(self,root):
        names=["class_"+str(i) for i in range(6)]+["boundary_scale259","frame_143","empty_predictions"]
        cases=[(n,n,[] if n=="empty_predictions" else [{"class_id":int(n[-1]) if n.startswith("class_") else 4}]) for n in names]
        references={n:{"fixture":n} for n in names}
        created=[]
        class Hardware:
            def __init__(self,board,experiment,bitstream):created.append(self);self.closed=False;self.calls=[]
            def process_frame(self,gray,predictions,mode="combined"):
                if self.closed:raise RuntimeError("closed")
                self.calls.append((gray,mode))
                if mode!="combined":raise AssertionError("Daily entry must select the qualified combined configuration")
                return {"signature":references[gray],"records":predictions,
                        "timing":{"blur3_calls":[{} for p in predictions if p["class_id"]==2]}}
            def close(self):self.closed=True
        a=SimpleNamespace(load_cases=lambda phase:cases,
                          signature=lambda result:result["signature"],
                          check_result=lambda result,predictions:None,
                          feature_diffs=lambda result,gray:{"sobel":0,"std7_q8":0,"std13_q8":0})
        board=SimpleNamespace(common=SimpleNamespace(dependencies=lambda root:(a,None)),
                              cv2=SimpleNamespace(setNumThreads=lambda n:None),
                              RoiBlurRuntime=Hardware)
        integrity={"v9_integrity":{"source_package":str(root.parent/"old")}}
        return board,integrity,references,created

    def test_stability_single_overlay_and_saved_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"entry";root.mkdir()
            board,integrity,refs,created=self.environment(root)
            with patch.object(entry,"ROOT",root),patch.object(entry,"initialize_runtime",return_value=integrity),patch.object(entry,"dependencies",return_value=(root.parent/"board",board)),patch.object(entry,"CombinedRuntime",board.RoiBlurRuntime),patch.object(entry,"references",return_value=refs),patch.object(entry,"_boot_id",return_value="boot-a"),redirect_stdout(io.StringIO()):
                r=entry.run_stability(2)
                self.assertTrue(r["pass"] and r["hardware_session_safe"] and r["entrypoint_stability_pass"])
                self.assertEqual(len(r["frames"]),18)
                self.assertEqual(len(created),1)
                self.assertTrue(created[0].closed)
                self.assertEqual(created[0].calls[9][0],"empty_predictions")
                self.assertFalse((root/"hardware_session_guard.json").exists())
                saved=json.loads((Path(r["result_directory"])/"v9_1_stability.json").read_bytes())
                self.assertEqual(len(saved["frames"]),18)
                entry.run_demo()
                self.assertEqual(len(created),2)
                self.assertTrue(created[1].closed)

    def test_active_failed_and_rebooted_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"entry";root.mkdir()
            board,integrity,refs,created=self.environment(root)
            with patch.object(entry,"ROOT",root),patch.object(entry,"initialize_runtime",return_value=integrity),patch.object(entry,"dependencies",return_value=(root.parent/"board",board)),patch.object(entry,"CombinedRuntime",board.RoiBlurRuntime),patch.object(entry,"_boot_id",return_value="boot-a"):
                rt=entry.Runtime()
                with self.assertRaisesRegex(RuntimeError,"Active/unfinished"):entry.Runtime()
                with self.assertRaisesRegex(AssertionError,"Qualified output"):
                    with rt:rt.process_frame("class_2",[{"class_id":2}],reference={"wrong":1})
                self.assertTrue(created[0].closed)
                marker=root/"hardware_session_guard.json"
                self.assertTrue(marker.exists())
                entry._FAILED=False # Simulate kernel restart, not board reboot.
                with self.assertRaisesRegex(RuntimeError,"Active/unfinished"):entry.Runtime()
                with patch.object(entry,"_boot_id",return_value="boot-b"):
                    with entry.Runtime() as new:new.process_frame("class_2",[{"class_id":2}])
                self.assertFalse(marker.exists())
                self.assertEqual(len(list(root.glob("hardware_session_guard.previous_*.json"))),1)
        entry._FAILED=False

    def test_programming_failure_marker_and_close_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"entry";root.mkdir()
            board,integrity,refs,created=self.environment(root)
            with patch.object(entry,"ROOT",root),patch.object(entry,"initialize_runtime",return_value=integrity),patch.object(entry,"dependencies",return_value=(root.parent/"board",board)),patch.object(entry,"CombinedRuntime",board.RoiBlurRuntime),patch.object(entry,"_boot_id",return_value="boot-a"):
                with patch.object(entry,"CombinedRuntime",side_effect=RuntimeError("programming")):
                    with self.assertRaisesRegex(RuntimeError,"programming"):entry.Runtime()
                self.assertTrue((root/"hardware_session_guard.json").exists())
                entry._FAILED=False
                with patch.object(entry,"_boot_id",return_value="boot-b"):
                    rt=entry.Runtime()
                    with patch.object(rt.runtime,"close",side_effect=RuntimeError("DMA")):
                        with self.assertRaisesRegex(RuntimeError,"Unsafe cleanup"):rt.close()
                    r=json.loads((rt.result_directory/"v9_1_session.json").read_bytes())
                    self.assertFalse(r["pass"] or r["hardware_session_safe"])
                    self.assertTrue((root/"hardware_session_guard.json").exists())
        entry._FAILED=False

if __name__=="__main__":unittest.main(verbosity=2)
