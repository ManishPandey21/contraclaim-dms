# Reference Page Error Fix - TODO

Goal: Fix runtime error on ReferencePage.tsx: `(r.raw || "").toLowerCase is not a function` and ensure backend returns correct data for references.

Status: Completed.

Changes Implemented:

1. Backend: Normalize parsed references source

   - File: backend/rbac_backend/services/document_service.py
   - Function: list_references
   - Parsed references are now built from `document.reference` (extracted metadata) instead of `document.references` (linked objects).
   - Normalization:
     - Accepts Array[str] or Array[object] with keys: letter_no, letterNo, raw.
     - Accepts multi-line string; splits by lines, trims.
     - Always returns `{"parsed": List[str], "linked": [...]}`.

2. Frontend: Robust guards around toLowerCase usage
   - File: client/src/pages/ReferencePage.tsx
   - Replaced `(r.raw || "").toLowerCase()` with `String(r.raw ?? "").toLowerCase()` in:
     - Parsed references dedupe key.
     - Search filter for parsed references.

Verification Steps:

- Restart backend and frontend dev servers if running.
- Open a reference page: `/reference/:id`.
- The page should render without the previous error.
- Parsed References tab should list parsed references and support search.
- Linked References tab should render linked references correctly.

Notes:

- The backend endpoint GET `/documents/{id}/references` now correctly separates:
  - `parsed`: strings (extracted from metadata `reference` field)
  - `linked`: detailed objects built from `document.references` with letterNo/title/date enrichment.
