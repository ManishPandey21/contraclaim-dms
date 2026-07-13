"""Section question-builders for the Contract Appraisal Report (v2 Phase 1).

Each of the report sections becomes a focused, citation-required question for the
existing ``RetrievalService.contract_iterative_qa`` engine — mirroring
``claim_assessment_service._TYPE_FOCUS``. This gives per-section citations, tenant
isolation and "Not found in uploaded documents" behaviour with no new AI code.

``APPRAISAL_PROMPT_VERSION`` is stored on every report for traceability; bump it
whenever the section set or wording changes materially.
"""

from __future__ import annotations

from typing import List, Tuple

APPRAISAL_PROMPT_VERSION = "1.0"

# Citation discipline appended to every section question so the engine answers
# only from the uploaded, tenant-scoped contract documents.
_CITATION_RULE = (
    " Use ONLY the selected contract document provided as evidence — do not use "
    "any other document, prior correspondence or outside knowledge. Cite the exact "
    "document name, clause number and page for every point. Where the selected "
    "document does not address it, answer exactly 'Not found in the selected "
    "document.' State Special/Particular vs General Conditions precedence where "
    "relevant. Do not provide legal advice or invent clauses, dates or amounts."
)

# (key, human title, focused question). Ordered as the report renders.
_SECTIONS: List[Tuple[str, str, str]] = [
    (
        "executive_summary",
        "Executive Summary",
        "Summarise the contract: nature of works, the parties, the contract price, key "
        "completion dates, the most claim-sensitive clauses and the major risks.",
    ),
    (
        "contract_particulars",
        "Contract Particulars",
        "Extract the contract particulars: project name, contract title, employer, "
        "contractor, engineer/PM, contract agreement date, letter of acceptance date, "
        "contract price, currency, commencement date, time for completion, completion "
        "date, defects liability period, governing law, dispute mechanism, performance "
        "security, advance payment, retention, price adjustment and delay/liquidated "
        "damages provisions.",
    ),
    (
        "document_inventory",
        "Uploaded Document Inventory",
        "List the contract documents reviewed with their type and any noted gaps or "
        "unreadable sections.",
    ),
    (
        "order_of_precedence",
        "Order of Precedence",
        "Identify the contractual order of precedence of the documents and which "
        "prevails in case of inconsistency. If absent, state the risk.",
    ),
    (
        "scope_of_work",
        "Scope of Work Appraisal",
        "Summarise the contractor's scope: main works, design/procurement/construction "
        "and testing-and-commissioning responsibility, interface and statutory-approval "
        "obligations, documentation/as-built and handover requirements, and any "
        "scope ambiguity or variation-prone areas.",
    ),
    (
        "roles_and_responsibilities",
        "Roles and Responsibilities",
        "Give a party-wise summary of responsibilities for the employer, contractor, "
        "engineer/consultant and any other named party.",
    ),
    (
        "time_and_delay",
        "Time, Milestones and Delay Provisions",
        "Analyse commencement, time for completion, sectional completion, delay "
        "damages, extension-of-time provisions, delay/EOT notice requirements and "
        "time-bars, concurrent-delay treatment and programme-submission requirements.",
    ),
    (
        "payment_provisions",
        "Payment and Commercial Provisions",
        "Analyse the price structure (lump sum / item-rate / BOQ), interim payment "
        "process, advance payment, retention, price adjustment, variation payment, "
        "final account, payment timelines and any conditions precedent to payment.",
    ),
    (
        "variation_management",
        "Variation and Change Management",
        "Analyse the variation instruction process, who may instruct, valuation method, "
        "notice/submission time limits, daywork and rate derivation, the engineer's "
        "determination mechanism and the records required to support variations.",
    ),
    (
        "claim_procedure",
        "Claim Procedure Appraisal",
        "Analyse notice requirements, time-bar clauses, claim submission timelines, "
        "contemporary-record requirements, the engineer's response/determination "
        "timelines and the risk of losing entitlement through non-compliance.",
    ),
    (
        "employer_obligations",
        "Employer's Obligations",
        "Extract the employer's obligations: site access/possession, drawings/approvals, "
        "payments, permits/clearances, interface support, decisions/approvals, free-issue "
        "materials and utility/land availability.",
    ),
    (
        "contractor_obligations",
        "Contractor's Obligations",
        "Extract the contractor's obligations: mobilisation, design, construction, "
        "quality, safety, programme, testing and commissioning, documentation, notices, "
        "submissions, insurance, statutory compliance and defects rectification.",
    ),
    (
        "risk_register",
        "Risk Register",
        "Identify contractual and administration risks (time, cost, scope, payment, "
        "variation, notice-compliance, interface, approvals, termination, dispute, "
        "documentation). For each give a severity and a recommended mitigation.",
    ),
    (
        "claim_variation_opportunities",
        "Claim and Variation Opportunity Matrix",
        "Identify contractual triggers that may give rise to claims or variations, with "
        "the relevant clause, the required notice and records. Do not state entitlement "
        "is confirmed unless the contract clearly supports it.",
    ),
    (
        "dispute_resolution",
        "Dispute Resolution Appraisal",
        "Summarise amicable settlement, engineer's determination/adjudication, dispute "
        "board or arbitration, the rules, seat/place/language and any pre-conditions.",
    ),
    (
        "insurance_security_indemnity",
        "Insurance, Security and Indemnity Provisions",
        "Summarise performance security, advance-payment security, insurance "
        "requirements, indemnities, any parent-company guarantee and "
        "validity/renewal requirements.",
    ),
    (
        "termination_suspension",
        "Termination and Suspension Provisions",
        "Analyse the employer's and contractor's rights to terminate, suspension "
        "provisions, notice and cure periods and payment after termination.",
    ),
    (
        "record_keeping",
        "Documentation and Record-Keeping Requirements",
        "List the records the project team should maintain to protect entitlement "
        "(progress reports, site instructions, correspondence, delay notices, "
        "labour/plant records, measurements, test reports, programme updates).",
    ),
    (
        "missing_or_conflicting",
        "Missing, Ambiguous or Conflicting Provisions",
        "Identify missing documents/clauses, ambiguous wording, contradictions and "
        "undefined terms, and the likely risk of each.",
    ),
    (
        "immediate_action_plan",
        "Immediate Action Plan",
        "Give a practical action plan: action required, priority, responsible team, "
        "related clause/document and timeline.",
    ),
    (
        "overall_appraisal",
        "Overall Contract Appraisal",
        "Give an overall assessment: contract-administration complexity, claim "
        "sensitivity, notice-compliance strictness, payment risk, variation risk, "
        "delay/EOT risk, documentation requirement level and an overall risk rating "
        "(low/medium/high/critical).",
    ),
]


def section_questions() -> List[Tuple[str, str, str]]:
    """Return (key, title, full question incl. citation discipline) for each section."""
    return [(key, title, focus + _CITATION_RULE) for key, title, focus in _SECTIONS]


STANDARD_DISCLAIMER = (
    "This Contract Document Appraisal Report is generated based on the uploaded "
    "contract documents available in Contraclaim DMS. It is intended for contract "
    "management, project administration and claims-support purposes only. It does not "
    "constitute legal advice. The report should be reviewed and approved by the "
    "authorised contract/legal/project team before reliance or external use."
)
