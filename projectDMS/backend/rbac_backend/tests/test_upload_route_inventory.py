"""Gate 5 bullet 3's denominator: every upload-capable route, and what bounds it.

`test_upload_limit_enforcement.py` answers "is anything unbounded". That is a
guard over a numerator. It cannot answer the question the bullet's wording
actually raises - *how many upload routes are there, and is this list of them
complete?* - and a guard whose denominator nobody derives is one new router away
from being green over a hole. R-A8T Part 2's independent review reported exactly
that failure mode twice (F-A8T2-5, F-A8T2-6): the guard was green because it was
looking at the wrong set.

So this module derives the set mechanically, classifies every member, and
**fails when a member cannot be classified**. An unclassifiable route is not a
warning here. It is a red test, because "the enforcement on this route cannot be
read from its source" and "this route has no enforcement" look identical from
outside and only one of them is safe to assume.

The classification follows a route through at most three delegate hops - route
-> controller -> service -> seam, which is what the real chains need - resolving
each call against the functions and methods of `routers/`, `services/` and
`utils/`. It is deliberately shallow beyond that, and it says so by refusing
rather than guessing whenever the shallowness runs out: an ambiguous delegate is
recorded as ambiguous and the route is reported, not assumed safe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rbac_backend.tests.upload_surface import (
    UNCLASSIFIABLE,
    ROUTERS,
    UploadRoute,
    render_inventory,
    upload_routes,
)

#: The routes known to be upload-capable when R-A8U derived the inventory. The
#: assertion below is `>=`, never `==`: a new upload route must not silently
#: join the API, and neither must this constant silently shrink.
KNOWN_UPLOAD_ROUTES = frozenset(
    {
        ("POST", "/bank-guarantees/import/preview"),
        ("POST", "/bank-guarantees/import"),
        ("POST", "/billing/webhooks/{provider}"),
        ("POST", "/contracts/upload-multipart"),
        ("POST", "/contracts/upload-chunk"),
        ("POST", "/deep-planning/analyze-document"),
        ("POST", "/documents"),
        ("POST", "/documents/{id}/enclosures"),
        ("POST", "/documents/bulk-upload"),
        ("POST", "/insurance/upload"),
        ("POST", "/insurance/{insurance_id}/documents/upload"),
        ("POST", "/key-dates/import/preview"),
        ("POST", "/key-dates/import"),
        ("POST", "/key-dates/eot-submissions/{submission_id}/import/preview"),
        ("POST", "/key-dates/eot-submissions/{submission_id}/import"),
        ("POST", "/key-dates/eot-determinations/{determination_id}/import/preview"),
        ("POST", "/key-dates/eot-determinations/{determination_id}/import"),
        ("POST", "/profiles/photo"),
        ("POST", "/upload-file"),
    }
)


@pytest.fixture(scope="module")
def routes() -> list:
    return upload_routes()


def test_the_router_tree_the_inventory_reads_is_real(routes: list) -> None:
    """A guard pointed at a directory that does not exist passes while checking
    nothing - a failure this repository has already had once."""
    assert ROUTERS.is_dir(), f"the router tree does not exist: {ROUTERS}"
    assert len(list(ROUTERS.glob("*.py"))) >= 20, "the router tree is implausibly small"
    assert routes, "the inventory found no upload-capable routes at all"


def test_every_upload_capable_route_is_classifiable(routes: list) -> None:
    """The property this module exists for.

    A row carrying `UNCLASSIFIABLE` in any column means the classifier looked at
    the route and could not say what bounds it. That is a finding, not a gap in
    the tooling: the route either has no seam, no configured limit, or reads
    before it checks.
    """
    unreadable = [route for route in routes if not route.is_classifiable]
    assert not unreadable, (
        "these upload-capable routes cannot be shown to enforce a server-side "
        "size limit from their own source:\n"
        + "\n".join(
            f"  {route.method} {route.path} ({route.module}::{route.function}) "
            f"read={route.read_method} limit={route.limit_source} "
            f"limit_before_read={route.limit_before_read} "
            f"persistence_before_limit={route.persistence_before_limit} "
            f"analysed_through={'->'.join(route.analysed_through)}"
            for route in unreadable
        )
    )


def test_no_route_reads_before_its_limit_decides(routes: list) -> None:
    """Property 2 of the bullet: the body is not fully materialised first."""
    late = [route for route in routes if route.limit_before_read == "no"]
    assert not late, (
        "these routes read the body before the size seam runs, so the limit "
        "bounds what is stored and not what the process holds:\n"
        + "\n".join(f"  {route.method} {route.path} ({route.function})" for route in late)
    )


def test_no_route_persists_before_its_limit_decides(routes: list) -> None:
    """Property 5: an over-limit upload fails before partial persistence.

    A refusal that has already written a document row, an S3 object or a spool
    file is a worse outcome than accepting the upload, because the tenant is
    left holding a record of something that was rejected.
    """
    early = [route for route in routes if route.persistence_before_limit == "yes"]
    assert not early, (
        "these routes persist something before the size limit decides:\n"
        + "\n".join(f"  {route.method} {route.path} ({route.function})" for route in early)
    )


def test_every_route_names_the_size_it_enforces(routes: list) -> None:
    """Property 1: the enforcement is server-side and the size is written down.

    The column records the expression, not a verdict, so a reviewer can see
    which routes read the deployment (`settings.*`) and which carry a module
    constant. `test_the_routers_pass_the_configured_limit_and_not_a_literal` is
    what requires the two large-file surfaces to read configuration; a 5 MB
    profile photo bounded by `MAX_PROFILE_PHOTO_SIZE_BYTES` is a limit, and
    demanding a settings key for it would be inventing a rule.
    """
    unbounded = [route for route in routes if route.limit_source == UNCLASSIFIABLE]
    assert not unbounded, (
        "these routes reach a size seam without naming the size they pass it:\n"
        + "\n".join(f"  {route.method} {route.path} ({route.function})" for route in unbounded)
    )


def test_the_large_file_surfaces_read_their_limit_from_configuration(routes: list) -> None:
    """The routes that accept whole documents must track the deployment.

    A route bounded by a literal keeps every runtime test green while ignoring
    what the deployment was configured to allow.
    """
    required = {
        "/documents/bulk-upload",
        "/deep-planning/analyze-document",
        "/billing/webhooks/{provider}",
        "/upload-file",
    }
    seen = set()
    for route in routes:
        if route.path not in required:
            continue
        seen.add(route.path)
        assert "settings." in route.limit_source, (
            f"{route.path} bounds its upload with {route.limit_source!r} rather "
            "than with a configured limit"
        )
    assert seen == required, f"these routes left the inventory: {sorted(required - seen)}"


def test_the_inventory_holds_every_route_that_was_known_when_it_was_derived(
    routes: list,
) -> None:
    """The denominator cannot shrink silently.

    If a route leaves the API it must leave this constant in the same commit,
    which is a deliberate act. If the classifier stops recognising one, this
    fails rather than quietly certifying a smaller surface.
    """
    found = {(route.method, route.path) for route in routes}
    missing = sorted(KNOWN_UPLOAD_ROUTES - found)
    assert not missing, (
        "the inventory no longer recognises these upload-capable routes; either "
        "they were removed (update KNOWN_UPLOAD_ROUTES in the same commit) or "
        f"the classifier stopped seeing them: {missing}"
    )


def test_a_new_upload_route_cannot_join_the_api_unnoticed(routes: list) -> None:
    """The other direction. A route the inventory finds but nobody declared is
    a route that joined the upload surface without anyone reviewing its limits.
    """
    found = {(route.method, route.path) for route in routes}
    undeclared = sorted(found - KNOWN_UPLOAD_ROUTES)
    assert not undeclared, (
        "these upload-capable routes are not in KNOWN_UPLOAD_ROUTES. Review the "
        "size, count and concurrency limits on each, then add it:\n"
        + "\n".join(f"  {method} {path}" for method, path in undeclared)
    )


def test_the_unauthenticated_webhook_is_counted_as_an_upload_surface(
    routes: list,
) -> None:
    """The route the annotation-shaped inventory could not see.

    `POST /api/billing/webhooks/{provider}` names no `UploadFile`. It takes a
    raw `Request` and did `await request.body()`, holding an entire
    client-supplied payload with no application limit, on an endpoint that is
    **intentionally unauthenticated**. An inventory built from `UploadFile`
    annotations reports 18 routes and a clean sheet; this one reports 19.
    """
    webhook = [route for route in routes if route.path == "/billing/webhooks/{provider}"]
    assert webhook, (
        "the billing webhook is not in the upload inventory, so the inventory is "
        "back to counting annotations instead of bodies"
    )
    assert webhook[0].read_method != UNCLASSIFIABLE, webhook[0]


def test_a_route_that_loses_its_seam_falls_out_of_the_inventory_as_unclassifiable(
    tmp_path: Path,
) -> None:
    """The mutation control for the inventory itself.

    A copy of the real router tree with one route's size seam deleted must
    produce an `UNCLASSIFIABLE` row. Without this, an inventory that classified
    everything as fine - by looking at nothing - would keep every test above
    green.
    """
    import re
    import shutil

    mutated_tree = tmp_path / "routers"
    shutil.copytree(ROUTERS, mutated_tree, ignore=shutil.ignore_patterns("__pycache__"))

    target = mutated_tree / "documents.py"
    source = target.read_text(encoding="utf-8-sig")
    mutated = re.sub(r"await spool_upload_file\((\w+)[^)]*\)", r"await \1.read()", source, count=1)
    assert mutated != source, "the mutation did not apply, so this control proves nothing"
    target.write_text(mutated, encoding="utf-8")

    mutated_routes = upload_routes(mutated_tree)
    unreadable = [route for route in mutated_routes if not route.is_classifiable]
    assert unreadable, (
        "removing a route's size seam left every row in the inventory classifiable"
    )


def test_persisting_before_the_limit_is_reported(tmp_path: Path) -> None:
    """The mutation control for the persistence column.

    A route rewritten to write its row *before* the size seam must be reported.
    Without this the column could read "no" for every route because the
    classifier never looks, which is exactly how F-A8T2-5 and -6 stayed
    invisible under a green guard.
    """
    import shutil

    mutated_tree = tmp_path / "routers"
    shutil.copytree(ROUTERS, mutated_tree, ignore=shutil.ignore_patterns("__pycache__"))

    target = mutated_tree / "profiles.py"
    source = target.read_text(encoding="utf-8-sig")
    needle = "    content = await read_upload_within_limit("
    assert needle in source, "the profile photo route no longer has the seam this control moves"
    mutated = source.replace(
        needle,
        "    await db.profiles.insert_one({'user_id': current_user.id})\n" + needle,
        1,
    )
    assert mutated != source, "the mutation did not apply, so this control proves nothing"
    target.write_text(mutated, encoding="utf-8")

    flagged = [
        route
        for route in upload_routes(mutated_tree)
        if route.path == "/profiles/photo" and route.persistence_before_limit.startswith("yes")
    ]
    assert flagged, (
        "a row written before the size seam was not reported as persistence "
        "before the limit"
    )


def test_an_unbounded_read_beside_a_seam_is_reported(tmp_path: Path) -> None:
    """The mutation control for the `LIMIT BEFORE READ?` column.

    A route that keeps its seam and *also* reads the body raw must not be
    reported as bounded. That is the shape a partial fix leaves behind, and the
    one a column that only asked "is there a seam?" would miss.
    """
    import shutil

    mutated_tree = tmp_path / "routers"
    shutil.copytree(ROUTERS, mutated_tree, ignore=shutil.ignore_patterns("__pycache__"))

    target = mutated_tree / "profiles.py"
    source = target.read_text(encoding="utf-8-sig")
    needle = "    content = await read_upload_within_limit("
    mutated = source.replace(needle, "    raw = await file.read()\n" + needle, 1)
    assert mutated != source, "the mutation did not apply, so this control proves nothing"
    target.write_text(mutated, encoding="utf-8")

    photo = [route for route in upload_routes(mutated_tree) if route.path == "/profiles/photo"]
    assert photo, "the mutated route left the inventory"
    assert photo[0].limit_before_read.startswith("no"), (
        "a raw whole-body read beside the seam was still reported as bounded: "
        f"{photo[0].limit_before_read}"
    )


def test_the_inventory_renders(routes: list) -> None:
    """The table is the artefact a receipt carries; it must render every row."""
    table = render_inventory(routes)
    assert table.count("\n") == len(routes) + 1
    for route in routes:
        assert route.path in table


def test_the_dataclass_reports_unclassifiable_honestly() -> None:
    """`is_classifiable` is what every test above trusts; break it here first."""
    clean = UploadRoute(
        module="m.py",
        method="POST",
        path="/x",
        function="f",
        upload_parameters=("file",),
        read_method="spool_upload_file",
        limit_source="settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB",
        limit_before_read="yes",
        persistence_before_limit="no",
        analysed_through=("f",),
    )
    assert clean.is_classifiable
    for field in ("read_method", "limit_source", "limit_before_read", "persistence_before_limit"):
        broken = UploadRoute(**{**clean.__dict__, field: UNCLASSIFIABLE})
        assert not broken.is_classifiable, field


# --------------------------------------------------------------------------- #
# R-A8U: the one body-reading surface this inventory deliberately does not own #
# --------------------------------------------------------------------------- #


def test_the_websocket_surface_is_exactly_the_one_that_was_reviewed() -> None:
    """A websocket reads client-supplied messages and is not an HTTP route.

    `routers/ws.py::websocket_notifications` does `await websocket.receive_text()`
    with no application-level bound. It is **not** a hole of the same shape as
    the four unauthenticated HTTP routes:

    * it authenticates first - the JWT (or the auth cookie) is decoded and a
      `user_id` established before the receive loop is ever entered, so an
      anonymous caller cannot reach the read at all;
    * uvicorn bounds a websocket message at its `ws_max_size`, 16 MB by default,
      and this deployment does not raise it (no `ws_max_size` appears anywhere in
      the backend, the Dockerfile or the compose files).

    So it is **documented debt**, not a finding, and this test is what keeps that
    statement true: it fails if a second websocket endpoint appears, or if this
    one stops authenticating before it reads. Bounding it properly means a
    per-message cap at the uvicorn layer, which is a deployment change rather
    than a code one and belongs in its own phase.
    """
    sources = {
        path.name: path.read_text(encoding="utf-8-sig")
        for path in sorted(ROUTERS.rglob("*.py"))
        if "__pycache__" not in path.parts
    }

    declaring = sorted(name for name, text in sources.items() if "@router.websocket" in text)
    assert declaring == ["ws.py"], (
        "a new websocket endpoint has appeared. A websocket reads client-supplied "
        "messages and no upload guard here covers it; review its size bound and "
        f"add it to this test in the same commit: {declaring}"
    )

    ws = sources["ws.py"]
    auth_at = min(ws.index("jwt.decode"), ws.index("if not user_id"))
    read_at = ws.index("receive_text")
    assert auth_at < read_at, (
        "the notifications websocket now reads from the client before it "
        "establishes a user, which makes it an unauthenticated unbounded read"
    )

    assert "ws_max_size" not in ws, (
        "ws.py now names ws_max_size; the 16 MB uvicorn default this debt is "
        "recorded against may no longer be what applies"
    )
