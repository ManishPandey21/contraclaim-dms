"""Deterministic skill-chain proposal.

Same inputs, same chain: routing reads only the issue type, the assertion
text and each skill's declared ``chains_to`` rules. No model call. A hand-off
target that is not installed is kept in the chain as an unavailable link with
its reason, so the user sees what was skipped.
"""

from __future__ import annotations

import re
import uuid
from typing import Dict, Iterable, List, Optional

from ...models.letter_bench import Assertion, ChainLink
from ...models.skill import EffectiveSkill, SkillChainRule
from .system_skills import ANALYSIS_SKILL_KEY, LETTERS_SKILL_KEY


def _keyword_hit(text: str, keywords: Iterable[str]) -> bool:
    lowered = (text or "").lower()
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])", lowered)
        for keyword in keywords
    )


def rule_targets(
    rule: SkillChainRule, issue_type: Optional[str], assertions: List[Assertion]
) -> Optional[List[str]]:
    """Assertion ids a rule applies to; [] means the whole letter; None means no match."""
    if rule.always:
        return []
    matched = [
        assertion.assertion_id
        for assertion in assertions
        if rule.keywords and _keyword_hit(assertion.quoted_text, rule.keywords)
    ]
    if matched:
        return matched
    if issue_type and issue_type in rule.issue_types:
        return []
    return None


def _link(skill: Optional[EffectiveSkill], skill_key: str, label: str, targets: List[str], role: str) -> ChainLink:
    link = ChainLink(
        link_id=uuid.uuid4().hex[:12],
        skill_key=skill_key,
        label=(skill.name if skill else None) or label,
        kind=skill.kind if skill else "evaluation",
        target_assertion_ids=list(targets),
        skill_version=skill.version if skill else None,
    )
    if skill is None or not skill.available:
        link.status = "unavailable"
        link.reason = (skill.unavailable_reason if skill else None) or "Not installed"
    elif role not in skill.roles_supported:
        link.status = "unavailable"
        link.reason = f"Not available when drafting as {role}"
    return link


def propose_chain(
    *,
    issue_type: Optional[str],
    assertions: List[Assertion],
    skills: List[EffectiveSkill],
    role: str,
    analysis_skill_key: str = ANALYSIS_SKILL_KEY,
    drafting_skill_key: str = LETTERS_SKILL_KEY,
) -> List[ChainLink]:
    by_key: Dict[str, EffectiveSkill] = {skill.skill_key: skill for skill in skills}
    analysis = by_key.get(analysis_skill_key)
    chain: List[ChainLink] = [_link(analysis, analysis_skill_key, "Analyse", [], role)]

    rules = list(analysis.chains_to) if analysis and analysis.available else []
    for rule in rules:
        if rule.skill_key == drafting_skill_key:
            continue
        targets = rule_targets(rule, issue_type, assertions)
        if targets is None:
            continue
        chain.append(
            _link(by_key.get(rule.skill_key), rule.skill_key, rule.label or rule.skill_key, targets, role)
        )
    # The reply is always the last link, whether or not the analysis skill declared it.
    chain.append(_link(by_key.get(drafting_skill_key), drafting_skill_key, "Draft reply", [], role))
    return chain


def apply_routing(chain: List[ChainLink], assertions: List[Assertion]) -> None:
    """Record on each assertion the available evaluation skill routed to it."""
    for link in chain:
        if link.kind != "evaluation" or link.status == "unavailable":
            continue
        for assertion in assertions:
            if assertion.assertion_id in link.target_assertion_ids and not assertion.routed_skill_key:
                assertion.routed_skill_key = link.skill_key


__all__ = ["apply_routing", "propose_chain", "rule_targets"]
