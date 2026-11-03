"""Generated from locked Q8 candidate; diagnostic timers only; cache calls preserved."""
def profiled_feature_process(self, gray, timeout_seconds=10.0):
    if self.closed or self.failed:
        raise RuntimeError('Driver closed or failed; recover before another frame')
    gray = np.asarray(gray)
    if gray.shape != (512, 512) or gray.dtype != np.uint8:
        raise ValueError('Input must be 512x512 uint8')
    if not np.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError('timeout_seconds must be positive and finite')
    begin = time.perf_counter()
    detail = {}
    stamp = [begin]
    def mark(name):
        now = time.perf_counter()
        detail[name] = (now-stamp[0])*1000
        stamp[0] = now
    self.sobel_in[:] = gray
    mark('input_sobel_copy_ms')
    self.q8_in[:] = np.pad(gray, ((6, 6), (6, 6)), mode='reflect')
    mark('input_q8_pad_copy_ms')
    self.sobel_in.flush()
    mark('explicit_sobel_flush_ms')
    self.q8_in.flush()
    mark('explicit_q8_flush_ms')
    start = time.perf_counter()
    try:
        self.in_flight = True
        self.dmas['sobel'].recvchannel.transfer(self.sobel_out)
        mark('submit_sobel_recv_ms')
        self.dmas['q8'].recvchannel.transfer(self.q8_out)
        mark('submit_q8_recv_ms')
        self.dmas['sobel'].sendchannel.transfer(self.sobel_in)
        mark('submit_sobel_send_ms')
        self.dmas['q8'].sendchannel.transfer(self.q8_in)
        mark('submit_q8_send_ms')
        deadline = time.monotonic() + timeout_seconds
        finished = {}
        while len(finished) != 2:
            status = {}
            for name, dma in self.dmas.items():
                send = dma.mmio.read(4)
                recv = dma.mmio.read(52)
                status[name] = [send, recv]
                if (send | recv) & 112:
                    raise RuntimeError('%s DMA error: MM2S=0x%08x S2MM=0x%08x' % (name, send, recv))
                if send & 2 and recv & 2 and (name not in finished):
                    finished[name] = (time.perf_counter() - start) * 1000
            if len(finished) == 2:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('Dual DMA timeout; status=%s' % status)
            time.sleep(0.0001)
        mark('poll_until_both_complete_ms')
        self.dmas['sobel'].sendchannel.wait()
        mark('wait_sobel_send_ms')
        self.dmas['sobel'].recvchannel.wait()
        mark('wait_sobel_recv_ms')
        self.dmas['q8'].sendchannel.wait()
        mark('wait_q8_send_ms')
        self.dmas['q8'].recvchannel.wait()
        mark('wait_q8_recv_ms')
        self.in_flight = False
        for name, expected in [('sobel', 512 * 512), ('q8', 512 * 512 * 4)]:
            actual = self.dmas[name].mmio.read(88)
            if actual != expected:
                raise RuntimeError('%s receive length %d, expected %d' % (name, actual, expected))
        mark('receive_length_checks_ms')
        self.sobel_out.invalidate()
        mark('explicit_sobel_invalidate_ms')
        self.q8_out.invalidate()
        mark('explicit_q8_invalidate_ms')
        sobel = np.asarray(self.sobel_out).copy()
        mark('sobel_owned_copy_ms')
        std7, std13 = _q8_path_decode(self.q8_out)
        mark('q8_decode_ms')
        end = time.perf_counter()
        self._dma_f1_detail = detail
        return (sobel, std7, std13, {'prepare_and_flush_ms': (start - begin) * 1000, 'dma_completion_observed_ms': finished, 'prepare_dma_and_copy_ms': (end - begin) * 1000})
    except Exception:
        self.failed = True
        raise
