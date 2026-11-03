"""Unqualified candidate: one contiguous DMA-buffer read before cached lane copies.
Call only after existing DMA completion, length and cache checks, under its lock.
No cache policy is changed. Returned planes own memory. No speedup is claimed.
"""
import numpy as np

def decode_staged(q8_output):
    words = np.asarray(q8_output)
    if words.shape != (512, 512) or words.dtype.kind != 'u' or words.dtype.itemsize != 4:
        raise ValueError('Q8 input must be 512x512 uint32')
    staged = np.array(words, dtype=np.dtype('<u4'), order='C', copy=True)
    lanes = staged.view('<u2').reshape(512, 512, 2)
    return (lanes[:, :, 0].astype(np.uint16, order='C', copy=True),
            lanes[:, :, 1].astype(np.uint16, order='C', copy=True))
