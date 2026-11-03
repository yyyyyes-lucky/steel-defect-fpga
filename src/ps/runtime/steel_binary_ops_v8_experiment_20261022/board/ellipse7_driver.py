"""Experimental ellipse7 driver. Failure requires an overlay reload.

Reusable allocation is setup cost; call_ms includes validation, normalization,
padding, cache maintenance, transfer/core wait, decode and the output copy.
"""
import time
import warnings
import numpy as np

_RETAINED_BUFFERS = []


class Ellipse7:
    def __init__(self, overlay, allocator=None):
        if allocator is None:
            from pynq import allocate
            allocator = allocate
        started = time.perf_counter()
        self.dma = overlay.axi_dma_ellipse
        self.core = overlay.ellipse7_0.mmio
        self.failed = self.closed = self.in_flight = False
        self.buffers = []
        self.recovery_safe = True
        try:
            self.buffers.append(allocator(shape=(518 * 518,), dtype=np.uint32))
            self.buffers.append(allocator(shape=(512 * 512,), dtype=np.uint32))
        except BaseException:
            for buf in self.buffers:
                buf.freebuffer()
            raise
        self.setup_ms = (time.perf_counter() - started) * 1000

    def dilate(self, raw, timeout_seconds=10.0):
        begin = time.perf_counter()
        if self.closed or self.failed or self.in_flight:
            raise RuntimeError('Ellipse driver unavailable; reload overlay after failure')
        raw = np.asarray(raw)
        if (raw.dtype != np.uint8 or raw.ndim != 2 or not raw.size
                or min(raw.shape) < 1 or max(raw.shape) > 512):
            raise ValueError('Expected nonempty 2D uint8 ROI of at most 512x512')
        if not np.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError('Timeout must be positive and finite')
        h, w = raw.shape
        source = self.buffers[0][:(h+6)*(w+6)].reshape(h+6, w+6)
        target = self.buffers[1][:h*w].reshape(h, w)
        try:
            source[:] = 0
            source[3:3+h, 3:3+w] = raw > 0
            source.flush()
            prepared = time.perf_counter()
            if not self.core.read(0x00) & 4:
                raise RuntimeError('Ellipse core not idle; reload overlay')
            self.core.write(0x10, int(h))
            self.core.write(0x18, int(w))
            self.core.read(0x24)
            self.in_flight = True
            self.dma.recvchannel.transfer(target)
            self.core.write(0x00, 1)
            self.dma.sendchannel.transfer(source)
            deadline = time.monotonic() + timeout_seconds
            done = False
            while True:
                send, recv = self.dma.mmio.read(0x04), self.dma.mmio.read(0x34)
                done = bool(self.core.read(0x00) & 2) or done
                if (send | recv) & 0x70:
                    raise RuntimeError('Ellipse DMA error: MM2S=0x%x S2MM=0x%x' % (send, recv))
                if send & 2 and recv & 2 and done:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Ellipse DMA/core timeout: MM2S=0x%x S2MM=0x%x done=%s' % (send, recv, done))
                time.sleep(0.0001)
            # Both channels are idle; these library waits cannot spin on in-flight DMA.
            self.dma.sendchannel.wait()
            self.dma.recvchannel.wait()
            self.in_flight = False
            actual_length = self.dma.mmio.read(0x58)
            if actual_length != target.nbytes:
                raise RuntimeError('Ellipse receive length %d != %d' % (actual_length, target.nbytes))
            status = int(self.core.read(0x20))
            if status:
                raise RuntimeError('Ellipse protocol status=0x%x' % status)
            transferred = time.perf_counter()
            target.invalidate()
            if np.any(target > 1):
                raise RuntimeError('Ellipse output is not binary 0/1')
            result = np.asarray(target).astype(np.uint8)
            end = time.perf_counter()
            return result, {'height': h, 'width': w, 'status': status,
                'input_bytes': int(source.nbytes), 'output_bytes': int(target.nbytes),
                'prepare_and_flush_ms': (prepared-begin)*1000,
                'launch_dma_core_wait_ms': (transferred-prepared)*1000,
                'invalidate_decode_copy_ms': (end-transferred)*1000,
                'call_ms': (end-begin)*1000}
        except BaseException:
            self.failed = True
            raise

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
                        raise TimeoutError('Ellipse DMA reset did not halt both channels')
                    time.sleep(0.001)
                self.in_flight = False
            except BaseException as exc:
                self.recovery_safe = False
                _RETAINED_BUFFERS.extend(self.buffers)
                warnings.warn('DMA buffers retained; reboot board before continuing: %s' % exc)
                return
        for buf in self.buffers:
            buf.freebuffer()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
