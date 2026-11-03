"""PS candidates only; no new hardware and no automatic promotion."""
from contextlib import contextmanager
import types
import cv2
import numpy as np
from gaussian_candidate import row_pass

VARIANTS=("gaussian_direct_pad","gray_histogram","orientation_int16")

def scalar_histogram(raw):
    raw=np.asarray(raw)
    if raw.dtype!=np.uint8 or raw.ndim!=2 or not raw.size or max(raw.shape)>512:
        raise ValueError("Histogram candidate expects nonempty uint8 ROI <=512x512")
    counts=cv2.calcHist([raw],[0],None,[256],[0,256]).ravel()
    # Counts <= 262144 are exact in float32; cumulative integer counts are exact.
    cumulative=np.cumsum(counts,dtype=np.int64)
    positions=(raw.size-1)*np.asarray([.5,.1,.9,.95],np.float64)
    lower=np.floor(positions).astype(np.int64)
    upper=np.ceil(positions).astype(np.int64)
    a=np.searchsorted(cumulative,lower,side="right").astype(np.float64)
    b=np.searchsorted(cumulative,upper,side="right").astype(np.float64)
    weight=positions-lower
    quantiles=np.where(weight>=.5,b-(b-a)*(1-weight),a+(b-a)*weight)
    # Keep the qualified float32 mean/std algorithm, not an algebraic substitute.
    values=np.asarray(raw,dtype=np.float32)
    rounded=lambda x:round(float(x),6)
    return {"mean":rounded(values.mean()),"median":rounded(quantiles[0]),
            "p10":rounded(quantiles[1]),"p90":rounded(quantiles[2]),
            "p95":rounded(quantiles[3]),"std":rounded(values.std())}

def orientation_int16(gray,threshold=64,bins=12):
    gray=np.asarray(gray)
    if gray.dtype!=np.uint8 or gray.ndim!=2 or not gray.size:
        raise ValueError("Expected uint8 grayscale ROI")
    gx=cv2.Sobel(gray,cv2.CV_16S,1,0,ksize=3,borderType=cv2.BORDER_CONSTANT)
    gy=cv2.Sobel(gray,cv2.CV_16S,0,1,ksize=3,borderType=cv2.BORDER_CONSTANT)
    # Each gradient is in [-1020,1020]; L1 magnitude fits signed 16 bits.
    selected=np.abs(gx)+np.abs(gy)>=threshold
    selected[[0,-1],:]=False
    selected[:,[0,-1]]=False
    count=int(np.count_nonzero(selected))
    if not count:return {"strong_edge_pixel_count":0,"entropy":None,"coherence":None}
    # Cast before transcendental operations, preserving float32 angles/hypot
    # and the original pixel/accumulation order.
    x=gx[selected].astype(np.float32);y=gy[selected].astype(np.float32)
    angles=np.mod(np.arctan2(y,x),np.pi)
    weights=np.hypot(x,y).astype(np.float64)
    hist,_=np.histogram(angles,bins=bins,range=(0,np.pi),weights=weights)
    probabilities=hist/max(float(hist.sum()),1e-9)
    nonzero=probabilities>0
    entropy=-float(np.sum(probabilities[nonzero]*np.log(probabilities[nonzero])))/np.log(bins)
    vector=np.sum(weights*np.exp(2j*angles))/max(float(weights.sum()),1e-9)
    return {"strong_edge_pixel_count":count,"entropy":round(float(entropy),6),
            "coherence":round(float(abs(vector)),6)}

@contextmanager
def variant(runtime,name):
    if name not in ("baseline",)+VARIANTS:raise ValueError("Unknown candidate")
    experiment=runtime.experiment
    module=experiment.base._fast._runtime
    driver=runtime.tail.base.gaussian
    with experiment.LOCK,experiment.base._MODE_LOCK,experiment.base._HARDWARE_LOCK:
        old_row,old_scalar,old_orientation=driver.row_pass,module.scalar_stats,module.orientation_metrics
        try:
            if name=="gaussian_direct_pad":driver.row_pass=types.MethodType(row_pass,driver)
            elif name=="gray_histogram":module.scalar_stats=scalar_histogram
            elif name=="orientation_int16":module.orientation_metrics=orientation_int16
            yield
        finally:
            driver.row_pass=old_row
            module.scalar_stats=old_scalar
            module.orientation_metrics=old_orientation
