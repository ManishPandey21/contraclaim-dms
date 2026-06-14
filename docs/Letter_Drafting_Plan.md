# Letter Drafting Plan for ContraClaim DMS

## 1. Purpose of This Document

This document defines the proposed workflow for implementing a **Letter Planning and Drafting Module** in **ContraClaim DMS** for contractual correspondence in infrastructure and construction projects.

The module shall support two drafting conditions:

1. **Reply Letter** — drafting a response to an incoming letter from Contractor / Engineer / Employer.
2. **Fresh Letter** — drafting a new contractual letter on any subject without necessarily linking it to an incoming letter.

The core principle of the module shall be:

> **First Plan → Then Validate → Then Draft → Then Review → Then Issue**

The objective is to ensure that every letter is drafted based on verified facts, contract provisions, previous correspondence, user-confirmed instructions, and project knowledge base records.

---

## 2. Proposed Module Name

**Letter Planning & Drafting Assistant**

This module should be available within ContraClaim DMS as a dedicated workflow for preparing contractual correspondence.

---

## 3. Supported Drafting Modes

| Mode | Purpose |
|---|---|
| Reply Letter Mode | Draft a reply against an existing incoming letter uploaded in DMS |
| Fresh Letter Mode | Draft a new contractual letter on any subject |

---

## 4. Common Workflow for Both Modes

The module should follow a structured workflow common to both reply letters and fresh letters.

### Step 1: Select Drafting Mode

The user should first select the drafting type:

```text
What do you want to draft?

[ ] Reply to an existing letter
[ ] Fresh contractual letter
```

---

### Step 2: Select Project and Contract Package

The user should select the relevant project context.

| Field | Example |
|---|---|
| Organization | UPMRC / GC / Contractor |
| Project | Kanpur Metro / Agra Metro |
| Contract Package | KNPCC-05 / KNPCC-06 / KNPAGDDC-01 |
| User Role | GC / Engineer / Employer / Contractor |

This is essential so that the AI retrieves only project-specific clauses, documents, correspondence, and records.

---

### Step 3: Select Letter Category

The user should classify the letter.

| Category | Use Case |
|---|---|
| Claim Reply | Reply to cost / time / prolongation claim |
| EOT Reply | Reply to extension of time request |
| Variation | Variation proposal, approval, rejection, or recommendation |
| Payment / IPC | IPC processing, withholding, recovery, or payment certification |
| Advance Recovery | Mobilisation advance or other advance recovery |
| TOC / Completion | Taking Over Certificate, statement at completion, final account |
| NCR / Quality | Non-conformance, rectification, quality compliance |
| Delay / Progress | Slow progress, delay notice, programme issue |
| Records Request | Contemporary records, substantiation, claim support |
| Arbitration / Dispute | Dispute notice, arbitration reply, conciliation record |
| General Contractual Letter | Any other contractual communication |

---

### Step 4: Gather Basic Inputs from User

The system should collect basic drafting inputs from the user before planning.

| Input | Required |
|---|---|
| Letter subject | Yes |
| Recipient | Yes |
| Sender role | Yes |
| Purpose of letter | Yes |
| Contract package | Yes |
| Required tone | Yes |
| Deadline / urgency | Optional |
| Key points to include | Yes |
| Desired conclusion | Yes |
| Attachments / references | Optional |

Suggested user input questions:

```text
1. What is the subject of the letter?
2. Who is the letter addressed to?
3. Who is issuing the letter?
4. What is the main purpose of the letter?
5. What decision / instruction should be communicated?
6. What key facts should be included?
7. Are there any contract clauses to be relied upon?
8. Are there any previous letters / documents to be referred?
9. Should the tone be firm, neutral, advisory, or conciliatory?
10. Is any timeline / deadline to be mentioned?
```

---

## 5. Workflow 1: Reply Letter Mode

Reply Letter Mode shall be used when a letter has been received from Contractor / Engineer / Employer and a formal reply is required.

