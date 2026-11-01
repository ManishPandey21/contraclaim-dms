# Mixed-PDF ingestion + two-tier summary — plan

**Date:** 2026-08-13
**Status:** plan only. No code changed.
**Companion:** [contractual_knowledge_extraction_target_design_2026-08-13.md](contractual_knowledge_extraction_target_design_2026-08-13.md)
(the architecture). This document is the *evidence* for that architecture plus two
additions it did not cover: mixed-page handling proven against a real file, and the
short/detailed summary feature.

**Method:** the sample PDF was actually processed with the project's own interpreter
(`backend/.venv`) and its own libraries (`pdfplumber`, `pypdf`), replicating the two
production decision points verbatim — `OCRService.is_pdf_textual(max_pages=5)` and
`contract_ocr_min_text_chars_per_page`. Every number below is measured output, not estimate.

---

## 1. What the sample document is

`92c9ceae-…​.pdf` — 9 pages, 765 KB. Producer metadata: `Canon iR C3226 PDF` /
`Adobe PSL 1.3e for Canon`, created 2024-05-23. It is a **Contractor's revised cost claim**:

> Employer: Uttar Pradesh Metro Rail Corporation · Engineer: General Consultant (TYPSA–ITALFERR JV)
> Contractor: Gulermak-Sam India Kanpur Metro JV · *Reworks due to soil collapse at Nayaganj Station*
> Total Basic Amount **₹22,140,168**

This is the document the master prompt was written from — its example Markdown uses these
exact headings ("Idling of Rig Machine", "Guide Wall & D-Wall Reworks") and the ₹22,140,168
figure appears in its integrity-test section. So it is the right benchmark, and it should
become **fixture #1** of the benchmark corpus.

It is also genuinely "a mix of all":

| Page | Character | Measured |
|---|---|---|
| 1–2 | **Pure scan, zero text layer** | 0 chars, 6 and 4 images, no text |
| 3 | Claim summary table + parties block | 1,213 chars, 3 images, 1 table |
| 4 | Date register (52 rows) **+ a second calculation table side-by-side** | 1,351 chars, 3 tables |
| 5 | **Landscape** (842×595), three tables, two-column narrative | 1,543 chars, 3 tables |
| 6–9 | Cost breakdown tables, long multi-line description cells | 217–998 chars each |

Page classification produced by the probe: `SCANNED_IMAGE` ×2, `MIXED_CONTENT` ×7,
`TABLE_HEAVY` ×7, landscape ×1, rotated 0, blank 0.

---

## 2. What happens today — measured, both paths

### 2.1 General document path: **pages 1–2 are silently lost**

```
OCRService.is_pdf_textual(max_pages=5)
  any text in first 5 pages? True        (page 3 has 1,213 chars)
  => decision: SKIP OCR for the entire document
  pages that would yield ZERO text: 2/9  [1, 2]
```

This is the §2.5 defect of the companion document, now **measured on a real file rather than
argued from the code path**. Pages 1–2 of a formal claim submission are the covering letter —
letter number, date, sender, recipient, subject, signature. Those are precisely the
contractual entities the DMS indexes, links, and drafts from. Today they enter the
evidentiary record as empty.

The document would be marked `completed`, appear fully processed in the UI, and answer
"who sent this and when?" with nothing.

### 2.2 Contract path: correct

```
per-page threshold (contract_ocr_min_text_chars_per_page=40)
  native-text pages kept: 7/9
  pages routed to OCR:    2/9  [1, 2]
  contiguous OCR batches: 1 -> [(1, 2)]
```

Exactly right, and it batches the two scanned pages into a single OCR call. This confirms
the companion document's core claim: **the contract path already implements the target
behaviour; the general path does not.** The fix is to route both through one engine, not to
invent a new pipeline.

---

## 3. Nine defect classes this document exposes

Ordered by contractual risk. Classes 1–3 are new findings that change the plan.

### 3.1 Split-digit corruption in the **native** text layer — 9 instances

Not OCR. This is the PDF's own text layer:

