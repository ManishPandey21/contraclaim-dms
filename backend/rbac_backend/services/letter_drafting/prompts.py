from __future__ import annotations

import string
from typing import Any, Dict, Iterable, List

from ...models.letter_drafting import PromptTemplateRecord


DRAFT_PROMPT_KEY = "letter_drafting.v2.draft"
STRATEGY_PROMPT_KEY = "letter_drafting.v2.strategy"

DEFAULT_DRAFT_TEMPLATE = """You are a Contract Correspondence AI Agent drafting strictly as {role}.

Active workspace: {active_workspace}
Recipient: {recipient}
Subject: {subject}
Recipient focus: {recipient_focus}

Current materials:
{current_materials}

Plan:
{plan}

Sources:
{sources}

Profile pattern:
{profile_pattern}

Rules:
- Use only the current materials and listed sources.
- Do not invent facts, dates, clause references, meetings, attachments, or legal conclusions.
- Cite clauses only when a clause source is listed.
- Prior correspondence is for history/style continuity only unless listed as fact evidence.
- Return exactly these headings in this order:
Draft Letter
Source Integrity Notes
Learning Update
- Omit Learning Update content unless finalized is true; if not finalized, write "N/A".
"""

DEFAULT_STRATEGY_TEMPLATE = """Prepare a strategy plan for a contractual letter.

Active workspace: {active_workspace}
Role: {role}
Recipient: {recipient}
Subject: {subject}
Recipient focus: {recipient_focus}

Current materials:
{current_materials}

Sources:
{sources}

You must first analyze the incoming letter and available sources fully. Do not proceed directly to drafting advice until the roadmap below is complete.

Return a structured roadmap with exactly these sections:
1. Incoming letter summary
2. Sender and subject verification
3. Letter reference number and date
4. Main issue classification
5. Requested action
6. Stated deadline
7. Contractual response deadline
8. Cited clauses
9. Clause correctness check
10. Clause applicability analysis
11. Counter-position or counter-clauses
12. Missing information
13. Recommended response strategy
14. Points the drafter must verify manually
15. Suggested structure for the reply letter

Rules:
- Identify whether the issue is a claim, delay, variation, payment, approval, dispute, notice, contractual compliance issue, request for information, or other issue type.
- For cited clauses, state whether each clause appears in the available contract sources, whether quoted wording is supported, whether the clause is applicable, whether the sender is relying on it correctly, whether counter-clauses exist, and whether legal/commercial review is required.
- If source material is insufficient for any verification, write "Not verified from available sources" and list it under manual drafter verification.
- Do not invent missing sender details, dates, references, deadlines, clauses, or contract wording.
"""


REQUIRED_STRATEGY_ROADMAP_SECTIONS = [
    "Incoming letter summary",
    "Sender and subject verification",
    "Letter reference number and date",
    "Main issue classification",
    "Requested action",
    "Stated deadline",
    "Contractual response deadline",
    "Cited clauses",
    "Clause correctness check",
    "Clause applicability analysis",
    "Counter-position or counter-clauses",
    "Missing information",
    "Recommended response strategy",
    "Points the drafter must verify manually",
    "Suggested structure for the reply letter",
]


STRATEGY_ROADMAP_ADDENDUM = """

Required strategic-plan roadmap:
1. Incoming letter summary
2. Sender and subject verification
3. Letter reference number and date
4. Main issue classification
5. Requested action
6. Stated deadline
7. Contractual response deadline
8. Cited clauses
9. Clause correctness check
10. Clause applicability analysis
11. Counter-position or counter-clauses
12. Missing information
13. Recommended response strategy
14. Points the drafter must verify manually
15. Suggested structure for the reply letter

Roadmap rules:
- Analyze the incoming letter and available sources fully before planning the reply.
- Classify the main issue and state whether classification is inferred or user-supplied.
- For cited clauses, verify existence, quoted wording support, applicability, sender reliance, counter-clauses, effect on sender position, and legal/commercial review need.
- If source material is insufficient, say "Not verified from available sources" and assign the item to manual drafter verification.
"""