---

### Step 1: Select Incoming Letter

The user should select an uploaded incoming letter from DMS.

The system should auto-fetch the following data:

| Data | Source |
|---|---|
| Incoming letter number | Metadata / OCR |
| Incoming letter date | Metadata / OCR |
| Sender | Metadata |
| Recipient | Metadata |
| Subject | Metadata / AI extraction |
| Main content | Document text |
| References cited in incoming letter | AI extraction |
| Attachments | DMS linked files |
| Linked correspondence | Reference tracking / chain ID |

---

### Step 2: AI Extracts Key Points from Incoming Letter

The AI should prepare a structured analysis of the incoming letter.

Suggested output format:

```text
Incoming Letter Analysis:

1. Letter No.:
2. Date:
3. Sender:
4. Recipient:
5. Subject:
6. Main request / claim:
7. Contract clauses cited by sender:
8. Amount claimed, if any:
9. Time extension requested, if any:
10. Documents submitted by sender:
11. Action requested from GC / Engineer / Employer:
12. Response deadline, if mentioned:
13. Potential contractual risk:
```

---

### Step 3: User Confirms or Corrects Extracted Points

Before planning, the system should show the extracted points to the user.

Suggested UI buttons:

```text
[Confirm Extracted Points]
[Edit Points]
[Add Missing Point]
```

This step is important because OCR or AI extraction may miss important contractual details.

---

### Step 4: System Retrieves Related Records from Knowledge Base

ContraClaim should search the project knowledge base for:

1. Previous correspondence on the same subject.
2. Earlier replies issued by GC / Engineer / Employer.
3. Contract clauses.
4. BOQ / Schedule provisions.
5. Approved drawings / instructions.
6. IPC / payment records.
7. Site records / minutes of meetings.
8. Claim or dispute history.
9. Previous AI drafts or approved letters.

Recommended retrieval filters:

| Filter | Requirement |
|---|---|
| Same project | Mandatory |
| Same contract package | Mandatory |
| Same sender / recipient | Preferred |
| Same subject keywords | Mandatory |
| Same clause | Preferred |
| Date range | Optional |
| Letter chain ID | Strongly preferred |

---

### Step 5: Prepare Reply Planning Sheet

Before drafting, the system should prepare a **Reply Planning Sheet**.

| Item | Details |
|---|---|
| Incoming Letter | Letter number and date |
| Subject | Subject of reply |
| Sender’s Main Point | What the sender has requested |
| Contractual Position | Applicable clause / provision |
| Previous Correspondence | Relevant previous letters |
| Factual Position | Site / payment / progress facts |
| Missing Records | Records not submitted by sender |
| Recommended Response | Accept / reject / seek clarification / partly accept |
| Tone | Firm / neutral / conciliatory |
| Action Required | What recipient must do |
| Risk Note | Any dispute / claim risk |
| Rights Reservation Needed | Yes / No |

---

### Step 6: Prepare Point-Wise Reply Matrix

For reply letters, the AI should prepare a matrix before drafting.

| Incoming Letter Point | Proposed Reply | Source / Basis |
|---|---|---|
| Contractor’s contention / request | Proposed response | Clause / previous letter / record |

This ensures that the reply addresses all important points raised in the incoming letter.

---

### Step 7: Completeness Check Before Drafting

The system should verify whether sufficient information is available.

| Condition | System Action |
|---|---|
| Critical facts missing | Block drafting and ask user questions |
| Minor facts missing | Proceed with placeholders |
| Sufficient facts available | Generate draft |

Critical missing inputs may include:

1. Incoming letter not selected.
2. Contract package not selected.
3. Main issue unclear.
4. Contractual position unclear.
5. Required decision not confirmed.
6. Previous correspondence not available where necessary.
7. Amount / date / event details missing.

Suggested block message:

