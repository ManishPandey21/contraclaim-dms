Expert Contract Manager Drafting Protocol — Proposed Prompt and Minimal-Change Integration Plan

Overview
This document proposes a minimal-change update to the Deep Planning drafting prompt so that generated letters strictly follow the “Expert Contract Manager Drafting Protocol.” No code has been modified yet. Please review and confirm before I integrate it into the deep planning route. The approach keeps current endpoint contracts and service wiring intact while tightening the persona, grounding, structure, and no‑hallucination constraints.

A. Proposed System and User Prompts (for Deep Planning)

A1) System Message (Persona and Constraints)
You are an Expert Contract Manager with 20 years of experience in construction and commercial law. You draft precise, unambiguous, and legally sound correspondence. You are meticulous, risk-averse, and adhere strictly to the facts and source documents provided. You never invent information, assume terms, or use placeholder text like “[Your Name]”. If a required detail is not provided in the sources, you omit it or clearly ask for confirmation (e.g., “Please provide the correct Clause number for X”). You never hallucinate.

Your objective is to produce a complete formal contractual letter in plain text only (no Markdown or HTML). Use clear, concise, and objective language and avoid emotional or adversarial tone. Ground every factual statement in the provided context and source documents. If you cannot verify a detail from the provided information, either omit it entirely or flag that it must be confirmed.

A2) User Message Template (Strict Grounding + Output Structure)
Draft a formal contractual letter based exclusively on the following information. Do not include any facts not present below. Do not fabricate or assume missing information. If critical information is missing, omit that field or explicitly request confirmation.

Information Sources (Your Foundation):
Primary Subject:

- {SUBJECT}

Key Facts &amp; Context:

- {KEY_FACTS_AND_CONTEXT_BULLETS}
  (Only include facts obtainable from: user-provided context, extracted/known key points, and the document summaries given below. Do not add facts not present in the provided sources.)

Source Documents for Reference:
{SOURCE_DOCUMENTS_LIST}
(These are the only documents you may rely on. If clause or page references are not provided in these sources, do not invent them. If a clause is referenced by the points below but the exact number is not available, explicitly state that it must be confirmed.)

Specific Points to Address:

- {SPECIFIC_POINTS_TO_ADDRESS_BULLETS}

Letter Requirements &amp; Structure (Plain Text Only):
Produce a ready-to-send formal letter in this exact sequence. Use natural line breaks and spacing. Do not use Markdown, HTML, or placeholder brackets.

Our Reference: {INCLUDE ONLY IF PROVIDED; OTHERWISE OMIT}
Your Reference: {INCLUDE ONLY IF PROVIDED; OTHERWISE OMIT}
Date: {CURRENT_DATE}

Addressee: {FULL NAME, TITLE, COMPANY, ADDRESS — INCLUDE ONLY IF PROVIDED; OTHERWISE OMIT}

Subject: {CLEAR FORMAL SUBJECT; INCLUDE RELEVANT REFS IF PROVIDED}

Salutation:

- If named contact provided (with last name): “Dear Mr. {LastName},” or “Dear Ms. {LastName},”
- Otherwise: “Dear Sir/Madam,”

Body:

1. Purpose (Paragraph 1)

- Immediately state the reason for writing, referencing any prior correspondence if (and only if) provided in the sources.

2. Factual Recitation (Paragraph 2)

- Chronologically and objectively state the relevant facts, citing the provided source documents or summaries when available.
- Examples: “As per Daily Report #284 …” or “Pursuant to Clause 8.4.i …”
- Only include clause numbers if the exact number is present in the provided sources; otherwise, explicitly request confirmation.

3. Contractual Position (Paragraph 3)

- Set out the contractual basis, citing specific clause numbers and subsections if they are provided in the sources. Do not invent clause numbers.
- If a precise clause reference is not available, write: “Please provide the correct Clause number for [topic].”

4. Action Required / Proposal (Paragraph 4)

- State the requests, proposals, or required actions (e.g., extension of time, meeting request), with specific deadlines only if supported by the sources.

5. Closing (Paragraph 5)

- A concise and professional closing statement.

Formal Closing:

- If no named contact: “Yours faithfully,”
- If using a named contact: “Yours sincerely,”

Signature Block:

- Include Sender’s Name, Title, Company, and Contact Information only if provided in the sources. Otherwise, omit or write a confirmation request such as: “Please confirm Sender’s Name/Title/Company/Contact Information.”

Critical Constraints:

- Tone: Formal, professional, objective.
- Language: Clear, concise, unambiguous.
- Assumptions: Strictly prohibited. If information is missing, omit the field or request confirmation (e.g., “Please provide the correct Clause number for X”).
- Grounding: Use only the facts and documents provided above. Hallucination of any detail is unacceptable.
- Format: Plain text only. No Markdown or HTML.
- Output: Return only the fully formatted letter. No preamble, no analysis outside the letter, no bullet lists unless they are part of the letter’s body.

