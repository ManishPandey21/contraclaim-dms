# RBAC Thorough Test Plan

## Updated enforcement (Jan 2026)
- Only superadmin can create organizations and projects (project creation enforces superadmin role; non-superadmin seed roles no longer include create/edit/delete/assign for orgs/projects).
- Dev header fallback is disabled unless `ALLOW_DEV_HEADERS=True`; requests without a valid JWT must be rejected.
- Scoping rules: superadmin = global; orgadmin/orguser = their organization and all projects within it; projectadmin/projectuser = their assigned projects within their organization.

Scope: Verify hardened RBAC semantics end-to-end across authentication, organization/project scoping, and permission/role assignment logic for:

- superadmin (global)
- superuser (explicitly assigned organizations/projects)
- orgadmin/orguser (single organization)
- projectadmin/projectuser (assigned project(s) within org)

This plan uses:

- Dev headers fallback (no tokens required):
  - X-User-Id: arbitrary identifier or email-like value (e.g., test@example.com)
  - X-User-Role(s): string or JSON array: "superadmin", "superuser", "orgadmin", "orguser", "projectadmin", "projectuser"
  - X-Org-Id: the user's organization id (string)
  - X-Proj-Id: the user's project id (string)
- Centralized scope logic in core/security.py:
  - authorize_scope
  - build_scope_query
  - validate_role_assignment
- Key routers updated to use centralized helpers:
  - users, projects, tags, parties, documents, email

Base URL

- http://localhost:8000

Run server

- Install dependencies (one-time):
  - cd backend
  - pip install -r requirements.txt
- Start server:
  - cd backend
  - uvicorn rbac_backend.main:app --reload --host 0.0.0.0 --port 8000

Notes

- Initial seed inserts default organizations, projects, and users on first startup if collections are empty.
- For dev-header tests, services do not require valid DB accounts, but if referenced entities are missing, lists may be empty. The objective here is to verify that access is allowed/denied according to scope rules without cross-tenant leakage.

===================

1. # Authentication and Role Resolution
   Endpoints: any (we will use a trivial GET)

Headers and expected behavior:

- superadmin:
  - X-User-Id: superadmin@example.com
  - X-User-Role: superadmin
  - Expect: global access; in CurrentUser, organization_id, organizations, projects sanitized to None/[]/[].
- superuser (assigned orgs/projects):
  - X-User-Id: suser@example.com
  - X-User-Role: superuser
  - X-Org-Id: org1 (becomes organizations = ["org1"])
  - Optional X-Proj-Id: proj1 (becomes projects = ["proj1"])
  - Expect: scope to those orgs/projects
- orgadmin:
  - X-User-Id: oadmin@example.com
  - X-User-Role: orgadmin
  - X-Org-Id: org1 (required)
  - Expect: access only within org1; project scoping enforced when provided
- orguser:
  - X-User-Id: ouser@example.com
  - X-User-Role: orguser
  - X-Org-Id: org1
  - Expect: read/ops within org1 per permission checks on endpoints
- projectadmin:
  - X-User-Id: padmin@example.com
  - X-User-Role: projectadmin
  - X-Org-Id: org1
  - X-Proj-Id: proj1 (required)
  - Expect: access only within proj1 and org1
- projectuser:
  - X-User-Id: puser@example.com
  - X-User-Role: projectuser
  - X-Org-Id: org1
  - X-Proj-Id: proj1
  - Expect: access only within proj1 and org1

Smoke command (e.g., GET /api/health with various headers) to confirm header parsing does not break system.

=================== 2) Users
===================
2.1 POST /api/users (create)

- Headers: as creator role
- Validate validate_role_assignment prevents privilege escalation:
  - orgadmin cannot assign ["superuser"] or ["superadmin"] (expect 403)
  - projectadmin cannot assign ["orgadmin"] or ["superuser"] or ["superadmin"] (expect 403)
  - superuser cannot assign ["superadmin"] (expect 403)
  - superadmin can assign any role (expect 201)
- Validate scope for target organization:

  - superuser with organizations=["org1"]: creating user in org2 should 403
  - orgadmin in org1 must set organization_id to org1 (or it will be auto-set)

  2.2 GET /api/users

- Use build_scope_query scoping:

  - superadmin: sees all
  - superuser: only users in allowed orgs
  - orgadmin/orguser: only users in their org
  - projectadmin/projectuser: only users in their org (and possibly subset by projects if implemented later)

  2.3 GET/PUT/DELETE /api/users/{id}

- authorize_scope on target user's organization

=================== 3) Projects
===================
3.1 GET /api/projects?organization_id={org?}

- build_scope_query applied

  - superuser with ["org1"]:
    - no org filter: returns projects in org1
    - explicit organization_id=org2: result should be empty
  - orgadmin/orguser in org1: cannot see org2 projects
  - projectadmin/projectuser: must only see assigned projects

  3.2 GET/PUT/DELETE /api/projects/{id}

- authorize_scope enforced based on project’s organization_id and project id.

  3.3 POST /api/projects

- authorize_scope for creation: non-superadmin must belong to the org

=================== 4) Tags and Subtags
===================
4.1 GET /api/tags

- build_scope_query scoping on organization_id

  - superuser: restrict to assigned orgs
  - orgadmin/orguser: only their org
  - project roles: constrained to their org (no project_id on tag)

  4.2 POST /api/tags

- orgadmin: create only within their org
- superadmin: allow "global" (no org) or specific org

  4.3 PUT/DELETE /api/tags/{tag_id}
  4.4 GET/POST/PUT/DELETE /api/tags/{tag_id}/subtags, /api/subtags/{subtag_id}

