"""One overlay, two DMAs, original-coordinate Sobel/std7/std13 maps."""
from pathlib import Path
import time
import warnings

import numpy as np

_RETAINED_BUFFERS = []


class DualFeatures:
    def __init__(self, bitstream="steel_dual.bit"):
        from pynq import Overlay, allocate

        bitstream = Path(bitstream).resolve()
        if not bitstream.is_file() or not bitstream.with_suffix(".hwh").is_file():
            raise FileNotFoundError("Matching steel_dual.bit/.hwh files are required")
        self.overlay = Overlay(str(bitstream))
        self.dmas = {"sobel": self.overlay.axi_dma_sobel, "q8": self.overlay.axi_dma_0}
        self.buffers = []
        self.in_flight = False
        self.failed = False
        self.closed = False
        try:
            for shape, dtype in [((512, 512), np.uint8), ((512, 512), np.uint8),
                                 ((524, 524), np.uint8), ((512, 512), np.uint32)]:
                self.buffers.append(allocate(shape=shape, dtype=dtype))
        except Exception:
            for buf in self.buffers:
                buf.freebuffer()
            raise
        self.sobel_in, self.sobel_out, self.q8_in, self.q8_out = self.buffers

    def process(self, gray, timeout_seconds=10.0):
        if self.closed or self.failed:
            raise RuntimeError("Driver closed or failed; recover before another frame")
        gray = np.asarray(gray)
        if gray.shape != (512, 512) or gray.dtype != np.uint8:
            raise ValueError("Input must be 512x512 uint8")
        if not np.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        begin = time.perf_counter()
        self.sobel_in[:] = gray
        self.q8_in[:] = np.pad(gray, ((6, 6), (6, 6)), mode="reflect")
        self.sobel_in.flush()
        self.q8_in.flush()
        start = time.perf_counter()
        try:
            self.in_flight = True
            self.dmas["sobel"].recvchannel.transfer(self.sobel_out)
            self.dmas["q8"].recvchannel.transfer(self.q8_out)
            self.dmas["sobel"].sendchannel.transfer(self.sobel_in)
            self.dmas["q8"].sendchannel.transfer(self.q8_in)
            deadline = time.monotonic() + timeout_seconds
            finished = {}
            while len(finished) != 2:
                status = {}
                for name, dma in self.dmas.items():
                    send = dma.mmio.read(0x04)
                    recv = dma.mmio.read(0x34)
                    status[name] = [send, recv]
                    if (send | recv) & 0x70:
                        raise RuntimeError("%s DMA error: MM2S=0x%08x S2MM=0x%08x" % (name, send, recv))
                    if (send & 2) and (recv & 2) and name not in finished:
                        finished[name] = (time.perf_counter() - start) * 1000
                if len(finished) == 2:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Dual DMA timeout; status=%s" % status)
                time.sleep(0.0001)
            for dma in self.dmas.values():
                dma.sendchannel.wait()
                dma.recvchannel.wait()
            self.in_flight = False
            for name, expected in [("sobel", 512 * 512), ("q8", 512 * 512 * 4)]:
                actual = self.dmas[name].mmio.read(0x58)
                if actual != expected:
                    raise RuntimeError("%s receive length %d, expected %d" % (name, actual, expected))
            self.sobel_out.invalidate()
            self.q8_out.invalidate()
            sobel = np.asarray(self.sobel_out).copy()
            words = np.asarray(self.q8_out).copy()
            std7 = (words & np.uint32(0xffff)).astype(np.uint16)
            std13 = (words >> np.uint32(16)).astype(np.uint16)
            end = time.perf_counter()
            return sobel, std7, std13, {
                "prepare_and_flush_ms": (start - begin) * 1000,
                "dma_completion_observed_ms": finished,
                "prepare_dma_and_copy_ms": (end - begin) * 1000,
            }
        except Exception:
            self.failed = True
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.in_flight:
            # Never free memory that a DMA may still access.
            try:
                for dma in self.dmas.values():
                    dma.mmio.write(0x00, 4)
                deadline = time.monotonic() + 1.0
                while True:
                    safe = True
                    for dma in self.dmas.values():
                        safe = safe and not (dma.mmio.read(0x00) & 4)
                        safe = safe and bool(dma.mmio.read(0x04) & 1)
                        safe = safe and bool(dma.mmio.read(0x34) & 1)
                    if safe:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("DMA reset did not halt both channels")
                    time.sleep(0.001)
            except Exception as exc:
                _RETAINED_BUFFERS.extend(self.buffers)
                warnings.warn("Buffers retained because DMA recovery failed. Restart the board: %s" % exc)
                return
        for buf in self.buffers:
            buf.freebuffer()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