| Page | Extracted | True value | Recoverable by |
|---|---|---|---|
| 3 | `1 ,900,000` | 1,900,000 | column sum + p6 total |
| 3 | `5 77,188` | 577,188 | column sum + p8 total |
| 6 | `9 50,000` | 950,000 | qty×rate, column sum |
| 7 | `7 0,500` | 70,500 | 2,350 × 30, column sum |
| 8 | `1 34,460` | 134,460 | 162 × 830, column sum |
| 8 | `4 42,728` | 442,728 | 286 × 1,548, column sum |
| 9 | `1 10,000` | 110,000 | 1 × 110,000.00 (rate col) |
| 9 | `1 15,000` | 115,000 | 1 × 115,000.00 (rate col) |
| 9 | `4 8,960` | 48,960 | 24 × 2,040 |

**This is the `₹22,140,168 → ₹2,214,016.8` failure mode the brief names, occurring for real,
on the path the brief assumes is trustworthy.** Parsed naively, `1 ,900,000` becomes `1` and
the claim subtotal reads ₹15,972,957 instead of ₹18,450,140 — a ₹2.5 crore error.

**Tested and ruled out:** `pdfplumber` `x_tolerance` tuning does **not** fix it. 17
split-digit tokens survive identically at every setting from 1.0 to 3.0. The glyph spacing
is in the document. So extraction tuning is not the answer — the repair must be structural.

**Design consequence:** *"native text layer ⇒ high confidence"* is false. The numeric
integrity engine must run on **every** page including clean digital ones, not only on
OCR'd or low-confidence pages. This changes the routing in the companion design: quality
scoring is unconditional; only *vision escalation* is conditional.

### 3.2 The document is fully self-verifying — ~20 independent arithmetic identities

Every corrupted value is recoverable **twice over**, deterministically:

```
p3 Σ line items      9,600,000+5,684,775+185,441+1,900,000+211,375+577,188+291,360
                   = 18,450,139  vs stated 18,450,140      ✓ (1 unit, rounding)
p3 overheads         18,450,140 × 20%  = 3,690,028         ✓ exact
p3 grand total       18,450,140 + 3,690,028 = 22,140,168   ✓ exact
p4 calc chain        B/C = 2,000,000/260 = 7,692.31        ✓ (formula declared in-page)
                     D×E = 92,308 · F×2 = 184,615
                     A×G = 52 × 184,615 = 9,600,000        ✓ ties to p3 line 1
p4 row count         52 date rows = "TOTAL 52" = A         ✓ structural cross-check
p5 Σ[i+ii+iii+iv]    2,087,710+313,056+2,990,989+293,020 = 5,684,775  ✓ ties to p3 line 2
p5 material/RMT      3,071.88+2,739.00+3,174.70 = 8,985.58 ✓ ties to row iv rate
p5 Σ volumes         = 598.20 m3  ✓ ties to p5 row i qty AND p3 line 3 (598.20 × 310)
p5 Σ areas           = 747.7473   ✓ ties to row iii qty
p5 Σ lengths         = 32.61      ✓ ties to rows ii and iv qty
p6/p7/p8 totals      tie to p3 lines 4, 5, 6                ✓
```

**This is the strongest argument in the whole programme for a deterministic-first design.**
Every one of the nine corruptions is caught and repaired with zero LLM spend. A vision model
is not needed to read these pages — it is needed only for pages 1–2, which have no text at all.

