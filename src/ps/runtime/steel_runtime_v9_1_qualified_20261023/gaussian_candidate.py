"""Qualified row driver protocol unchanged; replace only PS padding preparation."""
import time
import numpy as np

TAPS=(9,13,57,201)

def direct_pad(source,pixels,radius):
    destination=np.asarray(source)
    if np.shares_memory(destination,pixels):pixels=pixels.copy()
    destination[:,radius:radius+512]=pixels
    destination[:,:radius]=pixels[:,radius:0:-1]
    destination[:,radius+512:]=pixels[:,-2:-radius-2:-1]

def row_pass(self,pixels,kernel_id,timeout_seconds=10.0):
    if self.closed or self.failed or self.in_flight:
        raise RuntimeError("Driver unavailable; reload overlay after a failure")
    if isinstance(kernel_id,bool) or not isinstance(kernel_id,(int,np.integer)) or not 0<=kernel_id<4:
        raise ValueError("kernel_id must be integer 0..3")
    pixels=np.asarray(pixels)
    if pixels.dtype!=np.uint32 or pixels.ndim!=2 or pixels.shape[1]!=512 or not 1<=pixels.shape[0]<=512:
        raise ValueError("Expected 1..512 rows, 512 columns, uint32")
    if np.any(pixels>np.uint32(0x0fffffff)):
        raise ValueError("Input exceeds unsigned 28-bit data width")
    if not np.isfinite(timeout_seconds) or timeout_seconds<=0:
        raise ValueError("timeout_seconds must be positive and finite")
    begin=time.perf_counter()
    radius=TAPS[kernel_id]//2
    height,width=pixels.shape[0],512+2*radius
    source=self.buffers[0][:height*width].reshape(height,width)
    target=self.buffers[1][:height*512].reshape(height,512)
    direct_pad(source,pixels,radius)
    source.flush()
    prepared=time.perf_counter()
    try:
        if not self.core.read(0x00)&4:raise RuntimeError("Gaussian core is not idle; reload overlay")
        self.core.write(0x10,int(kernel_id));self.core.write(0x18,height)
        self.core.read(0x24)
        self.in_flight=True
        self.dma.recvchannel.transfer(target)
        self.core.write(0x00,1)
        self.dma.sendchannel.transfer(source)
        deadline=time.monotonic()+timeout_seconds
        core_done=False
        while True:
            send,recv=self.dma.mmio.read(0x04),self.dma.mmio.read(0x34)
            core_done=bool(self.core.read(0x00)&2) or core_done
            if (send|recv)&0x70:
                raise RuntimeError("Dark DMA error: MM2S=0x%x S2MM=0x%x"%(send,recv))
            if send&2 and recv&2 and core_done:break
            if time.monotonic()>=deadline:
                raise TimeoutError("Dark DMA/core timeout: MM2S=0x%x S2MM=0x%x done=%s"%(send,recv,core_done))
            time.sleep(.0001)
        self.dma.sendchannel.wait();self.dma.recvchannel.wait()
        self.in_flight=False
        actual_length=self.dma.mmio.read(0x58)
        if actual_length!=target.nbytes:
            raise RuntimeError("Dark receive length %d != %d"%(actual_length,target.nbytes))
        status=self.core.read(0x20)
        if status:raise RuntimeError("Gaussian core protocol status 0x%x"%status)
        transferred=time.perf_counter()
        target.invalidate()
        result=np.asarray(target).copy()
        end=time.perf_counter()
        return result,{"kernel_id":int(kernel_id),"rows":height,
                       "prepare_and_flush_ms":(prepared-begin)*1000,
                       "launch_dma_and_core_wait_ms":(transferred-prepared)*1000,
                       "invalidate_and_copy_ms":(end-transferred)*1000,
                       "row_pass_ms":(end-begin)*1000}
    except Exception:
        self.failed=True
        raise
