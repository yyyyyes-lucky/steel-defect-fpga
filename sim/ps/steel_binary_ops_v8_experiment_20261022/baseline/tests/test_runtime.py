import importlib.util
from pathlib import Path
import unittest

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("steel_runtime_test", ROOT / "steel_defect_runtime.py")
runtime_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_module)


class FakeFeatures:
    def __init__(self):
        self.calls = 0

    def process(self, gray):
        self.calls += 1
        return (np.zeros((512, 512), np.uint8), np.zeros((512, 512), np.uint16),
                np.zeros((512, 512), np.uint16), {"prepare_dma_and_copy_ms": 1.0})

    def close(self):
        pass


class FakeGaussian:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def response(self, gray, branch):
        self.calls += 1
        if self.fail:
            raise TimeoutError("mock DMA timeout")
        return np.full((512, 512), 4.0, np.float32), {"response_ms": 3.0}

    def close(self):
        pass


class FakeFixed:
    def response(self, gray, branch, q_coeff, q_pixel):
        return np.full((512, 512), 4.0, np.float32)


def prediction(class_id):
    return {"class_id": class_id, "detection_index": class_id,
            "confidence": 1.0, "box_xyxy_pixel": [4, 4, 24, 24]}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.original_process = runtime_module._fast.process_frame
        self.original_response = runtime_module._fast._runtime.broad_dark_response
        self.calls = []

        def fake_process(gray, predictions, **kwargs):
            response = runtime_module._fast._runtime.broad_dark_response(gray)
            self.calls.append((response, kwargs))
            return ([{"class_id": p["class_id"]} for p in predictions], [{} for _ in predictions], {})

        runtime_module._fast.process_frame = fake_process
        self.gray = np.zeros((512, 512), np.uint8)

    def tearDown(self):
        runtime_module._fast.process_frame = self.original_process
        runtime_module._fast._runtime.broad_dark_response = self.original_response

    def test_pl_routes_only_broad_classes_and_checks_fixed_model(self):
        features, gaussian = FakeFeatures(), FakeGaussian()
        runtime = runtime_module.SteelDefectRuntime(features, gaussian, fixed_model=FakeFixed())
        out = runtime.process_frame(self.gray, [prediction(1), prediction(2), prediction(4)], mode="pl_gaussian")
        self.assertEqual(features.calls, 1)
        self.assertEqual(gaussian.calls, 1)
        self.assertEqual(out["timing"]["actual_mode"], "pl_gaussian")
        self.assertEqual(out["timing"]["pl_integer_model_different_pixels"], 0)
        self.assertEqual(out["timing"]["requested_mode"], "pl_gaussian")
        self.assertIs(runtime_module._fast._runtime.broad_dark_response, self.original_response)

    def test_no_broad_roi_does_not_touch_gaussian(self):
        gaussian = FakeGaussian()
        runtime = runtime_module.SteelDefectRuntime(FakeFeatures(), gaussian, fixed_model=FakeFixed())
        out = runtime.process_frame(self.gray, [prediction(1)], mode="pl_gaussian")
        self.assertEqual(gaussian.calls, 0)
        self.assertEqual(out["timing"]["actual_mode"], "arm_float")

    def test_explicit_fallback_is_reported(self):
        runtime = runtime_module.SteelDefectRuntime(FakeFeatures(), FakeGaussian(fail=True),
                                                     fixed_model=FakeFixed(), allow_arm_fallback=True)
        out = runtime.process_frame(self.gray, [prediction(2)], mode="pl_gaussian")
        self.assertEqual(out["timing"]["actual_mode"], "arm_float")
        self.assertIn("mock DMA timeout", out["timing"]["fallback_reason"])

    def test_invalid_input_rejected_before_feature_access(self):
        features = FakeFeatures()
        runtime = runtime_module.SteelDefectRuntime(features)
        with self.assertRaises(ValueError):
            runtime.process_frame(np.zeros((16, 16), np.uint8), [], mode="arm_float")
        self.assertEqual(features.calls, 0)

    def test_invalid_prediction_rejected_before_feature_access(self):
        features = FakeFeatures()
        runtime = runtime_module.SteelDefectRuntime(features)
        bad = prediction(2)
        bad["box_xyxy_pixel"] = [-1, 4, 24, 24]
        with self.assertRaises(ValueError):
            runtime.process_frame(self.gray, [bad], mode="pl_gaussian")
        self.assertEqual(features.calls, 0)


if __name__ == "__main__":
    unittest.main()
