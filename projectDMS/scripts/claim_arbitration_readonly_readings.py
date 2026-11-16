"""Phase 4b readings P-02 to P-14: claims, arbitration, roles and skills, read-only.

Plan: docs/superpowers/specs/2026-11-16-claim-bench-phase-4b-hardening.md, §8.1.

Run by section F of scripts/post_cutover_readonly_checks.sh, piped into the
running backend container so it uses that container's own database settings:

    docker exec -i <backend> python - < scripts/claim_arbitration_readonly_readings.py

It must also run on an image that predates Phase 4b (production at 34d664a has
none of this code), so it imports only the backend's configuration and
pymongo, and it reads collections by name.

READ-ONLY, by construction:
  - every database access goes through ReadOnlyDatabase / ReadOnlyCollection,
    which expose find, count_documents, estimated_document_count, distinct,
    aggregate and list_collection_names, and nothing else;
  - aggregate refuses any pipeline containing $out or $merge;
  - tests/test_claim_arbitration_readonly_readings.py fails if a write method
    name appears anywhere in this file outside the guard's refusal list.

PRIVACY: the output is counts, type names and configuration flags. It never
prints a name, email address, document title or record id. The one
configuration value printed (ARBITRATION_REVIEWER_ROLE_MATRIX) holds role
names, not credentials, and is printed only if set.

Output: one JSON object per line, {"reading": "P-xx", ...}. A reading that
cannot be taken is printed with "status": "not_read" and the reason; it is
never reported as zero.

The first line ("reading": "DB") names the database read and whether it looks
like the application's (it holds users and roles). If it does not, every
reading is "not_read": an empty or wrong database is not evidence of an empty
production (plan, owner review B.7).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

# Write operations the guard refuses by name. Kept here, and only here, so the
# source scan can tell a refusal list from a call.
_REFUSED = frozenset(
    {
        "insert_one", "insert_many", "update_one", "update_many", "replace_one",
        "delete_one", "delete_many", "find_one_and_update", "find_one_and_replace",
        "find_one_and_delete", "bulk_write", "drop", "drop_index", "drop_indexes",
        "create_index", "create_indexes", "rename", "drop_collection",
        "create_collection", "command",
    }
)
_ALLOWED_COLLECTION = frozenset(
    {"find", "count_documents", "estimated_document_count", "distinct", "aggregate"}
)
_WRITE_STAGES = ("$out", "$merge")

READING_WINDOW_DAYS = 90

#: Reviewer role identities the plan seeds (D-06, AQ-3): skill-pack reviewers
#: and the arbitration gate roles named in approval_policy.DEFAULT_GATE_ROLES.
REVIEWER_ROLE_KEYS = (
    "senior_legal_approver",
    "chief_contract_expert",
    "delay_planning_expert",
    "arbitration_case_preparation_expert",
    "legal_reviewer",
    "contract_reviewer",
    "delay_reviewer",
    "quantum_reviewer",
    "export_authorizer",
)

#: Claim statuses the Phase 0 model accepts (models/claim.py ClaimStatus).
KNOWN_CLAIM_STATUSES = (
    "potential", "draft", "notified", "submitted", "under_review",
    "agreed", "rejected", "disputed", "closed",
)

#: One collection per case matrix (services/arbitration_drafting/matrix_registry.py).
MATRIX_COLLECTIONS = (
    "arbitration_document_index", "arbitration_chronology_matrix", "arbitration_clause_matrix",
    "arbitration_issue_matrix", "arbitration_claim_matrix", "arbitration_defence_matrix",
    "arbitration_counterclaim_matrix", "arbitration_rejoinder_matrix", "arbitration_quantum_annexures",
    "arbitration_notice_compliance", "arbitration_jurisdiction_matrix", "arbitration_expert_alignment",
)

#: Migrations Phase 0 to 4 add, newer than any production receipt.
PHASE_MIGRATIONS = (
    "20261114_0001", "20261114_0002", "20261114_0003",
    "20261115_0001", "20261116_0001", "20261117_0001",
    "20261118_0001", "20261119_0001",
)


class ReadOnlyCollection:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def __getattr__(self, name: str) -> Any:
        if name in _ALLOWED_COLLECTION:
            target = getattr(self._collection, name)
            if name == "aggregate":
                return self._aggregate(target)
            return target
        raise PermissionError(f"read-only readings: {name} is not a read operation")

    @staticmethod
    def _aggregate(target: Callable[..., Any]) -> Callable[..., Any]:
        def guarded(pipeline: List[Dict[str, Any]], *args: Any, **kwargs: Any) -> Any:
            for stage in pipeline:
                if any(key in stage for key in _WRITE_STAGES):
                    raise PermissionError("read-only readings: $out and $merge are refused")
            return target(pipeline, *args, **kwargs)

        return guarded


class ReadOnlyDatabase:
    def __init__(self, database: Any) -> None:
        self._database = database

    def list_collection_names(self) -> List[str]:
        return list(self._database.list_collection_names())

    def __getitem__(self, name: str) -> ReadOnlyCollection:
        return ReadOnlyCollection(self._database[name])

    def __getattr__(self, name: str) -> Any:
        raise PermissionError(f"read-only readings: database.{name} is not available")


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float" if not value.is_integer() else "float_integral"
    if isinstance(value, str):
        stripped = value.replace(",", "").strip()
        try:
            float(stripped)
        except ValueError:
            return "string_non_numeric"
        return "string_numeric"
    return type(value).__name__


def _histogram(values: Iterable[Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _group_count(collection: ReadOnlyCollection, field: str, match: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    pipeline: List[Dict[str, Any]] = []
    if match:
        pipeline.append({"$match": match})
    pipeline.append({"$group": {"_id": f"${field}", "n": {"$sum": 1}}})
    return {str(row["_id"]): int(row["n"]) for row in collection.aggregate(pipeline)}


def _reading(code: str, **fields: Any) -> Dict[str, Any]:
    return {"reading": code, "status": "read", **fields}


def _not_read(code: str, reason: str) -> Dict[str, Any]:
    return {"reading": code, "status": "not_read", "reason": reason}


def read_migrations(db: ReadOnlyDatabase) -> Dict[str, Any]:
    names = db.list_collection_names()
    # migrations/runner.py LEDGER_COLLECTION
    ledger_name = "schema_migrations" if "schema_migrations" in names else None
    if ledger_name is None:
        return _not_read("P-02", "no migration ledger collection found by the known names")
    applied = {str(v) for v in db[ledger_name].distinct("version")}
    return _reading(
        "P-02",
        ledger_collection=ledger_name,
        ledger_rows=db[ledger_name].count_documents({}),
        phase_migrations_applied=[v for v in PHASE_MIGRATIONS if v in applied],
        phase_migrations_absent=[v for v in PHASE_MIGRATIONS if v not in applied],
    )


def read_claims(db: ReadOnlyDatabase, now: datetime) -> Dict[str, Any]:
    if "claims" not in db.list_collection_names():
        return _reading("P-03", claims_collection_present=False, total=0)
    claims = db["claims"]
    since = now - timedelta(days=READING_WINDOW_DAYS)
    by_status = _group_count(claims, "status")
    amount_types: List[str] = []
    days_types: List[str] = []
    for row in claims.find({}, {"amount_claimed": 1, "eot_days_claimed": 1, "_id": 0}):
        amount_types.append(_type_name(row.get("amount_claimed")))
        days_types.append(_type_name(row.get("eot_days_claimed")))
    return _reading(
        "P-03",
        claims_collection_present=True,
        total=claims.count_documents({}),
        by_status=by_status,
        off_enum_statuses=sum(n for s, n in by_status.items() if s not in KNOWN_CLAIM_STATUSES),
        by_type=_group_count(claims, "type"),
        amount_claimed_types=_histogram(amount_types),
        eot_days_claimed_types=_histogram(days_types),
        with_bench=claims.count_documents({"bench": {"$type": "object"}}),
        with_origin=claims.count_documents({"origin": {"$type": "object"}}),
        with_contract_id=claims.count_documents({"contract_id": {"$nin": [None, ""]}}),
        updated_in_window=claims.count_documents({"updated_at": {"$gte": since}}),
        created_in_window=claims.count_documents({"created_at": {"$gte": since}}),
        window_days=READING_WINDOW_DAYS,
    )


def read_sla_rules(db: ReadOnlyDatabase) -> Dict[str, Any]:
    if "sla_rules" not in db.list_collection_names():
        return _reading("P-04", sla_rules_collection_present=False)
    value_types: List[str] = []
    organisations = 0
    for row in db["sla_rules"].find({}, {"notice_windows": 1, "_id": 0}):
        organisations += 1
        for value in (row.get("notice_windows") or {}).values():
            value_types.append(_type_name(value))
    return _reading(
        "P-04",
        sla_rules_collection_present=True,
        documents=organisations,
        notice_window_value_types=_histogram(value_types),
    )


def read_roles(db: ReadOnlyDatabase) -> Dict[str, Any]:
    roles = db["roles"]
    users = db["users"]
    by_key: Dict[str, Dict[str, Any]] = {}
    for key in REVIEWER_ROLE_KEYS:
        documents_by_id = roles.count_documents({"_id": key})
        documents_by_name = roles.count_documents({"name": {"$regex": f"^{key}$", "$options": "i"}})
        holders = users.count_documents({"roles": key})
        by_key[key] = {
            "role_documents_with_this_id": documents_by_id,
            "role_documents_with_this_name": documents_by_name,
            "users_holding_this_key": holders,
        }
    return _reading("P-05", reviewer_roles=by_key)


def read_multi_role_users(db: ReadOnlyDatabase) -> Dict[str, Any]:
    users = db["users"]
    multi = users.count_documents({"roles.1": {"$exists": True}})
    return _reading("P-06", users_total=users.count_documents({}), users_with_more_than_one_role=multi)


def read_arbitration(db: ReadOnlyDatabase, now: datetime) -> Dict[str, Any]:
    names = set(db.list_collection_names())
    if "arbitration_drafts" not in names and "arbitration_cases" not in names:
        return _reading("P-07", arbitration_collections_present=False)
    cases = db["arbitration_cases"]
    drafts = db["arbitration_drafts"]
    matrix_rows: Dict[str, int] = {}
    for name in MATRIX_COLLECTIONS:
        if name in names:
            matrix_rows[name] = db[name].count_documents({})
    claim_matrix: Dict[str, int] = {}
    if "arbitration_claim_matrix" in names:
        rows = db["arbitration_claim_matrix"]
        claim_matrix = {
            "carrying_an_escalation_origin": rows.count_documents({"origin_key": {"$nin": [None, ""]}}),
            "without_an_escalation_origin": rows.count_documents({"origin_key": {"$in": [None, ""]}}),
        }
    return _reading(
        "P-07",
        arbitration_collections_present=True,
        cases_by_status=_group_count(cases, "status") if "arbitration_cases" in names else {},
        drafts_total=drafts.count_documents({}) if "arbitration_drafts" in names else 0,
        drafts_without_case=(
            drafts.count_documents({"case_id": {"$in": [None, ""]}}) if "arbitration_drafts" in names else 0
        ),
        drafts_by_status=_group_count(drafts, "status") if "arbitration_drafts" in names else {},
        matrix_row_collections=matrix_rows,
        claim_matrix_rows_by_origin=claim_matrix,
    )


def read_skills(db: ReadOnlyDatabase) -> Dict[str, Any]:
    names = db.list_collection_names()
    skill_collections = sorted(n for n in names if n.startswith("skill_"))
    counts = {n: db[n].count_documents({}) for n in skill_collections}
    published = 0
    if "skill_versions" in names:
        published = db["skill_versions"].count_documents({"status": "published"})
    return _reading("P-08", skill_collections=counts, published_versions=published)


def read_capacity(db: ReadOnlyDatabase) -> Dict[str, Any]:
    projects = db["projects"]
    return _reading(
        "P-09",
        projects_total=projects.count_documents({}),
        projects_with_contractual_capacity=_group_count(
            projects, "contractual_capacity", {"contractual_capacity": {"$nin": [None, ""]}}
        ),
    )


def read_reviewer_matrix_setting(environ: Dict[str, str]) -> Dict[str, Any]:
    value = environ.get("ARBITRATION_REVIEWER_ROLE_MATRIX")
    if value is None or not value.strip():
        return _reading("P-10", arbitration_reviewer_role_matrix_set=False)
    return _reading("P-10", arbitration_reviewer_role_matrix_set=True, value=value.strip())


def read_usage(db: ReadOnlyDatabase, now: datetime) -> Dict[str, Any]:
    if "audit_events" not in db.list_collection_names():
        return _not_read("P-11", "no audit_events collection; read gateway access logs instead (section F prints them)")
    since = now - timedelta(days=READING_WINDOW_DAYS)
    events = db["audit_events"]
    pipeline = [
        {"$match": {"action": {"$regex": "^(claim|claims)[._]"}, "created_at": {"$gte": since}}},
        {"$group": {"_id": "$action", "n": {"$sum": 1}}},
    ]
    by_action = {str(row["_id"]): int(row["n"]) for row in events.aggregate(pipeline)}
    return _reading(
        "P-11",
        claim_audit_events_by_action=dict(sorted(by_action.items())),
        window_days=READING_WINDOW_DAYS,
        note="page views are not audited; the gateway request counts in section F complete this reading",
    )


def read_caseless_drafts(db: ReadOnlyDatabase, now: datetime) -> Dict[str, Any]:
    if "arbitration_drafts" not in db.list_collection_names():
        return _reading("P-12", arbitration_drafts_collection_present=False)
    since = now - timedelta(days=READING_WINDOW_DAYS)
    drafts = db["arbitration_drafts"]
    match = {"case_id": {"$in": [None, ""]}, "created_at": {"$gte": since}}
    creators = drafts.distinct("created_by", match)
    return _reading(
        "P-12",
        caseless_drafts_created_in_window=drafts.count_documents(match),
        distinct_creators=len(creators),
        window_days=READING_WINDOW_DAYS,
        note="callers by client come from the gateway request counts in section F",
    )


def read_case_visibility(db: ReadOnlyDatabase) -> Dict[str, Any]:
    """C-25: which arbitration cases the pre-fix case queries could not see.

    Production at 34d664a looks cases up with {"deleted_at": {"$exists": False}},
    while ArbitrationCase stores deleted_at as null. A case with a stored null is
    invisible to get_case and list_cases there, though it is not deleted.
    """
    if "arbitration_cases" not in db.list_collection_names():
        return _reading("P-13", arbitration_cases_collection_present=False)
    cases = db["arbitration_cases"]
    missing = {"deleted_at": {"$exists": False}}
    null = {"deleted_at": {"$type": "null"}}
    set_value = {"deleted_at": {"$exists": True, "$ne": None}}
    affected_ids = cases.distinct("_id", null)
    names = set(db.list_collection_names())
    dependents: Dict[str, int] = {}
    if affected_ids:
        for name in ("arbitration_drafts",) + MATRIX_COLLECTIONS:
            if name in names:
                dependents[name] = db[name].count_documents({"case_id": {"$in": affected_ids}})
    return _reading(
        "P-13",
        arbitration_cases_collection_present=True,
        cases_total=cases.count_documents({}),
        deleted_at_missing=cases.count_documents(missing),
        deleted_at_null=len(affected_ids),
        deleted_at_set=cases.count_documents(set_value),
        visible_to_pre_fix_query=cases.count_documents(missing),
        potentially_affected_by_pre_fix_query=len(affected_ids),
        affected_by_status=_group_count(cases, "status", null),
        affected_organisations=len(cases.distinct("organization_id", null)),
        records_referencing_affected_cases=dependents,
        note="affected = stored deleted_at null: not deleted, but not found by get_case or list_cases at 34d664a",
    )


CLAIM_TYPES = ("eot", "variation", "payment_ipc", "loss_expense", "acceleration", "defect", "other")


def read_notice_requirements(db: ReadOnlyDatabase) -> Dict[str, Any]:
    """C-08 / D-08 (4B-14): notice periods recorded under the rules before 4B-14.

    Before 4B-14 the client supplied ``origin`` and the clause, and the recorder could
    verify their own entry. Counts only: how many current periods are verified by the
    person who recorded them, how many claim an origin the server never checked, and
    how many carry a claim type outside the claim types.
    """
    if "claim_notice_requirements" not in db.list_collection_names():
        return _reading("P-14", claim_notice_requirements_collection_present=False)
    rows = db["claim_notice_requirements"]
    current = {"status": "current"}
    self_verified = {
        "status": "current",
        "verification": "verified",
        "created_by": {"$nin": [None, ""]},
        "$expr": {"$eq": ["$verified_by", "$created_by"]},
    }
    return _reading(
        "P-14",
        claim_notice_requirements_collection_present=True,
        requirements_total=rows.count_documents({}),
        by_status=_group_count(rows, "status"),
        current_by_origin=_group_count(rows, "origin", current),
        current_by_verification=_group_count(rows, "verification", current),
        current_verified_by_their_recorder=rows.count_documents(self_verified),
        current_with_unknown_claim_type=rows.count_documents({**current, "claim_type": {"$nin": list(CLAIM_TYPES)}}),
        organisations_with_current_requirements=len(rows.distinct("organization_id", current)),
        note="written before 4B-14: origin was client-supplied and self-verification was allowed",
    )


def database_check(db: ReadOnlyDatabase, database_name: Optional[str]) -> Dict[str, Any]:
    """Whether this is the application database: it must hold users and roles."""
    names = set(db.list_collection_names())
    users = db["users"].estimated_document_count() if "users" in names else 0
    roles = db["roles"].estimated_document_count() if "roles" in names else 0
    return {
        "reading": "DB",
        "status": "read",
        "database": database_name,
        "collections": len(names),
        "initialised": bool(users and roles),
    }


def collect(
    db: Any,
    environ: Dict[str, str],
    now: Optional[datetime] = None,
    database_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Every reading, each isolated: one failing reading never hides the others."""
    guarded = db if isinstance(db, ReadOnlyDatabase) else ReadOnlyDatabase(db)
    moment = now or datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        check = database_check(guarded, database_name)
    except PermissionError:
        raise
    except Exception as exc:  # noqa: BLE001
        check = {"reading": "DB", "status": "not_read", "database": database_name,
                 "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
    steps = [
        ("P-02", lambda: read_migrations(guarded)),
        ("P-03", lambda: read_claims(guarded, moment)),
        ("P-04", lambda: read_sla_rules(guarded)),
        ("P-05", lambda: read_roles(guarded)),
        ("P-06", lambda: read_multi_role_users(guarded)),
        ("P-07", lambda: read_arbitration(guarded, moment)),
        ("P-08", lambda: read_skills(guarded)),
        ("P-09", lambda: read_capacity(guarded)),
        ("P-10", lambda: read_reviewer_matrix_setting(environ)),
        ("P-11", lambda: read_usage(guarded, moment)),
        ("P-12", lambda: read_caseless_drafts(guarded, moment)),
        ("P-13", lambda: read_case_visibility(guarded)),
        ("P-14", lambda: read_notice_requirements(guarded)),
    ]
    out: List[Dict[str, Any]] = [check]
    if not check.get("initialised"):
        reason = (
            "the database read holds no application users and roles: it is empty or not the "
            "application database, which is not evidence of an empty production"
        )
        for code, step in steps:
            if code == "P-10":  # configuration, not data
                out.append(step())
            else:
                out.append(_not_read(code, reason))
        return out
    for code, step in steps:
        try:
            out.append(step())
        except PermissionError:
            raise
        except Exception as exc:  # noqa: BLE001 - a reading that fails is reported, not hidden
            out.append(_not_read(code, f"{type(exc).__name__}: {str(exc)[:200]}"))
    return out


def _backend_database() -> tuple:
    """The database the running backend uses, and its name, from its own settings."""
    from pymongo import MongoClient

    from rbac_backend.core.config import settings

    try:
        from rbac_backend.core.database import _database_name

        name = _database_name()
    except ImportError:  # older images: fall back to the URL path
        from urllib.parse import urlparse

        name = urlparse(settings.DATABASE_URL).path.lstrip("/").split("/")[0] or "contraclaim"
    kwargs: Dict[str, Any] = {"serverSelectionTimeoutMS": 10_000}
    replica_set = getattr(settings, "MONGODB_REPLICA_SET", None)
    if replica_set and "replicaset=" not in settings.DATABASE_URL.lower():
        kwargs["replicaSet"] = replica_set
    client = MongoClient(settings.DATABASE_URL, **kwargs)
    return client[name], name


def main() -> int:
    try:
        database, name = _backend_database()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"reading": "ALL", "status": "not_read", "reason": f"no database: {type(exc).__name__}"}))
        return 1
    for row in collect(database, dict(os.environ), database_name=name):
        print(json.dumps(row, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
