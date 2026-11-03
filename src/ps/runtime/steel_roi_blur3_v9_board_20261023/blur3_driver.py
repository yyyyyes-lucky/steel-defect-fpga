"""Packed uint8 transport for sigma3 blur. Padding/cache/DMA/copy are timed."""
import time
import warnings
import numpy as np

_RETAINED_BUFFERS=[]

class Blur3:
    def __init__(self,overlay,allocator=None):
        if allocator is None:
            from pynq import allocate
            allocator=allocate
        begin=time.perf_counter()
        self.dma=overlay.axi_dma_blur3
        self.core=overlay.blur3_0.mmio
        self.failed=self.closed=self.in_flight=False
        self.recovery_safe=True
        self.buffers=[]
        try:
            self.buffers.append(allocator(shape=(530*530,),dtype=np.uint8))
            self.buffers.append(allocator(shape=(512*512,),dtype=np.uint8))
        except BaseException:
            for b in self.buffers:b.freebuffer()
            raise
        self.setup_ms=(time.perf_counter()-begin)*1000

    def blur(self,raw,timeout_seconds=10.):
        begin=time.perf_counter()
        if self.failed or self.closed or self.in_flight:
            raise RuntimeError("Blur3 unavailable; reboot board after failure")
        raw=np.asarray(raw)
        if raw.dtype!=np.uint8 or raw.ndim!=2 or not raw.size or max(raw.shape)>512:
            raise ValueError("Expected nonempty 2D uint8 ROI, at most 512x512")
        if not np.isfinite(timeout_seconds) or timeout_seconds<=0:
            raise ValueError("Timeout must be finite and positive")
        h,w=raw.shape
        count=(h+18)*(w+18);wire=(count+3)&~3
        source=self.buffers[0][:wire]
        target=self.buffers[1][:h*w].reshape(h,w)
        try:
            padded=np.pad(raw,((9,9),(9,9)),mode="reflect")
            source[:count]=padded.ravel()
            source[count:]=0
            source.flush()
            prepared=time.perf_counter()
            if not self.core.read(0)&4:raise RuntimeError("Blur3 core not idle")
            self.core.write(0x10,int(h));self.core.write(0x18,int(w))
            self.core.read(0x24)
            self.in_flight=True
            self.dma.recvchannel.transfer(target)
            self.core.write(0,1)
            self.dma.sendchannel.transfer(source)
            deadline=time.monotonic()+timeout_seconds
            done=False
            while True:
                send=self.dma.mmio.read(0x04);recv=self.dma.mmio.read(0x34)
                done=bool(self.core.read(0)&2) or done
                if (send|recv)&0x70:
                    raise RuntimeError("Blur3 DMA error: %x %x"%(send,recv))
                if send&2 and recv&2 and done:break
                if time.monotonic()>=deadline:
                    raise TimeoutError("Blur3 DMA/core timeout: %x %x done=%s"%(send,recv,done))
                time.sleep(.0001)
            self.dma.sendchannel.wait();self.dma.recvchannel.wait()
            self.in_flight=False
            if self.dma.mmio.read(0x58)!=target.nbytes:
                raise RuntimeError("Blur3 received length mismatch")
            status=int(self.core.read(0x20))
            if status:raise RuntimeError("Blur3 protocol status=%x"%status)
            transferred=time.perf_counter()
            target.invalidate()
            result=np.asarray(target).copy()
            end=time.perf_counter()
            return result,{"height":h,"width":w,"status":status,
                "input_bytes":int(source.nbytes),"output_bytes":int(target.nbytes),
                "prepare_and_flush_ms":(prepared-begin)*1000,
                "launch_dma_core_wait_ms":(transferred-prepared)*1000,
                "invalidate_copy_ms":(end-transferred)*1000,
                "call_ms":(end-begin)*1000}
        except BaseException:
            self.failed=True
            raise

    def close(self):
        if self.closed:return
        self.closed=True
        if self.in_flight:
            try:
                self.dma.mmio.write(0,4)
                deadline=time.monotonic()+1.
                while True:
                    if (not self.dma.mmio.read(0)&4 and
                        self.dma.mmio.read(4)&1 and self.dma.mmio.read(0x34)&1):break
                    if time.monotonic()>=deadline:raise TimeoutError("DMA reset failed")
                    time.sleep(.001)
                self.in_flight=False
            except BaseException as exc:
                self.recovery_safe=False
                _RETAINED_BUFFERS.extend(self.buffers)
                warnings.warn("Buffers retained; reboot board: "+str(exc))
                return
        for b in self.buffers:b.freebuffer()