```text
DRAFT BLOCKED: Insufficient source material.

Required inputs:
1. Please confirm the contractual clause to be relied upon.
2. Please confirm whether the claim is to be rejected or records are to be sought.
3. Please provide previous related correspondence, if available.
```

---

### Step 8: Generate Draft Reply Letter

The AI should draft the reply letter using the following structure:

1. Letter header.
2. Subject.
3. References.
4. Opening paragraph.
5. Background facts.
6. Contractual provisions.
7. Point-wise reply to sender’s contentions.
8. Observations / analysis.
9. Decision / instruction.
10. Action required.
11. Reservation of rights.
12. Professional closing.

---

### Step 9: Prepare Source Integrity Notes

After generating the draft, the system should show source integrity notes.

```text
Source Integrity Notes:

1. Clause relied upon:
2. Previous letters used:
3. Facts taken from records:
4. User-provided instructions:
5. Placeholders requiring confirmation:
6. Items not supported by available documents:
```

This helps prevent unsupported drafting and hallucinated facts.

---

### Step 10: User Review and Edit

The user should be able to:

1. Edit the draft manually.
2. Ask AI to make the tone firmer.
3. Ask AI to make the tone more conciliatory.
4. Add or remove points.
5. Insert clause reference.
6. Generate shorter version.
7. Generate detailed contractual version.
8. Convert the draft to formal letter format.

Suggested UI buttons:

```text
[Make Firmer]
[Make More Polite]
[Add Contractual Reasoning]
[Add Clause Reference]
[Make Short]
[Make Detailed]
[Regenerate]
[Approve Draft]
```

---

### Step 11: Approval and Finalization

The system should save the following:

| Record | Purpose |
|---|---|
| Draft version | Version history |
| User edits | Audit trail |
| Final approved draft | Issued letter record |
| Linked incoming letter | Correspondence chain |
| Status update | Workflow tracking |

Suggested status flow:

| Stage | Status |
|---|---|
| Draft requested | Under Process |
| Draft generated | Draft Prepared |
| Under review | Pending Review |
| Approved | Approved for Issue |
| Issued | Replied |

---

## 6. Workflow 2: Fresh Letter Mode

Fresh Letter Mode shall be used where there is no incoming letter, but the user wants to issue a new contractual letter.

Examples:

1. Instruction to Contractor.
2. Notice for slow progress.
3. Request for contemporary records.
4. Recovery notice.
5. Statement at Completion request.
6. TOC-related letter.
7. Warning for non-compliance.
8. Payment withholding notice.
9. General contractual clarification.
10. Submission / recommendation to Engineer or Employer.

---

### Step 1: User Selects Fresh Letter Mode

```text
Draft Type: Fresh Contractual Letter
```

---

### Step 2: User Provides Subject and Purpose

The system should ask:

```text
1. What is the subject of the letter?
2. Why is this letter being issued?
3. Who is the recipient?
4. What action do you want from the recipient?
5. What is the contractual basis?
6. Is this letter a notice, instruction, request, recommendation, or warning?
```

---

### Step 3: User Selects Letter Purpose

| Purpose | Example |
|---|---|
| Instruction | Instruct Contractor to submit revised programme |
| Notice | Notice for slow progress |
| Request | Request for substantiation / records |
| Recovery | Recovery of advance / overpayment |
| Approval | Approval subject to conditions |
| Rejection | Rejection of proposal / rate / claim |
| Recommendation | Recommendation to Employer / Engineer |
| Clarification | Clarify contractual position |
| Completion | TOC / Statement at Completion / Final Account |

---

### Step 4: System Gathers Mandatory Inputs

| Field | Required |
|---|---|
| Project / Contract package | Yes |
| Sender | Yes |
| Recipient | Yes |
| Subject | Yes |
| Purpose | Yes |
| Background facts | Yes |
| Relevant clause | Preferred |
| Action required | Yes |
| Timeline for compliance | Optional |
| Previous related letters | Optional |
| Attachments | Optional |
| Tone | Yes |

---