- authorize_scope on tag’s organization_id

=================== 5) Parties
===================
5.1 GET /api/parties

- build_scope_query with org_field="organizationId", project_field="projects"

  - orgadmin/orguser in org1: only parties with organizationId==org1
  - projectadmin/projectuser (proj1): parties whose projects include proj1

  5.2 GET/PUT/DELETE /api/parties/{id}

- Enforcement according to role (org vs project) or superuser’s assigned orgs

  5.3 POST/DELETE /api/parties/{party_id}/projects/{project_id}

- authorize_scope on target project’s org/project

=================== 6) Documents
===================
6.1 GET /api/documents

- build_scope_query scoping with optional organization_id/project_id filtering
- Verify combinations:

  - Without filters: role-scoped results only
  - With org filter not in scope: 0 results
  - With project filter not in scope: 0 results
  - With tags/subTags filters: ensure combined with scope (AND behavior via $and)

  6.2 GET /api/documents/{id}

- authorize_scope on document’s org/project

  6.3 PUT/DELETE /api/documents/{id}

- authorize_scope

  6.4 References and Enclosures:

- GET/POST/DELETE references and enclosures route: authorize_scope (document’s org/project)

  6.5 GET /api/documents/export

- Scope filter combined with explicit filters and search

=================== 7) Email
===================
7.1 POST /api/email/share-document

- authorize_scope on target document’s org/project

  - orgadmin in org1 cannot share document in org2
  - projectadmin in proj1 cannot share document in proj2

  7.2 GET /api/email/suggestions
  7.3 POST /api/email/resolve-recipients

- RBAC-aware filters; confirm scoping for parties/representatives as per org/project

=================== 8) Contracts
===================
8.1 Upload multipart/chunk, status, download, list

- \_authz_check denies cross-org or cross-project operations
- List by org/project scoping; no leakage

  8.2 Search

- \_authz_check for org/project on search

=================== 9) Database indices
===================

- On startup: ensure_indexes runs from core.database.connect()
- main.py also creates several indices
- Verify no errors on startup and that index creation does not crash if collections are absent

===================
Example Curl Snippets (Dev Headers)
===================

Base variable:

- Windows CMD: set BASE=http://localhost:8000
- PowerShell: $env:BASE="http://localhost:8000"
- Bash: export BASE=http://localhost:8000

Health (sanity):
curl -i %BASE%/api/health

Projects list (superuser in org1)
curl -i -H "X-User-Id:suser@example.com" -H "X-User-Role:superuser" -H "X-Org-Id:org1" %BASE%/api/projects

Projects list (orgadmin org1)
curl -i -H "X-User-Id:oadmin@example.com" -H "X-User-Role:orgadmin" -H "X-Org-Id:org1" %BASE%/api/projects

Projects list (projectuser proj1)
curl -i -H "X-User-Id:puser@example.com" -H "X-User-Role:projectuser" -H "X-Org-Id:org1" -H "X-Proj-Id:proj1" %BASE%/api/projects

Users list (superadmin)
curl -i -H "X-User-Id:superadmin@example.com" -H "X-User-Role:superadmin" %BASE%/api/users

Users create attempt (orgadmin trying to assign superuser — expect 403)
curl -i -X POST %BASE%/api/users ^
-H "Content-Type: application/json" ^
-H "X-User-Id:oadmin@example.com" -H "X-User-Role:orgadmin" -H "X-Org-Id:org1" ^
--data "{\"username\":\"x1\",\"email\":\"x1@example.com\",\"password\":\"password\",\"roles\":[\"superuser\"],\"organization_id\":\"org1\"}"

Tags list (orgadmin org1)
curl -i -H "X-User-Id:oadmin@example.com" -H "X-User-Role:orgadmin" -H "X-Org-Id:org1" %BASE%/api/tags

Parties list (projectuser proj1)
curl -i -H "X-User-Id:puser@example.com" -H "X-User-Role:projectuser" -H "X-Org-Id:org1" -H "X-Proj-Id:proj1" %BASE%/api/parties

Documents list (orguser org1)
curl -i -H "X-User-Id:ouser@example.com" -H "X-User-Role:orguser" -H "X-Org-Id:org1" ^
"%BASE%/api/documents?limit=5"

Share document (projectuser — expect 403 if not in doc's project)
curl -i -X POST %BASE%/api/email/share-document ^
-H "Content-Type: application/json" ^
-H "X-User-Id:puser@example.com" -H "X-User-Role:projectuser" -H "X-Org-Id:org1" -H "X-Proj-Id:proj1" ^
--data "{\"recipient_email\":\"r@example.com\",\"subject\":\"S\",\"message\":\"M\",\"document_id\":\"DOC_ID\"}"

Contracts list (orgadmin org1)
curl -i -H "X-User-Id:oadmin@example.com" -H "X-User-Role:orgadmin" -H "X-Org-Id:org1" ^
"%BASE%/api/contracts/list?organization_id=org1&amp;limit=5"

===================
Pass/Fail Criteria
===================

- No endpoint returns data outside the caller’s scope (org/project).
- Superadmin access global; superuser constrained to assigned orgs/projects; org/project roles constrained as defined.
- Role assignment prevention enforced (validate_role_assignment).
- Export and listing endpoints combine explicit filters with scope filters correctly.
- Tag/Subtag and Parties endpoints respect authorize_scope/build_scope_query logic.
- No runtime exceptions / 500 errors introduced by RBAC hardening changes.
- Database indexes creation does not break startup.

If any failures:

- Capture the exact curl, headers, request payload, and response body.
- Refer to affected router and adjust scope filters or authorization logic accordingly.
