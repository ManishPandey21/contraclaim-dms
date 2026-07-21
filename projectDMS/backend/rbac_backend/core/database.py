"""MongoDB connectivity utilities and index management."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qs, urlparse
from typing import Optional

import motor.motor_asyncio

from .config import settings

logger = logging.getLogger(__name__)

client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
database: Optional[motor.motor_asyncio.AsyncIOMotorDatabase] = None
_index_task: Optional[asyncio.Task[None]] = None

INDEX_CREATION_ATTEMPTS = 5
INDEX_CREATION_BASE_DELAY = 1.0


def _mongo_client() -> motor.motor_asyncio.AsyncIOMotorClient:
    kwargs = {
        "appname": settings.MONGODB_APP_NAME,
        "serverSelectionTimeoutMS": int(settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS),
        "connectTimeoutMS": int(settings.MONGODB_CONNECT_TIMEOUT_MS),
        "socketTimeoutMS": int(settings.MONGODB_SOCKET_TIMEOUT_MS),
        "maxPoolSize": int(settings.MONGODB_MAX_POOL_SIZE),
        "minPoolSize": int(settings.MONGODB_MIN_POOL_SIZE),
        "retryWrites": bool(settings.MONGODB_RETRY_WRITES),
    }
    if settings.MONGODB_REPLICA_SET and "replicaset=" not in settings.DATABASE_URL.lower():
        kwargs["replicaSet"] = settings.MONGODB_REPLICA_SET
    return motor.motor_asyncio.AsyncIOMotorClient(settings.DATABASE_URL, **kwargs)


def _database_name() -> str:
    configured = str(getattr(settings, "MONGODB_DATABASE", "") or "").strip()
    if configured:
        return configured
    parsed = urlparse(settings.DATABASE_URL)
    path_name = parsed.path.lstrip("/").split("/")[0]
    return path_name or "contraclaim"


def _connection_has_replica_set() -> bool:
    if settings.MONGODB_REPLICA_SET:
        return True
    parsed = urlparse(settings.DATABASE_URL)
    query = parse_qs(parsed.query or "")
    return any(key.lower() == "replicaset" for key in query)

async def connect() -> None:
    """Establish a MongoDB connection and kick off index creation."""

    global client, database, _index_task

    if client is None:
        client = _mongo_client()

    database = client[_database_name()]

    try:
        await client.admin.command("ping")
        environment = str(getattr(settings, "ENVIRONMENT", "development") or "development").lower()
        if environment == "production" and not settings.MONGODB_ALLOW_STANDALONE_PRODUCTION:
            if not _connection_has_replica_set():
                raise RuntimeError("MongoDB production connection is missing replicaSet configuration")
            try:
                await client.admin.command("replSetGetStatus")
            except Exception as exc:
                raise RuntimeError("MongoDB production connection must target a healthy replica set") from exc
    except Exception:
        # Cleanup on failure
        if client is not None:
            client.close()
        client = None
        database = None
        raise

    # Ensure indexes asynchronously so slow index creation never blocks startup.
    if database is not None:
        if _index_task is None or _index_task.done():
            _index_task = asyncio.create_task(ensure_indexes_with_retry(database))

async def disconnect() -> None:
    """Close the MongoDB connection and cancel index creation if pending."""

    global client, database, _index_task

    if _index_task is not None and not _index_task.done():
        _index_task.cancel()
        try:
            await _index_task
        except asyncio.CancelledError:
            pass
        finally:
            _index_task = None

    if client is not None:
        client.close()
    client = None
    database = None

async def get_db():
    global client, database
    # Ensure database is available
    if database is None:
        if client is None:
            client = _mongo_client()
        database = client[_database_name()]
    if database is None:
        raise RuntimeError("Database connection is not initialised")
    yield database

async def get_database():
    """Get database instance for direct access"""
    global client, database
    if database is None:
        if client is None:
            client = _mongo_client()
        database = client[_database_name()]
    return database

async def ensure_indexes(db):
    """Create indexes to improve RBAC scoped queries and general performance."""

    # Users
    await db.users.create_index("organization_id", background=True)
    await db.users.create_index("roles", background=True)
    await db.users.create_index("projects", background=True)
    await db.users.create_index("account_type", background=True)

    # Organizations
    # _id is implicitly indexed by MongoDB; do not attempt to create an _id index with options
    await db.organizations.create_index("name", background=True)

    # Projects
    await db.projects.create_index("organization_id", background=True)

    # Documents
    await db.documents.create_index("organization_id", background=True)
    await db.documents.create_index("project_id", background=True)
    await db.documents.create_index([("$**", "text")], background=True)
    await db.documents.create_index(
        [("organization_id", 1), ("project_id", 1), ("createdAt", -1)],
        background=True,
    )
    await db.documents.create_index("tags", background=True)
    await db.documents.create_index("file_type", background=True)
    await db.documents.create_index("lifecycle_state", background=True)
    await db.documents.create_index("file_object_id", background=True)
    await db.documents.create_index("current_version_id", background=True)
    await db.documents.create_index("contract_upload_id", background=True)
    await db.documents.create_index("sha256", background=True)
    await db.documents.create_index("storage_key", background=True)
    await db.documents.create_index(
        [("organization_id", 1), ("project_id", 1), ("lifecycle_state", 1), ("createdAt", -1)],
        background=True,
    )
    await db.documents.create_index(
        [("organization_id", 1), ("project_id", 1), ("uploadType", 1), ("status", 1), ("createdAt", -1)],
        background=True,
    )
    await db.documents.create_index(
        [("organization_id", 1), ("project_id", 1), ("status", 1), ("updatedAt", -1)],
        background=True,
    )
    await db.documents.create_index(
        [("organization_id", 1), ("project_id", 1), ("letterNoNormalized", 1)],
        background=True,
    )
    # Stage-1 duplicate-upload precheck: exact file hash within org/project
    await db.documents.create_index(
        [("organization_id", 1), ("project_id", 1), ("sha256", 1)],
        background=True,
    )
    await db.reference_sync_queue.create_index(
        [("document_id", 1), ("reference_key", 1), ("status", 1)],
        name="uq_reference_sync_pending",
        unique=True,
        background=True,
        partialFilterExpression={
            "status": "pending",
            "reference_key": {"$exists": True},
        },
    )
    await db.documents.create_index(
        [("organization_id", 1), ("project_id", 1), ("letterNo", 1)],
        background=True,
    )

    # Immutable file storage architecture
    await db.file_objects.create_index("sha256", background=True)
    await db.file_objects.create_index(
        [("organization_id", 1), ("project_id", 1), ("sha256", 1), ("document_type", 1)],
        background=True,
    )
    await db.file_objects.create_index("storage_key", background=True)
    await db.file_objects.create_index("document_id", background=True)
    await db.file_objects.create_index("document_ids", background=True)
    await db.file_objects.create_index(
        [("storage_provider", 1), ("bucket", 1), ("storage_key", 1)],
        background=True,
    )
    await db.file_objects.create_index(
        [("organization_id", 1), ("project_id", 1), ("createdAt", -1)],
        background=True,
    )
    await db.document_versions.create_index(
        [("document_id", 1), ("version_number", -1)],
        background=True,
    )
    await db.document_versions.create_index("file_object_id", background=True)
    await db.contracts.create_index(
        [("organization_id", 1), ("project_id", 1), ("title", 1)],
        background=True,
    )
    await db.contracts.create_index("current_version_id", background=True)
    await db.contracts.create_index(
        [("organization_id", 1), ("project_id", 1), ("lifecycle_state", 1), ("updatedAt", -1)],
        background=True,
    )
    await db.contract_versions.create_index(
        [("contract_id", 1), ("version_number", -1)],
        background=True,
    )
    await db.contract_versions.create_index("document_id", background=True)
    await db.contract_versions.create_index("file_object_id", background=True)
    await db.contract_versions.create_index("upload_id", background=True)
    await db.document_audit_events.create_index(
        [("resource_type", 1), ("resource_id", 1), ("createdAt", -1)],
        background=True,
    )
    await db.document_audit_events.create_index(
        [("organization_id", 1), ("project_id", 1), ("createdAt", -1)],
        background=True,
    )
    await db.document_audit_events.create_index(
        [("actor_user_id", 1), ("createdAt", -1)],
        background=True,
    )
    await db.document_audit_events.create_index(
        [("actor_id", 1), ("createdAt", -1)],
        background=True,
    )

    # Letters
    await db.letters.create_index("organization_id", background=True)
    await db.letters.create_index("project_id", background=True)
    await db.letters.create_index("conversation_id", background=True)
    await db.letters.create_index("previous_letter_id", background=True)
    await db.letters.create_index(
        [("organization_id", 1), ("project_id", 1), ("createdAt", -1)],
        background=True,
    )
    await db.letters.create_index(
        [("organization_id", 1), ("project_id", 1), ("status", 1), ("updatedAt", -1)],
        background=True,
    )
    await db.letters.create_index(
        [("organization_id", 1), ("project_id", 1), ("created_at", -1)],
        background=True,
    )
    await db.letters.create_index(
        [("organization_id", 1), ("project_id", 1), ("status", 1), ("updated_at", -1)],
        background=True,
    )

    # Letter drafting v2
    await db.letter_draft_runs.create_index("run_id", unique=True, background=True)
    await db.letter_draft_runs.create_index(
        [("letter_id", 1), ("idempotency_key", 1)],
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        background=True,
    )
    await db.letter_draft_runs.create_index(
        [("thread_id", 1), ("state_version", -1)],
        partialFilterExpression={"thread_id": {"$type": "string"}},
        background=True,
    )
    await db.letter_draft_runs.create_index(
        [("letter_id", 1), ("mode", 1), ("started_at", -1)],
        background=True,
    )
    await db.letter_draft_runs.create_index(
        [("letter_id", 1), ("status", 1), ("completed_at", -1)],
        background=True,
    )
    await db.letter_draft_runs.create_index(
        [("letter_id", 1), ("approved_at", -1), ("issued_at", -1)],
        background=True,
    )
    await db.letter_draft_runs.create_index(
        [("letter_id", 1), ("approval_status", 1), ("completed_at", -1)],
        background=True,
    )
    await db.letter_draft_events.create_index("event_id", unique=True, background=True)
    await db.letter_draft_events.create_index(
        [("letter_id", 1), ("run_id", 1), ("created_at", -1)],
        background=True,
    )
    await db.letter_draft_events.create_index(
        [("event_type", 1), ("created_at", -1)],
        background=True,
    )
    await db.letter_draft_assignments.create_index("assignment_id", unique=True, background=True)
    await db.letter_draft_assignments.create_index(
        [("letter_id", 1), ("run_id", 1), ("status", 1)],
        background=True,
    )
    await db.letter_draft_assignments.create_index(
        [("reviewer_user_id", 1), ("status", 1), ("due_at", 1)],
        background=True,
    )
    await db.letter_draft_comments.create_index("comment_id", unique=True, background=True)
    await db.letter_draft_comments.create_index(
        [("letter_id", 1), ("run_id", 1), ("created_at", 1)],
        background=True,
    )
    await db.draft_context_packs.create_index("context_pack_id", unique=True, background=True)
    await db.draft_context_packs.create_index(
        [("letter_id", 1), ("run_id", 1), ("created_at", -1)],
        background=True,
    )
    await db.letter_draft_input_snapshots.create_index("snapshot_id", unique=True, background=True)
    await db.letter_draft_input_snapshots.create_index("run_id", unique=True, background=True)
    await db.letter_draft_evidence_snapshots.create_index("snapshot_id", unique=True, background=True)
    await db.letter_draft_evidence_snapshots.create_index("run_id", unique=True, background=True)
    await db.letter_draft_effects.create_index("effect_id", unique=True, background=True)
    await db.letter_draft_effects.create_index("effect_key", unique=True, background=True)
    await db.letter_draft_effects.create_index([("run_id", 1), ("created_at", -1)], background=True)
    await db.letter_draft_outbox.create_index("event_id", unique=True, background=True)
    await db.letter_draft_outbox.create_index([("status", 1), ("created_at", 1)], background=True)
    await db.letter_draft_shadow_comparisons.create_index("run_id", unique=True, background=True)
    await db.draft_context_packs.create_index(
        [("project.organization_id", 1), ("project.project_id", 1), ("created_at", -1)],
        background=True,
    )
    await db.issued_letters.create_index("issued_letter_id", unique=True, background=True)
    await db.issued_letters.create_index(
        [("letter_id", 1), ("run_id", 1)],
        unique=True,
        background=True,
    )
    await db.issued_letters.create_index(
        [("issued_by", 1), ("issued_at", -1)],
        background=True,
    )
    await db.prompt_templates.create_index(
        [("prompt_key", 1), ("version", -1), ("enabled", 1)],
        background=True,
    )

    # Arbitration pleadings drafting
    await db.arbitration_drafts.create_index(
        [("organization_id", 1), ("project_id", 1), ("contract_id", 1), ("draft_type", 1), ("status", 1)],
        background=True,
    )
    await db.arbitration_drafts.create_index(
        [("organization_id", 1), ("project_id", 1), ("created_at", -1)],
        background=True,
    )
    await db.arbitration_draft_versions.create_index(
        [("draft_id", 1), ("version", 1)],
        unique=True,
        background=True,
    )
    await db.arbitration_selected_references.create_index(
        [("draft_id", 1), ("source_type", 1), ("source_id", 1)],
        background=True,
    )
    await db.arbitration_claim_heads.create_index(
        [("draft_id", 1), ("head_type", 1)],
        background=True,
    )
    await db.arbitration_paragraph_responses.create_index(
        [("draft_id", 1), ("source_paragraph_number", 1)],
        background=True,
    )
    await db.arbitration_generation_runs.create_index(
        [("draft_id", 1), ("created_at", -1)],
        background=True,
    )
    await db.arbitration_drafts.create_index(
        [("case_id", 1), ("updated_at", -1)],
        background=True,
    )
    await db.arbitration_cases.create_index(
        [("organization_id", 1), ("project_id", 1), ("contract_id", 1), ("status", 1)],
        background=True,
    )
    await db.arbitration_cases.create_index(
        [("organization_id", 1), ("project_id", 1), ("updated_at", -1)],
        background=True,
    )
    await db.arbitration_cases.create_index(
        [("status", 1), ("readiness_score", 1), ("updated_at", -1)],
        background=True,
    )
    for _matrix_collection in [
        "arbitration_document_index",
        "arbitration_chronology_matrix",
        "arbitration_clause_matrix",
        "arbitration_issue_matrix",
        "arbitration_claim_matrix",
        "arbitration_defence_matrix",
        "arbitration_counterclaim_matrix",
        "arbitration_rejoinder_matrix",
        "arbitration_quantum_annexures",
        "arbitration_notice_compliance",
    ]:
        await db[_matrix_collection].create_index(
            [("case_id", 1), ("draft_id", 1), ("created_at", 1)],
            background=True,
        )
        await db[_matrix_collection].create_index(
            [("organization_id", 1), ("project_id", 1), ("case_id", 1)],
            background=True,
        )
        await db[_matrix_collection].create_index(
            [("case_id", 1), ("readiness_status", 1), ("approval_status", 1), ("updated_at", -1)],
            background=True,
        )
    await db.arbitration_document_index.create_index(
        [("case_id", 1), ("exhibit_id", 1)],
        unique=True,
        partialFilterExpression={"exhibit_id": {"$exists": True}},
        background=True,
    )
    await db.arbitration_readiness_checks.create_index(
        [("case_id", 1), ("draft_id", 1), ("check_key", 1)],
        background=True,
    )
    await db.arbitration_readiness_checks.create_index(
        [("case_id", 1), ("status", 1), ("updated_at", -1)],
        background=True,
    )
    await db.arbitration_agent_runs.create_index(
        [("case_id", 1), ("created_at", -1)],
        background=True,
    )
    await db.arbitration_agent_runs.create_index(
        [("case_id", 1), ("status", 1), ("created_at", -1)],
        background=True,
    )
    await db.arbitration_agent_runs.create_index("background_job_id", background=True)
    await db.arbitration_bundle_exports.create_index(
        [("case_id", 1), ("status", 1), ("created_at", -1)],
        background=True,
    )
    await db.arbitration_bundle_exports.create_index(
        [("case_id", 1), ("format", 1), ("created_at", -1)],
        background=True,
    )
    await db.arbitration_bundle_exports.create_index("background_job_id", background=True)
    await db.arbitration_bundle_exports.create_index("expires_at", background=True)

    # Matter chronology builder
    await db.matter_chronologies.create_index(
        [("organization_id", 1), ("project_id", 1), ("contract_id", 1), ("chronology_type", 1), ("status", 1)],
        background=True,
    )
    await db.matter_chronologies.create_index(
        [("organization_id", 1), ("project_id", 1), ("updated_at", -1)],
        background=True,
    )
    await db.matter_chronology_events.create_index(
        [("chronology_id", 1), ("event_date", 1), ("created_at", 1)],
        background=True,
    )
    await db.matter_chronology_events.create_index(
        [("organization_id", 1), ("project_id", 1), ("verification_status", 1)],
        background=True,
    )
    await db.matter_chronology_events.create_index(
        [("chronology_id", 1), ("source_document_id", 1), ("metadata.content_hash", 1)],
        background=True,
    )
    await db.matter_chronology_events.create_index(
        [("chronology_id", 1), ("event_classification", 1), ("supports_party", 1), ("pleading_use", 1)],
        background=True,
    )
    await db.matter_chronology_event_revisions.create_index(
        [("chronology_id", 1), ("event_id", 1), ("revision", 1)],
        unique=True,
        background=True,
    )
    await db.matter_chronology_exports.create_index(
        [("chronology_id", 1), ("created_at", -1)],
        background=True,
    )

    # Parties / Representatives
    await db.parties.create_index("organization_id", background=True)
    await db.parties.create_index("projects", background=True)
    await db.representatives.create_index("organization_id", background=True)
    await db.representatives.create_index("project_id", background=True)
    await db.representatives.create_index("party_id", background=True)

    # Tags
    await db.tags.create_index("organization_id", background=True)
    await db.tags.create_index("project_id", background=True)
    await db.tags.create_index("visibility", background=True)

    # Tasks
    await db.tasks.create_index("organization_id", background=True)
    await db.tasks.create_index("project_id", background=True)
    await db.tasks.create_index("assigned_to", background=True)
    await db.tasks.create_index("linked_claim_id", background=True)
    # Workflow assignment linkage (Phase 0): list tasks for an artifact, build the
    # "my tasks" board, and group by stage/type within a tenant.
    await db.tasks.create_index([("resource_type", 1), ("resource_id", 1)], background=True)
    await db.tasks.create_index([("assigned_to", 1), ("status", 1)], background=True)
    await db.tasks.create_index(
        [("organization_id", 1), ("project_id", 1), ("task_type", 1)], background=True
    )

    # Key Date / Milestone Tracker
    await db.key_date_milestones.create_index(
        [("organization_id", 1), ("project_id", 1), ("current_approved_key_date", 1)], background=True
    )
    await db.key_date_milestones.create_index("responsible_party_id", background=True)
    await db.key_date_eot_applications.create_index(
        [("milestone_id", 1), ("created_at", 1)], background=True
    )
    await db.key_date_extension_history.create_index(
        [("milestone_id", 1), ("revision_number", 1)], background=True
    )
    await db.key_date_achievements.create_index("milestone_id", background=True)
    await db.key_date_notifications.create_index(
        [("milestone_id", 1), ("notification_type", 1)], background=True
    )

    # Contract Controls: Variation Register + Bank Guarantee Register
    await db.variations.create_index(
        [("organization_id", 1), ("project_id", 1), ("contract_id", 1), ("status", 1)], background=True
    )
    await db.variations.create_index("variation_number", background=True)
    await db.bank_guarantees.create_index(
        [("organization_id", 1), ("project_id", 1), ("bg_status", 1), ("bg_expiry_date", 1)], background=True
    )
    await db.bank_guarantees.create_index("contract_id", background=True)
    await db.bg_extension_history.create_index(
        [("bg_id", 1), ("revision_number", 1)], background=True
    )
    await db.bg_notifications.create_index(
        [("bg_id", 1), ("notification_type", 1)], background=True
    )
    # Insurance Register
    await db.insurance_policies.create_index(
        [("organization_id", 1), ("project_id", 1), ("date_of_expiry", 1)], background=True
    )
    await db.insurance_policies.create_index("contract_id", background=True)
    await db.insurance_policies.create_index("insurance_type", background=True)
    await db.insurance_policies.create_index("created_by", background=True)
    # Enforce the no-duplicate-policy rule (same contract + type + number).
    await db.insurance_policies.create_index(
        [("project_id", 1), ("contract_id", 1), ("insurance_type", 1), ("policy_number", 1)],
        unique=True,
        partialFilterExpression={"policy_number": {"$type": "string"}},
        background=True,
    )
    await db.insurance_types.create_index(
        [("organization_id", 1), ("name", 1)], background=True
    )
    await db.insurance_notifications.create_index(
        [("insurance_id", 1), ("notification_type", 1)], background=True
    )
    await db.contract_master.create_index(
        [("organization_id", 1), ("project_id", 1), ("contract_id", 1)], background=True, unique=True
    )

    # Claims (Phase 4)
    await db.claims.create_index(
        [("organization_id", 1), ("project_id", 1), ("status", 1), ("created_at", -1)],
        background=True,
    )
    await db.claims.create_index([("organization_id", 1), ("project_id", 1), ("type", 1)], background=True)
    await db.claims.create_index("responsible_party_id", background=True)
    await db.claims.create_index("response_due_date", background=True)
    await db.claims.create_index("event_date", background=True)
    await db.claims.create_index("claim_ref", background=True)
    await db.claim_assessments.create_index(
        [("claim_id", 1), ("created_at", -1)], background=True
    )

    # Evidence graph / Contract Intelligence Timeline
    await db.project_events.create_index(
        [("organization_id", 1), ("project_id", 1), ("event_date", -1)], background=True
    )
    await db.project_events.create_index(
        [("organization_id", 1), ("project_id", 1), ("event_type", 1), ("status", 1)], background=True
    )
    await db.project_events.create_index(
        [("source_entity_type", 1), ("source_entity_id", 1)], background=True
    )
    await db.event_links.create_index(
        [("organization_id", 1), ("project_id", 1), ("status", 1), ("created_at", -1)], background=True
    )
    await db.event_links.create_index(
        [("link_group_id", 1), ("revision", -1)], background=True
    )
    await db.event_links.create_index(
        [("source_type", 1), ("source_id", 1), ("status", 1)], background=True
    )
    await db.event_links.create_index(
        [("target_type", 1), ("target_id", 1), ("status", 1)], background=True
    )
    await db.ai_extractions.create_index(
        [("source_document_id", 1), ("content_hash", 1), ("schema_version", 1)],
        unique=True,
        background=True,
    )
    await db.ai_extractions.create_index(
        [("organization_id", 1), ("project_id", 1), ("created_at", -1)], background=True
    )

    await db.drawing_references.create_index(
        [("organization_id", 1), ("project_id", 1), ("drawing_number", 1), ("revision", 1)], background=True
    )
    await db.drawing_references.create_index(
        [("organization_id", 1), ("project_id", 1), ("status", 1)], background=True
    )
    await db.drawing_references.create_index(
        [("organization_id", 1), ("project_id", 1), ("location", 1)], background=True
    )
    await db.delay_events.create_index(
        [("organization_id", 1), ("project_id", 1), ("start_date", -1)], background=True
    )
    await db.delay_events.create_index(
        [("organization_id", 1), ("project_id", 1), ("responsibility", 1), ("status", 1)], background=True
    )
    await db.delay_events.create_index(
        [("organization_id", 1), ("project_id", 1), ("location", 1)], background=True
    )
    await db.programme_milestones.create_index(
        [("organization_id", 1), ("project_id", 1), ("planned_date", -1)], background=True
    )
    await db.programme_milestones.create_index(
        [("organization_id", 1), ("project_id", 1), ("milestone_type", 1), ("status", 1)], background=True
    )
    await db.programme_milestones.create_index(
        [("organization_id", 1), ("project_id", 1), ("location", 1)], background=True
    )

    # Contract appraisal (Contract Appraisal Report — v2)
    await db.contract_appraisal_jobs.create_index(
        [("organization_id", 1), ("project_id", 1), ("status", 1), ("created_at", -1)],
        background=True,
    )
    await db.contract_appraisal_reports.create_index(
        [("organization_id", 1), ("project_id", 1), ("report_version", -1)], background=True
    )
    await db.contract_appraisal_reports.create_index("job_id", background=True)
    await db.contract_appraisal_review_comments.create_index(
        [("report_id", 1), ("created_at", -1)], background=True
    )
    for _reg in ("contract_obligations", "contract_risks", "contract_key_dates"):
        await db[_reg].create_index(
            [("organization_id", 1), ("project_id", 1), ("report_id", 1)], background=True
        )

    # SLA / time-bar rules (per-org notice-window overrides)
    await db.sla_rules.create_index("organization_id", background=True, unique=True)

    # Approval workflow records (one per resource)
    await db.approvals.create_index(
        [("resource_type", 1), ("resource_id", 1)], background=True, unique=True
    )
    await db.approvals.create_index(
        [("organization_id", 1), ("project_id", 1), ("state", 1)], background=True
    )

    # Emails/logs (if present)
    await db.email_logs.create_index("organization_id", background=True)
    await db.email_logs.create_index("project_id", background=True)
    await db.document_share_tokens.create_index("token_hash", unique=True, background=True)
    await db.document_share_tokens.create_index("document_id", background=True)
    await db.document_share_tokens.create_index(
        [("organization_id", 1), ("project_id", 1), ("created_at", -1)],
        background=True,
    )
    await db.document_share_tokens.create_index(
        "expires_at",
        expireAfterSeconds=0,
        background=True,
    )

    # SMTP settings
    await db.smtp_settings.create_index("scope_type", background=True)
    await db.smtp_settings.create_index("organization_id", background=True)
    await db.smtp_settings.create_index("project_id", background=True)
    await db.smtp_settings.create_index(
        [("scope_type", 1), ("organization_id", 1), ("project_id", 1)],
        unique=True,
        background=True,
    )

    # Email groups
    await db.email_groups.create_index("organization_id", background=True)
    await db.email_groups.create_index("project_id", background=True)

    # AI Style Profiles
    await db.ai_style_profiles.create_index("organization_id", background=True)
    await db.ai_style_profiles.create_index("project_id", background=True)
    await db.ai_style_profiles.create_index("updated_at", background=True)

    # Contract ingest jobs
    await db.contract_ingest_jobs.create_index("upload_id", unique=True, background=True)
    await db.contract_ingest_jobs.create_index(
        [("organization_id", 1), ("project_id", 1), ("createdAt", -1)],
        background=True,
    )
    await db.contract_ingest_jobs.create_index("status", background=True)
    await db.contract_ingest_jobs.create_index(
        [("status", 1), ("updatedAt", 1)],
        background=True,
    )
    await db.contract_upload_sessions.create_index(
        "expiresAt",
        expireAfterSeconds=0,
        background=True,
    )
    await db.contract_upload_sessions.create_index(
        [("organization_id", 1), ("project_id", 1), ("user_id", 1), ("createdAt", -1)],
        background=True,
    )

    # RBAC restructuring and monetisation
    await db.role_assignments.create_index(
        [("user_id", 1), ("scope_type", 1), ("organization_id", 1), ("project_id", 1)],
        background=True,
    )
    await db.organization_memberships.create_index(
        [("user_id", 1), ("organization_id", 1), ("status", 1)],
        background=True,
    )
    await db.project_memberships.create_index(
        [("user_id", 1), ("organization_id", 1), ("project_id", 1), ("status", 1)],
        background=True,
    )
    await db.expert_allocations.create_index(
        [("expert_user_id", 1), ("organization_id", 1), ("project_id", 1), ("status", 1)],
        background=True,
    )
    await db.expert_allocations.create_index(
        [("letter_id", 1), ("drafting_request_id", 1), ("status", 1)],
        background=True,
    )
    await db.plans.create_index("code", unique=True, background=True)
    await db.subscriptions.create_index(
        [("organization_id", 1), ("project_id", 1), ("package_id", 1), ("status", 1)],
        background=True,
    )
    await db.entitlements.create_index(
        [("subscription_id", 1), ("key", 1)],
        background=True,
    )
    await db.usage_events.create_index(
        [("organization_id", 1), ("project_id", 1), ("event_type", 1), ("created_at", -1)],
        background=True,
    )
    await db.usage_periods.create_index(
        [("organization_id", 1), ("project_id", 1), ("period", 1)],
        background=True,
    )
    await db.quota_buckets.create_index(
        [("subscription_id", 1), ("quota_key", 1), ("expires_at", 1)],
        background=True,
    )
    await db.billing_records.create_index(
        [("organization_id", 1), ("project_id", 1), ("created_at", -1)],
        background=True,
    )
    await db.billing_records.create_index("event_id", background=True)
    await db.billing_webhook_events.create_index("event_id", unique=True, background=True)
    await db.billing_webhook_events.create_index(
        [("provider", 1), ("received_at", -1)],
        background=True,
    )
    await db.offboarding_exports.create_index(
        [("organization_id", 1), ("project_id", 1), ("status", 1), ("expires_at", 1)],
        background=True,
    )
    await db.audit_events.create_index(
        [("organization_id", 1), ("project_id", 1), ("created_at", -1)],
        background=True,
    )
    await db.audit_events.create_index(
        [("actor_id", 1), ("action", 1), ("created_at", -1)],
        background=True,
    )
    await db.terms_versions.create_index("version", unique=True, background=True)
    await db.terms_versions.create_index([("is_active", 1), ("created_at", -1)], background=True)
    await db.security_terms_acceptances.create_index(
        [("user_id", 1), ("org_id", 1), ("terms_version", 1), ("terms_hash", 1)],
        unique=True,
        background=True,
    )
    await db.security_terms_acceptances.create_index(
        [("org_id", 1), ("accepted_at", -1)],
        background=True,
    )

    # Bulk upload jobs
    await db.bulk_upload_jobs.create_index("job_id", unique=True, background=True)
    await db.bulk_upload_jobs.create_index("created_at", background=True)
    await db.bulk_upload_jobs.create_index(
        [("organization_id", 1), ("project_id", 1), ("status", 1), ("created_at", -1)],
        background=True,
    )

    # Retrieval engine collections
    await db.ingestion_jobs.create_index("job_id", unique=True, background=True)
    await db.ingestion_jobs.create_index([("org_id", 1), ("project_id", 1), ("document_id", 1)], background=True)
    await db.ingestion_jobs.create_index([("document_id", 1), ("content_hash", 1)], background=True)
    await db.document_vectors.create_index("source_hash", background=True)
    await db.document_vectors.create_index(
        [("organization_id", 1), ("project_id", 1), ("clause_number", 1)],
        background=True,
    )
    await db.document_vectors.create_index(
        [("org_id", 1), ("project_id", 1), ("letter_no", 1)],
        background=True,
    )
    await db.chunks.create_index("chunk_id", unique=True, background=True)
    await db.chunks.create_index([("document_id", 1), ("org_id", 1), ("project_id", 1)], background=True)
    await db.chunks.create_index("content_hash", background=True)
    await db.storage_reconciliation_runs.create_index(
        [("run_type", 1), ("created_at", -1)],
        background=True,
    )
    await db.storage_reconciliation_runs.create_index(
        [("org_id", 1), ("project_id", 1), ("created_at", -1)],
        background=True,
    )
    await db.rag_runs.create_index([("org_id", 1), ("project_id", 1), ("run_type", 1), ("created_at", -1)], background=True)
    await db.agent_conversations.create_index("conversation_id", unique=True, background=True)
    await db.agent_messages.create_index([("conversation_id", 1), ("created_at", -1)], background=True)


async def ensure_indexes_with_retry(db, attempts: int = INDEX_CREATION_ATTEMPTS) -> None:
    """Attempt to create indexes with exponential backoff on failure."""

    delay = INDEX_CREATION_BASE_DELAY
    for attempt in range(1, attempts + 1):
        try:
            await ensure_indexes(db)
            logger.info("Database indexes ensured (attempt %s)", attempt)
            return
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
            raise
        except Exception as exc:  # pragma: no cover - logged for observability
            logger.warning(
                "Failed to create database indexes on attempt %s/%s: %s",
                attempt,
                attempts,
                exc,
            )
            if attempt == attempts:
                logger.error("Giving up on database index creation after %s attempts", attempts)
                return
            await asyncio.sleep(delay)
            delay *= 2

def is_connected() -> bool:
    """Return True if DB client and database are initialized."""
    global client, database
    return client is not None and database is not None
