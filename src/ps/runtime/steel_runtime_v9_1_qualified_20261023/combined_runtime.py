"""Compose the three board-probed PS changes on an unchanged v9 overlay."""
from contextlib import contextmanager
import types
from candidates import scalar_histogram, orientation_int16
from gaussian_candidate import row_pass

CONFIGURATIONS=("baseline","combined","without_histogram")
VERSION="v9_combined_candidate_20261023"

@contextmanager
def configuration(runtime,name):
    if name not in CONFIGURATIONS:raise ValueError("Unknown configuration: "+str(name))
    e=runtime.experiment
    m=e.base._fast._runtime
    driver=runtime.tail.base.gaussian
    with e.LOCK,e.base._MODE_LOCK,e.base._HARDWARE_LOCK:
        original=(driver.row_pass,m.scalar_stats,m.orientation_metrics)
        try:
            if name!="baseline":
                driver.row_pass=types.MethodType(row_pass,driver)
                m.orientation_metrics=orientation_int16
                if name=="combined":m.scalar_stats=scalar_histogram
            yield
        finally:
            driver.row_pass,m.scalar_stats,m.orientation_metrics=original

class CombinedRuntime:
    """A separately named candidate entry; no global default is changed."""
    def __init__(self,board,experiment,bitstream):
        self.runtime=board.RoiBlurRuntime(experiment,bitstream)
        self.closed=False
    def process_frame(self,gray,predictions,mode="combined"):
        if self.closed:raise RuntimeError("Combined runtime closed")
        with configuration(self.runtime,mode):
            result=self.runtime.process_frame(gray,predictions,use_blur=True)
        result["timing"]["combined_version"]=VERSION
        result["timing"]["combined_configuration"]=mode
        return result
    def close(self):
        if self.closed:return
        self.closed=True
        self.runtime.close()