### Step 5: AI Searches Knowledge Base for Supporting Material

For fresh letters, the system should search:

1. Contract clauses related to the subject.
2. Similar previous letters.
3. Related correspondence chain.
4. Site records / minutes of meetings.
5. IPC / payment records.
6. Variation / EOT / claim records.
7. Approved formats and templates.

Example:

If the subject is:

> Submission of Statement at Completion after issue of TOC

The system should retrieve:

1. TOC letter.
2. Completion date.
3. Testing and commissioning records.
4. GCC Clause 11.7.
5. Previous correspondence on IPC / completion.
6. Any instruction that no further IPC will be processed after TOC.

---

### Step 6: Prepare Fresh Letter Planning Sheet

| Item | Details |
|---|---|
| Letter Purpose | Notice / instruction / request / approval |
| Subject | Draft subject |
| Recipient | Contractor / Engineer / Employer |
| Background | Why the letter is required |
| Trigger Event | Event requiring the letter |
| Contractual Basis | Clause / provision |
| Factual Basis | Records supporting the letter |
| Previous Correspondence | Related references |
| Decision / Instruction | What is to be communicated |
| Action Required | What recipient must do |
| Timeline | Compliance period |
| Tone | Firm / neutral / advisory |
| Risk Level | Low / medium / high |
| Rights Reservation | Required / not required |

---

### Step 7: Completeness Check Before Drafting

For fresh letters, the following inputs are critical:

1. Subject.
2. Recipient.
3. Purpose.
4. Project / contract package.
5. Factual background.
6. Required action.
7. Contractual basis, where the letter is claim-related, payment-related, dispute-related, or contractual in nature.

If critical information is missing, the system should ask:

```text
Please provide the following before drafting:

1. What is the trigger event for this letter?
2. What action should the recipient take?
3. Should the letter rely on any specific GCC / SCC clause?
```

---

### Step 8: Generate Fresh Letter Draft

The AI should use the following structure:

1. Letter header.
2. Subject.
3. References, if any.
4. Opening paragraph.
5. Background / trigger event.
6. Contractual provision.
7. Observations.
8. Instruction / request / decision.
9. Timeline for compliance.
10. Consequence of non-compliance, if required.
11. Reservation of rights.
12. Professional closing.

---

### Step 9: User Review and Refinement

The user should be able to select:

```text
[Make Firmer]
[Make More Polite]
[Add Contractual Reasoning]
[Add Clause Reference]
[Make Short]
[Make Detailed]
[Convert to Employer Submission]
[Convert to Contractor Letter]
```

---

### Step 10: Save and Link Fresh Letter

For fresh letters, the system should create:

| Record | Purpose |
|---|---|
| New outgoing letter | DMS record |
| Draft version | Draft history |
| AI planning sheet | Reasoning / planning record |
| Supporting sources | Audit / traceability |
| Linked documents | Future retrieval |
| Status | Draft / approved / issued |

---

## 7. Combined End-to-End Process Flow

```text
Start
  ↓
Select Drafting Mode
  ↓
Select Project / Contract Package
  ↓
Select Letter Category
  ↓
Gather User Inputs
  ↓
Retrieve Relevant Documents from Knowledge Base
  ↓
Prepare Letter Planning Sheet
  ↓
Check Completeness
  ↓
If Critical Inputs Missing → Ask User Questions
  ↓
If Sufficient → Generate Draft
  ↓
Show Source Integrity Notes
  ↓
User Review / Edit
  ↓
Approval Workflow
  ↓
Generate Final Letter
  ↓
Save in DMS and Link Correspondence
End
```

---

## 8. Suggested UI Screens

### Screen 1: Drafting Mode Selection

```text
Create Letter

Choose drafting type:

[ Reply to Existing Letter ]
[ Fresh Contractual Letter ]
```

---

### Screen 2: Project and Contract Selection

```text
Organization:
Project:
Contract Package:
User Role:
Letter Category:
```

---

