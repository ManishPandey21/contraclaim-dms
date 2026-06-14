from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from rbac_backend.core.database import get_database


def _legacy_sources(letter: Dict[str, Any]) -> List[Dict[str, Any]]:
    migrated: List[Dict[str, Any]] = []
    for idx, source in enumerate(letter.get("draft_sources") or [], start=1):
        if not isinstance(source, dict):
            continue
        source_type = source.get("source_type") or "other"
        if source_type not in {
            "current_input",
            "contract_clause",
            "context_document",
            "prior_correspondence",
            "graph_thread",
            "comment",
            "other",
        }:
            source_type = "other"
        migrated.append(
            {
                "source_id": source.get("source_id") or source.get("id") or f"legacy-source:{idx}",
                "source_type": source_type,
                "allowed_use": "clause" if source_type == "contract_clause" else "fact",
                "organization_id": letter.get("organization_id"),
                "project_id": letter.get("project_id"),
                "label": source.get("label") or "Legacy source",
                "text": source.get("text") or source.get("snippet"),
                "snippet": source.get("snippet"),
                "document_id": source.get("document_id"),
                "letter_id": source.get("letter_id"),
                "clause_number": source.get("clause_number"),
                "clause_title": source.get("clause_title"),
                "page_numbers": source.get("page_numbers") or [],
                "score": source.get("score"),
                "metadata": source.get("metadata") or {},
            }
        )
    return migrated


def _run_from_letter(letter: Dict[str, Any], mode: str) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    letter_id = str(letter.get("_id"))
    role = (letter.get("strategy_role") or "contractor").lower()
    if role not in {"contractor", "engineer", "employer"}:
        role = "contractor"

    plan = letter.get("strategy_plan") if mode == "strategy" else letter.get("draft_plan")
    draft_body = "" if mode == "strategy" else (letter.get("draft_output") or "")

    return {
        "run_id": f"legacy-{mode}-{uuid.uuid4()}",
        "letter_id": letter_id,
        "mode": mode,
        "status": "completed",
        "role": role,
        "recipient_focus": letter.get("strategy_recipient"),
        "inputs": {
            "subject": letter.get("subject"),
            "recipient": letter.get("recipient"),
            "migrated_from": "letters",
        },
        "context_bundle": {
            "active_workspace": {
                "organization_id": letter.get("organization_id"),
                "project_id": letter.get("project_id"),
                "letter_id": letter_id,
                "letter_no": letter.get("letter_no"),
            },
            "current_materials": [value for value in [letter.get("content")] if value],
            "selected_document_ids": letter.get("context_document_ids") or [],
            "prior_correspondence_ids": letter.get("thread_letters") or [],
            "graph_thread_codes": [
                node.get("normCode") or node.get("code")
                for node in (letter.get("graph_thread") or [])
                if isinstance(node, dict) and (node.get("normCode") or node.get("code"))
            ],
            "comments": [],
            "threshold_inputs": {
                "sender_profile": True,
                "letter_purpose": bool(letter.get("subject")),
                "intended_recipient": bool(letter.get("recipient")),
                "key_issue_or_event": bool(letter.get("content") or plan),
                "main_factual_basis": bool(letter.get("content") or letter.get("draft_sources")),
            },
        },
        "sources": _legacy_sources(letter),
        "plan": plan,
        "draft_artifact": {
            "draft_letter": draft_body,
            "source_integrity_notes": "Migrated from legacy letter fields.",
            "raw_model_output": draft_body,
            "model_name": None,
            "prompt_version": None,
        }
        if mode == "draft"
        else None,
        "validation_report": {
            "blocking": bool(letter.get("reviewer_blocking") or False),
            "findings": letter.get("reviewer_findings") or [],
        },
        "warnings": ["Migrated from legacy letter fields; legacy fields were not deleted."],
        "trace": letter.get("draft_trace") or letter.get("strategy_graph_trace") or [],
        "started_at": letter.get("graph_started_at") or letter.get("strategy_graph_started_at") or now,
        "completed_at": letter.get("graph_completed_at") or letter.get("strategy_graph_completed_at") or now,
        "created_by": letter.get("created_by"),
        "legacy_migration": True,
    }


async def migrate(dry_run: bool = True) -> int:
    db = await get_database()
    cursor = db.letters.find(
        {
            "$or": [
                {"draft_output": {"$exists": True, "$ne": ""}},
                {"strategy_plan": {"$exists": True, "$ne": ""}},
            ]
        }
    )
    letters: List[Dict[str, Any]] = await cursor.to_list(length=None)
    created = 0
    for letter in letters:
        legacy_letter_id = str(letter.get("_id"))
        modes = []
        if letter.get("strategy_plan"):
            modes.append("strategy")
        if letter.get("draft_output"):
            modes.append("draft")
        for mode in modes:
            exists = await db.letter_draft_runs.find_one(
                {
                    "letter_id": legacy_letter_id,
                    "mode": mode,
                    "legacy_migration": True,
                }
            )
            if exists:
                continue
            run = _run_from_letter(letter, mode)
            created += 1
            if not dry_run:
                await db.letter_draft_runs.insert_one(run)
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy letter draft fields into v2 draft runs.")
    parser.add_argument("--apply", action="store_true", help="Write migration records. Defaults to dry-run.")
    args = parser.parse_args()
    created = asyncio.run(migrate(dry_run=not args.apply))
    mode = "created" if args.apply else "would create"
    print(f"{mode} {created} legacy draft run records")


if __name__ == "__main__":
    main()
