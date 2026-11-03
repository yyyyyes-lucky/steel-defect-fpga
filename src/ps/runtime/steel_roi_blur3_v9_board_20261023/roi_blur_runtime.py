"""Qualified tail32 plus optional blur3, ellipse remains PS."""
from contextlib import contextmanager
import cv2
import numpy as np
from integrated_runtime import Tail32Runtime
from blur3_driver import Blur3

@contextmanager
def blur_operator(module,driver,calls):
    original=module.otsu_dark
    def otsu_dark(roi):
        smooth,timing=driver.blur(roi)
        calls.append(timing)
        threshold,dark=cv2.threshold(smooth,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
        dark=module.filter_components(dark,20)
        selected,rejected=smooth[dark>0],smooth[dark==0]
        dark_mean=float(selected.mean()) if selected.size else None
        bright_mean=float(rejected.mean()) if rejected.size else None
        contrast=None if dark_mean is None or bright_mean is None else bright_mean-dark_mean
        return dark,{"otsu_threshold":module.rounded(threshold),
            "dark_mean":None if dark_mean is None else module.rounded(dark_mean),
            "bright_mean":None if bright_mean is None else module.rounded(bright_mean),
            "gray_group_contrast":None if contrast is None else module.rounded(contrast)}
    module.otsu_dark=otsu_dark
    try:yield
    finally:module.otsu_dark=original

class RoiBlurRuntime:
    def __init__(self,experiment,bitstream):
        self.experiment=experiment
        self.tail=Tail32Runtime(experiment,bitstream)
        self.closed=self.failed=False
        try:self.blur=Blur3(self.tail.base.features.overlay)
        except BaseException:
            self.tail.close()
            raise

    def process_frame(self,gray,predictions,use_blur=True):
        if self.failed or self.closed:raise RuntimeError("ROI runtime unavailable")
        e=self.experiment
        calls=[]
        try:
            with e.LOCK,e.base._MODE_LOCK,e.base._HARDWARE_LOCK:
                if use_blur:
                    with blur_operator(e.base._fast._runtime,self.blur,calls):
                        result=self.tail.process_frame(gray,predictions)
                else:result=self.tail.process_frame(gray,predictions)
            expected=sum(p["class_id"]==2 for p in predictions) if use_blur else 0
            if len(calls)!=expected:raise AssertionError("Blur3 routing/call count differs")
            result["timing"]["roi_blur_version"]="steel_roi_blur3_v9_20261023"
            result["timing"]["blur3_requested"]=bool(use_blur)
            result["timing"]["blur3_calls"]=calls
            return result
        except BaseException:
            self.failed=True
            raise

    def close(self):
        if self.closed:return
        self.closed=True
        try:self.blur.close()
        finally:self.tail.close()
        if self.blur.in_flight or self.blur.failed or not self.blur.recovery_safe:
            raise RuntimeError("Blur3 failure; reboot board")