### Screen 3A: Reply Letter Input Screen

```text
Select Incoming Letter:
Auto-extracted Subject:
Auto-extracted Sender:
Auto-extracted Date:
Auto-extracted Key Points:

User Instructions:
[Text Box]

Desired Reply Position:
[Accept] [Reject] [Seek Records] [Partly Accept] [Recommend Approval] [Other]
```

---

### Screen 3B: Fresh Letter Input Screen

```text
Subject:
Recipient:
Purpose:
Background Facts:
Required Action:
Contract Clause:
Timeline:
Tone:
User Instructions:
```

---

### Screen 4: Planning Sheet Review

```text
Drafting Plan Generated

1. Issue:
2. Facts:
3. Contractual basis:
4. Previous correspondence:
5. Recommended position:
6. Required action:
7. Missing information:
8. Risk note:
```

Suggested buttons:

```text
[Approve Plan and Draft Letter]
[Edit Plan]
[Add More Inputs]
```

---

### Screen 5: Draft Review

```text
Draft Letter Generated

Available Actions:
[Regenerate]
[Make Firmer]
[Make Polite]
[Add Clause Reasoning]
[Shorten]
[Detailed Version]
[Approve Draft]
[Export to DOCX/PDF]
```

---

## 9. Backend Services

Suggested backend service files:

| Service | Function |
|---|---|
| `letter_planning_service.py` | Creates planning sheet |
| `draft_input_validator.py` | Checks missing inputs |
| `retrieval_service.py` | Fetches clauses and previous letters |
| `letter_drafting_service.py` | Generates draft |
| `source_validation_service.py` | Checks whether draft is source-supported |
| `letter_version_service.py` | Saves draft versions |
| `letter_workflow_service.py` | Updates approval and issue status |

---

## 10. Suggested API Endpoints

```text
POST /api/letter-drafting/start
POST /api/letter-drafting/collect-inputs
POST /api/letter-drafting/prepare-plan
POST /api/letter-drafting/validate-inputs
POST /api/letter-drafting/generate-draft
POST /api/letter-drafting/revise-draft
POST /api/letter-drafting/approve
POST /api/letter-drafting/export
```

---

## 11. Data Model Suggestions

### 11.1 LetterDraftRequest for Reply Letter

```json
{
  "mode": "reply",
  "project_id": "string",
  "contract_package": "KNPCC-05",
  "incoming_document_id": "string",
  "letter_category": "claim_reply",
  "sender_role": "GC",
  "recipient_role": "Contractor",
  "subject": "string",
  "user_instructions": "string",
  "desired_position": "reject_and_seek_records",
  "tone": "firm_contractual",
  "required_action": "submit substantiation",
  "timeline_days": 7,
  "clauses_to_consider": ["GCC 17.1"],
  "linked_documents": []
}
```

---

### 11.2 LetterDraftRequest for Fresh Letter

```json
{
  "mode": "fresh",
  "project_id": "string",
  "contract_package": "KNPCC-05",
  "letter_category": "completion",
  "sender_role": "GC",
  "recipient_role": "Contractor",
  "subject": "Submission of Statement at Completion",
  "background_facts": "TOC issued w.e.f. 17.02.2026 after completion of testing and commissioning.",
  "required_action": "Submit Statement at Completion under GCC Clause 11.7",
  "tone": "firm_contractual",
  "timeline_days": 7,
  "clauses_to_consider": ["GCC 11.7"],
  "linked_documents": ["TOC letter"]
}
```

---

## 12. AI Prompt Flow

The AI should work in two stages:

1. Planning stage.
2. Drafting stage.

---

### 12.1 Stage 1: Planning Prompt

