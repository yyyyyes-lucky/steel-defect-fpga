"""Exact software models for candidate binary primitives; no FPGA access."""
import cv2
import numpy as np

# OpenCV MORPH_ELLIPSE (7,7), NOT a square or a separable rectangle.
ELLIPSE = np.array([[0,0,0,1,0,0,0], [0,1,1,1,1,1,0],
                    [1,1,1,1,1,1,1], [1,1,1,1,1,1,1],
                    [1,1,1,1,1,1,1], [0,1,1,1,1,1,0],
                    [0,0,0,1,0,0,0]], dtype=np.uint8)
OFFSETS = tuple((int(y)-3, int(x)-3) for y, x in np.argwhere(ELLIPSE))


def ge_mask(values, threshold):
    """Preserve an existing float32 value's comparison; never quantize it."""
    values = np.asarray(values)
    if values.ndim != 2 or not values.size or values.dtype not in (np.uint8, np.float32):
        raise ValueError('Expected nonempty 2D uint8 or float32')
    if isinstance(threshold, bool) or not isinstance(threshold, (int, np.integer)) or not 0 <= threshold <= 255:
        raise ValueError('Expected integer threshold 0..255')
    if values.dtype == np.uint8:
        return values >= threshold
    if not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError('Float dark response must be finite and nonnegative')
    # IEEE binary32 nonnegative finite values have monotonically ordered bits.
    # Clear the sign of -0.0, which compares exactly like +0.0 in V31.
    bits = values.view(np.uint32) & np.uint32(0x7fffffff)
    limit = np.float32(threshold).view(np.uint32)
    return bits >= limit


def dilate7(raw):
    """33-tap binary OR, centered anchor, ROI exterior zero, uint8 0/1."""
    raw = np.asarray(raw)
    if raw.ndim != 2 or not raw.size or raw.dtype != np.uint8 or max(raw.shape) > 512:
        raise ValueError('Expected nonempty uint8 ROI at most 512x512')
    source = np.pad((raw > 0).astype(np.uint8), 3, mode='constant')
    height, width = raw.shape
    result = np.zeros_like(raw)
    for dy, dx in OFFSETS:
        np.bitwise_or(result, source[3+dy:3+dy+height, 3+dx:3+dx+width], out=result)
    return result


def check_kernel():
    if len(OFFSETS) != 33 or not np.array_equal(ELLIPSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))):
        raise RuntimeError('OpenCV ellipse differs from the frozen 33-tap kernel')
