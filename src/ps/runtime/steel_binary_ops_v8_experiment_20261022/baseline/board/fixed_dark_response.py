"""Integer Gaussian feasibility model; software only, not an FPGA kernel.

Coefficients sum exactly to 2**coefficient_bits. Each horizontal/vertical
pass rounds half up and keeps fractional_bits. All pixels stay nonnegative.
CV_64F implements exact integer MACs. If a full sum could exceed 2**53,
split each input into two 16-bit limbs and reconstruct the sum in uint64.
Border handling is explicitly REFLECT_101 at every pass, as in V31.
"""
import cv2
import numpy as np

TAPS = {1.0: 9, 1.5: 13, 7.0: 57, 25.0: 201}


def kernel(sigma, coefficient_bits):
    if sigma not in TAPS:
        raise ValueError("Unsupported frozen Gaussian sigma")
    if isinstance(coefficient_bits, bool) or not isinstance(coefficient_bits, int) or not 10 <= coefficient_bits <= 30:
        raise ValueError("coefficient_bits must be integer 10..30")
    values = cv2.getGaussianKernel(TAPS[sigma], sigma, cv2.CV_64F).ravel()
    scale = 1 << coefficient_bits
    integers = np.floor(values * scale + 0.5).astype(np.int64)
    integers[len(integers) // 2] += scale - int(integers.sum())
    if np.any(integers < 0) or not np.array_equal(integers, integers[::-1]) or int(integers.sum()) != scale:
        raise ValueError("Invalid quantized Gaussian kernel")
    return integers


def _axis_fixed(pixels, coefficients, coefficient_bits, horizontal):
    weights = coefficients.astype(np.float64)
    unit = np.ones(1, np.float64)
    kx, ky = (weights, unit) if horizontal else (unit, weights)
    def convolve(values):
        return cv2.sepFilter2D(values.astype(np.float64), cv2.CV_64F, kx, ky,
                               borderType=cv2.BORDER_REFLECT_101)
    # Positive weights bound every intermediate sum. Two 16-bit limbs each
    # have a sum < 2**46 at coefficient_bits <= 30: all double MACs are exact.
    if int(pixels.max()) * (1 << coefficient_bits) < (1 << 53):
        sums = convolve(pixels).astype(np.uint64)
    else:
        low = convolve(pixels & np.uint32(0xffff)).astype(np.uint64)
        high = convolve(pixels >> np.uint32(16)).astype(np.uint64)
        sums = low + (high << np.uint64(16))
    return ((sums + np.uint64(1 << (coefficient_bits - 1))) >> np.uint64(coefficient_bits)).astype(np.uint32)


def blur_fixed(pixels, sigma, coefficient_bits):
    if pixels.ndim != 2 or pixels.size == 0 or pixels.dtype != np.uint32:
        raise ValueError("Expected nonempty uint32 fixed-point image")
    coefficients = kernel(sigma, coefficient_bits)
    horizontal = _axis_fixed(pixels, coefficients, coefficient_bits, True)
    return _axis_fixed(horizontal, coefficients, coefficient_bits, False)


def response(gray, branch, coefficient_bits=18, fractional_bits=12):
    if gray.ndim != 2 or gray.dtype != np.uint8:
        raise ValueError("Expected uint8 grayscale image")
    if branch not in ("inclusion", "broad"):
        raise ValueError("Unknown dark-response branch")
    if isinstance(fractional_bits, bool) or not isinstance(fractional_bits, int) or not 0 <= fractional_bits <= 20:
        raise ValueError("fractional_bits must be integer 0..20")
    first, second = (1.0, 7.0) if branch == "inclusion" else (1.5, 25.0)
    pixels = gray.astype(np.uint32) << fractional_bits
    smooth = blur_fixed(pixels, first, coefficient_bits)
    background = blur_fixed(smooth, second, coefficient_bits)
    dark = np.maximum(background.astype(np.int64) - smooth.astype(np.int64), 0).astype(np.uint32)
    return dark.astype(np.float32) / np.float32(1 << fractional_bits)


def arithmetic_estimate(branch, coefficient_bits, fractional_bits):
    sigmas = (1.0, 7.0) if branch == "inclusion" else (1.5, 25.0)
    taps = [TAPS[s] for s in sigmas]
    data_bits = 8 + fractional_bits
    kernels = [kernel(s, coefficient_bits) for s in sigmas]
    return {"taps": taps, "pixel_bits": data_bits,
            "conservative_mac_accumulator_bits": data_bits + coefficient_bits + 1,
            "coefficient_maxima": [int(k.max()) for k in kernels],
            "coefficient_sums": [int(k.sum()) for k in kernels],
            "direct_multiplications_per_pixel": 2 * sum(taps),
            "symmetric_multiplications_per_pixel": 2 * sum((n + 1) // 2 for n in taps),
            "streaming_vertical_line_storage_bits": sum(n - 1 for n in taps) * 512 * data_bits,
            "note": "Analytic two-stage separable FIR estimate only; excludes padding/control/FIFOs and BRAM packing; no synthesis, timing, DSP mapping or throughput result"}