```text
You are ContraClaim DMS Letter Planning Assistant.

Do not draft the letter yet.

First prepare a contractual letter planning sheet based on:
1. User inputs
2. Incoming letter, if any
3. Project knowledge base
4. Contract clauses
5. Previous correspondence
6. Site/payment/progress records

Classify the letter as:
- Reply Letter, or
- Fresh Contractual Letter

Identify:
- Purpose
- Issues
- Facts
- Contractual basis
- Missing information
- Recommended position
- Required action
- Risk level
- Whether rights reservation is required

If critical information is missing, do not draft. Ask specific questions.
```

---

### 12.2 Stage 2: Drafting Prompt

```text
You are ContraClaim DMS Contractual Letter Drafting Assistant.

Draft a formal contractual letter using only:
1. Confirmed user inputs
2. Retrieved project documents
3. Verified contract clauses
4. Previous correspondence
5. Approved planning sheet

Do not invent facts, dates, clauses, amounts, or references.

If any minor information is missing, use [CONFIRM: ...] placeholder.

The draft must include:
1. Subject
2. References
3. Opening paragraph
4. Background facts
5. Contractual basis
6. Analysis / observations
7. Decision / instruction
8. Action required
9. Reservation of rights, if applicable
10. Professional closing
```

---

## 13. Input Gathering Logic

### 13.1 Reply Letter Input Questions

For Reply Letter Mode, the system should ask:

1. Which incoming letter do you want to reply to?
2. What is the desired response?
   - Accept
   - Reject
   - Seek substantiation
   - Partly accept
   - Recommend approval
   - Put on record
3. Should the reply be firm or conciliatory?
4. Are there any specific points to include?
5. Should any clause be specifically cited?
6. Is any amount / recovery / deduction involved?
7. Is any deadline to be given?
8. Should rights be reserved?
9. Should previous correspondence be considered?
10. Should the draft be short or detailed?

---

### 13.2 Fresh Letter Input Questions

For Fresh Letter Mode, the system should ask:

1. What is the subject?
2. Why is the letter being issued?
3. Who is the recipient?
4. What action is required from the recipient?
5. What is the contractual basis?
6. What facts/events triggered the letter?
7. Are any previous letters to be referred?
8. Is any deadline to be given?
9. Is any consequence of non-compliance to be mentioned?
10. Should the tone be firm, neutral, or advisory?

---

## 14. Validation Rules

### 14.1 The System Should Not Draft If:

1. Project is not selected.
2. Recipient is not known.
3. Subject is missing.
4. User has not confirmed the purpose.
5. Incoming letter is missing in Reply Letter Mode.
6. Contractual position is unclear in claim / dispute / payment matters.
7. Amounts are mentioned but not verified.
8. Clause references are uncertain.
9. The draft requires previous correspondence but none is available.

---

### 14.2 The System May Draft With Placeholders If:

1. Exact letter number is missing.
2. Exact date is missing.
3. Attachment list is not finalized.
4. Timeline is to be confirmed.
5. Name / designation is to be confirmed.

Example placeholders:

```text
[CONFIRM: Letter No.]
[CONFIRM: Date]
[CONFIRM: Clause reference]
[CONFIRM: Amount]
```

---

## 15. Recommended AI Output Format

Every AI drafting action should produce three outputs:

### 15.1 Drafting Plan

```text
Purpose:
Issues:
Facts:
Clauses:
Recommended position:
Required action:
Missing inputs:
Risk note:
```

---

### 15.2 Draft Letter

The formal contractual draft letter.

---

### 15.3 Source Integrity Notes

```text
Documents relied upon:
Clauses relied upon:
User-provided facts:
AI assumptions:
Placeholders requiring confirmation:
Unsupported points excluded:
```

---

## 16. Special Rules for ContraClaim

### Rule 1: No Direct Draft Without Planning

AI should first prepare a plan. Draft should be generated only after plan validation.

---

### Rule 2: No Hallucinated Clause

If the clause text is not available in the knowledge base, AI should say:

```text
Clause reference not found in project knowledge base. Please upload or confirm the relevant clause.
```

---

### Rule 3: No Unsupported Dates or Amounts

AI should not invent:

