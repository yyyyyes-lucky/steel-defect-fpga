"""Frozen Gaussian response with only final PS difference/decode replaced."""
import time
import numpy as np
from gauss_tail import finish_response

def response(self, gray, branch, timeout_seconds=10.0):
    if self.closed or self.failed:
        raise RuntimeError("Driver unavailable; reload overlay")
    gray = np.asarray(gray)
    if gray.shape != (512, 512) or gray.dtype != np.uint8:
        raise ValueError("Expected 512x512 uint8 grayscale frame")
    if branch not in ("inclusion", "broad"):
        raise ValueError("Unknown dark-response branch")
    begin = time.perf_counter()
    first, second = (0, 2) if branch == "inclusion" else (1, 3)
    values = gray.astype(np.uint32) << np.uint32(20)
    passes = []
    values, timing = self.row_pass(values, first, timeout_seconds)
    passes.append(timing)
    transposed, timing = self.row_pass(values.T, first, timeout_seconds)
    passes.append(timing)
    smooth = np.ascontiguousarray(transposed.T)
    values, timing = self.row_pass(smooth, second, timeout_seconds)
    passes.append(timing)
    transposed, timing = self.row_pass(values.T, second, timeout_seconds)
    passes.append(timing)
    background = transposed.T
    dark = finish_response(background, smooth)
    return dark, {"branch": branch, "passes": passes,
                  "response_ms": (time.perf_counter() - begin) * 1000,
                  "scope": "PS padding/transposes/copies, four DMA row passes, PS subtraction/decode"}
