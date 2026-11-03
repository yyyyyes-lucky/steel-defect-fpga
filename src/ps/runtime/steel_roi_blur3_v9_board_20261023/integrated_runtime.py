"""Versioned candidate: same v8 hardware, tail32 default, ellipse remains PS."""
from contextlib import contextmanager
import types
from candidate_response import response


class Tail32Runtime:
    VERSION = "gauss_tail32_full184_v1_20261023"

    def __init__(self, experiment_module, bitstream):
        self.module = experiment_module
        self.runtime = experiment_module.ExperimentRuntime(bitstream, enable_ellipse=False)
        self.base = self.runtime.base
        self.original_response = self.base.gaussian.response
        self.failed = self.closed = False

    def process_frame(self, gray, predictions, variant="tail32"):
        if variant not in ("original", "tail32"):
            raise ValueError("Unknown variant")
        with self.module.LOCK, self.module.base._MODE_LOCK, self.module.base._HARDWARE_LOCK:
            if self.closed or self.failed:
                raise RuntimeError("Runtime unavailable after close/failure")
            self.base.gaussian.response = (types.MethodType(response, self.base.gaussian)
                if variant == "tail32" else self.original_response)
            try:
                result = self.base.process_frame(gray, predictions, mode="pl_gaussian")
                result["timing"]["candidate_version"] = self.VERSION
                result["timing"]["gaussian_variant"] = variant
                return result
            except BaseException:
                self.failed = True
                raise
            finally:
                self.base.gaussian.response = self.original_response

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.runtime.close()
        for driver in (self.base.gaussian, self.base.features):
            if getattr(driver, "in_flight", False) or getattr(driver, "failed", False):
                raise RuntimeError("Hardware session unsafe; reboot board")
