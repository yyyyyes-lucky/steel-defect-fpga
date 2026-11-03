"""Hardware-oriented INTEGER model only; this is not a PL driver."""
import numpy as np

# Frozen Q8 coefficients, sum 256; REFLECT_101, no intermediate rounding.
# Local OpenCV agreement is evidence only for that version; board must compare.
COEFFICIENTS = (0, 1, 3, 4, 9, 14, 20, 28, 32, 34, 32, 28, 20, 14, 9, 4, 3, 1, 0)


def blur3(gray):
    if gray.dtype != np.uint8 or gray.ndim != 2 or not gray.size:
        raise ValueError("Expected nonempty uint8 grayscale ROI")
    h, w = gray.shape
    padded = np.pad(gray.astype(np.uint32), ((0, 0), (9, 9)), mode="reflect")
    horizontal = np.zeros((h, w), np.uint32)
    for offset, weight in enumerate(COEFFICIENTS):
        if weight:
            horizontal += padded[:, offset:offset+w] * np.uint32(weight)
    # horizontal <= 255*256, vertical <= 255*65536; uint32 is ample.
    padded = np.pad(horizontal, ((9, 9), (0, 0)), mode="reflect")
    vertical = np.zeros((h, w), np.uint32)
    for offset, weight in enumerate(COEFFICIENTS):
        if weight:
            vertical += padded[offset:offset+h, :] * np.uint32(weight)
    return ((vertical+np.uint32(32768)) >> np.uint32(16)).astype(np.uint8)
