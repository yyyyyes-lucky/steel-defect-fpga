"""Exact Q20 tail candidate. uint28 inputs imply safe signed int32 difference."""
import numpy as np


def finish_response(background, smooth):
    for value in (background, smooth):
        if value.dtype != np.uint32 or value.ndim != 2 or not value.size:
            raise ValueError("Expected nonempty uint32 image")
        if np.any(value > np.uint32(0x0fffffff)):
            raise ValueError("Gaussian output exceeds unsigned 28-bit contract")
    if background.shape != smooth.shape:
        raise ValueError("Gaussian output shape mismatch")
    # Both operands lie in [0, 2**28-1]; signed subtraction cannot overflow.
    difference = background.astype(np.int32)
    np.subtract(difference, smooth.astype(np.int32), out=difference)
    np.maximum(difference, 0, out=difference)
    result = difference.astype(np.float32)
    np.divide(result, np.float32(1 << 20), out=result)
    return result
