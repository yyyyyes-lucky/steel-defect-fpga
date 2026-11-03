"""Host checks of experiment lifecycle, failure handling and comparison design."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "board_src"))
import gaussian_v10_runner as runner


class Tests(unittest.TestCase):
    def environment(self, root, fault=None):
        def pred(name):
            cls = int(name[-1]) if name.startswith("class_") else 2
            return [] if name == "empty_predictions" else [{"class_id": cls}]
        names = ["class_" + str(i) for i in range(6)] + [
            "boundary_scale259", "frame_143", "empty_predictions", "frame_19", "frame_20", "frame_21"]
        samples = [(n, n, pred(n)) for n in names]
        full = [(f"full_{i}", f"full_{i}", [{"class_id": 2}] * (3 if i < 35 else 2)) for i in range(184)]
        extra = samples[6:9]
        refs = {n: {"name": n} for n, _, _ in full + samples}
        created = []
        class Runtime:
            def __init__(self, board, experiment, bit):
                self.hw = "new" if "v10" in str(bit) else "old"
                self.closed = False
                created.append(self)
            def process_frame(self, gray, predictions, mode):
                assert mode == "combined" and not self.closed
                pl = any(p["class_id"] in (2, 4) for p in predictions)
                t = {"actual_mode": "pl_gaussian" if pl else "arm_float", "fallback_reason": None,
                     "pl_integer_model_different_pixels": 0 if pl else None,
                     "combined_configuration": "combined",
                     "blur3_calls": [{"status": 0} for p in predictions if p["class_id"] == 2],
                     "end_to_end_static_pipeline_ms": 8 if self.hw == "new" else 10,
                     "gaussian_response_ms": 3 if pl else 0}
                if fault == "oracle" and self.hw == "new" and pl:
                    t["pl_integer_model_different_pixels"] = 1
                if fault == "timeout" and self.hw == "new":
                    raise TimeoutError("injected DMA timeout")
                if fault == "fallback" and self.hw == "new":
                    t["fallback_reason"] = "injected"
                return {"signature": refs[gray], "timing": t}
            def close(self):
                self.closed = True
                if fault == "close":
                    raise RuntimeError("injected unsafe cleanup")
        def save(path, value):
            path.write_text(json.dumps(value), encoding="utf-8")
        entry = SimpleNamespace(_guard=lambda p: None, _boot_id=lambda: "boot-test",
                                _save=save, CombinedRuntime=Runtime)
        a = SimpleNamespace(check_result=lambda *args: None,
                            signature=lambda v: v["signature"],
                            feature_diffs=lambda *args: {"sobel": 0, "std7": 0, "std13": 0})
        return entry, a, (full, samples, extra), refs, created

    def invoke(self, root, phase, fault=None):
        entry, a, data, refs, created = self.environment(root, fault)
        runner._FAILED = False
        with patch.object(runner, "ROOT", root), patch.object(runner, "initialize_runtime", return_value={}), \
             patch.object(runner, "dependencies", return_value=(entry, root / "v9", None, a, None)), \
             patch.object(runner, "datasets", return_value=data), \
             patch.object(runner, "references", return_value=refs), \
             patch.object(runner, "primitives", return_value=[{"exact": True}] * 24), \
             redirect_stdout(io.StringIO()):
            value = runner.run(phase)
        return value, created

    def test_smoke_abba_and_safe_old_hardware(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r, runtimes = self.invoke(root, "smoke")
            self.assertTrue(r["pass"] and r["hardware_session_safe"])
            self.assertEqual([x.hw for x in runtimes], ["old", "new", "new", "old"])
            self.assertTrue(all(x.closed for x in runtimes))
            self.assertEqual(len(r["frames"]), 24)
            self.assertEqual(len(r["timing_samples"]), 72)
            self.assertEqual(r["last_loaded_hardware"], "old")
            self.assertFalse((root / "hardware_session_guard.json").exists())
            for p in r["performance"].values():
                self.assertEqual(p["wins"], 6)
                self.assertEqual(p["speedup_median"], 1.25)
                self.assertEqual(p["saved_ms_by_order"], {"old_first": 2, "new_first": 2})

    def test_full_and_rollback(self):
        for phase, count, hw in (("full", 187, "new"), ("rollback", 1, "old")):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                r, created = self.invoke(Path(tmp), phase)
                self.assertTrue(r["pass"])
                self.assertEqual(len(r["frames"]), count)
                self.assertEqual(len(created), 1)
                self.assertEqual(r["last_loaded_hardware"], hw)

    def test_faults_never_pass_or_remove_guard(self):
        for fault in ("timeout", "oracle", "fallback", "close"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with self.assertRaises((RuntimeError, AssertionError, TimeoutError)):
                    self.invoke(root, "smoke", fault)
                self.assertTrue((root / "hardware_session_guard.json").exists())
                r = json.loads(next(root.glob("results/*/gaussian_v10_acceptance.json")).read_bytes())
                self.assertFalse(r["pass"] or r["completed"] or r["hardware_session_safe"])
                self.assertTrue(r["errors"] and runner._FAILED)
                with self.assertRaisesRegex(RuntimeError, "Previous failed"):
                    runner.run("smoke")
        runner._FAILED = False

    def test_real_fixtures_and_tamper(self):
        sys.path.insert(0, str(HERE.parent / "steel_runtime_v9_1_qualified_20261023"))
        import combined_runtime
        sys.path.insert(0, str(HERE.parent / "steel_binary_ops_v8_experiment_20261022/baseline/board"))
        from fixed_dark_response import kernel
        class Driver:
            def row_pass(self, center, mode):
                coeff = kernel((1., 1.5, 7., 25.)[mode], 30)
                radius = len(coeff) // 2
                padded = np.pad(center, ((0, 0), (radius, radius)), mode="reflect")
                out = np.stack([((np.convolve(row.astype(np.int64), coeff, "valid") + (1 << 29)) >> 30)
                                for row in padded]).astype(np.uint32)
                return out, {}
        from contextlib import nullcontext
        rt = SimpleNamespace(runtime=SimpleNamespace(tail=SimpleNamespace(
            base=SimpleNamespace(gaussian=Driver()))))
        with patch.object(runner, "ROOT", HERE / "gaussian_csa"), \
             patch.object(combined_runtime, "configuration", return_value=nullcontext()):
            rows = runner.primitives(rt)
            self.assertEqual(len(rows), 24)
            self.assertEqual({r["kernel_id"] for r in rows}, {0, 1, 2, 3})



if __name__ == "__main__":
    unittest.main(verbosity=2)
