"""Q30 coefficients / Q20 pixels, one row-FIR DMA reused for four passes.

The caller owns the overlay. Any transaction failure disables this instance;
reload the overlay before constructing another driver after a failure.
"""
import time
import warnings

import numpy as np

TAPS = (9, 13, 57, 201)
_RETAINED_BUFFERS = []


class GaussianRows:
    def __init__(self, overlay):
        from pynq import allocate

        self.dma = overlay.axi_dma_dark
        self.core = overlay.gaussian_row_q30_0.mmio
        self.buffers = []
        self.failed = self.closed = self.in_flight = False
        try:
            self.buffers.append(allocate(shape=(512 * 712,), dtype=np.uint32))
            self.buffers.append(allocate(shape=(512 * 512,), dtype=np.uint32))
        except Exception:
            for buf in self.buffers:
                buf.freebuffer()
            raise

    def row_pass(self, pixels, kernel_id, timeout_seconds=10.0):
        if self.closed or self.failed or self.in_flight:
            raise RuntimeError("Driver unavailable; reload overlay after a failure")
        if isinstance(kernel_id, bool) or not isinstance(kernel_id, (int, np.integer)) or not 0 <= kernel_id < 4:
            raise ValueError("kernel_id must be integer 0..3")
        pixels = np.asarray(pixels)
        if pixels.dtype != np.uint32 or pixels.ndim != 2 or pixels.shape[1] != 512 or not 1 <= pixels.shape[0] <= 512:
            raise ValueError("Expected 1..512 rows, 512 columns, uint32")
        if np.any(pixels > np.uint32(0x0fffffff)):
            raise ValueError("Input exceeds unsigned 28-bit data width")
        if not np.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        begin = time.perf_counter()
        radius = TAPS[kernel_id] // 2
        height, width = pixels.shape[0], 512 + 2 * radius
        source = self.buffers[0][:height * width].reshape(height, width)
        target = self.buffers[1][:height * 512].reshape(height, 512)
        source[:] = np.pad(pixels, ((0, 0), (radius, radius)), mode="reflect")
        source.flush()
        prepared = time.perf_counter()
        try:
            if not self.core.read(0x00) & 4:
                raise RuntimeError("Gaussian core is not idle; reload overlay")
            self.core.write(0x10, int(kernel_id))
            self.core.write(0x18, height)
            self.core.read(0x24)  # Clear stale status-valid, not the status value.
            self.in_flight = True
            self.dma.recvchannel.transfer(target)
            self.core.write(0x00, 1)
            self.dma.sendchannel.transfer(source)
            deadline = time.monotonic() + timeout_seconds
            core_done = False
            while True:
                send, recv = self.dma.mmio.read(0x04), self.dma.mmio.read(0x34)
                core_done = bool(self.core.read(0x00) & 2) or core_done
                if (send | recv) & 0x70:
                    raise RuntimeError("Dark DMA error: MM2S=0x%x S2MM=0x%x" % (send, recv))
                if send & 2 and recv & 2 and core_done:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Dark DMA/core timeout: MM2S=0x%x S2MM=0x%x done=%s" % (send, recv, core_done))
                time.sleep(0.0001)
            self.dma.sendchannel.wait()
            self.dma.recvchannel.wait()
            self.in_flight = False
            actual_length = self.dma.mmio.read(0x58)
            if actual_length != target.nbytes:
                raise RuntimeError("Dark receive length %d != %d" % (actual_length, target.nbytes))
            status = self.core.read(0x20)
            if status:
                raise RuntimeError("Gaussian core protocol status 0x%x" % status)
            transferred = time.perf_counter()
            target.invalidate()
            result = np.asarray(target).copy()
            end = time.perf_counter()
            return result, {"kernel_id": int(kernel_id), "rows": height,
                            "prepare_and_flush_ms": (prepared - begin) * 1000,
                            "launch_dma_and_core_wait_ms": (transferred - prepared) * 1000,
                            "invalidate_and_copy_ms": (end - transferred) * 1000,
                            "row_pass_ms": (end - begin) * 1000}
        except Exception:
            self.failed = True
            raise

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
        difference = np.maximum(background.astype(np.int64) - smooth.astype(np.int64), 0).astype(np.uint32)
        dark = difference.astype(np.float32) / np.float32(1 << 20)
        return dark, {"branch": branch, "passes": passes,
                      "response_ms": (time.perf_counter() - begin) * 1000,
                      "scope": "PS padding/transposes/copies, four DMA row passes, PS subtraction/decode"}

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.in_flight:
            try:
                self.dma.mmio.write(0x00, 4)
                deadline = time.monotonic() + 1.0
                while True:
                    if (not self.dma.mmio.read(0x00) & 4 and
                            self.dma.mmio.read(0x04) & 1 and self.dma.mmio.read(0x34) & 1):
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Dark DMA reset failed")
                    time.sleep(0.001)
            except Exception as exc:
                _RETAINED_BUFFERS.extend(self.buffers)
                warnings.warn("DMA buffers retained; restart board: %s" % exc)
                return
        for buf in self.buffers:
            buf.freebuffer()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
