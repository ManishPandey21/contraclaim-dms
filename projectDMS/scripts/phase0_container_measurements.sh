#!/usr/bin/env bash
# Phase 0 container measurements.
#
# Answers two questions that cannot be answered on the Windows dev host:
#   1. Does `ocrmypdf --pages 1-2` emit a 2-page PDF or the full document?
#      Task 1.1's page mapping depends on the answer.
#   2. Does the deployed ClamAV scan inside RAR archives?
#      Phase 5 admits .rar uploads on the assumption that it does.
#
# READ-ONLY: writes only to a temporary directory, touches no application data.
#
# Run INSIDE the backend container:
#   docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
#     exec -T backend bash /app/scripts/phase0_container_measurements.sh
set -uo pipefail

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "=================================================="
echo "1. ocrmypdf --pages output shape"
echo "=================================================="

python - "$WORK/in.pdf" <<'PY'
import sys
import pikepdf

pdf = pikepdf.new()
for _ in range(9):
    pdf.add_blank_page(page_size=(595, 842))
pdf.save(sys.argv[1])
pdf.close()
print("INPUT_PAGE_COUNT: 9")
PY

if ocrmypdf --pages 1-2 --force-ocr --output-type pdf \
      "$WORK/in.pdf" "$WORK/out.pdf" >"$WORK/ocr.log" 2>&1; then
  echo "ocrmypdf exit: 0"
else
  echo "ocrmypdf exit: NONZERO"
  tail -20 "$WORK/ocr.log"
fi

if [ -f "$WORK/out.pdf" ]; then
  python - "$WORK/out.pdf" <<'PY'
import sys
import pikepdf

with pikepdf.open(sys.argv[1]) as pdf:
    print("MEASURED_OUTPUT_PAGE_COUNT:", len(pdf.pages))
PY
else
  echo "MEASURED_OUTPUT_PAGE_COUNT: NO OUTPUT PRODUCED - measurement incomplete"
fi

echo
echo "=================================================="
echo "2. ClamAV RAR support"
echo "=================================================="

clamscan --version 2>&1 || echo "clamscan not present in this container"

python - "$WORK/probe.rar" <<'PY'
import sys

# Minimal RAR v4 signature. Enough to see whether clamd recognises the format.
with open(sys.argv[1], "wb") as handle:
    handle.write(b"Rar!\x1a\x07\x00" + b"\x00" * 64)
print("wrote RAR probe")
PY

clamdscan --stream "$WORK/probe.rar" 2>&1 || echo "clamdscan returned non-zero (see above)"

echo
echo "clamd.conf archive settings:"
grep -iE "^[[:space:]]*(ScanRAR|ScanArchive|MaxRecursion|MaxFileSize)" \
  /etc/clamav/clamd.conf 2>/dev/null || echo "clamd.conf not readable from this container"

echo
echo "=================================================="
echo "Measurement complete. Paste this output verbatim into"
echo "docs/architecture/phase0_extraction_measurements_2026-08-14.md"
echo "=================================================="