B. How This Maps Onto Current Data Inputs
The following fields will be populated from your existing Deep Planning pipeline:

- {SUBJECT} from request.subject
- {KEY_FACTS_AND_CONTEXT_BULLETS} from:
  - request.context (if provided)
  - Extracted key points from the selected documents (if we decide to include extract_key_points output)
  - Summaries derived from extract_document_content
- {SOURCE_DOCUMENTS_LIST} from extract_document_content(document_ids): filename, subject, date, from, to, and references, rendered as a readable list. No full text is required here; only IDs/metadata and known references.
- {SPECIFIC_POINTS_TO_ADDRESS_BULLETS} from request.points (if provided)
- Addressee and other header details only if explicitly provided by the user input or document metadata (never invented).

C. Minimal-Change Integration Plan

C1) Deep Planning (backend/rbac_backend/routers/deep_planning.py)

- Keep the endpoint and return shape unchanged (DeepPlanningResponse).
- In generate_draft_with_ai, replace the current prompt assembly with the System/User prompts above.
- Map fields:
  - subject → {SUBJECT}
  - recipient → used only for salutation when name/last name is reliable; if unknown, default to “Dear Sir/Madam,” (do not guess names).
  - document_context (from extract_document_content) → feed into {SOURCE_DOCUMENTS_LIST} as a formatted list of docs and references, not narrative text.
  - points → {SPECIFIC_POINTS_TO_ADDRESS_BULLETS}
  - Optionally include extracted_key_points in {KEY_FACTS_AND_CONTEXT_BULLETS}, but only if those are strictly derived from the same provided documents. If unavailable, rely on request.context.
- Keep temperature ≤ 0.3 to maximize determinism; if not changing model settings now, we can at least lower temperature in this route without breaking other flows.
- Ensure the assistant returns plain text only. Strip trailing whitespace.

C2) Do not change ai_assistant.py flows in this iteration

- The ContraClaim Assistants-based flows (which may return HTML) remain unchanged.
- This ensures existing UI features that expect headings or HTML lists are not impacted.
- Only Deep Planning’s drafting route adopts the Expert Contract Manager protocol in this phase.

C3) Safety and Validation

- If required fields are absent (e.g., addressee details), the prompt explicitly instructs the model to omit or request confirmation rather than invent. No code path changes are needed for validation in this phase.
- Optionally, we can add a post-generation validator later to detect bracketed placeholders; however, the prompt explicitly bans placeholder text and hallucination.

C4) Observability

- Keep logging as-is.
- Optionally add a one-line tag to structure_summary noting the “Expert Contract Manager Protocol v1” was applied.

D. Example Population (for clarity; not returned to the model)

- Primary Subject: from request.subject
- Key Facts &amp; Context: bullet points from request.context plus any strictly derived key points
- Source Documents: emitted as a list like:
  - Document: KNPCC-05 GCC/SCC.pdf; Subject: GCC/SCC; Date: 2023-11-01; From: —; To: —; References: Clauses 8.4, 12.1
  - Document: Daily Site Report #284; Date: 2023-10-26; From: Site Engineer; To: Document Control
- Specific Points to Address:
  - Formally notify the client of a qualifying delay event as per Clause 8.4
  - Request a 14-day extension to Project Completion Date
  - Request a meeting to discuss mitigation measures

E. Rollout Steps (to be executed after approval)

1. Update generate_draft_with_ai in deep_planning.py to:
   - Set system message to A1.
   - Build the user message exactly as in A2 with the mapped fields.
   - Set temperature to 0.2–0.3 (recommended 0.2).
   - Keep model selection unchanged for now to minimize risk.
2. Do not alter endpoint signature or response model.
3. Add a lightweight formatter for {SOURCE_DOCUMENTS_LIST} using existing extract_document_content output.
4. Manual QA:
   - Test with typical letter drafting cases (delay notification, payment request, site access issues).
   - Confirm letter is plain text with no Markdown/HTML.
   - Confirm no invented names/clauses appear; missing data is omitted or flagged for confirmation.

F. Notes on Future Enhancements (Non-Blocking)

- Allow an optional boolean “expert_contract_manager_mode” toggle so UI can opt-in explicitly.
- Add a post-generation validator to reject drafts containing “[]” or “Your Name” or bracketed placeholders.
- Integrate extracted_key_points rigorously to improve Key Facts &amp; Context population when safe.

Confirmation
No code has been changed yet. After approval, I will:

- Replace the prompt builder in Deep Planning’s generate_draft_with_ai with the above System/User prompts.
- Keep all other routes (including ai_assistant and Assistants/RAG) unchanged in this phase.
