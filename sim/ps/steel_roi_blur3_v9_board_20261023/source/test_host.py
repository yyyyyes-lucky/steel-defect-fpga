"""Host tests use fake hardware; never evidence of board performance."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/"qualified_tail32"))
sys.path.insert(0,str(ROOT/"board_src"))
import board_runner as runner
import blur3_driver as driver_module
import roi_blur_runtime as integration
import full_runner as tail_runner
import acceptance_common as common
from blur3_model import blur3
from integrated_runtime import Tail32Runtime

class Buffer(np.ndarray):
    def __new__(cls,shape,dtype):
        obj=np.zeros(shape,dtype).view(cls)
        obj.events=[]
        return obj
    def __array_finalize__(self,parent):
        if parent is not None:self.events=getattr(parent,"events",[])
    def flush(self):self.events.append("flush")
    def invalidate(self):self.events.append("invalidate")
    def freebuffer(self):self.events.append("free")

class FakeDevice:
    def __init__(self):
        self.h=self.w=0;self.target=None
        self.length=0;self.done=False;self.reset=False;self.stall=False
        self.protocol=0;self.error=0;self.bad_length=False;self.reset_fails=False
        self.source=None
        self.core=SimpleNamespace(read=self.core_read,write=self.core_write)
        self.mmio=SimpleNamespace(read=self.dma_read,write=self.dma_write)
        self.recvchannel=SimpleNamespace(transfer=self.recv,wait=lambda:None)
        self.sendchannel=SimpleNamespace(transfer=self.send,wait=lambda:None)
    def core_read(self,offset):
        if offset==0:return 6 if self.done else 4
        if offset==0x20:return self.protocol
        return 0
    def core_write(self,offset,value):
        if offset==0x10:self.h=value
        elif offset==0x18:self.w=value
        elif offset==0:self.done=False
    def recv(self,target):self.target=target
    def send(self,source):
        self.source=np.asarray(source).copy()
        h,w=self.h,self.w
        padded=self.source[:(h+18)*(w+18)].reshape(h+18,w+18)
        assert np.all(self.source[(h+18)*(w+18):]==0)
        assert self.source.size%4==0
        raw=padded[9:9+h,9:9+w]
        self.target[:]=blur3(raw)
        self.length=self.target.nbytes-int(self.bad_length)
        self.done=not self.stall
    def dma_read(self,offset):
        if offset==0:return 4 if self.reset and self.reset_fails else 0
        if offset in (4,0x34):
            if self.reset:return 0 if self.reset_fails else 1
            return self.error or (2 if self.done else 0)
        if offset==0x58:return self.length
        return 0
    def dma_write(self,offset,value):
        if offset==0 and value==4:self.reset=True
    @property
    def overlay(self):
        return SimpleNamespace(axi_dma_blur3=self,blur3_0=SimpleNamespace(mmio=self.core))

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a,cls.exp=common.dependencies(ROOT.parent/"steel_binary_ops_v8_experiment_20261022")

    def test_driver_all_fixtures_and_ownership(self):
        device=FakeDevice()
        d=driver_module.Blur3(device.overlay,Buffer)
        with patch.object(runner,"HERE",ROOT/"hls"):
            for _,raw,expected in runner.fixtures():
                before=raw.copy()
                result,timing=d.blur(raw)
                np.testing.assert_array_equal(result,expected)
                np.testing.assert_array_equal(raw,before)
                self.assertFalse(np.shares_memory(result,d.buffers[1]))
                self.assertEqual(timing["input_bytes"],((raw.shape[0]+18)*(raw.shape[1]+18)+3)&~3)
        raw=np.arange(180,dtype=np.uint8).reshape(12,15)[::2,::3]
        np.testing.assert_array_equal(d.blur(raw)[0],blur3(raw))
        for raw in (np.zeros((0,2),np.uint8),np.ones((3,3),np.float32),np.zeros((513,2),np.uint8)):
            with self.assertRaises(ValueError):d.blur(raw)
        with self.assertRaises(ValueError):d.blur(np.ones((1,1),np.uint8),float("nan"))
        self.assertFalse(d.failed)
        self.assertIn("flush",d.buffers[0].events)
        self.assertIn("invalidate",d.buffers[1].events)
        d.close()
        self.assertTrue(all("free" in b.events for b in d.buffers))

    def test_driver_failure_paths(self):
        for failure in ("stall","bad_length","protocol","error"):
            dev=FakeDevice();setattr(dev,failure,True if failure in ("stall","bad_length") else 16)
            d=driver_module.Blur3(dev.overlay,Buffer)
            with self.assertRaises((RuntimeError,TimeoutError)):
                d.blur(np.ones((2,3),np.uint8),.001)
            self.assertTrue(d.failed)
            with self.assertRaises(RuntimeError):d.blur(np.ones((2,3),np.uint8))
            d.close()
            self.assertTrue(d.recovery_safe)
            self.assertTrue(all("free" in b.events for b in d.buffers))
        dev=FakeDevice();dev.stall=dev.reset_fails=True
        d=driver_module.Blur3(dev.overlay,Buffer)
        with self.assertRaises(TimeoutError):d.blur(np.ones((1,1),np.uint8),.001)
        with self.assertWarns(UserWarning):d.close()
        self.assertFalse(d.recovery_safe)
        self.assertTrue(all("free" not in b.events for b in d.buffers))
        driver_module._RETAINED_BUFFERS.clear()

    def factory(self,cache):
        a,exp=self.a,self.exp
        class Features:
            failed=in_flight=False
            def __init__(self):self.overlay=FakeDevice().overlay
            def process(self,gray):
                key=gray.tobytes()
                if key not in cache:
                    cache[key]=(a.cpu_sobel(gray),a.q8_reference(gray,7),a.q8_reference(gray,13),{})
                return cache[key]
            def close(self):pass
        class Gaussian(exp.base._gaussian_module.GaussianRows):
            def __init__(self):self.failed=self.closed=self.in_flight=False
            def row_pass(self,pixels,kernel_id,timeout_seconds=10.):
                return pixels.copy(),{"kernel_id":kernel_id}
            def close(self):self.closed=True
        def make(*args,**kwargs):
            return exp.base.SteelDefectRuntime(Features(),Gaussian(),
                fixed_model=SimpleNamespace(response=lambda *args:np.zeros((512,512),np.float32)))
        return make

    def test_real_runtime_full_runner_and_guards(self):
        cache={}
        factory=self.factory(cache)
        original_blur=integration.Blur3
        def fake_blur(overlay):return original_blur(overlay,Buffer)
        with tempfile.TemporaryDirectory(prefix="roi_blur3_test_") as tmp:
            folder=Path(tmp);(folder/"evidence").mkdir()
            shutil.copy2(ROOT/"hls/fixtures.bin",folder/"fixtures.bin")
            integrity={"source_package":str(ROOT.parent/"steel_binary_ops_v8_experiment_20261022"),
                       "scope":"HOST MOCK"}
            with patch.object(self.exp.base.SteelDefectRuntime,"open_board",side_effect=factory),patch.object(common,"boot_id",return_value="host-test-boot"),patch.object(integration,"Blur3",side_effect=fake_blur),redirect_stdout(io.StringIO()):
                with patch.object(tail_runner,"HERE",folder/"tail"),patch.object(tail_runner,"initialize_runtime",return_value=integrity):
                    tail_runner._FAILED=False
                    prior=tail_runner.run()
                common.save_json(folder/"evidence/gauss_tail32_full184.json",prior)
                with patch.object(runner,"HERE",folder),patch.object(runner,"initialize_runtime",return_value=integrity):
                    result=runner.run("full")
                    self.assertTrue(result["pass"] and result["hardware_session_safe"])
                    self.assertEqual(len(result["primitives"]),120)
                    self.assertEqual(len(result["frames"]),184)
                    self.assertEqual(sum(x["roi_count"] for x in result["frames"]),403)
                    self.assertEqual(sum(len(x["timing"]["pl"]["blur3_calls"]) for x in result["frames"]),86)
                    self.assertEqual(len(result["supplemental"]),3)
                    self.assertFalse((folder/"hardware_session_guard.json").exists())
                    smoke=runner.run("smoke")
                    self.assertEqual(len(smoke["paired_timing"]),24)
                    self.assertTrue(smoke["pass"])
                    with patch.object(self.exp.base.SteelDefectRuntime,"open_board",side_effect=RuntimeError("programming")):
                        with self.assertRaisesRegex(RuntimeError,"programming"):runner.run()
                    runner._FAILED=False
                    with self.assertRaisesRegex(RuntimeError,"Unfinished"):runner.guard()
                    with patch.object(common,"boot_id",return_value="after-reboot"):runner.guard()
        runner._FAILED=False

    def test_patch_restore_on_downstream_failure(self):
        m=self.exp.base._fast._runtime
        original=m.otsu_dark
        fake=SimpleNamespace(blur=lambda raw:(_ for _ in ()).throw(RuntimeError("injected")))
        with self.assertRaisesRegex(RuntimeError,"injected"):
            with integration.blur_operator(m,fake,[]):m.otsu_dark(np.zeros((3,3),np.uint8))
        self.assertIs(m.otsu_dark,original)

if __name__=="__main__":unittest.main(verbosity=2)
