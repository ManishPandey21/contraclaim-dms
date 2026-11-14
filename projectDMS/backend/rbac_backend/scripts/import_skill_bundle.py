"""Import a skill bundle (zip) as a DRAFT version.

    python -m rbac_backend.scripts.import_skill_bundle pack.zip --version 1.1.0 \
        --kind drafting --manifest manifest.json

The bundle is validated exactly as an upload through the API is. Nothing is
published: publishing needs a passing evaluation report and goes through
``POST /api/skills/{skill_key}/versions/{version}/publish``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover - direct script execution
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from rbac_backend.core.config import settings
from rbac_backend.core.database import disconnect, get_database
from rbac_backend.models.skill import SkillImportOptions, SkillManifest
from rbac_backend.services.ai_guardrails import AIOutputGuardrailService
from rbac_backend.services.skills.loader import SkillBundleError, load_skill_zip
from rbac_backend.services.skills.registry import SkillRegistry


async def _main_async(args: argparse.Namespace) -> int:
    try:
        loaded = load_skill_zip(Path(args.bundle).read_bytes())
        options = SkillImportOptions(
            version=args.version,
            kind=args.kind,
            manifest=SkillManifest(**json.loads(Path(args.manifest).read_text(encoding="utf-8"))),
        )
    except (SkillBundleError, ValueError) as exc:
        sys.stderr.write(f"Bundle rejected: {exc}\n")
        return 2
    guardrails = AIOutputGuardrailService.from_settings(settings)
    findings = guardrails.scan_evidence([loaded.skill_md, *[ref.text for ref in loaded.references]])
    summary = {
        "skill_key": loaded.skill_key,
        "version": options.version,
        "content_hash": loaded.content_hash,
        "references": [ref.path for ref in loaded.references],
        "guardrail_findings": len(findings),
        "status": "dry_run" if args.dry_run else "draft",
    }
    if not args.dry_run:
        db = await get_database()
        try:
            await SkillRegistry(db).create_draft(
                loaded,
                version=options.version,
                kind=options.kind,
                manifest=options.manifest,
                created_by=args.actor,
                guardrail_findings=[finding.model_dump(mode="json") for finding in findings],
            )
        finally:
            await disconnect()
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundle", help="Path to the skill zip")
    parser.add_argument("--version", required=True, help="Semantic version, e.g. 1.1.0")
    parser.add_argument("--kind", required=True, choices=["analysis", "evaluation", "drafting"])
    parser.add_argument("--manifest", required=True, help="Path to the manifest JSON written for this version")
    parser.add_argument("--actor", default="cli", help="Recorded as created_by")
    parser.add_argument("--dry-run", action="store_true", help="Validate and hash only; write nothing")
    return asyncio.run(_main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
