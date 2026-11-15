"""System skill packs bundled with the application.

The prose lives in ``initial_data/skills/<skill_key>/``. The manifests below
are authored by us: the packs themselves carry no schema. A seed migration
copies these into ``skills``/``skill_versions``; the registry also falls back
to this bundle when the collections are empty, so a missing migration degrades
to "system skills at their bundled version" rather than "no skills".

The letter packs ship published. The claim, EOT, pleadings and hearing packs
ship as DRAFTS: they are method text that has not had legal review, and each
names the technical and legal approvals it must hold before it can be
published. A bundled draft never resolves for drafting.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, List

from ...models.claim_skill_outputs import (
    CLAIM_HEAD_TEST_SCHEMA_KEY,
    DELAY_EVENT_SCHEMA_KEY,
    HEARING_DELIVERABLE_SCHEMA_KEY,
    PLEADING_HEAD_SCHEMA_KEY,
)
from ...models.skill import (
    SYSTEM_SCOPE,
    SkillApprovalRequirement,
    SkillChainRule,
    SkillEvalReport,
    SkillManifest,
    SkillReference,
    SkillVersion,
)
from .loader import load_skill_directory

SYSTEM_SKILL_VERSION = "1.0.0"
SKILLS_DIR = Path(__file__).resolve().parents[2] / "initial_data" / "skills"

ANALYSIS_SKILL_KEY = "indian-infra-letter-analysis"
LETTERS_SKILL_KEY = "indian-infra-contract-letters"
VARIATIONS_SKILL_KEY = "indian-infra-variations"
CLAIMS_SKILL_KEY = "indian-infra-claims"
EOT_SKILL_KEY = "indian-infra-eot-claims"
PLEADINGS_SKILL_KEY = "indian-infra-arbitration-pleadings"
HEARING_SKILL_KEY = "indian-infra-arbitration-hearing"

# Reviewer roles. The legal role is the one the arbitration gates already use.
LEGAL_APPROVER_ROLE = "senior_legal_approver"
CHIEF_CONTRACT_EXPERT_ROLE = "chief_contract_expert"
DELAY_PLANNING_EXPERT_ROLE = "delay_planning_expert"
CASE_PREPARATION_EXPERT_ROLE = "arbitration_case_preparation_expert"

AWAITING_APPROVAL_REASON = "Awaiting legal approval"

CONTRACT_FORMS = ["FIDIC", "CPWD", "NHAI_EPC", "MORTH_EPC"]

# Skills the letter packs hand off to by name. Until a version of one is
# published it is shown in the chain as an unavailable link, with the reason,
# instead of being silently dropped.
KNOWN_UNINSTALLED_SKILLS: Dict[str, str] = {
    CLAIMS_SKILL_KEY: "Claims analysis",
    EOT_SKILL_KEY: "Extension of time review",
}

_KINDS = {
    ANALYSIS_SKILL_KEY: "analysis",
    LETTERS_SKILL_KEY: "drafting",
    VARIATIONS_SKILL_KEY: "evaluation",
    CLAIMS_SKILL_KEY: "evaluation",
    EOT_SKILL_KEY: "evaluation",
    PLEADINGS_SKILL_KEY: "drafting",
    HEARING_SKILL_KEY: "drafting",
}

# Packs bundled as drafts. Everything else in the bundle is published.
DRAFT_SKILL_KEYS = frozenset(
    {CLAIMS_SKILL_KEY, EOT_SKILL_KEY, PLEADINGS_SKILL_KEY, HEARING_SKILL_KEY}
)


def _approvals(*technical_roles: str) -> List[SkillApprovalRequirement]:
    return [
        *[SkillApprovalRequirement(kind="technical", reviewer_role=role) for role in technical_roles],
        SkillApprovalRequirement(kind="legal", reviewer_role=LEGAL_APPROVER_ROLE),
    ]


_MANIFESTS: Dict[str, SkillManifest] = {
    ANALYSIS_SKILL_KEY: SkillManifest(
        roles_supported=["contractor", "engineer", "employer"],
        native_role=None,
        contract_forms=CONTRACT_FORMS,
        output_schema_key="letter_analysis.v1",
        required_inputs=["incoming_letter_text"],
        use_when="A letter needs to be dissected into its assertions, clauses, risks and required actions.",
        chains_to=[
            SkillChainRule(
                skill_key=VARIATIONS_SKILL_KEY,
                label="Variation evaluation",
                issue_types=["variation"],
                keywords=[
                    "variation",
                    "change of scope",
                    "extra item",
                    "extra work",
                    "additional work",
                    "deviation",
                    "change order",
                ],
            ),
            SkillChainRule(
                skill_key=EOT_SKILL_KEY,
                label="Extension of time review",
                issue_types=["delay"],
                keywords=["extension of time", "eot", "time for completion"],
            ),
            SkillChainRule(
                skill_key=CLAIMS_SKILL_KEY,
                label="Claims analysis",
                issue_types=["claim"],
                keywords=["notice of claim", "compensation", "additional payment"],
            ),
            SkillChainRule(skill_key=LETTERS_SKILL_KEY, label="Draft reply", always=True),
        ],
    ),
    LETTERS_SKILL_KEY: SkillManifest(
        roles_supported=["contractor", "engineer", "employer"],
        native_role="employer",
        contract_forms=CONTRACT_FORMS,
        output_schema_key="reply_paragraph.v1",
        required_inputs=["assertion", "verdict"],
        use_when="Drafting a notice, reply, instruction or determination, answered point by point.",
        validators=[
            "every_assertion_answered",
            "exact_sub_clause_cited",
            "reservation_of_rights",
        ],
    ),
    VARIATIONS_SKILL_KEY: SkillManifest(
        roles_supported=["contractor", "engineer", "employer"],
        native_role="employer",
        contract_forms=CONTRACT_FORMS,
        output_schema_key="variation_evaluation.v1",
        required_inputs=["boq_rates", "original_scope"],
        use_when="Deciding whether something is a variation and how it should be valued.",
        validators=["valuation_limb_stated"],
        chains_to=[
            SkillChainRule(
                skill_key=EOT_SKILL_KEY,
                label="Extension of time review",
                keywords=["critical path", "extension of time", "eot", "delay"],
            ),
            SkillChainRule(skill_key=LETTERS_SKILL_KEY, label="Draft reply", always=True),
        ],
    ),
    CLAIMS_SKILL_KEY: SkillManifest(
        roles_supported=["contractor", "engineer", "employer"],
        native_role=None,
        contract_forms=CONTRACT_FORMS,
        output_schema_key=CLAIM_HEAD_TEST_SCHEMA_KEY,
        required_inputs=["event_summary", "contract_clauses"],
        use_when=(
            "Preparing a claim, or testing one head of claim on event, notice, "
            "contractual basis, causation and quantum."
        ),
        required_approvals=_approvals(CHIEF_CONTRACT_EXPERT_ROLE),
        chains_to=[
            SkillChainRule(
                skill_key=EOT_SKILL_KEY,
                label="Extension of time review",
                issue_types=["delay"],
                keywords=["extension of time", "eot", "critical path", "delay", "time for completion"],
            ),
            SkillChainRule(
                skill_key=PLEADINGS_SKILL_KEY,
                label="Arbitration pleadings",
                issue_types=["dispute"],
                keywords=["arbitration", "statement of claim", "notice of dispute"],
            ),
            SkillChainRule(skill_key=LETTERS_SKILL_KEY, label="Draft letter", always=True),
        ],
    ),
    EOT_SKILL_KEY: SkillManifest(
        roles_supported=["contractor", "engineer", "employer"],
        native_role=None,
        contract_forms=CONTRACT_FORMS,
        output_schema_key=DELAY_EVENT_SCHEMA_KEY,
        required_inputs=["delay_event", "baseline_programme"],
        use_when=(
            "Classifying delay events by risk owner and criticality, and reasoning "
            "about concurrency, for an extension of time."
        ),
        required_approvals=_approvals(DELAY_PLANNING_EXPERT_ROLE, CHIEF_CONTRACT_EXPERT_ROLE),
        chains_to=[
            SkillChainRule(skill_key=LETTERS_SKILL_KEY, label="Draft letter", always=True),
        ],
    ),
    PLEADINGS_SKILL_KEY: SkillManifest(
        # Pleadings are drafted for a party to the dispute, not for the Engineer.
        roles_supported=["contractor", "employer"],
        native_role=None,
        contract_forms=CONTRACT_FORMS,
        output_schema_key=PLEADING_HEAD_SCHEMA_KEY,
        required_inputs=["claim_head", "approved_matrix_rows"],
        use_when=(
            "Drafting a Statement of Claim, Statement of Defence, Rejoinder or "
            "Counterclaim from approved case matrices."
        ),
        required_approvals=_approvals(CHIEF_CONTRACT_EXPERT_ROLE),
        chains_to=[
            SkillChainRule(
                skill_key=HEARING_SKILL_KEY,
                label="Hearing preparation",
                issue_types=["hearing"],
                keywords=["hearing", "opening statement", "closing submissions", "cross-examination"],
            ),
        ],
    ),
    HEARING_SKILL_KEY: SkillManifest(
        roles_supported=["contractor", "employer"],
        native_role=None,
        contract_forms=CONTRACT_FORMS,
        output_schema_key=HEARING_DELIVERABLE_SCHEMA_KEY,
        required_inputs=["filed_pleadings", "approved_matrix_rows"],
        use_when=(
            "Preparing hearing materials: strategy note, opening, timeline, hearing "
            "matrix, deck, examination plans, closing and speaking notes."
        ),
        required_approvals=_approvals(CASE_PREPARATION_EXPERT_ROLE),
    ),
}

SEED_EVAL_NOTE = (
    "Seeded system pack. Structural conformance checked by the automated suite; "
    "golden-set scoring on real letters awaits domain sign-off."
)


@lru_cache(maxsize=1)
def system_skill_versions() -> List[SkillVersion]:
    versions: List[SkillVersion] = []
    for skill_key, manifest in _MANIFESTS.items():
        loaded = load_skill_directory(SKILLS_DIR / skill_key)
        is_draft = skill_key in DRAFT_SKILL_KEYS
        if loaded.skill_key != skill_key:
            raise RuntimeError(
                f"Bundled skill folder {skill_key} declares name {loaded.skill_key}"
            )
        versions.append(
            SkillVersion(
                skill_key=skill_key,
                version=SYSTEM_SKILL_VERSION,
                owner_scope=SYSTEM_SCOPE,
                name=loaded.name,
                description=loaded.description,
                kind=_KINDS[skill_key],  # type: ignore[arg-type]
                content_hash=loaded.content_hash,
                skill_md=loaded.skill_md,
                references=[
                    SkillReference(path=ref.path, text=ref.text, content_hash=ref.content_hash)
                    for ref in loaded.references
                ],
                manifest=manifest,
                status="draft" if is_draft else "published",
                eval_report=None
                if is_draft
                else SkillEvalReport(
                    suite="seed-structural",
                    score=1.0,
                    threshold=1.0,
                    case_count=1,
                    signed_off_by=None,
                    notes=SEED_EVAL_NOTE,
                ),
                created_by="system",
                published_by=None if is_draft else "system",
            )
        )
    return versions


def system_skill_version(skill_key: str) -> SkillVersion | None:
    for version in system_skill_versions():
        if version.skill_key == skill_key:
            return version
    return None


__all__ = [
    "ANALYSIS_SKILL_KEY",
    "AWAITING_APPROVAL_REASON",
    "CASE_PREPARATION_EXPERT_ROLE",
    "CHIEF_CONTRACT_EXPERT_ROLE",
    "CLAIMS_SKILL_KEY",
    "DELAY_PLANNING_EXPERT_ROLE",
    "DRAFT_SKILL_KEYS",
    "EOT_SKILL_KEY",
    "HEARING_SKILL_KEY",
    "LEGAL_APPROVER_ROLE",
    "PLEADINGS_SKILL_KEY",
    "KNOWN_UNINSTALLED_SKILLS",
    "LETTERS_SKILL_KEY",
    "SYSTEM_SKILL_VERSION",
    "VARIATIONS_SKILL_KEY",
    "system_skill_version",
    "system_skill_versions",
]
