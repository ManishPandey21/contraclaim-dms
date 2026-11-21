# Extraction review permission: audit and remediation proposal (4B-49, AQ-12)

Status: **target decided by the owner on 2026-11-17 (section 6); not implemented.**
No access has been withdrawn. Nothing here is deployed. Existing production access
stays as it is until reading P-15, the impact analysis, the migration arrangements
and a separate release approval are all complete.

## 1. What the permission does

`dms.document.review_extraction` lets a person approve or reject document pages held
for human review, and so release held-back extraction evidence. It is live in
production. It is checked in one place, `ExtractionReviewService`, on four routes:

| Route | Check |
|---|---|
| `GET /api/documents/{id}/extraction-review` | view the document; `can_review` is this permission |
| `POST …/pages/{page}/approve`, `POST …/pages/{page}/reject` | this permission on the document |
| `POST …/extraction-review/finalize` | this permission on the document |

`GET /api/document-extraction-reviews` (the queue) lists documents the caller may view.
Release 1.1.1 meant the permission to be granted by name to Organization Admin and
Project Admin only (`EXPLICIT_GRANT_ONLY_PERMISSIONS`). That holds when roles are
seeded. It does not hold when the permission is checked.

## 2. Every path by which it is held today

Produced by code, not by reading: `tests/test_review_extraction_inheritance_matrix.py`
classifies every seed role; `tests/integration/test_review_extraction_inheritance_mongo.py`
checks the same classifier against the real `PolicyService` and the live route for 19
personas on a real MongoDB.

| Path | Mechanism | Survives explicit-grant-only? |
|---|---|---|
| Named on a role | the role document lists the permission | yes |
| Named by catalogue id | the role lists the permission document's id; the role API accepts catalogue ids (`POST /api/roles/{id}/permissions/{permission_id}`, or a role update listing the ids `GET /api/permissions` returns); the Permissions page saves names | yes (see §4) |
| `dms.admin` widening | `PolicyService.has_permission` also accepts `dms.admin` for any `dms.*` check | **no** |
| `*` wildcard | a role listing `*` | **no** |
| Super Admin shortcut | the `superadmin` key on the principal; a system alias such as `super-admin` keeps it | **no** |

Not paths, checked:

- **Legacy role spellings** (`organization-admin`, `Project Admin`) resolve one hop to
  their canonical role and get exactly its paths.
- **Super User** has no role document, so no path.
- **A deactivated role** confers nothing, the Super Admin key included.
- **The user document's own `permissions` field** is not read by the check.
- **Organisation and project memberships** carry no roles or permissions.
- **Service accounts** (`account_type: system_service`) get no shortcut and no
  exclusion; they are judged by their roles like anyone else.
- **Workers:** no worker or service calls these routes. After a decision, the router
  publishes the resolved review in the background of that same request, using the
  decision the authorised person made. No other caller exists in the codebase.
- **Legacy permission aliases:** none is declared for this permission.
- **Development headers** (`X-User-Role`) can claim any role, but only when
  `ALLOW_DEV_HEADERS` is true, which the production configuration gate refuses.

### Seed roles

| Role | Paths today | Held by name? |
|---|---|---|
| Super Admin | Super Admin shortcut, `dms.admin` | no |
| Organization Admin | named, `dms.admin` | yes |
| Contract Manager - Organization | `dms.admin` | **no: inheritance only** |
| Project Admin | named, `dms.admin` | yes |
| every other seed role | none | — |

So, from the seed catalogue alone, two roles hold the permission only by inheritance:
**Super Admin** and **Contract Manager - Organization**. The seed comment says the
second must not have it. Custom roles with `*` or `dms.admin` would add more, and only
a production reading can say whether any exist.

## 3. Production reading P-15 (not yet taken)

`scripts/claim_arbitration_readonly_readings.py` now takes reading **P-15** with the
others (section F of `scripts/post_cutover_readonly_checks.sh`, read-only, AQ-11). It
resolves every user's roles as the server does and reports counts only:

- users holding the permission by name, by name only through a catalogue id, only by
  inheritance, and not at all;
- inheritance-only users by path, by seed role, by account type, and how many are
  disabled; custom roles are counted, never named;
- role documents carrying the name, `dms.admin` or `*`.

It prints no name, email or id. **Not read:** until P-15 is taken on production, the
number of affected people is unknown, and nothing in §5 may proceed past step 1.

## 4. A defect found and fixed in passing (4B-10)

