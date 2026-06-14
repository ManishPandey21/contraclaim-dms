import sys
import os
import tempfile
import importlib
import json
from pathlib import Path

# Ensure backend is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# Monkeypatch UPLOADS_DIR to a likely non-writable path to force fallback
from rbac_backend.core import config as _config
_settings = _config.settings
if os.name == 'nt':
    _settings.UPLOADS_DIR = r"C:\Windows\System32\restricted\uploads"
else:
    _settings.UPLOADS_DIR = "/root/restricted/uploads"

# Reload modules to recompute BASE_UPLOAD_DIR using resolve_uploads_dir()
import rbac_backend.routers.contracts as _contracts
_contracts = importlib.reload(_contracts)

import rbac_backend.services.contracts_ingest as _ingest
_ingest = importlib.reload(_ingest)

results = {}

# 1) Verify contracts.BASE_UPLOAD_DIR chosen is writable (fallback works)
resolved_dir = _contracts.BASE_UPLOAD_DIR
results["contracts_BASE_UPLOAD_DIR"] = resolved_dir

write_ok = False
try:
    os.makedirs(resolved_dir, exist_ok=True)
    testfile = os.path.join(resolved_dir, f".test_write_{os.getpid()}.txt")
    with open(testfile, "wb") as f:
        f.write(b"test")
    os.remove(testfile)
    write_ok = True
except Exception as e:
    results["contracts_write_error"] = str(e)
results["contracts_write_ok"] = write_ok

# 2) Exercise _preprocess_pdf_with_ocr() path fallback (OCR may be unavailable; copy should succeed)
tmp_pdf = os.path.join(tempfile.gettempdir(), f"test_{os.getpid()}.pdf")
with open(tmp_pdf, "wb") as f:
    f.write(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF")

dest_path, sidecar, ocr = _contracts._preprocess_pdf_with_ocr(tmp_pdf, language="eng")
results["preprocess_dest_exists"] = os.path.exists(dest_path)
results["preprocess_dest_path"] = dest_path.replace("\\", "/")
results["preprocess_sidecar_exists"] = bool(sidecar and os.path.exists(sidecar))
results["preprocess_ocr_performed"] = bool(ocr)

# 3) Record ingest module resolved BASE_UPLOAD_DIR (for consistency)
results["ingest_BASE_UPLOAD_DIR"] = _ingest.BASE_UPLOAD_DIR

# Write results to file for inspection
out_path = os.path.abspath(os.path.join("tmp", "contracts_critical_test.out.json"))
os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, "w", encoding="utf-8") as out:
    json.dump(results, out, indent=2)

print(out_path)
