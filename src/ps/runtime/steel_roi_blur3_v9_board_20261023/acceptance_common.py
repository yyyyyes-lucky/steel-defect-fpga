"""Verified helper functions extracted unchanged from prior probe."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
BASE_NAME="steel_binary_ops_v8_experiment_20261022"

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dependencies(root):
    sys.path.insert(0, str(root))
    for name in ("experiment_runtime",):
        old = sys.modules.get(name)
        if old is not None and Path(old.__file__).resolve() != root / (name+".py"):
            raise RuntimeError("Conflicting runtime module; restart kernel")
    spec = importlib.util.spec_from_file_location("_gauss_roi_acceptance", root / "run_board_acceptance.py")
    a = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(a)
    import experiment_runtime as experiment
    # Validate module access before programming hardware (the previous v1 bug).
    if not hasattr(experiment.base._fast._runtime, "orientation_metrics"):
        raise RuntimeError("Missing frozen ROI implementation")
    return a, experiment

def initialize_runtime(base_root=None):
    root = (Path(base_root) if base_root else HERE.parent / BASE_NAME).resolve()
    a, _ = dependencies(root)
    return {"source_package": str(root), "package": str(HERE),
        "manifest_sha256": sha(HERE / "manifest.json"), "source_integrity": a.inspect_hardware()}

def boot_id():
    p = Path("/proc/sys/kernel/random/boot_id")
    return p.read_text().strip() if p.is_file() else "unknown"

def save_json(p, data):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
