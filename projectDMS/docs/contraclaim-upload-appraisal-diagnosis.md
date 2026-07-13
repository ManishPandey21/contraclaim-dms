# ContraClaim — Contract Upload & Appraisal Failure: Cross-Environment Diagnosis

**Date:** 2026-06-30
**Test account:** superadmin@example.com (Super Admin)
**Org / Project:** KPIL-GULERMARK JV / KNPCC-11
**Files:** Vol_1..Vol_5 KNPCC11 PDFs (0.64 MB, 1.1 MB, 9.6 MB, 1.2 MB, 3.3 MB)
**Environments compared:** Production (`web.contraclaim.com` → `api.contraclaim.com`) and Local (`localhost:5173` → backend `:8000` via Vite proxy)

---

## Headline

The contract **upload fails**, and because nothing finishes uploading/indexing, the **AI appraisal has no source content** ("generation error", "0% supported"). The underlying cause is the same in both environments: **the backend runs OCR / text-extraction synchronously and blocks its worker, so concurrent requests get `503 Service Unavailable` and the client gives up at its 20-second timeout.**

The single most important piece of evidence: on **local**, the login endpoint returned `200` at first, then started returning **`503`** the moment the upload's OCR processing began — i.e. the heavy upload work starved even authentication. The production symptom (`upload-multipart` → `503`) is the same starvation pattern.

---

## What was observed

### Production (`api.contraclaim.com`)

Upload network sequence on "Upload Contracts":

| Request | Method | Status |
|---|---|---|
| `/api/contracts/upload-session` | POST | 200 |
| `/api/contracts/upload-multipart` | POST | **503** (repeated, with client retries) |

- The UI sat on "Uploading…" indefinitely; the **Upload Progress** panel then showed **every file** — including the small 0.64 MB and 1.1 MB ones — as **`failed`, 0%, "timeout of 20000ms exceeded"**, all via the `multipart` path.
- Probing `/api/contracts/upload-multipart` directly:
  - Empty `POST` → `422` (FastAPI validation: `files` field required) — **the route and app are healthy for trivial requests**.
  - `GET` → `405 Method Not Allowed`.
  - The `503` only occurs when an **actual file payload / real processing** is involved.
- Appraisal page (`/contracts/appraisal`): an appraisal already existed for KPIL-GULERMARK JV / KNPCC-11 but every section read **"Requires Human Review (generation error)"**, with badges **"Completeness needs review", "Risk: critical", "0% supported"**, and the document picker said **"No completed contracts. Upload one under 'Upload Contract'."**

### Local (`localhost:5173` → `:8000`)

- Org/Project pre-resolved correctly; all 5 files staged; `Upload Contracts` clicked.
- Upload network sequence:

| Request | Method | Status |
|---|---|---|
| `/api/contracts/upload-session` | POST | 200 |
| `/api/contracts/upload-multipart` | POST | **200** ✅ (works locally) |
| `/api/contracts/status?upload_id=…` | GET | polling |

- Upload Progress then showed **Vol_1 "processing 20% — Running OCR / text preparation"** (real progress, unlike prod), while **Vol_2 failed with "timeout of 20000ms exceeded"**, and Vol_3/4/5 queued. Note Vol_3 (9.6 MB) used the `chunked` path; the others used `multipart`.
- Mid-processing the session **dropped to the login screen**, and subsequent **login attempts returned `503`** (`/api/login`: first `200`, then `503`, `503`, `503`…), surfaced in the UI as **"timeout of 20000ms exceeded"**. Login stayed unavailable for the duration of OCR — the backend was saturated.

---

## Root cause

1. **Synchronous, blocking OCR/text-extraction on the request worker.** The upload handler performs heavy CPU work (OCR / text preparation) inline. While it runs, the worker can't serve anything else.
2. **Too few workers / no work offloading.** With the worker blocked, other requests (login, status polling, the next file's upload) return **`503`** or hang. We reproduced this locally by watching `/api/login` flip from `200` to `503` once OCR started.
3. **Client-side global timeout is far too short.** The frontend uses a **20,000 ms (20 s)** request timeout for everything. Upload + OCR routinely exceeds 20 s, so even when the server is making progress the client aborts with "timeout of 20000ms exceeded" and marks the file failed (then retries, compounding the load).
4. **Production `upload-multipart` 503.** Same starvation, plus likely a reverse-proxy/gateway in front of the API with limited workers and/or a short `proxy_read_timeout`, returning `503` when the upstream worker is busy. (The endpoint itself is healthy for trivial requests — `422`/`405` — so it is not a missing route.)
5. **Appraisal failure is downstream, not a separate bug.** "generation error / 0% supported / No completed contracts" simply reflects that no contract ever finished uploading and being indexed into clauses, so the clause-grounded appraisal has nothing to cite.

### Why it "works on local" (sometimes)
Local has no front proxy and the very first request runs before OCR starts, so the initial `upload-multipart` returns `200`. But the same blocking behavior then starves the single dev worker — which is why login began returning `503` and files hit the 20 s timeout. The difference is timing/proxying, not a fundamentally different code path.

---

## Recommended fixes (backend unless noted)

1. **Offload OCR/extraction to a background queue** (Celery / RQ / arq + a separate worker, or FastAPI `BackgroundTasks` at minimum). The upload endpoint should persist the file, enqueue processing, and return immediately; the client already polls `/api/contracts/status`, so drive completion from there.
2. **Run multiple workers and keep request handlers non-blocking.** `gunicorn -k uvicorn.workers.UvicornWorker --workers N`; run CPU-bound OCR in a process pool so one job can't freeze the event loop / starve other requests. This alone removes the `503`-under-load behavior.
3. **Raise / scope the client timeout (frontend).** The global 20 s axios timeout is the proximate cause of the "timeout of 20000ms exceeded" failures. Either raise it substantially for upload/processing calls, or (better) make uploads fully async + status-polled so requests return fast and the timeout never applies to long work.
4. **Production proxy config.** Check nginx/ingress `client_max_body_size`, `proxy_read_timeout`/`proxy_send_timeout`, and upstream worker count/health; ensure the gateway doesn't 503 on long-running multipart requests. Confirm any storage backend (S3/MinIO/GridFS) the multipart path depends on is configured in prod.
5. **Re-test appraisal after uploads succeed.** Once contracts reach "completed" and clauses are indexed, regenerate the appraisal (delete the existing errored Version 1 first — the UI requires deleting it to regenerate).

---

## Status of the requested run
- Production: upload could not complete (`upload-multipart` 503); existing appraisal shows generation errors.
- Local: uploads started and OCR began (`upload-multipart` 200), but the backend became saturated by synchronous OCR, returning `503` to login and timing out other files. Could not reach the local appraisal step until the backend frees up or is restarted with the fixes above.