A role can hold a permission by its catalogue id (an ObjectId string) instead of its
name. The Permissions page saves names, but the role API accepts ids
(`POST /api/roles/{id}/permissions/{permission_id}`, or a role update listing the ids
`GET /api/permissions` returns), and roles written by older tools may hold them.
Reading P-15 counts such holders (`by_name_through_a_catalogue_id_only`).
`has_permission` honoured ids, but the explicit check that 4B-10 put on
`dms.claim.determine` and `dms.claim.strategy.read` did not, so a grant of either saved
by id was refused. 4B-49 makes the explicit check accept the catalogue id (and a
catalogue label) of the same permission. `*`, `dms.admin` and the
Super Admin shortcut are still refused. Proof:
`test_a_grant_saved_by_catalogue_id_is_a_grant_by_name` (real MongoDB, real route). It
fails without the fix with `explicit_grant_required`.

Production effect: none today. Claim Bench is off there, and `dms.document.review_extraction` is
not yet explicit-only. It matters for step 3 below: without it, closing the inheritance
would also withdraw grants an administrator made deliberately.

Related, fixed separately (2026-11-17): `GET /api/me` sent the client the catalogue id
rather than the name for such grants, so the client hid screens gated on the name
(the extraction-review queue among them) although the server allowed them. It now
sends the names (`get_effective_permission_names`; proof
`tests/integration/test_effective_permission_names_mongo.py`).

Correction (2026-11-17): the first version of this section said the Permissions page
saves ids, so that no grant made through the product worked. That was wrong: the page
saves names. The fixes stand for roles that hold ids.

## 5. Proposed migration path (explicit-grant-only)

Each step needs the owner's approval. None is started.

1. **Read P-15 on production** (authorised read-only access, AQ-11). Record the counts
   in `docs/POST_CUTOVER_OPERATIONAL_ASSESSMENT_2026-11-15.md`.
2. **Identify the affected people.** For each inheritance-only group P-15 shows,
   someone with authorised access to the data, not this tool, lists the actual users
   and asks their organisation's administrator whether they review extraction today.
   The audit log answers "who has used it" (`policy.authorize` rows with
   `metadata.permission = dms.document.review_extraction` and `result = allow`); that is
   a further read-only reading if wanted.
3. **Grant by name first.** For each person who needs it, the organisation's
   administrator adds the permission by name to a role they hold. This adds no access.
   The likely candidates are custom roles; for Contract Manager - Organization, the
   owner decides between granting it by name on that role (which reverses the release
   1.1.1 decision) and moving those people to a role that has it.
4. **Close the inheritance.** Add `dms.document.review_extraction` to
   `SUPERADMIN_EXCLUDED_PERMISSIONS` (`core/permissions.py`). From then on only a named
   grant (or its catalogue id) passes. Super Admin, `*` and `dms.admin` holders lose
   it. The pinned matrix test changes in the same commit, and the inheritance
   integration test flips its expectations. Super Admin keeps viewing documents but
   can no longer approve or reject pages. That is the same rule as for claim strategy
   and determination, and it is the owner's call.
5. **Verify after release.** Re-take P-15. The "only by inheritance" count must not
   matter any more: every remaining holder is by name. Watch the audit log for
   `explicit_grant_required` denials on this permission for two weeks. Each one is
   either a person step 3 missed (grant by name) or an intended withdrawal.

Rollback for step 4 is a one-line revert. No data changes in any step, except the role
grants in step 3, which administrators make through the product.

## 6. Owner decision, 2026-11-17 (AQ-12)

| Role | Decision |
|---|---|
| Contract Manager - Organization | Must not receive `dms.document.review_extraction` automatically through `dms.admin`. |
| Super Admin | Must not acquire extraction approval authority merely through platform privileges. |
| Organization Admin, Project Admin | May keep the permission they hold by name, subject to normal scope and audit controls. |
| Contract Managers who genuinely review extraction | May receive an explicit, scoped grant by name. |

Consequences for the path in section 5:

- Step 3 is settled for Contract Manager - Organization: the permission is **not**
  added to that seed role. A Contract Manager who reviews extraction gets a named
  grant on a role they hold in the organisation or project where they review, made
  by that scope's administrator and audited like any role change.
- Step 4 is settled in principle: the permission joins
  `SUPERADMIN_EXCLUDED_PERMISSIONS`, so `dms.admin`, `*` and the Super Admin
  shortcut no longer pass it. Super Admin keeps viewing documents.
- **Nothing in step 4 is implemented or released yet.** The gate is, in order:
  P-15 read on production; the impact analysis of step 2; the named grants of step
  3 in place; and a separate release approval from the owner. Until then the code
  is unchanged and the pinned matrix test keeps describing today's behaviour.

## 7. Still open

- Who takes P-15, and when (AQ-11). Procedure:
  `docs/PHASE_4B_READINGS_OPERATOR_PROCEDURE.md`.
- The release approval for step 4, after the gate above.
- Custom roles carrying `dms.admin` or `*`: unknown until P-15. Each is handled as in
  step 2, case by case.
