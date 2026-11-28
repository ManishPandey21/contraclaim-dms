"""System skill packs bundled with the application.

The prose lives in ``initial_data/skills/<skill_key>/``. The manifests below
are authored by us: the packs themselves carry no schema. A seed migration
copies these into ``skills``/``skill_versions``; the registry also falls back
to this bundle when the collections are empty, so a missing migration degrades
to "system skills at their bundled version" rather than "no skills".
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, List

from ...models.skill import (
    SYSTEM_SCOPE,
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

CONTRACT_FORMS = ["FIDIC", "CPWD", "NHAI_EPC", "MORTH_EPC"]

# Skills the packs hand off to by name but that are not installed. They are
# shown in the chain as unavailable links instead of being silently dropped.
KNOWN_UNINSTALLED_SKILLS: Dict[str, str] = {
    CLAIMS_SKILL_KEY: "Claims analysis",
    EOT_SKILL_KEY: "Extension of time review",
}

_KINDS = {
    ANALYSIS_SKILL_KEY: "analysis",
    LETTERS_SKILL_KEY: "drafting",
    VARIATIONS_SKILL_KEY: "evaluation",
}

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
                status="published",
                eval_report=SkillEvalReport(
                    suite="seed-structural",
                    score=1.0,
                    threshold=1.0,
                    case_count=1,
                    signed_off_by=None,
                    notes=SEED_EVAL_NOTE,
                ),
                created_by="system",
                published_by="system",
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
    "CLAIMS_SKILL_KEY",
    "EOT_SKILL_KEY",
    "KNOWN_UNINSTALLED_SKILLS",
    "LETTERS_SKILL_KEY",
    "SYSTEM_SKILL_VERSION",
    "VARIATIONS_SKILL_KEY",
    "system_skill_version",
    "system_skill_versions",
]
