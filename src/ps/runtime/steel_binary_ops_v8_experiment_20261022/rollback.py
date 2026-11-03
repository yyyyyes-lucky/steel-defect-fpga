"""Explicit return to the immutable baseline; never call after unsafe DMA failure."""
from run_board_acceptance import OLD_BIT, sha256

def restore():
    import run_board_acceptance
    if run_board_acceptance._FAILED_HARDWARE_SESSION:
        raise RuntimeError('Previous hardware test failed; reboot board before rollback')
    from pynq import Overlay
    overlay = Overlay(str(OLD_BIT), download=True)
    if "axi_dma_ellipse" in overlay.ip_dict:
        raise RuntimeError("Unexpected ellipse IP in baseline overlay")
    print("BASELINE_OVERLAY_RESTORED=True")
    print("BIT_SHA256=" + sha256(OLD_BIT))
    print("恢复的是旧硬件位流；正式业务请重新打开原正式版 Notebook。")
    return overlay

if __name__ == "__main__":
    restore()
