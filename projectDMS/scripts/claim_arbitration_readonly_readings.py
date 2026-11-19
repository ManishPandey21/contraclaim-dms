"""Phase 4b readings P-02 to P-16: claims, arbitration, roles and skills, read-only.

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
    "20261118_0001", "20261119_0001", "20261120_0001",
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
        other_type=_read_other_claim_types(db, claims),
    )


def _read_other_claim_types(db: ReadOnlyDatabase, claims: ReadOnlyCollection) -> Dict[str, Any]:
    """Claim-type confirmation (2026-11-18): how many stored "other" claims predate it.

    A claim stored before the change has no ``type_confirmed`` and reads as confirmed,
    so its notice assessment can show a verified "other" period nobody chose for it.
    Counts only, for the owner's decision on whether those claims become provisional;
    nothing is changed.
    """
    legacy = {"type": "other", "type_confirmed": {"$exists": False}}
    verified_other_orgs: List[str] = []
    if "claim_notice_requirements" in db.list_collection_names():
        verified_other_orgs = [
            str(org)
            for org in db["claim_notice_requirements"].distinct(
                "organization_id", {"status": "current", "claim_type": "other", "verification": "verified"}
            )
            if org
        ]
    return {
        "other_total": claims.count_documents({"type": "other"}),
        "legacy_without_type_confirmed": claims.count_documents(legacy),
        "provisional": claims.count_documents({"type": "other", "type_confirmed": False}),
        "confirmed": claims.count_documents({"type": "other", "type_confirmed": True}),
        "legacy_by_status": _group_count(claims, "status", legacy),
        "legacy_with_frozen_evidence": claims.count_documents({**legacy, "evidence_frozen_at": {"$nin": [None, ""]}}),
        # These would read "verified against the contract" today without a person having chosen the type.
        "legacy_in_organisations_with_a_verified_other_period": claims.count_documents(
            {**legacy, "organization_id": {"$in": verified_other_orgs}}
        ),
        "organisations_with_legacy_other_claims": len(
            [org for org in claims.distinct("organization_id", legacy) if org]
        ),
    }


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
    return _reading(
        "P-05",
        reviewer_roles=by_key,
        custom_role_compatibility=_read_custom_role_compatibility(db),
        billing_permission_impact=_read_billing_permission_impact(db),
    )


#: DEF-4 target grants, inlined so the reading runs on images that predate the plan module
#: (tests/test_claim_arbitration_readonly_readings.py keeps it equal to
#: services.billing_permission_plan.TARGET_ROLE_GRANTS).
_DEF4_FINANCIAL = frozenset({
    "billing.record.view", "billing.plan.view", "billing.invoice.view", "billing.invoice.download",
    "subscription.history.view", "subscription.usage.view",
})
_DEF4_MUTATIONS = frozenset({
    "subscription.upgrade", "subscription.downgrade", "subscription.cancel", "subscription.addon.manage",
    "subscription.trial.manage",
})
DEF4_TARGET_ROLE_GRANTS = {
    "orgadmin": _DEF4_FINANCIAL | {"subscription.status.view"},
    "contraclaim_billing_admin": _DEF4_FINANCIAL | {"subscription.status.view"} | _DEF4_MUTATIONS,
    "projectadmin": frozenset({"subscription.status.view"}),
}
#: Billing views each legacy name reaches today (core.permissions LEGACY_PERMISSION_ALIASES).
DEF4_ALIAS_REACH = {
    "organizations:read": frozenset({"billing.plan.view", "billing.invoice.view", "billing.invoice.download", "subscription.history.view"}),
    "orgs:view": frozenset({"billing.plan.view", "billing.invoice.view", "billing.invoice.download", "subscription.history.view"}),
    "reports:view": frozenset({"subscription.usage.view"}),
}
_DEF4_BUILT_IN = frozenset({
    "superadmin", "orgadmin", "orguser", "projectadmin", "projectuser", "contractmgr_org", "reporter",
    "doccontroller", "settings_manager", "limited_user", "contraclaim_billing_admin",
})


def _read_billing_permission_impact(db: ReadOnlyDatabase) -> Dict[str, Any]:
    """DEF-4: who the explicit billing policy would change, before anyone decides to turn it on.

    Counts only; organisation ids are listed, never user names or emails. Per active user:
    the billing views they reach today only through a legacy alias (organizations:read,
    orgs:view, reports:view) and not by an explicit grant; whether the planned grant step
    (DEF4_TARGET_ROLE_GRANTS) covers all of them (kept) or not (lost, at least in part).
    Also: wildcard holders (they lose billing under the flag), Project Admins whose
    role-name checkout ends, holders of subscription.entitlement.manage (today they see every
    organisation's service state), custom roles holding the legacy or the explicit names,
    and what the grant step must add to each built-in role document.

    Approximations, stated: role references are matched by stored key or id (no alias
    resolution); a custom role counts wherever the user is, not only in its own organisation
    (over-counts); a role is inactive only when ``is_active`` is false, as the application reads it.
    """
    legacy = set(DEF4_ALIAS_REACH)

    def billing(name: str) -> bool:
        return name.startswith(("billing.", "subscription."))

    roles = {
        str(row["_id"]): row
        for row in db["roles"].find({}, {"permissions": 1, "is_system": 1, "is_active": 1})
    }
    losing_by_role: Dict[str, int] = {}
    keeping_by_role: Dict[str, int] = {}
    losing_by_org: Dict[str, int] = {}
    counts = {
        "active_users_read": 0,
        "users_relying_on_legacy_aliases_for_billing": 0,
        "of_whom_keep_billing_after_the_grant_step": 0,
        "of_whom_lose_billing_after_the_grant_step": 0,
        "users_with_explicit_billing_names_today": 0,
        "wildcard_holders_losing_billing_under_the_flag": 0,
        "project_admins_with_role_name_checkout_today": 0,
        "users_holding_subscription_entitlement_manage": 0,
    }
    for user in db["users"].find({}, {"roles": 1, "organization_id": 1, "organizations": 1, "disabled": 1, "is_active": 1}):
        if user.get("disabled") or user.get("is_active") is False:
            continue
        counts["active_users_read"] += 1
        keys = [str(key) for key in (user.get("roles") or [])]
        if "superadmin" in keys:
            continue
        held: set = set()
        for key in keys:
            role = roles.get(key) or {}
            if role.get("is_active") is False:
                continue
            held.update(str(name) for name in (role.get("permissions") or []))
        explicit = {name for name in held if billing(name)}
        if explicit:
            counts["users_with_explicit_billing_names_today"] += 1
        if "*" in held:
            counts["wildcard_holders_losing_billing_under_the_flag"] += 1
        if "projectadmin" in keys:
            counts["project_admins_with_role_name_checkout_today"] += 1
        if "subscription.entitlement.manage" in held:
            counts["users_holding_subscription_entitlement_manage"] += 1
        reach = set().union(*(DEF4_ALIAS_REACH[name] for name in held & legacy)) if held & legacy else set()
        relied = reach - explicit
        if not relied:
            continue
        counts["users_relying_on_legacy_aliases_for_billing"] += 1
        role_type = next((key for key in keys if key in _DEF4_BUILT_IN), "custom")
        granted = set().union(*(DEF4_TARGET_ROLE_GRANTS.get(key, frozenset()) for key in keys)) if keys else set()
        if relied <= granted:
            counts["of_whom_keep_billing_after_the_grant_step"] += 1
            keeping_by_role[role_type] = keeping_by_role.get(role_type, 0) + 1
        else:
            counts["of_whom_lose_billing_after_the_grant_step"] += 1
            losing_by_role[role_type] = losing_by_role.get(role_type, 0) + 1
            for org in {str(o) for o in [user.get("organization_id"), *(user.get("organizations") or [])] if o}:
                losing_by_org[org] = losing_by_org.get(org, 0) + 1
    custom = {key: role for key, role in roles.items() if key not in _DEF4_BUILT_IN and not role.get("is_system")}
    return {
        **counts,
        "losing_billing_by_role_type": dict(sorted(losing_by_role.items())),
        "keeping_billing_by_role_type": dict(sorted(keeping_by_role.items())),
        "losing_billing_by_organization": dict(sorted(losing_by_org.items())),
        "custom_roles_holding_legacy_view_names": sum(
            1 for role in custom.values() if legacy & {str(n) for n in role.get("permissions") or []}
        ),
        "custom_roles_holding_explicit_billing_names": sum(
            1 for role in custom.values() if any(billing(str(n)) for n in role.get("permissions") or [])
        ),
        "grant_step_names_missing_per_built_in_role": {
            key: sorted(set(names) - {str(n) for n in (roles.get(key) or {}).get("permissions") or []})
            for key, names in sorted(DEF4_TARGET_ROLE_GRANTS.items())
        },
    }


def _read_custom_role_compatibility(db: ReadOnlyDatabase) -> Dict[str, Any]:
    """SEC-1 to SEC-4 and JG-7(c): what the security changes would change for stored data.

    Counts only. A custom role now grants only where it was granted (its organisation,
    its project, to a member); a role naming no organisation grants an organisation's
    user nothing; a role named like a built-in role keeps the built-in meaning; a
    disabled or ``is_active: false`` account stops authenticating. Read before
    deployment; nothing here is changed by the code.
    """
    try:
        from rbac_backend.services.role_grants import role_is_built_in
    except ImportError:
        return {"status": "not_read", "reason": "this image has no services.role_grants"}
    roles = list(db["roles"].find({}, {"name": 1, "is_system": 1, "scope": 1, "organization_id": 1, "project_id": 1, "is_active": 1, "permissions": 1}))
    by_id = {str(role["_id"]): role for role in roles}
    custom = {key: role for key, role in by_id.items() if not role_is_built_in(role)}
    from rbac_backend.core.role_reference import normalize_role_key
    from rbac_backend.services.role_grants import BUILT_IN_ROLE_KEYS

    stripped_keys = {"".join(ch for ch in key if ch.isalnum()) for key in BUILT_IN_ROLE_KEYS}

    def _named_like_built_in(name: Any) -> bool:
        text = str(name or "").strip().lower()
        return normalize_role_key(text) in BUILT_IN_ROLE_KEYS or "".join(ch for ch in text if ch.isalnum()) in stripped_keys

    # Review R-1: such a role is a custom role now (bounded like any other); before it was
    # treated as built-in by its name.
    named_like_built_in = sum(1 for role in custom.values() if _named_like_built_in(role.get("name")))
    counts = {
        "custom_roles_total": len(custom),
        "custom_roles_without_organisation_or_project": sum(
            1 for role in custom.values() if not role.get("organization_id") and not role.get("project_id")
        ),
        "roles_named_like_a_built_in_role": named_like_built_in,
        "users_total": 0,
        "users_with_custom_roles_only": 0,
        "users_holding_a_custom_role_of_another_organisation": 0,
        "users_holding_a_project_role_without_the_project": 0,
        "organisation_users_holding_a_role_naming_no_organisation": 0,
        "users_disabled": 0,
        "users_is_active_false_but_not_disabled": 0,
    }
    changes: Dict[str, Any] = {}
    project_orgs: Dict[str, Optional[str]] = {
        str(row["_id"]): (str(row["organization_id"]) if row.get("organization_id") else None)
        for row in db["projects"].find({}, {"organization_id": 1})
    } if "projects" in db.list_collection_names() else {}
    for user in db["users"].find({}, {"roles": 1, "organization_id": 1, "organizations": 1, "projects": 1, "disabled": 1, "is_active": 1}):
        counts["users_total"] += 1
        if user.get("disabled"):
            counts["users_disabled"] += 1
        elif user.get("is_active") is False:
            counts["users_is_active_false_but_not_disabled"] += 1
        references = [str(reference) for reference in (user.get("roles") or [])]
        held = [custom[ref] for ref in references if ref in custom]
        if references and len(held) == len(references):
            counts["users_with_custom_roles_only"] += 1
        organisations = {str(org) for org in [user.get("organization_id"), *(user.get("organizations") or [])] if org}
        projects = {str(project) for project in (user.get("projects") or []) if project}
        if any(role.get("organization_id") and str(role["organization_id"]) not in organisations for role in held):
            counts["users_holding_a_custom_role_of_another_organisation"] += 1
        if any(role.get("project_id") and str(role["project_id"]) not in projects for role in held):
            counts["users_holding_a_project_role_without_the_project"] += 1
        if organisations and any(not role.get("organization_id") and not role.get("project_id") for role in held):
            counts["organisation_users_holding_a_role_naming_no_organisation"] += 1
        _classify_user(changes, user, held, organisations, projects, project_orgs)
    summary: Dict[str, Any] = {}
    for key, value in changes.items():
        organisations_seen = value.pop("_orgs")
        per_organisation = value.pop("_per_org")
        # Counts only: how many organisations, and users per organisation (largest first), never which.
        summary[key] = {
            **value,
            "organisations_affected": len(organisations_seen),
            "users_per_organisation": sorted(per_organisation.values(), reverse=True),
        }
    counts["changes"] = summary
    counts["users_with_a_non_objectid_id"] = sum(1 for _ in db["users"].find({"_id": {"$not": {"$type": "objectId"}}}, {"_id": 1}))
    counts["custom_roles_carrying_a_name_that_passes_a_non_delegable_check"] = sum(
        1 for role in custom.values() if _passes_non_delegable(role.get("permissions") or [])
    )
    return counts


#: Each class of user whose access SEC-1 to SEC-4 change, with the re-grant that keeps
#: the intended access properly scoped (applied by an administrator before enforcement,
#: never automatically).
CHANGE_CLASSES = {
    "custom_role_of_an_organisation_the_user_is_not_in": (
        "organisation",
        "re-create the role in the user's own organisation (or add the user to that organisation, if intended) and assign that",
    ),
    "project_role_for_a_project_the_user_is_not_assigned_to": (
        "project",
        "assign the user to the project, or replace the role with one for a project they are on",
    ),
    "project_role_for_a_project_of_another_organisation": (
        "project",
        "replace with a role of the user's organisation; a project of another organisation is never reachable",
    ),
    "role_naming_no_organisation_held_by_an_organisation_user": (
        "platform",
        "re-create the role in the user's organisation with the same permissions (each permission delegable there)",
    ),
    "account_is_active_false_but_not_disabled": (
        "account",
        "confirm the account is meant to be inactive; if not, set is_active true before enforcement",
    ),
}


def _classify_user(changes: Dict[str, Any], user: Dict[str, Any], held: List[Dict[str, Any]],
                   organisations: Any, projects: Any, project_orgs: Dict[str, Optional[str]]) -> None:
    def note(key: str, organisation: Optional[str], entries: Iterable[Any]) -> None:
        bucket = changes.setdefault(key, {
            "role_type": CHANGE_CLASSES[key][0], "recommended_regrant": CHANGE_CLASSES[key][1],
            "users": 0, "permission_entries_affected": {}, "_orgs": set(), "_per_org": {},
        })
        bucket["users"] += 1
        label = organisation or "(none)"
        bucket["_orgs"].add(label)
        bucket["_per_org"][label] = bucket["_per_org"].get(label, 0) + 1
        for entry in entries:
            name = str(entry)
            bucket["permission_entries_affected"][name] = bucket["permission_entries_affected"].get(name, 0) + 1

    home = str(user.get("organization_id")) if user.get("organization_id") else None
    seen = set()
    for role in held:
        org = str(role["organization_id"]) if role.get("organization_id") else None
        project = str(role["project_id"]) if role.get("project_id") else None
        entries = role.get("permissions") or []
        if project:
            owner = project_orgs.get(project)
            if owner and owner not in organisations:
                key = "project_role_for_a_project_of_another_organisation"
            elif project not in projects:
                key = "project_role_for_a_project_the_user_is_not_assigned_to"
            else:
                continue
        elif org:
            if org in organisations:
                continue
            key = "custom_role_of_an_organisation_the_user_is_not_in"
        else:
            if not organisations:
                continue
            key = "role_naming_no_organisation_held_by_an_organisation_user"
        if (key, id(role)) not in seen:
            seen.add((key, id(role)))
            note(key, home, entries)
    if not user.get("disabled") and user.get("is_active") is False:
        note("account_is_active_false_but_not_disabled", home, [])


def _passes_non_delegable(entries: Iterable[Any]) -> bool:
    """DEF-4: a role entry that is, or passes a check for, a non-delegable permission."""
    try:
        from rbac_backend.core.permissions import equivalent_permissions
    except ImportError:  # pragma: no cover - only in an image without the backend
        return False
    prefixes = ("platform.", "billing.", "subscription.", "permissions:", "roles:")
    exact = {"*", "system:admin", "dms.admin", "drafting.admin"}
    for entry in entries:
        for name in set(equivalent_permissions(str(entry)) or set()) | {str(entry)}:
            lowered = name.lower()
            if lowered in exact or lowered.startswith(prefixes):
                return True
    return False


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


#: KD-1: the two week bases services/key_date_service.calculate_key_date knows.
_KD_LOA_PLUS = "loa_plus_weeks"
_KD_LOA_PLUS_MINUS_1 = "loa_plus_weeks_minus_1"


def _kd_date(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _kd_calc(start: datetime, week: Any, basis: str) -> Optional[datetime]:
    """calculate_key_date, restated here so the script runs on an image without it."""
    try:
        weeks = int(week)
    except (TypeError, ValueError):
        return None
    if basis == _KD_LOA_PLUS_MINUS_1:
        weeks -= 1
    return start + timedelta(days=weeks * 7)


def read_key_date_contract_basis(db: ReadOnlyDatabase) -> Dict[str, Any]:
    """KD-1 (High): key dates of a contract other than the main one, calculated from the
    main contract's LOA date and week basis before the fix.

    Counts only. For each milestone of a contract other than ``"primary"``, its stored
    ``calculated_key_date`` is compared with the date from its own contract's Contract
    Master entry and with the date from the main contract's. A milestone that matches
    the main contract's and not its own was calculated wrongly; one with no approved
    revision (``current_revision`` 0) has its baseline from that calculation too, and
    is the one a recalculation would change. Nothing is changed by reading this.
    """
    names = db.list_collection_names()
    if "key_date_milestones" not in names:
        return _reading("P-16", key_date_milestones_collection_present=False)
    masters: Dict[tuple, Dict[str, Any]] = {}
    contracts_per_project: Dict[str, set] = {}
    if "contract_master" in names:
        for row in db["contract_master"].find({}, {"project_id": 1, "contract_id": 1, "contract_start_date": 1, "week_basis": 1}):
            project, contract = str(row.get("project_id") or ""), str(row.get("contract_id") or "")
            masters[(project, contract)] = row
            contracts_per_project.setdefault(project, set()).add(contract)
    milestones = db["key_date_milestones"]
    counts = {
        "secondary_milestones": 0,
        "own_contract_has_no_loa": 0,
        "matches_own_contract": 0,
        "matches_main_contract_not_own": 0,
        "matches_neither": 0,
        "no_stored_calculation": 0,
    }
    wrong_unrevised = 0
    wrong_projects: set = set()
    projection = {"project_id": 1, "contract_id": 1, "contractual_week_number": 1,
                  "calculated_key_date": 1, "current_revision": 1}
    for row in milestones.find({"contract_id": {"$nin": [None, "", "primary"]}}, projection):
        counts["secondary_milestones"] += 1
        project, contract = str(row.get("project_id") or ""), str(row.get("contract_id"))
        stored = _kd_date(row.get("calculated_key_date"))
        own = masters.get((project, contract)) or {}
        main = masters.get((project, "primary")) or {}
        own_start = _kd_date(own.get("contract_start_date"))
        if stored is None:
            counts["no_stored_calculation"] += 1
            continue
        if own_start is None:
            counts["own_contract_has_no_loa"] += 1
            continue
        week = row.get("contractual_week_number")
        own_date = _kd_calc(own_start, week, str(own.get("week_basis") or _KD_LOA_PLUS))
        main_start = _kd_date(main.get("contract_start_date"))
        main_date = _kd_calc(main_start, week, str(main.get("week_basis") or _KD_LOA_PLUS)) if main_start else None
        if own_date is not None and stored.date() == own_date.date():
            counts["matches_own_contract"] += 1
        elif main_date is not None and stored.date() == main_date.date():
            counts["matches_main_contract_not_own"] += 1
            wrong_projects.add(project)
            if int(row.get("current_revision") or 0) == 0:
                wrong_unrevised += 1
        else:
            counts["matches_neither"] += 1
    return _reading(
        "P-16",
        key_date_milestones_collection_present=True,
        milestones_total=milestones.count_documents({}),
        milestones_by_contract_kind={
            "primary_or_unset": milestones.count_documents({"contract_id": {"$in": [None, "", "primary"]}}),
            "other": counts["secondary_milestones"],
        },
        projects_with_several_contracts=sum(1 for keys in contracts_per_project.values() if len(keys) > 1),
        secondary_milestones=counts,
        calculated_from_main_contract_with_no_approved_revision=wrong_unrevised,
        projects_affected=len(wrong_projects),
        note="KD-1: a match with the main contract's date and not the milestone's own means it was calculated from the main contract",
    )


#: 4B-49 (AQ-12): the permission audited by P-15, and the grants that stand in for it.
REVIEW_EXTRACTION = "dms.document.review_extraction"
DMS_ADMIN = "dms.admin"
WILDCARD = "*"
SUPER_ADMIN_KEY = "superadmin"

#: How a user can come to hold REVIEW_EXTRACTION. The two named paths survive
#: explicit-grant-only (PolicyService.has_explicit_grant): the permission's name on one
#: of the user's roles, or its catalogue id there (the role API accepts ids). The
#: rest are inheritance.
NAMED = "named"
NAMED_BY_PERMISSION_DOCUMENT = "named_by_permission_document"
NAMED_PATHS = (NAMED, NAMED_BY_PERMISSION_DOCUMENT)
DMS_ADMIN_WIDENING = "dms_admin"
WILDCARD_GRANT = "wildcard"
SUPER_ADMIN_SHORTCUT = "superadmin"
INHERITED_PATHS = (DMS_ADMIN_WIDENING, WILDCARD_GRANT, SUPER_ADMIN_SHORTCUT)


def _is_object_id_text(value: str) -> bool:
    return len(value) == 24 and all(ch in "0123456789abcdefABCDEF" for ch in value)


def permission_document_references(permission_documents: Iterable[Dict[str, Any]], permission: str) -> set:
    """Role-permission entries other than the name that PermissionService reads as `permission`.

    PermissionService.get_user_permissions looks a role's entry up in `permissions` by
    ObjectId (an entry that parses as one) or by `name`, and names the document by the
    first of name, id/code or string _id that contains "." or ":". So the document
    `{"_id": "dms.document.review_extraction", "name": "Review DMS Document Extraction"}`
    makes the label a grant, and a document's ObjectId is one too.
    """
    references = set()
    for document in permission_documents:
        name = document.get("name")
        legacy = document.get("id") if isinstance(document.get("id"), str) else None
        code = document.get("code") if isinstance(document.get("code"), str) else None
        raw_id = document.get("_id")
        canonical = None
        for candidate in (name, legacy or code, raw_id if isinstance(raw_id, str) else None):
            if isinstance(candidate, str) and ("." in candidate or ":" in candidate):
                canonical = candidate.strip()
                break
        if canonical != permission:
            continue
        if isinstance(name, str) and name.strip() and not _is_object_id_text(name):
            references.add(name)
        if raw_id is not None and not isinstance(raw_id, str) and _is_object_id_text(str(raw_id)):
            references.add(str(raw_id))
    references.discard(permission)
    return references


def review_extraction_grant_paths(
    references: Iterable[Any],
    role_documents: List[Dict[str, Any]],
    by_document: set,
    resolve: Callable[[Any, List[Dict[str, Any]]], Any],
) -> frozenset:
    """Every path by which PolicyService.has_permission grants REVIEW_EXTRACTION to these role references.

    Mirrors PolicyService.has_permission for a `dms.*` permission outside
    SUPERADMIN_EXCLUDED_PERMISSIONS, as of 4B-49: the Super Admin key on the
    principal; the name, `dms.admin` or `*` on a role document the reference
    resolves to (core.role_reference, one hop); or a permission document that
    names it (`permission_document_references`). Users' own `permissions` field is
    not read by the check and is not a path. Pinned against the real PolicyService
    by tests/integration/test_review_extraction_inheritance_mongo.py.
    """
    paths = set()
    for reference in references or ():
        resolution = resolve(reference, role_documents)
        if not resolution.keeps_role_key:
            continue
        if resolution.key == SUPER_ADMIN_KEY:
            paths.add(SUPER_ADMIN_SHORTCUT)
        document = resolution.document
        if not document:
            continue
        held = {str(entry).strip() for entry in (document.get("permissions") or []) if entry is not None}
        if REVIEW_EXTRACTION in held:
            paths.add(NAMED)
        if DMS_ADMIN in held:
            paths.add(DMS_ADMIN_WIDENING)
        if WILDCARD in held:
            paths.add(WILDCARD_GRANT)
        if held & by_document:
            paths.add(NAMED_BY_PERMISSION_DOCUMENT)
    return frozenset(paths)


def read_review_extraction_holders(db: ReadOnlyDatabase) -> Dict[str, Any]:
    """AQ-12 / 4B-49: who holds dms.document.review_extraction only by inheritance.

    Counts by role key and by path, never a name or id. A user is counted under each
    role key they carry. "Only by inheritance" means the check passes and no role
    the user holds names the permission, so explicit-grant-only would withdraw it.
    """
    try:
        from rbac_backend.core.role_reference import resolve_role_reference
    except ImportError:
        return _not_read("P-15", "this image has no core.role_reference; the roles cannot be resolved as the server does")
    names = set(db.list_collection_names())
    role_documents = list(db["roles"].find({}, {"permissions": 1, "name": 1, "is_active": 1, "scope": 1}))
    permission_documents = (
        list(db["permissions"].find({}, {"name": 1, "id": 1, "code": 1})) if "permissions" in names else []
    )
    by_document = permission_document_references(permission_documents, REVIEW_EXTRACTION)

    roles_by_path: Dict[str, int] = {}
    for document in role_documents:
        if document.get("is_active") is False:
            continue
        held = {str(entry).strip() for entry in (document.get("permissions") or []) if entry is not None}
        for path, present in (
            (NAMED, REVIEW_EXTRACTION in held),
            (DMS_ADMIN_WIDENING, DMS_ADMIN in held),
            (WILDCARD_GRANT, WILDCARD in held),
            (NAMED_BY_PERMISSION_DOCUMENT, bool(held & by_document)),
        ):
            if present:
                roles_by_path[path] = roles_by_path.get(path, 0) + 1

    holders = {"by_name": 0, "by_name_through_a_catalogue_id_only": 0, "only_by_inheritance": 0, "not_holding": 0}
    inherited_by_path: Dict[str, int] = {}
    inherited_by_role: Dict[str, int] = {}
    inherited_by_account_type: Dict[str, int] = {}
    inherited_disabled = 0
    for user in db["users"].find({}, {"roles": 1, "account_type": 1, "disabled": 1, "is_active": 1}):
        references = list(user.get("roles") or [])
        paths = review_extraction_grant_paths(references, role_documents, by_document, resolve_role_reference)
        if paths & set(NAMED_PATHS):
            holders["by_name"] += 1
            if NAMED not in paths:
                holders["by_name_through_a_catalogue_id_only"] += 1
            continue
        if not paths:
            holders["not_holding"] += 1
            continue
        holders["only_by_inheritance"] += 1
        if user.get("disabled") or user.get("is_active") is False:
            inherited_disabled += 1
        for path in paths:
            inherited_by_path[path] = inherited_by_path.get(path, 0) + 1
        account_type = str(user.get("account_type") or "client_user")
        inherited_by_account_type[account_type] = inherited_by_account_type.get(account_type, 0) + 1
        for key in {resolve_role_reference(ref, role_documents).key for ref in references}:
            if key:
                inherited_by_role[key] = inherited_by_role.get(key, 0) + 1
    known = set(_SEED_ROLE_KEYS)
    custom_roles = sum(count for key, count in inherited_by_role.items() if key not in known)
    return _reading(
        "P-15",
        permission=REVIEW_EXTRACTION,
        role_documents_by_path=roles_by_path,
        permission_document_spellings=len(by_document),
        users=holders,
        only_by_inheritance_by_path=inherited_by_path,
        only_by_inheritance_by_seed_role={k: v for k, v in sorted(inherited_by_role.items()) if k in known},
        only_by_inheritance_holding_custom_roles=custom_roles,
        only_by_inheritance_by_account_type=inherited_by_account_type,
        only_by_inheritance_disabled=inherited_disabled,
        note="a user counts under every role key they carry; custom role keys are counted, not named",
    )


#: Seed role keys (initial_data/default_roles.py) P-15 may name; any other key is a
#: custom role, whose name is the organisation's and is not printed.
_SEED_ROLE_KEYS = (
    "superadmin", "superuser", "orgadmin", "orguser", "contractmgr_org", "projectadmin", "projectuser",
    "doccontroller", "reporter", "settings_manager", "limited_user", "contraclaim_drafting_manager",
    "contraclaim_expert_drafter", "contraclaim_expert_reviewer", "contraclaim_billing_admin",
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
        ("P-15", lambda: read_review_extraction_holders(guarded)),
        ("P-16", lambda: read_key_date_contract_basis(guarded)),
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
