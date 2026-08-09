"""The acceptance matrix: which records each role actually sees, per navbar state.

Asserting a predicate proves a function returned a dict. This asserts the
*records that survive it*, which is the thing the specification is actually
about:

    Selecting Organisation A / Project A1 produces the same Organisation-A /
    Project-A1 effective scope in every applicable data-bearing module.

A seeded dataset spans two organisations and three projects. Each case resolves
a scope, applies the resulting filter to that dataset, and asserts the exact set
of surviving ids. A leak shows up as an unexpected id, not as a shape mismatch.

The evaluator below supports only the operators these builders emit. It is
deliberately small: if it silently ignored an operator it would under-filter and
turn every test green, so unknown operators raise instead.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from rbac_backend.core.effective_scope import EffectiveScope
from rbac_backend.core.security import build_scope_query
from rbac_backend.services.authorization_service import AuthorizationService

# --- fixture dataset -------------------------------------------------------

DOCUMENTS = [
    {"_id": "d-A1-a", "organization_id": "org-A", "project_id": "proj-A1"},
    {"_id": "d-A1-b", "organization_id": "org-A", "project_id": "proj-A1"},
    {"_id": "d-A2-a", "organization_id": "org-A", "project_id": "proj-A2"},
    {"_id": "d-B1-a", "organization_id": "org-B", "project_id": "proj-B1"},
    {"_id": "d-B1-b", "organization_id": "org-B", "project_id": "proj-B1"},
]

ALL_IDS = {doc["_id"] for doc in DOCUMENTS}
ORG_A_IDS = {"d-A1-a", "d-A1-b", "d-A2-a"}
ORG_B_IDS = {"d-B1-a", "d-B1-b"}
PROJ_A1_IDS = {"d-A1-a", "d-A1-b"}
PROJ_A2_IDS = {"d-A2-a"}


# --- a deliberately strict mini Mongo evaluator ----------------------------


def _match_condition(actual, condition) -> bool:
    if isinstance(condition, dict):
        for operator, operand in condition.items():
            if operator == "$in":
                if actual not in [str(v) for v in operand]:
                    return False
            elif operator == "$nin":
                if actual in [str(v) for v in operand]:
                    return False
            elif operator == "$ne":
                if actual == operand:
                    return False
            elif operator == "$exists":
                if (actual is not None) != bool(operand):
                    return False
            elif operator == "$regex":
                continue  # non-tenant text predicate; irrelevant to visibility
            elif operator == "$options":
                continue
            else:
                raise AssertionError(
                    f"evaluator does not implement {operator!r}; implement it rather "
                    f"than let the matrix under-filter and pass by accident"
                )
        return True
    return actual == condition


def matches(document, query) -> bool:
    for field, condition in query.items():
        if field == "$and":
            if not all(matches(document, sub) for sub in condition):
                return False
        elif field == "$or":
            if not any(matches(document, sub) for sub in condition):
                return False
        elif field == "$nor":
            if any(matches(document, sub) for sub in condition):
                return False
        else:
            if not _match_condition(document.get(field), condition):
                return False
    return True


def visible(query, dataset=None):
    """The ids a query actually returns from the dataset."""
    return {doc["_id"] for doc in (dataset or DOCUMENTS) if matches(doc, query)}


def test_the_evaluator_itself_is_trustworthy():
    """Guard the guard: a broken evaluator would make every case below vacuous."""
    assert visible({}) == ALL_IDS
    assert visible({"organization_id": "org-A"}) == ORG_A_IDS
    assert visible({"organization_id": {"$in": ["org-B"]}}) == ORG_B_IDS
    assert visible({"organization_id": "org-A", "project_id": {"$in": ["proj-A1"]}}) == PROJ_A1_IDS
    assert visible({"_id": {"$in": []}}) == set()
    assert visible({"$or": [{"organization_id": "org-A"}, {"organization_id": "org-B"}]}) == ALL_IDS
    with pytest.raises(AssertionError):
        visible({"organization_id": {"$unsupported": 1}})


# --- actors ----------------------------------------------------------------


def actor(roles, org=None, project=None, orgs=(), projects=()):
    return SimpleNamespace(
        id="u1", roles=list(roles), organization_id=org, project_id=project,
        organizations=list(orgs), projects=list(projects),
        account_type="client_user", disabled=False,
    )


def document_query(user, filters=None):
    return asyncio.run(AuthorizationService().build_document_query(user, filters or {}))


# role, navbar state -> the ids that role must see
MATRIX = [
    # --- global tier: platform-wide entitlement, narrowed by the navbar ----
    ("superadmin, nothing selected", actor(["superadmin"]), ALL_IDS),
    ("superadmin, Org-A", actor(["superadmin"], org="org-A"), ORG_A_IDS),
    ("superadmin, Org-A + Proj-A1", actor(["superadmin"], org="org-A", project="proj-A1"), PROJ_A1_IDS),
    ("superadmin, Org-A + Proj-A2", actor(["superadmin"], org="org-A", project="proj-A2"), PROJ_A2_IDS),
    ("superadmin, Org-B", actor(["superadmin"], org="org-B"), ORG_B_IDS),
    # --- super user: bounded by assignment, narrowed by the navbar --------
    ("superuser {A,B}, nothing selected", actor(["superuser"], orgs=["org-A", "org-B"]), ALL_IDS),
    ("superuser {A,B}, Org-A", actor(["superuser"], org="org-A", orgs=["org-A", "org-B"]), ORG_A_IDS),
    ("superuser {A,B}, Org-B", actor(["superuser"], org="org-B", orgs=["org-A", "org-B"]), ORG_B_IDS),
    ("superuser {A} only", actor(["superuser"], orgs=["org-A"]), ORG_A_IDS),
    # --- organisation tier: pinned to its own organisation ----------------
    ("orgadmin A, All Projects", actor(["orgadmin"], org="org-A", orgs=["org-A"]), ORG_A_IDS),
    ("orgadmin A, Proj-A1", actor(["orgadmin"], org="org-A", project="proj-A1", orgs=["org-A"]), PROJ_A1_IDS),
    ("orguser A, All Projects", actor(["orguser"], org="org-A", orgs=["org-A"]), ORG_A_IDS),
    ("orgadmin B", actor(["orgadmin"], org="org-B", orgs=["org-B"]), ORG_B_IDS),
    # --- project tier: bounded by assignment ------------------------------
    ("projectuser A/p1", actor(["projectuser"], org="org-A", projects=["proj-A1"]), PROJ_A1_IDS),
    ("projectuser A/p1+p2, All", actor(["projectuser"], org="org-A", projects=["proj-A1", "proj-A2"]), ORG_A_IDS),
    ("projectuser A/p1+p2, Proj-A1", actor(["projectuser"], org="org-A", project="proj-A1", projects=["proj-A1", "proj-A2"]), PROJ_A1_IDS),
    ("projectadmin A/p2", actor(["projectadmin"], org="org-A", projects=["proj-A2"]), PROJ_A2_IDS),
    # --- no reach: deny by default ----------------------------------------
    ("orgadmin with no organisation", actor(["orgadmin"]), set()),
    ("projectuser with no assignment", actor(["projectuser"], org="org-A"), set()),
    ("unknown role", actor(["reporter"]), set()),
]


@pytest.mark.parametrize("label,user,expected", MATRIX, ids=[row[0] for row in MATRIX])
def test_central_scope_returns_exactly_the_permitted_records(label, user, expected):
    assert visible(build_scope_query(user)) == expected, label


@pytest.mark.parametrize("label,user,expected", MATRIX, ids=[row[0] for row in MATRIX])
def test_document_library_returns_exactly_the_permitted_records(label, user, expected):
    """The Document Library must agree with the central scope, record for record.

    Every row here returned a wider set before the EffectiveScope migration for
    any case involving superadmin or an active selection.
    """
    try:
        query = document_query(user)
    except Exception:
        # A caller with no reach may refuse rather than return an empty page;
        # both are "sees nothing", which is what the matrix asserts.
        assert expected == set(), label
        return
    assert visible(query) == expected, label


# --- consistency: every module agrees for the same context -----------------


CONTEXTS = [row for row in MATRIX if row[2]]  # skip the deny rows


@pytest.mark.parametrize("label,user,expected", CONTEXTS, ids=[row[0] for row in CONTEXTS])
def test_modules_do_not_disagree_for_the_same_context(label, user, expected):
    """Navigating between modules must not silently change what is in scope.

    Predicate *shape* may differ per collection; the set of records must not.
    """
    central = visible(build_scope_query(user))
    library = visible(document_query(user))
    assert central == library == expected, (
        f"{label}: central={central} library={library} expected={expected}"
    )


# --- counts must match their lists -----------------------------------------


@pytest.mark.parametrize("label,user,expected", MATRIX, ids=[row[0] for row in MATRIX])
def test_dashboard_counts_reconcile_with_the_visible_list(label, user, expected):
    """An aggregate may never count records its list would not show.

    A count computed from a broader dataset than the underlying list is the
    quiet form of this bug: nothing leaks on screen, but the number tells the
    user how much exists outside their scope.
    """
    scope = EffectiveScope.resolve(user)
    counted = visible(scope.mongo_filter()) if not scope.is_denied else set()
    listed = visible(build_scope_query(user))
    assert counted == listed, f"{label}: aggregate {counted} != list {listed}"
    assert len(counted) == len(expected), label


# --- negative set: nothing a client controls may widen the result ----------


def test_a_query_parameter_cannot_widen_beyond_entitlement():
    """An org-tier caller naming another organisation is refused, not served."""
    user = actor(["orgadmin"], org="org-A", orgs=["org-A"])
    assert visible(build_scope_query(user, organization_id="org-B")) == set()


def test_an_explicit_organisation_cannot_switch_the_working_context():
    """Entitled to {A,B}, working in A, filtering for B: nothing.

        Final Data Scope = EffectiveScope ∩ Explicit Resource Filter

    Organisation B is inside the caller's entitlement, so this is not privilege
    escalation -- it is the working context refusing to be replaced. Serving B
    would let one request disagree with every other module on screen while the
    navbar still said A.
    """
    user = actor(["superuser"], org="org-A", orgs=["org-A", "org-B"])
    assert visible(build_scope_query(user, organization_id="org-B")) == set()


def test_an_explicit_organisation_may_narrow_a_wider_context():
    """The filter is still a filter: it narrows when it lies inside the scope."""
    user = actor(["superuser"], orgs=["org-A", "org-B"])  # nothing selected
    assert visible(build_scope_query(user)) == ALL_IDS
    assert visible(build_scope_query(user, organization_id="org-B")) == ORG_B_IDS


def test_an_explicit_project_cannot_escape_the_selected_project():
    """Working in Project A1, filtering for A2: nothing, even when assigned both."""
    user = actor(
        ["projectuser"], org="org-A", project="proj-A1", projects=["proj-A1", "proj-A2"]
    )
    assert visible(build_scope_query(user, project_id="proj-A2")) == set()
    # The same filter naming the project actually being worked in is fine.
    assert visible(build_scope_query(user, project_id="proj-A1")) == PROJ_A1_IDS


def test_an_explicit_project_may_narrow_within_all_projects():
    user = actor(["projectuser"], org="org-A", projects=["proj-A1", "proj-A2"])
    assert visible(build_scope_query(user)) == ORG_A_IDS
    assert visible(build_scope_query(user, project_id="proj-A2")) == PROJ_A2_IDS


def test_an_explicit_filter_still_cannot_exceed_entitlement():
    """The outer boundary is unchanged: unreachable ids stay unreachable."""
    for user in (
        actor(["superuser"], orgs=["org-A", "org-B"]),
        actor(["superadmin"]),
        actor(["orgadmin"], org="org-A", orgs=["org-A"]),
    ):
        assert visible(build_scope_query(user, organization_id="org-ZZZ")) == set()


@pytest.mark.parametrize(
    "carrier,filters",
    [
        ("query parameter / route parameter", {"organization_id": "org-B"}),
        ("request body / export filter", {"project_id": "proj-B1"}),
        ("both at once", {"organization_id": "org-B", "project_id": "proj-B1"}),
    ],
)
def test_the_document_path_refuses_a_filter_outside_the_working_context(carrier, filters):
    """Whatever carries the filter, it lands in the same place.

    Query strings, route parameters, request bodies and export filters all
    arrive as ``filters`` on the builder, so one refusal covers every carrier --
    which is the point of resolving scope in one place.
    """
    user = actor(["superadmin"], org="org-A")
    try:
        query = document_query(user, filters)
    except Exception:
        return  # refused outright, which is the stronger answer
    assert visible(query) == set(), carrier


def test_a_manipulated_project_cannot_reach_another_organisation():
    user = actor(["orgadmin"], org="org-A", orgs=["org-A"])
    assert visible(build_scope_query(user, project_id="proj-B1")) == set()


def test_a_project_user_cannot_reach_a_sibling_project():
    user = actor(["projectuser"], org="org-A", projects=["proj-A1"])
    assert visible(build_scope_query(user, project_id="proj-A2")) == set()


def test_an_unknown_organisation_denies_rather_than_falling_back():
    """The dangerous failure is a filter that silently drops to unscoped."""
    for user in (
        actor(["superuser"], orgs=["org-A", "org-B"]),
        actor(["orgadmin"], org="org-A", orgs=["org-A"]),
    ):
        assert visible(build_scope_query(user, organization_id="org-ZZZ")) == set()


def test_no_role_can_reach_records_outside_its_entitlement():
    """The blanket invariant: whatever the navbar says, entitlement still caps."""
    for label, user, _expected in MATRIX:
        scope = EffectiveScope.resolve(user)
        for state in (None, "org-A", "org-B", "org-ZZZ"):
            seen = visible(build_scope_query(user, organization_id=state))
            for doc_id in seen:
                document = next(d for d in DOCUMENTS if d["_id"] == doc_id)
                assert scope.permits_organization(document["organization_id"]), (
                    f"{label}: returned {doc_id} from an organisation outside entitlement"
                )
