import os
import shutil
import sys
import tempfile
from pathlib import Path

# Isolated, offline config BEFORE ppfe is imported (settings are cached).
_tmp = Path(tempfile.mkdtemp(prefix="ppfe-test-"))
ROOT = Path(__file__).resolve().parents[1]
os.environ.update({
    "PPFE_DATA_DIR": str(_tmp / "data"),
    "PPFE_MODEL_DIR": str(_tmp / "models"),
    "PPFE_OFFLINE": "1",
    "PPFE_HRRR_SOURCE": "synthetic",
    "PPFE_INFERENCE_BACKEND": "local",
    "PPFE_MAX_WORKERS": "1",
})
(_tmp / "models").mkdir(parents=True)
for f in (ROOT / "models").glob("*.onnx"):
    shutil.copy(f, _tmp / "models" / f.name)
sys.path.insert(0, str(ROOT / "src"))