class PromptRegistry:
    """Versioned prompt registry with DB override support and variable validation."""

    COLLECTION = "prompt_templates"

    def __init__(self, db: Any):
        self.db = db

    async def get_enabled(self, prompt_key: str) -> PromptTemplateRecord:
        if self.db is not None:
            doc = await self.db[self.COLLECTION].find_one(
                {"prompt_key": prompt_key, "enabled": True},
                sort=[("version", -1)],
            )
            if doc:
                record = PromptTemplateRecord(**doc)
                if prompt_key == STRATEGY_PROMPT_KEY:
                    record = record.model_copy(
                        update={"template": ensure_strategy_roadmap(record.template)}
                    )
                return record
        template = DEFAULT_STRATEGY_TEMPLATE if prompt_key == STRATEGY_PROMPT_KEY else DEFAULT_DRAFT_TEMPLATE
        if prompt_key == STRATEGY_PROMPT_KEY:
            template = ensure_strategy_roadmap(template)
        return PromptTemplateRecord(
            prompt_key=prompt_key,
            version=1,
            supported_payload_schema=sorted(extract_template_variables(template)),
            template=template,
            enabled=True,
        )

    async def get_latest(self, prompt_key: str) -> Optional[PromptTemplateRecord]:
        if self.db is not None:
            doc = await self.db[self.COLLECTION].find_one(
                {"prompt_key": prompt_key},
                sort=[("version", -1)],
            )
            if doc:
                return PromptTemplateRecord(**doc)
        return None

    async def update_prompt(self, prompt_key: str, template: str, current_user_id: str) -> PromptTemplateRecord:
        if prompt_key == STRATEGY_PROMPT_KEY:
            template = ensure_strategy_roadmap(template)
            
        latest = await self.get_latest(prompt_key)
        next_version = (latest.version + 1) if latest else 1
        
        if self.db is not None:
            await self.db[self.COLLECTION].update_many(
                {"prompt_key": prompt_key, "enabled": True},
                {"$set": {"enabled": False}},
            )
            
        new_record = PromptTemplateRecord(
            prompt_key=prompt_key,
            version=next_version,
            supported_payload_schema=sorted(extract_template_variables(template)),
            template=template,
            enabled=True,
        )
        
        if self.db is not None:
            doc = new_record.model_dump(by_alias=True)
            if "_id" in doc and doc["_id"] is None:
                del doc["_id"]
            await self.db[self.COLLECTION].insert_one(doc)
            
        return new_record

    @staticmethod
    def validate_template(template: str, required_variables: Iterable[str]) -> List[str]:
        present = extract_template_variables(template)
        return sorted(set(required_variables) - present)


def extract_template_variables(template: str) -> set[str]:
    formatter = string.Formatter()
    variables: set[str] = set()
    for _, field_name, _, _ in formatter.parse(template or ""):
        if field_name:
            variables.add(field_name.split(".", 1)[0].split("[", 1)[0])
    return variables


def ensure_strategy_roadmap(template: str) -> str:
    """Ensure DB-overridden strategy prompts still include the required roadmap."""

    missing = [
        section
        for section in REQUIRED_STRATEGY_ROADMAP_SECTIONS
        if section.lower() not in (template or "").lower()
    ]
    if not missing:
        return template
    return (template or "").rstrip() + STRATEGY_ROADMAP_ADDENDUM


def render_prompt(record: PromptTemplateRecord, payload: Dict[str, Any]) -> str:
    missing = PromptRegistry.validate_template(record.template, payload.keys())
    if missing:
        # Missing extra payload keys in template is allowed; this branch is only defensive
        # and will not be reached for the current call shape.
        pass
    return record.template.format(**payload)
