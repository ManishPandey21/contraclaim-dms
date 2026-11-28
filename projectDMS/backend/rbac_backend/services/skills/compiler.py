"""Compile a skill version into a prompt block.

Three properties matter:

1. **Brace safety.** Prompt templates are rendered with ``str.format``; skill
   prose is free text. The block is therefore attached AFTER formatting
   (``attach_skill_block``). ``escape_braces`` exists for the one case where
   skill text must travel inside a template.
2. **Role stance.** Two packs are written for the Employer. A short, versioned
   overlay re-points the method to the drafting party. A skill is refused for
   a role its manifest does not list.
3. **Method, not evidence.** The block says so explicitly, and incoming
   material stays wrapped by the existing untrusted-content guard.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Optional

from ...models.skill import SkillVersion
from ..letter_drafting.prompts import UNTRUSTED_CONTENT_GUARD, ensure_untrusted_guard

ROLE_OVERLAY_VERSION = 1

# Wording is a governed artefact: changing it changes legal output, so bump
# ROLE_OVERLAY_VERSION when it changes. Pending domain-owner approval.
ROLE_OVERLAYS: Dict[str, str] = {
    "employer": (
        "ROLE: You are drafting for the EMPLOYER. Apply the method as written."
    ),
    "contractor": (
        "ROLE: You are drafting for the CONTRACTOR. This method was written from the "
        "Employer's side. Apply the same discipline (governing form, priority of "
        "documents, exact sub-clauses, notice periods, point-by-point reply) but protect "
        "the Contractor's position: read every reference to protecting the Employer as "
        "protecting the Contractor, treat the incoming letter as coming from the Employer "
        "or the Engineer, preserve the Contractor's entitlement to time and money, and "
        "reserve the Contractor's rights. Do not preserve the Employer's right to levy "
        "liquidated damages on the Contractor's behalf."
    ),
    "engineer": (
        "ROLE: You are drafting for the ENGINEER. This method was written from the "
        "Employer's side. Apply the same discipline but act fairly between the Parties "
        "as the Contract requires: state submissions received from each side, apply the "
        "clause wording to the facts, and give reasons. Do not advocate for either "
        "Party; where the method says to protect the Employer, instead record the "
        "contractual position neutrally."
    ),
}

METHOD_NOTICE = (
    "The following is a METHOD to follow. It is not evidence and not a source. "
    "Never cite it, never treat its clause numbers as the wording of this contract: "
    "clause numbering differs between contract forms and editions, so cite a clause "
    "only when it appears in the listed sources."
)


class SkillRoleNotSupported(ValueError):
    pass


@dataclass(frozen=True)
class CompiledSkill:
    skill_key: str
    version: str
    content_hash: str
    role: str
    overlay_key: str
    block: str
    compiled_hash: str


def escape_braces(text: str) -> str:
    """Make free text safe to embed inside a ``str.format`` template."""
    return text.replace("{", "{{").replace("}", "}}")


def overlay_key_for(role: str) -> str:
    return f"{role}.v{ROLE_OVERLAY_VERSION}"


def compile_skill(
    version: SkillVersion,
    role: str,
    *,
    overlay_text: Optional[str] = None,
    include_references: bool = True,
) -> CompiledSkill:
    role = (role or "").strip().lower()
    if role not in version.manifest.roles_supported:
        raise SkillRoleNotSupported(
            f"{version.name} is not available when drafting as {role or 'an unknown role'}"
        )
    overlay = overlay_text or ROLE_OVERLAYS.get(role)
    if not overlay:
        raise SkillRoleNotSupported(f"No role overlay is defined for {role}")

    parts = [
        f"=== SKILL: {version.skill_key} v{version.version} ===",
        METHOD_NOTICE,
        overlay,
        "--- METHOD ---",
        _strip_front_matter(version.skill_md).strip(),
    ]
    if include_references:
        for reference in version.references:
            parts.extend([f"--- REFERENCE NOTE: {reference.path} ---", reference.text.strip()])
    parts.append("=== END SKILL ===")
    block = "\n\n".join(part for part in parts if part)
    return CompiledSkill(
        skill_key=version.skill_key,
        version=version.version,
        content_hash=version.content_hash,
        role=role,
        overlay_key=overlay_key_for(role),
        block=block,
        compiled_hash=hashlib.sha256(block.encode("utf-8")).hexdigest(),
    )


def attach_skill_block(rendered_prompt: str, compiled: CompiledSkill) -> str:
    """Attach a compiled skill to an ALREADY RENDERED prompt.

    The skill goes first (method), the task prompt second, and the
    untrusted-content guard is guaranteed to be present after both.
    """
    combined = f"{compiled.block}\n\n{rendered_prompt.strip()}\n"
    return ensure_untrusted_guard(combined)


def _strip_front_matter(skill_md: str) -> str:
    if skill_md.startswith("---"):
        end = skill_md.find("\n---", 3)
        if end != -1:
            return skill_md[end + 4 :]
    return skill_md


__all__ = [
    "CompiledSkill",
    "METHOD_NOTICE",
    "ROLE_OVERLAYS",
    "ROLE_OVERLAY_VERSION",
    "SkillRoleNotSupported",
    "UNTRUSTED_CONTENT_GUARD",
    "attach_skill_block",
    "compile_skill",
    "escape_braces",
    "overlay_key_for",
]