Recommended repair rule (respects the brief's "do not silently alter values"):

> Accept a numeric repair **only when two independent structural checks agree** (row-level
> qty×rate *and* column-sum-vs-stated-total). Record `before / after / reason / method /
> confidence / page`. One check agreeing, or the two disagreeing ⇒ flag and escalate.
> Never repair on a single check.

### 3.3 Naive numeric checking produces false escalations — costs money

My first probe used "last three numeric columns = qty × rate = amount." It fired **12 false
mismatches**:

- p5 row ii: `32.61 × 3,200 ≠ 313,056` — because a separate **Nos = 3** column applies.
  `3 × 32.61 × 3,200 = 313,056` ✓
- p5 table 2: eight "mismatches" against a table whose formulas are **declared in its own
  header** — `Area A=[h*l]`, `Volume [l*b*h]`. Not qty×rate at all.
- p6/p8: read the `S/N` serial (1, 2) as a quantity.

Every false positive would route a perfectly good page to a paid vision call.

**Design consequence:** the integrity engine must be **column-role aware** — map headers to
roles (`S/N`, `Nos`, `Qty`, `Unit`, `Rate`, `Amount`) before checking, honour in-table
declared formulas, and treat *"roles not confidently identified"* as **not checkable**
rather than **failed**. Tolerance matters too: p7 rows carry a displayed qty of 58 against a
true 57.5, a 0.86% discrepancy that must pass a 1% tolerance rather than escalate.

### 3.4 Mixed date conventions in one column — the highest contractual risk

Page 4's 52-row idle register mixes `M/D/YYYY` and `D/M/YYYY` **in the same column**:

```
2/26/2023 2/27/2023 2/28/2023      -> must be M/D (Feb 26-28)
1/3/2023 2/3/2023 ... 12/3/2023    -> must be D/M (1-12 March)
3/13/2023 ... 3/24/2023            -> must be M/D (Mar 13-24)
3/5/2023, 5/5/2023                 -> genuinely AMBIGUOUS
```

The signature of an Excel export where days ≤ 12 were re-interpreted as months. These 52
dates establish the claim period behind **₹9,600,000** of idling charges and would feed EOT
and chronology.

This collides directly with recent project work — `CLAUDE.md` records the move to day-first
`DD-MM-YYYY` parsing and rendering. A day-first parse of `2/26/2023` yields month 26 and
fails; a silent fallback to month-first would mis-date the March entries.

**Design consequence:** dates get their own integrity check, separate from numerics:
detect mixed convention within a column; use chronological monotonicity as disambiguating
evidence; and where ambiguity survives, **mark the value uncertain and never promote it to
an authoritative chronology entry.** The brief's "dated events as candidates, not
authoritative entries" is exactly right, and this document shows why.

### 3.5 Multi-column reading order destroyed — pages 4 and 5

Page 4 interleaves two side-by-side tables into single lines:

```
5/3/2023 1 Idle A Grab Idle Days 52 Days
6/3/2023 1 Idle B Monthly Rental 2,000,000 INR
```

Page 5 is worse — the narrative sentence is shredded by the adjacent table's header cells:

```
GUIDE WALL & D-WALL REWORKS COST WITH INCLUDING ALL TOOLS ... AS PER THE Depth Thickness Length Volume
DW No Area
SPECIFICATION, DRAWINGS AND DIRECTION OF ENGINEER IN CHARGE ... (mtr) (mtr) (mtr) (m3)
```

A chunk built from this text is unusable for Q&A and unsafe for drafting. Notably
`find_tables()` **did** recover the structures correctly (p4 table 2 = clean Date|Count|Status).
So the fix is available without an LLM: **build chunks from the table structures and the
column-segmented text, not from the flat `extract_text()` stream.**

This is the clearest justification in the corpus for the optional Docling adapter — and
equally, evidence that native table detection already covers much of it. Measure before
enabling Docling.

### 3.6 Landscape page mid-document

Page 5 is 842×595 among eight 595×842 pages. `rotation` is 0, so it is a genuine landscape
page, not a rotated portrait. Page classification and any page-image rendering for vision
must carry per-page dimensions and orientation rather than assuming a document-level page box.

### 3.7 Multi-line description cells

Page 8's description cells run to seven wrapped lines inside one table cell. Naive
line-based clause/row splitting will fragment a single item into seven pseudo-rows and
detach the amount from its description. Table-aware cell extraction handles it; flat text
does not.

### 3.8 Cross-page value dependency

Pages 6–9 each compute a total that is a **line item on page 3**. Chunked independently and
retrieved independently, a question like "what is the mobilization charge?" can return page
6's ₹1,900,000 with no indication it rolls into the page 3 claim. Hierarchical chunking must
carry `section_path` up to the claim summary, and the knowledge layer should record the
roll-up relationship.

### 3.9 Every page carries images (3–6 each)

Even text-bearing pages 3–9 have embedded images — stamps, signatures, logos, scanned
insets. `images > 0` is therefore **not** a scan indicator in this corpus; the text-density
threshold is what discriminates. A classifier keyed on image presence would send all 9 pages
to OCR and triple the cost. The existing `contract_ocr_min_text_chars_per_page` heuristic is
the correct discriminator and should be preserved as-is.

---

## 4. What this evidence changes about the master prompt's plan

The brief's architecture survives. Its **ordering and emphasis do not.**

| Brief says | Evidence says | Change |
|---|---|---|
| Vision is the fallback for low-confidence pages | 7 of 9 pages need **no** model at all; the 9 corruptions are deterministically repairable; only pages 1–2 need OCR and neither needs vision | Move vision later and expect it to fire rarely. Budget from measurement, not guesswork. |
| Confidence gates route to vision | Naive checks produced **12 false mismatches** on a correct document | Column-role awareness is a *prerequisite* for vision routing, not a refinement. Ship it before enabling any paid call. |
| Native text ⇒ trustworthy, OCR ⇒ suspect | 9 corruptions occurred in the **native** layer | Quality scoring must be unconditional. Only escalation is conditional. |
| Numeric integrity as one check among many | It is the mechanism that recovers every corrupted value on this document | Promote to the highest-value deterministic component. |
| Dates listed among many entities | Mixed D/M and M/D in one column, gating ₹9.6M | Dates need a dedicated check and an explicit "uncertain" state. |
| PyMuPDF for Stage 1 | `pdfplumber` + `pypdf` produced every measurement here, including correct table recovery on the hardest page | Confirms the companion document's rejection of the new AGPL dependency. |
| Docling for layout | Native `find_tables()` recovered p4/p5 structures correctly | Keep Docling optional and off by default; enable only where measured to help. |

**One-line summary:** the brief is calibrated for a document that needs an LLM to be read.
This document needs an LLM for **2 of 9 pages**, and needs *arithmetic* for the other 7.

---

## 5. Two-tier summary — short + detailed

### 5.1 Today

A single `summary: Optional[str]` on the document
(`models/document.py:108`, `models/documents.py:27`), populated by regex-scraping a
`Summary:` block out of the OpenAI report text
(`text_processing_service.py:370-387`, `:219-221`). One tier, unstructured, ungrounded, and
dependent on the model having emitted a parseable block.

### 5.2 Proposed shape

Two fields with genuinely different jobs — not a long version and a short version of the
same prose:

```
short_summary   : 1-2 sentences, <= ~240 chars. Card/list/search-result rendering.
                  "What is this document?" Answer: type, parties, subject, headline value.

detailed_summary: structured, sectioned, provenance-carrying. Document detail view,
                  drafting context, arbitration bundles.
                  "What does this document establish?"
```

**The critical design decision: `detailed_summary` is a *view over the canonical JSON*, not
a second LLM pass over raw text.**

That single choice buys everything:

- **Cheap** — the figures, totals, dates, parties, and section hierarchy are already
  extracted and already integrity-checked. Composition is mostly deterministic.
- **Grounded** — every figure it cites carries `page` + `conversion_id` + check status. No
  new hallucination surface.
- **Honest** — values that failed or could not be checked render *as uncertain*, rather
  than being smoothed into confident prose.
- **Regenerable** — a new conversion regenerates the summary without re-querying a model
  for facts it already holds.

The LLM's role narrows to prose connective tissue over a fact table it may not add to. That
is a `strict=True` call with a deterministic fallback per repo convention (`CLAUDE.md`):
if the model call fails, the fallback renders the structured facts as plain sections. The
summary degrades in *style*, never in *accuracy*.

### 5.3 What a good detailed summary of this document looks like

```markdown
**Contractor's revised cost claim — reworks due to soil collapse, Nayaganj Station**

Employer: Uttar Pradesh Metro Rail Corporation Ltd  ·  Engineer: General Consultant
(TYPSA–ITALFERR JV)  ·  Contractor: Gulermak-Sam India Kanpur Metro JV
Date of commencement: 09 March 2021                                         [p.3]

**Claim heads**                                                    verified ✓
| # | Head                              | Amount (INR) | Source | Check |
|---|-----------------------------------|-------------:|--------|-------|
| 1 | Idling of rig machine             |    9,600,000 | p.3/p.4| ✓ 52 × 184,615 |
| 2 | Guide wall & D-wall reworks       |    5,684,775 | p.3/p.5| ✓ Σ i–iv |
| 3 | Concrete filling works            |      185,441 | p.3/p.5| ✓ 598.20 × 310 |
| 4 | Mobilization & demobilization     |    1,900,000 | p.3/p.6| ✓ Σ, repaired ⚠ |
| 5 | Ground improvement                |      211,375 | p.3/p.7| ✓ Σ, repaired ⚠ |
| 6 | Drilling, rebaring, coupler fixing|      577,188 | p.3/p.8| ✓ Σ, repaired ⚠ |
| 7 | Additional equipment purchase     |      291,360 | p.3/p.9| ✓ Σ, repaired ⚠ |
| — | Sub-total                         |   18,450,140 | p.3    | ✓ Σ items |
| — | Overheads @ 20%                   |    3,690,028 | p.3    | ✓ exact |
| — | **Total basic amount**            |**22,140,168**| p.3    | ✓ exact |

**Basis of the idling claim**  52 idle days · monthly rental 2,000,000 ·
260 working hrs · 12-hr shift · 2 shifts/day → 184,615/day            [p.4]

⚠ **Uncertain — requires review**
- 4 amounts were repaired from split-digit text-layer corruption; each confirmed by two
  independent checks. Originals retained.                            [p.3,6,7,8,9]
- The 52 idle dates use mixed D/M and M/D conventions; the claim period
  (≈26 Feb – 24 Aug 2023) is inferred from sequence order, not stated.  [p.4]
- Pages 1–2 are unindexed scans; letter number, date and signatory not
  yet extracted.                                                      [p.1,2]
```

The uncertainty block is the point. A summary that quietly said "claim period 26 Feb – 24
Aug 2023" would be asserting something the document does not unambiguously state, in a
system whose output feeds arbitration.

### 5.4 Schema and placement

Additive only:

```
documents.short_summary       : str
documents.detailed_summary    : { markdown, facts[], generated_from_conversion_id,
                                  model, prompt_version, generated_at, degraded: bool }
documents.summary             : KEEP — becomes an alias/back-fill of short_summary
                                for one release so no consumer breaks
```

Generation belongs in the **normalizer stage** of the companion design's pipeline (Phase 5),
after integrity checks and before indexing — so the summary can never describe values the
quality engine has not seen. Regeneration on a new conversion is automatic; the summary is
keyed to `conversion_id` and superseded with it.

RBAC: no new surface. Summaries live on the document and inherit its scope.

---

## 6. Revised phase plan

Changes to the companion document's plan, driven by §3–§4. Phases 0–4 remain the whole
integrity win at zero LLM cost.

| Phase | Change vs companion doc | Why |
|---|---|---|
| **0** | **Add:** turn this PDF into fixture #1 with a golden-values file (all ~20 identities, the 9 corruptions, the 52 dates, the 2 scanned pages). **Add:** sample production documents for pages with zero extracted characters. | Quantifies §2.1's blast radius; gives every later phase a pass/fail gate. |
| **1** | unchanged — extract `PageExtractionEngine`, contract path first | |
| **2** | unchanged, now with a measured acceptance test: **pages 1–2 must reach OCR** | §2.1 |
| **3** | **Expanded and re-scoped.** Quality engine gains: (a) column-role mapping before any numeric check; (b) in-table declared-formula support; (c) split-digit detection + **dual-confirmation repair**; (d) a dedicated date-convention checker with an `uncertain` state; (e) 1% rounding tolerance. Runs on **all** pages, native included. | §3.1–§3.4. This phase now carries most of the value. |
| **3b** | **New.** Structure-first chunk assembly: build chunks from detected tables + column-segmented text, never from flat `extract_text()`. | §3.5, §3.7 |
| **4** | unchanged — conversion activation and vector supersession | |
| **5** | **Add:** two-tier summary as a view over canonical JSON (§5). | user request |
| **6** | unchanged, but **budget it from Phase 3 measurements**. On this document, expected vision spend after Phases 1–3 is **zero**. | §4 |
| **7** | unchanged — Docling optional, enable only where measured to beat native `find_tables()` on fixtures | §3.5 |
| 8–10 | unchanged | |

---

## 7. Recommendations

1. **Build the benchmark harness in Phase 0, before any pipeline change.** This document
   alone yields ~20 arithmetic assertions, 9 known corruptions with known true values, 52
   ambiguous dates, and 2 zero-text pages. That is a genuine regression gate, and it exists
   today at the cost of writing down expected values. Everything after is measurable.

2. **Re-order the brief: arithmetic before AI.** Phases 1–3 fix the ₹2.5-crore error class,
   recover the lost covering letter, and cost nothing per document. Vision (Phase 6) should
   be enabled only after the deterministic layer is measured, because every false escalation
   is real money and this document produced 12 of them under a naive rule.

3. **Treat "native text layer" as untrusted for numerics.** This is the single most
   counter-intuitive finding and it inverts the brief's confidence model. Score every page.

4. **Never silently normalize a date in this corpus.** Mixed-convention columns are present
   in real claim registers and gate EOT entitlement. Uncertain must stay uncertain, and must
   not enter chronology as authoritative.

5. **Make the detailed summary a view over extracted facts, not a second LLM pass.** It is
   cheaper, auditable, regenerable, and structurally incapable of inventing a figure the
   pipeline never extracted.

6. **Sample production before planning a backfill.** Every document ingested through the
   general path under the 5-page heuristic may be missing scanned pages. The query is cheap
   — count documents whose page count exceeds their pages-with-text count. Do it in Phase 0;
   the answer decides whether backfill is a footnote or a project.

---

## 8. Open questions

1. **Repair vs flag-only.** §3.2 proposes auto-repairing split digits when two independent
   checks agree. The conservative alternative is to flag all nine and require human review.
   Auto-repair is defensible and fully audited, but it does write a value the document
   renders differently. **This is a product/legal call, not an engineering one** — the
   output feeds arbitration. My recommendation is auto-repair with dual confirmation and a
   visible "repaired" marker, but I have not assumed it.
2. **Date disambiguation by sequence.** Using chronological monotonicity to resolve
   `1/3/2023` as 1 March is inference. Acceptable for a *candidate* chronology entry;
   not acceptable silently. Confirm the candidate-review workflow exists before relying on it.
3. **Summary length and audience.** Is `detailed_summary` for the claims engineer (figures
   and checks, as drafted in §5.3) or for a reviewer wanting narrative? The §5.3 shape
   assumes the former.
4. **Where the detailed summary renders.** Document detail view only, or also injected into
   drafting and arbitration context? The latter raises its accuracy bar and argues further
   for the facts-table design.

---

## 9. Verification status

- **Measured, reproducible:** page count, per-page character counts, image/table/vector
  counts, orientation, both production routing decisions, the 9 split-digit corruptions, the
  x_tolerance non-fix (6 settings), the 12 false-positive numeric mismatches, and the
  arithmetic identities in §3.2 — all produced by running `backend/.venv` against the file
  in this session. Probe scripts are in the session scratchpad, not the repo.
- **Read from source:** the current single-`summary` implementation (§5.1) with file:line.
- **Inference, flagged as such:** that pages 1–2 are the covering letter — they contain no
  text layer, so this is inferred from position, page size, and the document's structure.
  It cannot be confirmed without running OCR, which this host cannot do (no `tesseract` on
  the Windows host; the backend container has it). **Confirm during Phase 0** by OCR-ing
  pages 1–2 inside the backend container.
- **Not attempted:** no production data was queried, no migration run, no code changed.