1. Dates.
2. Amounts.
3. Clause numbers.
4. Letter numbers.
5. Contractual events.
6. Completion status.
7. Payment status.

---

### Rule 4: Reply Must Address Incoming Letter Points

For reply letters, AI should prepare a point-wise reply matrix:

| Incoming Letter Point | Proposed Reply | Source / Basis |
|---|---|---|

---

### Rule 5: Fresh Letter Must Have Trigger Event

Fresh letters must clearly mention why the letter is being issued.

Example:

> Pursuant to issuance of the Taking Over Certificate w.e.f. 17.02.2026 and completion of testing and commissioning of system works, the Contractor is required to submit the Statement at Completion in accordance with GCC Clause 11.7.

---

## 17. Practical Example: Reply Letter Flow

### User Input

```text
Mode: Reply Letter
Incoming letter: Contractor Letter No. ABC/479
Subject: Load bearing wall claim
Desired position: Reject claim
Key point: All walls are load bearing walls. Employer-provided drawings do not entitle extra payment.
Tone: Firm contractual
```

### System Planning Output

```text
Issue:
Contractor has claimed extra payment / adjustment regarding wall design.

Contractual Position:
Check BOQ, Employer’s Requirements, design responsibility, drawings provision, and payment schedule.

Recommended Reply:
Reject additional payment if scope already includes such work and no separate payable item exists.

Action:
Inform Contractor that no extra payment / deduction is admissible.
```

---

## 18. Practical Example: Fresh Letter Flow

### User Input

```text
Mode: Fresh Letter
Subject: Submission of Statement at Completion
Recipient: Contractor
Trigger: TOC issued w.e.f. 17.02.2026
Clause: GCC 11.7
Instruction: Submit Statement at Completion
Additional point: Since TOC is issued, no further IPC will be processed
Tone: Firm contractual
```

### System Planning Output

```text
Purpose:
To instruct Contractor to submit Statement at Completion after TOC.

Contractual Basis:
GCC Clause 11.7.

Decision:
Contractor to submit Statement at Completion. No further IPC shall be processed after TOC.

Action Required:
Submit statement within specified time.
```

---

## 19. Implementation Priority

### Phase 1: Basic Drafting Module

1. Reply / Fresh mode selection.
2. Manual user input form.
3. Basic planning sheet.
4. AI draft generation.
5. Save draft and version history.

---

### Phase 2: Knowledge-Based Drafting

1. Retrieve previous letters.
2. Retrieve contract clauses.
3. Retrieve linked correspondence.
4. Generate source integrity notes.
5. Add citation / source validation.

---

### Phase 3: Workflow Integration

1. Draft approval workflow.
2. Status update: Under Process / Replied / Issued.
3. DOCX / PDF export.
4. Email issue integration.
5. Audit log.

---

### Phase 4: Advanced Contract Intelligence

1. Clause validation.
2. Claim risk scoring.
3. Point-wise reply matrix.
4. Similar past letter suggestions.
5. No-hallucination checker.
6. Contractual position recommendation engine.

---

## 20. Final Recommended Workflow

```text
1. User opens Letter Planning & Drafting Module
2. User selects Reply Letter or Fresh Letter
3. User selects Project / Contract Package
4. User selects Letter Category
5. System gathers required inputs
6. System retrieves relevant documents and clauses
7. AI prepares Drafting Plan
8. System checks missing information
9. User confirms / edits plan
10. AI generates draft letter
11. AI shows source integrity notes
12. User reviews and edits draft
13. Draft goes for approval
14. Final letter is issued
15. DMS updates correspondence chain and letter status
```

---

## 21. Core Implementation Principle

ContraClaim should always follow this drafting discipline:

> **Reference → Facts → Contract Clause → Analysis → Decision → Action Required → Reservation of Rights**

This will make the drafting process professional, auditable, source-backed, and suitable for future contractual review, dispute avoidance, conciliation, arbitration, or legal scrutiny.
