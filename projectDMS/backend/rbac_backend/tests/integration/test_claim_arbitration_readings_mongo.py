"""4B-01: the Phase 4b readings against a real MongoDB.

Seeds a database shaped like production at 34d664a plus the cases the plan
asks about (string amounts, float days, an off-enum status, a hand-made
reviewer role, users with several roles, case-less drafts), runs the reader,
and checks three things:

- the readings are right;
- the database is unchanged, document for document and index for index;
- the output carries no name, email, title or record id from the data.

Opt-in: set ``READONLY_READINGS_MONGODB_URI`` to a disposable replica set. CI
sets it in the real-Mongo step, where it must run, not skip.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Dict

import pytest
from bson import json_util
from pymongo import MongoClient

pytestmark = pytest.mark.integration

MONGODB_URI_ENV = "READONLY_READINGS_MONGODB_URI"
READER = Path(__file__).resolve().parents[4] / "scripts" / "claim_arbitration_readonly_readings.py"
NOW = datetime(2026, 11, 16, 12, 0)


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


def _reader() -> ModuleType:
    spec = importlib.util.spec_from_file_location("claim_arbitration_readonly_readings", READER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: Strings that must never appear in the output.
PRIVATE = (
    "asha.rao@example.com", "Asha Rao", "vikram@example.com", "Vikram Shah",
    "Delayed handover of Site", "CLM-PRIVATE-7", "claim-private-1", "draft-private-1",
    "Particular Conditions clause text", "Private Manager", "u3@example.com",
)


def _seed(db: Any) -> None:
    recent, old = NOW - timedelta(days=10), NOW - timedelta(days=400)
    db.schema_migrations.insert_many(
        [{"version": v, "name": f"m{v}", "applied_at": old} for v in ("20260906_0001", "20261114_0001")]
    )
    db.schema_migrations.create_index("version", unique=True)
    db.claims.insert_many(
        [
            {"_id": "claim-private-1", "claim_ref": "CLM-PRIVATE-7", "title": "Delayed handover of Site",
             "type": "eot", "status": "submitted", "amount_claimed": 184500000.0, "eot_days_claimed": 146,
             "created_at": recent, "updated_at": recent},
            {"_id": "c2", "type": "variation", "status": "draft", "amount_claimed": "1,20,000",
             "eot_days_claimed": 30.0, "created_at": old, "updated_at": old},
            {"_id": "c3", "type": "eot", "status": "pending_review", "amount_claimed": None,
             "created_at": old, "updated_at": recent},
            # Claim-type confirmation: two legacy "other" claims (one in an organisation with
            # a verified "other" period), one provisional, one confirmed.
            {"_id": "c-other-1", "type": "other", "status": "draft", "organization_id": "org-a", "created_at": old, "updated_at": old},
            {"_id": "c-other-2", "type": "other", "status": "submitted", "organization_id": "org-b",
             "evidence_frozen_at": old, "created_at": old, "updated_at": old},
            {"_id": "c-other-3", "type": "other", "type_confirmed": False, "status": "draft", "organization_id": "org-a",
             "created_at": old, "updated_at": old},
            {"_id": "c-other-4", "type": "other", "type_confirmed": True, "status": "draft", "organization_id": "org-a",
             "created_at": old, "updated_at": old},
        ]
    )
    db.sla_rules.insert_one({"organization_id": "org-a", "notice_windows": {"eot": "28", "variation": 14}})
    db.roles.insert_many(
        [
            {"_id": "orgadmin", "name": "orgadmin", "permissions": ["dms.admin", "dms.document.review_extraction"]},
            {"_id": "contractmgr_org", "name": "contractmgr_org", "permissions": ["dms.admin"]},
            {"_id": "senior_legal_approver", "name": "senior_legal_approver"},
            {"_id": "65f000000000000000000001", "name": "Chief_Contract_Expert"},
            # SEC: custom roles of org-a, of a project, and naming no organisation.
            {"_id": "custom-a", "name": "Site viewer", "organization_id": "org-a", "permissions": ["dms.claim.view"]},
            {"_id": "custom-p", "name": "P1 manager", "organization_id": "org-a", "project_id": "p1", "permissions": []},
            {"_id": "custom-none", "name": "Orphan", "permissions": ["dms.claim.view"]},
            {"_id": "65f000000000000000000002", "name": "Organization User", "permissions": []},
        ]
    )
    db.users.insert_many(
        [
            {"_id": "u1", "email": "asha.rao@example.com", "full_name": "Asha Rao",
             "roles": ["orgadmin", "senior_legal_approver"]},
            {"_id": "u2", "email": "vikram@example.com", "full_name": "Vikram Shah", "roles": ["orgadmin"]},
            {"_id": "u3", "email": "u3@example.com", "full_name": "Private Manager", "roles": ["contractmgr_org"]},
            {"_id": "u4", "email": "u4@example.com", "roles": ["custom-a"], "organization_id": "org-b", "disabled": True},
            {"_id": "u5", "email": "u5@example.com", "roles": ["custom-p", "custom-none"], "organization_id": "org-a",
             "projects": [], "is_active": False},
        ]
    )
    db.projects.insert_many([{"_id": "p1", "name": "NH-48"}, {"_id": "p2", "name": "Metro", "contractual_capacity": "engineer"}])
    db.arbitration_cases.insert_many(
        [
            # C-25 shapes: deleted_at absent (visible to the pre-fix query), stored null (not
            # deleted, but invisible to it), and set (soft-deleted).
            {"_id": "case-1", "status": "intake", "title": "Private case", "organization_id": "org-a"},
            {"_id": "case-null-1", "status": "intake", "deleted_at": None, "organization_id": "org-a"},
            {"_id": "case-null-2", "status": "drafting", "deleted_at": None, "organization_id": "org-b"},
            {"_id": "case-gone", "status": "intake", "deleted_at": old, "organization_id": "org-a"},
        ]
    )
    db.arbitration_clause_matrix.insert_many(
        [{"_id": "cl1", "case_id": "case-null-1"}, {"_id": "cl2", "case_id": "case-null-2"}, {"_id": "cl3", "case_id": "case-1"}]
    )
    db.arbitration_drafts.insert_many(
        [
            {"_id": "draft-private-1", "case_id": None, "status": "draft", "created_by": "u1", "created_at": recent},
            {"_id": "d2", "case_id": "", "status": "draft", "created_by": "u1", "created_at": recent},
            {"_id": "d3", "case_id": "case-1", "status": "approved", "created_by": "u2", "created_at": recent},
            {"_id": "d4", "case_id": None, "status": "draft", "created_by": "u2", "created_at": old},
        ]
    )
    db.arbitration_claim_matrix.insert_many(
        [{"_id": "r1", "case_id": "case-1", "origin_key": "claim:c1:h1"}, {"_id": "r2", "case_id": "case-1"}]
    )
    db.audit_events.insert_many(
        [
            {"action": "claim.updated", "created_at": recent, "actor_id": "u1"},
            {"action": "claim.updated", "created_at": old, "actor_id": "u1"},
            {"action": "letter.sent", "created_at": recent},
        ]
    )
    db.documents.insert_one({"_id": "doc-1", "text": "Particular Conditions clause text"})
    db.claim_notice_requirements.insert_many(
        [
            {"_id": "nr-1", "organization_id": "org-a", "status": "current", "claim_type": "eot", "origin": "clause_index",
             "verification": "verified", "created_by": "u1", "verified_by": "u1"},
            {"_id": "nr-2", "organization_id": "org-a", "status": "current", "claim_type": "Delay claim",
             "origin": "entered", "verification": "extracted_unverified", "created_by": "u1"},
            {"_id": "nr-3", "organization_id": "org-b", "status": "current", "claim_type": "variation",
             "origin": "clause_index", "verification": "verified", "created_by": "u1", "verified_by": "u2"},
            {"_id": "nr-4", "organization_id": "org-a", "status": "superseded", "claim_type": "eot",
             "origin": "entered", "verification": "verified", "created_by": "u2", "verified_by": "u2"},
            {"_id": "nr-5", "organization_id": "org-a", "status": "current", "claim_type": "other",
             "origin": "clause_index", "verification": "verified", "created_by": "u1", "verified_by": "u2"},
        ]
    )


def _seed_key_dates(db: Any) -> None:
    """KD-1 (P-16): project p1 has a main contract and a depot contract with its own LOA and basis."""
    loa, depot_loa = datetime(2026, 1, 5), datetime(2026, 4, 15)
    db.contract_master.insert_many([
        {"_id": "cm-p1-main", "project_id": "p1", "contract_id": "primary", "contract_start_date": loa,
         "week_basis": "loa_plus_weeks"},
        {"_id": "cm-p1-depot", "project_id": "p1", "contract_id": "pkg-depot", "contract_start_date": depot_loa,
         "week_basis": "loa_plus_weeks_minus_1"},
    ])

    def milestone(_id: str, contract: str, calculated: datetime, revision: int = 0) -> Dict[str, Any]:
        return {"_id": _id, "project_id": "p1", "contract_id": contract, "contractual_week_number": 4,
                "calculated_key_date": calculated, "current_revision": revision}

    db.key_date_milestones.insert_many([
        milestone("kd-main", "primary", loa + timedelta(days=28)),
        milestone("kd-depot-right", "pkg-depot", depot_loa + timedelta(days=21)),
        milestone("kd-depot-wrong", "pkg-depot", loa + timedelta(days=28)),           # from the main contract
        milestone("kd-depot-wrong-revised", "pkg-depot", loa + timedelta(days=28), 1),
        milestone("kd-civil", "pkg-civil", loa + timedelta(days=28)),                  # no LOA of its own
    ])


def _fingerprint(db: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name in sorted(db.list_collection_names()):
        docs = list(db[name].find({}).sort("_id", 1))
        indexes = sorted(json.dumps(ix, sort_keys=True, default=str) for ix in db[name].list_indexes())
        payload = json_util.dumps(docs, sort_keys=True) + "|" + "|".join(indexes)
        out[name] = hashlib.sha256(payload.encode()).hexdigest()
    return out


def test_the_readings_are_right_change_nothing_and_name_no_one() -> None:
    reader = _reader()
    client = MongoClient(_uri(), serverSelectionTimeoutMS=5_000)
    if not client.admin.command("hello").get("isWritablePrimary"):
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    name = f"readonly_readings_{uuid.uuid4().hex[:12]}"
    db = client[name]
    try:
        _seed(db)
        _seed_key_dates(db)
        before = _fingerprint(db)
        rows = reader.collect(
            db, environ={"ARBITRATION_REVIEWER_ROLE_MATRIX": "readiness=senior_legal_approver"}, now=NOW,
            database_name=name,
        )
        after = _fingerprint(db)
        assert after == before, "the readings changed the database"

        by_code = {row["reading"]: row for row in rows}
        assert [row["reading"] for row in rows] == ["DB"] + [f"P-{n:02d}" for n in range(2, 17)]
        assert by_code["DB"]["initialised"] is True and by_code["DB"]["database"] == name
        assert all(row["status"] == "read" for row in rows), [r for r in rows if r["status"] != "read"]

        p02 = by_code["P-02"]
        assert p02["phase_migrations_applied"] == ["20261114_0001"]
        assert "20261119_0001" in p02["phase_migrations_absent"]

        p03 = by_code["P-03"]
        assert p03["other_type"] == {
            "other_total": 4,
            "legacy_without_type_confirmed": 2,
            "provisional": 1,
            "confirmed": 1,
            "legacy_by_status": {"draft": 1, "submitted": 1},
            "legacy_with_frozen_evidence": 1,
            "legacy_in_organisations_with_a_verified_other_period": 1,
            "organisations_with_legacy_other_claims": 2,
        }
        assert "c-other-1" not in json.dumps(p03)
        assert p03["total"] == 7 and p03["off_enum_statuses"] == 1
        assert p03["amount_claimed_types"] == {"float_integral": 1, "null": 5, "string_numeric": 1}
        assert p03["eot_days_claimed_types"] == {"float_integral": 1, "int": 1, "null": 5}
        assert p03["updated_in_window"] == 2 and p03["created_in_window"] == 1
        assert p03["with_bench"] == 0 and p03["with_contract_id"] == 0

        assert by_code["P-04"]["notice_window_value_types"] == {"int": 1, "string_numeric": 1}

        roles = by_code["P-05"]["reviewer_roles"]
        assert roles["senior_legal_approver"] == {
            "role_documents_with_this_id": 1, "role_documents_with_this_name": 1, "users_holding_this_key": 1,
        }
        # A hand-made custom role with a reviewer name is visible, and holds no one by key.
        assert roles["chief_contract_expert"]["role_documents_with_this_name"] == 1
        assert roles["chief_contract_expert"]["role_documents_with_this_id"] == 0

        assert by_code["P-05"]["custom_role_compatibility"] == {
            "custom_roles_total": 3,
            "custom_roles_without_organisation_or_project": 1,
            "roles_named_like_a_built_in_role": 2,
            "users_total": 5,
            "users_with_custom_roles_only": 2,
            "users_holding_a_custom_role_of_another_organisation": 1,
            "users_holding_a_project_role_without_the_project": 1,
            "organisation_users_holding_a_role_naming_no_organisation": 1,
            "users_disabled": 1,
            "users_is_active_false_but_not_disabled": 1,
        }
        assert "u4" not in json.dumps(by_code["P-05"]) and "custom-a" not in json.dumps(by_code["P-05"])
        assert by_code["P-06"] == {"reading": "P-06", "status": "read", "users_total": 5, "users_with_more_than_one_role": 2}

        p07 = by_code["P-07"]
        assert p07["drafts_total"] == 4 and p07["drafts_without_case"] == 3
        assert p07["claim_matrix_rows_by_origin"] == {
            "carrying_an_escalation_origin": 1, "without_an_escalation_origin": 1,
        }
        assert by_code["P-08"]["published_versions"] == 0
        assert by_code["P-09"]["projects_with_contractual_capacity"] == {"engineer": 1}
        assert by_code["P-10"]["arbitration_reviewer_role_matrix_set"] is True
        assert by_code["P-11"]["claim_audit_events_by_action"] == {"claim.updated": 1}
        assert by_code["P-12"]["caseless_drafts_created_in_window"] == 2
        assert by_code["P-12"]["distinct_creators"] == 1
        p13 = by_code["P-13"]
        assert p13["cases_total"] == 4
        assert (p13["deleted_at_missing"], p13["deleted_at_null"], p13["deleted_at_set"]) == (1, 2, 1)
        assert p13["visible_to_pre_fix_query"] == 1
        assert p13["potentially_affected_by_pre_fix_query"] == 2
        assert p13["affected_by_status"] == {"drafting": 1, "intake": 1}
        assert p13["affected_organisations"] == 2
        assert p13["records_referencing_affected_cases"]["arbitration_clause_matrix"] == 2
        assert p13["records_referencing_affected_cases"]["arbitration_drafts"] == 0
        assert "case-null-1" not in json.dumps(p13) and "org-b" not in json.dumps(p13)
        p14 = by_code["P-14"]
        assert p14["requirements_total"] == 5
        assert p14["by_status"] == {"current": 4, "superseded": 1}
        assert p14["current_by_origin"] == {"clause_index": 3, "entered": 1}
        assert p14["current_by_verification"] == {"extracted_unverified": 1, "verified": 3}
        assert p14["current_verified_by_their_recorder"] == 1  # nr-1; nr-4 is superseded
        assert p14["current_with_unknown_claim_type"] == 1
        assert p14["organisations_with_current_requirements"] == 2
        assert "nr-1" not in json.dumps(p14) and "org-a" not in json.dumps(p14)

        p15 = by_code["P-15"]
        assert p15["users"] == {
            "by_name": 2, "by_name_through_a_catalogue_id_only": 0, "only_by_inheritance": 1, "not_holding": 2,
        }
        assert p15["only_by_inheritance_by_path"] == {"dms_admin": 1}
        assert p15["only_by_inheritance_by_seed_role"] == {"contractmgr_org": 1}
        assert p15["role_documents_by_path"] == {"named": 1, "dms_admin": 2}
        assert "u3" not in json.dumps(p15)

        p16 = by_code["P-16"]
        assert p16["milestones_total"] == 5
        assert p16["milestones_by_contract_kind"] == {"primary_or_unset": 1, "other": 4}
        assert p16["projects_with_several_contracts"] == 1
        assert p16["secondary_milestones"] == {
            "secondary_milestones": 4, "own_contract_has_no_loa": 1, "matches_own_contract": 1,
            "matches_main_contract_not_own": 2, "matches_neither": 0, "no_stored_calculation": 0,
        }
        assert p16["calculated_from_main_contract_with_no_approved_revision"] == 1
        assert p16["projects_affected"] == 1
        assert "kd-depot" not in json.dumps(p16) and "pkg-depot" not in json.dumps(p16)

        text = json.dumps(rows, default=str)
        for secret in PRIVATE:
            assert secret not in text, secret
        for user_id in ("u1", "u2", "u3"):
            assert f'"{user_id}"' not in text
    finally:
        client.drop_database(name)
        client.close()
