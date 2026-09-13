# Gate 4 bullet 8 — literal requirement mapping

**Bullet:** "Production Org-Admin permissions are manually validated in staging/prod."

**Status: NOT EARNED — UNTICKED.** R-A8W Stage B measured it against staging on
2026-09-13 and it failed on F-A8W-B3 (a foreign organisation read by id answered
500). R-A8X fixed that defect offline; an offline fix earns nothing, so the bullet
stays unticked until a real staging execution of the updated spec passes on a
release that contains the fix.

## What "manually validated" is taken to mean

Carried forward, not re-made: `READINESS_CONVERGENCE.md` §7 records that one
execution of `client/e2e/staging/org-admin-permissions.spec.ts` is the measurement
for both Gate 3 bullet 2 and this bullet, and the R-A8W owner decision added that
the run must be **reviewed row by row by a person (or Claude acting as reviewer)
with an independent probe and a database/audit read-back**, which is what
"manually" buys over an automated green. The bullet's wording does not name the
properties; the owner decision for R-A8W does: org-admin behaviour, project and
organisation scope, foreign-tenant denial, and selector / direct-API behaviour.

## Mapping

| # | Requirement (literal source) | Test assertion in the updated spec | Manual review needed? | Status |
|---|---|---|---|---|
| 1 | Org-admin can save and retrieve role permissions (bullet: "permissions ... validated"; Gate 3 b2 shared measurement) | `a Client DMS permission granted through the API is read back`; `the Client DMS group is offered, and a grant saved in the browser survives a reload` (browser save through Save Changes + step-up, server read-back equals `[dms.document.share]`, still ticked after reload) | Yes: confirm the role row's `organization_id` is the org admin's organisation, and an audit `role_updated` row exists for the run's role | R-A8W: API half PASS, browser half FAILED on F-A8W-B4 (spec). Spec corrected in R-A8X. **Not re-executed** |
| 2 | A permission write needs step-up (org-admin behaviour) | `the permission write is refused without a step-up token` (403) | No | R-A8W PASS twice |
| 3 | Organisation scope: the admin sees its own organisation and no other | `the organisation list holds the org admin's own organisation and no other`; own organisation by id 200 in `a foreign organisation addressed directly is refused` | Yes: read the policy audit rows for the run (all `allow` rows name the own organisation) | R-A8W PASS |
| 4 | Foreign-tenant denial by direct id (organisation) | `a foreign organisation addressed directly is refused`: status in {403, 404}, never 200 (leak) or 401 (forced logout) | Yes: confirm no foreign row in the body and a `scope_denied` policy decision | R-A8W **FAIL (500)** = F-A8W-B3. Fixed in R-A8X (`test_organization_foreign_access_refusal.py`, class guard `test_domain_error_reraise_guard.py`). **Not re-executed** |
| 5 | Project scope: visible projects are the admin's own, non-empty | `every visible project belongs to the org admin's organisation` | No | R-A8W PASS |
| 6 | Foreign-tenant denial by direct id (project) | `a foreign project, or a foreign-organisation filter, leaks nothing` (direct read 403/404) | No | R-A8W PASS (403) |
| 7 | Selector behaviour: asking for a foreign organisation's rows by parameter leaks nothing | same test, `?organization_id=<foreign>` → 200 with no foreign object, or 403/404, never 401 | Yes: repeat with the `X-Org-Id` header by hand. On this branch the token path ignores `X-Org-Id` (dev-header path only), so the query parameter is the selector the spec can assert | R-A8W PASS; the manual header probe answered 200/0 rows, and **500 when combined with a foreign id** - the same F-A8W-B3 path |
| 8 | No foreign role or user visible | `roles and users of a foreign organisation are not visible` | No | R-A8W PASS |
| 9 | The admin pages in the browser receive no foreign object and never force a logout | `navigating the admin pages surfaces no foreign data and no forced logout` (now after accepting the terms through the terms page, so the walk measures real pages) | No | R-A8W PASS |
| 10 | A write naming a foreign organisation does not land in it — roles | **Not asserted by the spec** (read-only against the foreign tenant by rule) | **Yes, by hand**: POST a run-tagged role naming the foreign organisation; confirm 0 foreign roles changed and where the row landed | R-A8W manual probe: accepted, stored in the admin's own organisation (see below). Disposition A |
| 11 | A write naming a foreign organisation does not land in it — users (found by the R-A8X review: an org admin could move any user in its tenant, itself included, into a foreign tenant through `PUT /api/users/{id}`) | `the org admin cannot move itself into a foreign organisation`: PUT own record with the foreign `organization_id` → 403/404, and `/api/users/me` still names the own organisation | Yes: confirm in the database that no user row changed organisation during the run | Fixed offline in R-A8X (`test_foreign_org_move_refusal.py`). **Not re-executed** |
| 12 | A write naming a foreign organisation does not land in it — projects (same review: `PUT /api/projects/{id}` re-parented a project and leaked foreign-org existence) | Not asserted: the default org-admin role lacks `dms.project.manage`, so the spec would measure the permission gate, not this check | **Yes, by hand** with a role that holds `dms.project.manage`: PUT a staging project naming the foreign organisation → 403, project unchanged | Fixed offline in R-A8X (`test_foreign_org_move_refusal.py`). **Not re-executed** |
| 13 | Foreign-tenant denial by direct id — users and roles | `a foreign user and a foreign role addressed directly are refused` (needs `E2E_FOREIGN_USER_ID`, `E2E_FOREIGN_ROLE_ID` seeded in the foreign tenant) | No | New in R-A8X. **Not executed** |
| 14 | Foreign organisation update and delete by id | Not asserted in the browser run (a regression would write to the foreign tenant); covered offline by `test_organization_foreign_access_refusal.py` (PUT → 403) | **Yes, by hand**: PUT with an unchanged payload → 403; the foreign organisation's `updated_at` unchanged | Offline PASS in R-A8X |

## Foreign-write semantics (F-A8W-B5)

R-A8W observed `POST /api/roles` from an org admin with `organization_id` naming a
foreign organisation return 200, and the role stored in the **admin's own**
organisation. No foreign write happened.

The contract, read from source rather than from what feels cleaner:

* `routers/roles.py::_create_role_policy_scope` authorises an org admin's role
  creation against `current_user.organization_id`, **not** the requested value:
  `if "orgadmin" in roles: return (str(current_user.organization_id), None)`.
* `services/role_service.py::create_role`, org-admin branch, then **overwrites**
  the payload: `scope = "organization"; organization_id = str(org_id);
  project_id = None`. A project admin is clamped the same way to its own
  organisation.
* Only the superadmin branch honours `role_data.organization_id`.
* `RoleCreate` declares `organization_id` as an optional field with no validator,
  `docs/AUTHZ.md` says nothing about rejecting a body scope, and neither the
  Gate 4 bullet nor the R-A8W owner decision states that a foreign scope in a
  body must be refused.

**Disposition: A — correct, deliberate scope clamping** by the source's own
design, implemented twice (authorisation and persistence). Isolation holds. It is
recorded as a **product/owner question, not a defect**: a client that sends a
foreign `organization_id` gets a success that silently means something else, and
a 403/422 would be more honest. Changing it is a behaviour change to a live API
and is not made in R-A8X.

## What re-earns the bullet

One staging execution of the updated spec on a release containing the R-A8X fix,
9/9, with rows 1, 3, 4, 7 and 10 reviewed by hand against the database and the
policy audit log, and the result recorded here requirement by requirement.
