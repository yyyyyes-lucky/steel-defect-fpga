"""PYNQ DMA driver and exact integer reference for the Q8 local-std core."""

from pathlib import Path
import time
import warnings

import numpy as np


IMAGE_WIDTH = 512
IMAGE_HEIGHT = 512
PAD = 6
PADDED_WIDTH = IMAGE_WIDTH + 2 * PAD
PADDED_HEIGHT = IMAGE_HEIGHT + 2 * PAD

_RETAINED_DMA_BUFFERS = []


def _window_sum_reflect101(gray, size, squared=False):
    radius = size // 2
    padded = np.pad(gray, ((radius, radius), (radius, radius)), mode="reflect")
    values = padded.astype(np.int64)
    if squared:
        values = values * values
    integral = np.pad(
        np.cumsum(np.cumsum(values, axis=0), axis=1),
        ((1, 0), (1, 0)),
        mode="constant",
    )
    return (
        integral[size:, size:]
        + integral[:-size, :-size]
        - integral[:-size, size:]
        - integral[size:, :-size]
    )


def q8_reference(gray, size):
    """Return the exact uint16 result expected from one RTL window size."""
    gray = np.asarray(gray)
    if gray.shape != (IMAGE_HEIGHT, IMAGE_WIDTH) or gray.dtype != np.uint8:
        raise ValueError("gray must be a 512x512 uint8 array")
    if size not in (7, 13):
        raise ValueError("size must be 7 or 13")

    count = size * size
    total = _window_sum_reflect101(gray, size)
    total_square = _window_sum_reflect101(gray, size, squared=True)
    mean_q8 = (total * 256 + count // 2) // count
    meansq_q8 = (total_square * 256 + count // 2) // count
    variance_q2 = np.maximum(meansq_q8 * 256 - mean_q8 * mean_q8, 0)

    # The RTL uses a restoring integer square root, which is floor(sqrt(x)).
    return np.sqrt(variance_q2).astype(np.uint16)


class FpgaLocalStdQ8:
    """Own one Q8 overlay, DMA instance, and its physically contiguous buffers."""

    def __init__(self, bitstream="system_q8.bit"):
        try:
            from pynq import Overlay, allocate
        except ImportError as exc:
            raise RuntimeError("This class must run in a PYNQ Python environment") from exc

        bitstream = Path(bitstream).resolve()
        hwh = bitstream.with_suffix(".hwh")
        if not bitstream.is_file() or not hwh.is_file():
            raise FileNotFoundError(
                "The matching .bit and .hwh files must share one directory and basename"
            )

        self.overlay = Overlay(str(bitstream))
        if not hasattr(self.overlay, "axi_dma_0"):
            raise RuntimeError("axi_dma_0 is missing; verify that the bit/hwh pair matches")
        self.dma = self.overlay.axi_dma_0
        self.in_flight = False
        self.input = allocate(
            shape=(PADDED_HEIGHT, PADDED_WIDTH), dtype=np.uint8
        )
        try:
            self.output = allocate(
                shape=(IMAGE_HEIGHT, IMAGE_WIDTH), dtype=np.uint32
            )
        except Exception:
            self.input.freebuffer()
            raise

    def process(self, gray, timeout_seconds=10.0):
        gray = np.asarray(gray)
        if gray.shape != (IMAGE_HEIGHT, IMAGE_WIDTH) or gray.dtype != np.uint8:
            raise ValueError("gray must be a 512x512 uint8 array")

        self.input[:] = np.pad(gray, ((PAD, PAD), (PAD, PAD)), mode="reflect")
        self.input.flush()

        start = time.perf_counter()
        self.in_flight = True
        # Arm S2MM first so the Q8 output cannot fill the stream before receive starts.
        self.dma.recvchannel.transfer(self.output)
        self.dma.sendchannel.transfer(self.input)

        deadline = time.monotonic() + timeout_seconds
        while True:
            send_status = self.dma.mmio.read(0x04)
            recv_status = self.dma.mmio.read(0x34)
            if (send_status | recv_status) & 0x70:
                raise RuntimeError(
                    "DMA error: MM2S=0x%08x S2MM=0x%08x"
                    % (send_status, recv_status)
                )
            if (send_status & 2) and (recv_status & 2):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "DMA did not finish within %.1f seconds" % timeout_seconds
                )
            time.sleep(0.0001)

        self.dma.sendchannel.wait()
        self.dma.recvchannel.wait()
        self.in_flight = False
        self.output.invalidate()
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        words = np.asarray(self.output).copy()
        std7_q8 = (words & np.uint32(0xFFFF)).astype(np.uint16)
        std13_q8 = (words >> np.uint32(16)).astype(np.uint16)
        return std7_q8, std13_q8, elapsed_ms

    def close(self):
        if self.in_flight:
            try:
                self.dma.mmio.write(0x00, 4)
                deadline = time.monotonic() + 1.0
                while True:
                    reset_pending = self.dma.mmio.read(0x00) & 4
                    halted = ((self.dma.mmio.read(0x04) & 1) and
                              (self.dma.mmio.read(0x34) & 1))
                    if not reset_pending and halted:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("DMA reset did not halt both channels")
                    time.sleep(0.001)
            except Exception as exc:
                _RETAINED_DMA_BUFFERS.extend([self.input, self.output])
                warnings.warn(
                    "DMA recovery failed; buffers were retained. Restart the board "
                    "before another FPGA run: %s" % exc
                )
                return
        self.input.freebuffer()
        self.output.freebuffer()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

