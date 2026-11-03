"""Isolated host candidates. Not installed in any qualified runtime."""
import numpy as np


def decode_owned_planes(q8_output):
    """Decode low/high 16 bits; outputs own memory, even across DMA reuse.

    Caller must have completed DMA and invalidated its receive buffer before
    entry, and must prevent reuse until both copies finish (existing lock).
    Normalization handles byte order and noncontiguous inputs; the ordinary
    little-endian contiguous DMA array requires no normalization allocation.
    """
    words = np.asarray(q8_output)
    if words.shape != (512, 512) or words.dtype.kind != 'u' or words.dtype.itemsize != 4:
        raise ValueError('Q8 input must be 512x512 uint32')
    packed = np.ascontiguousarray(words, dtype=np.dtype('<u4'))
    lanes = packed.view('<u2').reshape(512, 512, 2)
    return (lanes[:, :, 0].astype(np.uint16, order='C', copy=True),
            lanes[:, :, 1].astype(np.uint16, order='C', copy=True))


def validate_maps_minmax(local_std_frames, shape=(512, 512)):
    """Same public validation, with an allocation-free real-float fast path.

    Keep the original expression for other numeric dtypes, preserving its
    behavior instead of silently narrowing the accepted input contract.
    np.min/max (not nanmin/nanmax or OpenCV minMaxLoc) propagate NaN.
    """
    if len(local_std_frames) != 2:
        raise ValueError('local_std_frames must contain (sigma2_map, sigma4_map)')
    maps = tuple(np.asarray(x) for x in local_std_frames)
    for name, values in zip(('sigma2', 'sigma4'), maps):
        if values.shape != shape or not np.issubdtype(values.dtype, np.number):
            raise ValueError('local_std_%s map must be numeric and 512x512' % name)
        if values.dtype.kind == 'f' and values.dtype.itemsize in (4, 8):
            low, high = np.min(values), np.max(values)
            valid = np.isfinite(low) and np.isfinite(high) and low >= 0
        else:
            valid = np.isfinite(values).all() and not np.any(values < 0)
        if not valid:
            raise ValueError('local_std_%s map must contain finite nonnegative values' % name)
    return maps
