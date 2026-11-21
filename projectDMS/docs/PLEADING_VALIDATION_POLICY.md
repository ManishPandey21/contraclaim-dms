# Pleading validation: ledger context exemption

**Policy:** `pleading-ledger-context-exemption`, version **1**
**Hash:** the SHA-256 of the canonical policy object, `citation_states.CONTEXT_EXEMPTION_POLICY_HASH` (recorded on every version and validation artifact; `dc8d5437926780b2c8d19fb17f51086257d6d68e59f234bcede4b5d6fcb87d3c` at introduction).
**Code:** `backend/rbac_backend/services/arbitration_drafting/citation_states.py` (`CONTEXT_EXEMPTION_POLICY`, `is_exempt_context_row`).
**Status:** owner **conditional approval** 2026-11-18 (JG-5(f)). **Not legal sign-off.** The authorised legal reviewer must approve this filing-validation policy before production release. Until then it is in code on the feature branch only; nothing is merged or deployed.

## What changed

Before JG-5, every pleading ledger contained rows the builders add automatically as context: counsel's approved analysis (issue matrix, limitation / pre-arbitration / scope checks), which readiness requires, and the project registers (claims, variations, payment certificates, bank guarantees), whose status is a workflow status ("submitted", "notified"), never an evidence verification. Their mere presence counted as "user-supplied or unverified selected evidence" (an approval blocker) and as "manual fact sources" (a warning, so the version could not reach `passed`). No Statement of Claim that had passed readiness could be approved.

Under this policy an uncited, untouched, in-scope context row no longer blocks or warns. Everything else is unchanged.

## Controls (owner's conditions)

| # | Condition | Enforcement | Tests |
|---|---|---|---|
| 1 | Only server-classified context rows | `ledger_context` is written only by the case-analysis and register builders (`context.py`); a selected reference's projection sets it to `None`. It must also agree with the row's `source_type` and `source_origin`; none of the three alone is enough | `test_a_row_the_server_did_not_classify_as_context_still_blocks` (5 forgeries), `test_a_selected_reference_cannot_become_context` |
| 2 | Cited or relied-upon unverified sources still block | A context row cited by key, or whose reference number (a citation containing a digit, e.g. `CLM-009`) appears in the text, is not exempt and is checked as a citation | `test_a_relied_upon_context_row_is_checked_as_a_citation` (key alone, key and reference, reference alone) |
| 3 | Missing, stale, altered, unauthorised or cross-case evidence never exempt | Never exempt: statuses stale, revoked, withdrawn, authority_denied; flags authority_denied, not_verified_for_filing, unverified_ai_suggestion; user-supplied register rows; revision or content drift; a `context_scope` other than this draft's case (analysis) or project (registers), or none. Evidence rows are never context | `test_missing_stale_altered_unauthorised_or_cross_case_context_is_never_exempt` (13 cases) |
| 4 | Analysis distinct from verified evidence | Analysis rows stay `is_user_supplied` in the ledger; citing one is an unverified citation with the reason "Case analysis ... is not evidence and cannot be cited as a source" | `test_cited_case_analysis_is_refused_as_a_source_of_fact`, `test_only_an_approved_server_classified_analysis_row_is_case_analysis` |
| 5 | Every amount, date, clause and name has provenance or a gap | The validator's supported text is built from evidence rows and draft inputs only; context rows support nothing. The existing same-sentence citation and `[Evidence required]` rules are unchanged | `test_context_rows_never_support_an_amount_date_or_clause` |
| 6 | Context rows never enter filed material | Pleading DOCX/PDF render `full_markdown` only. The deterministic generator's "List of Relied-Upon Documents / Annexures" omits context rows. The filing bundle (DOCX, PDF, ZIP, manifest) carries each approved version as a filing projection whose source schedule is evidence only (`_filing_projection`), after the stored version's hash and validation are checked | `test_the_generated_annexure_list_names_no_context_row`, `test_the_filed_source_schedule_carries_evidence_only`; journey: pleading DOCX and PDF, bundle DOCX, PDF and ZIP, manifest and ZIP source schedules contain no context row |
| 7 | Regression, security and mutation tests | `tests/test_jg5_pleading_path.py` (51); `tests/integration/test_claim_journey_http_mongo.py` | 13 boundary mutants, each failing a test (see the register) |
| 8 | Version, hash and audit trail | Every version's `structured_output` records `context_policy` {name, version, hash} and `context_rows_exempted`; the `generated` and `version_saved` audit events carry both; workflow validation artifacts record the policy and the source-drift branch honours a version's exemptions only under the policy hash now in force (a policy change forces re-validation); the filed source schedule carries the policy stamp | `test_the_workflow_honours_only_exemptions_recorded_under_the_current_policy`, `test_uncited_server_classified_context_does_not_block_and_is_recorded`; journey asserts the stamp and the exempted keys |

## Known limits

- "Relied upon" is detected by key citation and by a context row's reference number appearing in the text. A sentence that paraphrases a register entry without its reference or key is not detected; the factual-assertion rule (same-sentence citation of verified evidence, or `[Evidence required]`) still applies to it, and context rows cannot support its amounts, dates, clauses or names.
- Filing bundles assembled before this change, or by a path other than `_filing_bundle_drafts`, are not covered; no other path exists in this repository.

## Changing this policy

Any change to the classes, the never-exempt conditions or the controls changes the hash. Bump `version`, record the change here with the new hash, and obtain legal review again. Versions validated under an earlier hash lose their recorded exemptions in workflow validation and must be re-validated.
