# Security remediation: SEC-1 to SEC-4 and JG-7(c) (2026-11-19)

**Owner direction (2026-11-19):** fix the four JG-7(c) security findings before custom roles reach row visibility; then implement an opt-in, permission-aware `build_scope_query`. No production role, historical claim or export behaviour is changed automatically. Standing restrictions unchanged: rollout off, skills unpublished, PR #114 draft, no production migration, deployment or merge, Phase 5 on hold.

**What each status means here**

| Status | Meaning |
|---|---|
| Implemented | Code and tests on `claude/zen-ptolemy-695liz` |
| Independently verified | Local real-MongoDB and unit suites green, mutants killed, CI run named in the register green except the known secret-scan findings |
| Production-approved | **None of these.** Needs P-05 read in production, authorised security review, staging acceptance (two consecutive deployed runs) and the owner's written acceptance |

Audit that found them: `docs/JG7C_CUSTOM_ROLE_SCOPE_AUDIT.md` (2026-11-18).

## Tickets, by severity

### SEC-2 (High): a role could carry permissions its creator could not delegate

- **Finding.** `role_service._ensure_permissions_assignable` refused only `*`, `system:admin`, `dms.admin`, `drafting.admin` and the `platform.` / `billing.` / `subscription.` / `permissions:` / `roles:` prefixes, compared as strings. An organisation admin could create a role holding any other permission, including the explicit-grant-only legal material (`dms.claim.strategy.read`, `dms.claim.determine`, `dms.claim.evidence.confirm_contract`, `dms.claim.signal.view`), and assign it to themselves or anyone in their organisation. A catalogue ObjectId was not resolved, so `dms.admin` given by id passed the string check. A project admin naming another project silently got a role in their first project.
- **Fix.** `services/role_service.py` `_resolve_permission_entry`, `_ensure_permissions_assignable`, `ensure_role_delegable`, `_validate_role_scope`; `core/permissions.py` `known_permission_name`. Every entry is resolved (catalogue id, label, name) and stored as the name. A non-Super-Admin may put on a role only a known permission (else 422) that is not non-delegable (judged on the entry and on its name, 403) and that they hold in the role's own organisation and project (an explicit-grant permission only by name, never through a wildcard; 403). Applies to create, edit, replace-permissions and add-permission. An organisation role must name an existing organisation and a project role an existing project of it (422). A project admin naming another project is refused (403). Built-in role keys (`orguser`, `projectuser`, `user`, the ContraClaim expert keys) are now reserved names.
- **Tests.** `I/test_security_remediation_mongo.py::test_a_role_carries_only_what_its_creator_may_delegate_there` (HTTP, step-up, real RBAC: valid role stored in the creator's organisation; explicit-grant permission 403; `dms.admin` by catalogue id 403; unknown 422; add and edit with a privileged permission 403 and nothing written; another project 403; Super Admin organisation role without an organisation refused); `T/test_role_management_phase6.py` (15, updated: reserved names now 403 for organisation admins).
- **Migration risk.** None stored is changed. Existing roles that would fail the check keep working and stay editable for fields other than permissions; their holders are bounded by SEC-1. P-05 reports how many exist.

### SEC-1 (High): custom-role permissions were pooled across organisations

- **Finding.** `PermissionService.user_has_permission` pooled every permission on every role a user held, with no reading of the role's `organization_id` or `project_id` (`permission_service.py:1095-1112` at `82973e9`). A role of organisation A held by a member of B granted its permissions in B; a project role granted in every project; the Redis cache held the pooled set.
- **Fix.** New `services/role_grants.py`. Each role is evaluated where it was granted: built-in roles (Super Admin, Super User, organisation/project admin/user, expert and reviewer keys, every seeded role, anything `is_system` or `scope: system`, and documents named like a built-in role) keep their meaning; a custom organisation role counts only for a member of that organisation and, when the request names one, only in it; a custom project role only for a user assigned to that project, which must belong to the role's organisation, and only in that project; a custom role naming neither grants an organisation's user nothing. `PermissionService.scoped_grants` is the one place role references are loaded; `user_has_permission`, `user_has_explicit_permission`, `get_user_permissions` and `get_effective_permission_names` read it, so what the client is told matches what the server allows. `PolicyService.has_permission` / `has_explicit_grant` / `authorize` pass the request's organisation and project. The cache key moved to `user_perms:v4:` and holds each role's names with their scope (a pooled v3 entry is never read). A membership change (`organization_id`, `organizations`, `projects`) or disabling is now an authority change that clears the cache (`services/user_service.py`). Custom-role names no longer grant `users:read` / `users:create` by name. `PermissionService._get_db` no longer caches the handle (a per-route service outlived the database it first saw).
- **Tests.** `I/test_security_remediation_mongo.py`: cross-organisation (a role of A held by a member of B grants nothing, and is not advertised; a member of both organisations gets each role only in its own), project roles (only in their project, only for assigned users, `project_memberships` honoured and revocation honoured), platform, revoked and mixed roles, explicit grants, built-in roles unchanged; `T/test_role_grants.py` (4, including every seeded role being built-in); `T/test_authority_cache_invalidation_fail_closed.py`, `T/test_role_soft_delete_contract.py`, `T/test_role_reference_single_definition.py` updated for the cache format and the single loader.
- **Migration risk.** Behaviour change at deployment, no data change: a user holding a custom role of an organisation they are not a member of, a project role for a project they are not on, or a role naming no organisation, loses those permissions. A cold permission cache on deployment (new key). **P-05 must be read first** (counts below).

### SEC-3 (High): roles could be assigned to users they did not fit

- **Finding.** An organisation admin could assign a role naming no organisation (`users.py:1016` refused only a role naming another organisation); a project admin's check never compared the role's organisation; a Super Admin could assign an organisation-A role to a user of B. Assignment did not check that the assigner could delegate the role's permissions.
- **Fix.** `routers/users.py` `_fence_added_roles`, run on create and on update for **roles being added** (a role already held is left alone, so no stored assignment is changed by an unrelated edit), for every caller, Super Admin included. For a custom role: none for a disabled account (409); a role naming no organisation only to a user bound to no organisation, by a Super Admin (403); an organisation role only to a user of that organisation (403); a project role only to a user assigned to it, in the user's organisation, while the project is active (403); and the assigner must be able to delegate every permission on it (SEC-2, 403). Built-in roles keep `_enforce_role_assignment_scope`, which now judges the projects the update sets, not only the stored ones.
- **Tests.** `I/test_security_remediation_mongo.py::test_a_role_must_fit_the_user_it_is_given_to` (Super Admin cross-organisation 403; organisation-less 403; project not assigned 403; privileged role by an admin who lacks the permission 403; nothing written; valid assignment 200 and effective; disabled account 409) and `::test_concurrent_valid_and_invalid_assignments` (8 concurrent requests: invalid ones all refused, valid ones applied, the foreign role never stored).
- **Migration risk.** None stored is changed. Other assignment routes: reviewer roles are granted only through reviewer assignments (4B-08, unchanged); SSO first-login provisioning (`services/oidc_service.py`) uses one configured default role, and a mismatched one grants nothing under SEC-1.

### SEC-4 (Medium): a disabled account kept authenticating

- **Finding.** The access-token path (`core/security.py principal_from_access_token`, behind every request and the notifications WebSocket) did not check `disabled`; disabling relied on clearing the user's Redis sessions, which a deployment without a runtime Redis does not have, and `is_active: false` was not honoured anywhere.
- **Fix.** `services/account_status.py` `account_is_active` (inactive when `disabled` is set or `is_active` is explicitly `false`), applied to the access-token path, development headers, background-worker principal resolution, password login, token refresh and SSO login. A disabled account stops on its next request (401). Disabling or deactivating also clears the account's sessions (`routers/users.py`) and its cached permissions.
- **Tests.** `I/test_security_remediation_mongo.py::test_a_disabled_account_stops_on_its_next_request` (`disabled` and `is_active: false`: the same token is refused; the background-worker principal is refused; a disabled service account stops too).
- **Migration risk.** Users stored with `is_active: false` but not `disabled` can no longer sign in. P-05 counts them.

### JG-7(c): custom-role row visibility (opt-in)

- **Built after SEC-1 to SEC-4 passed.** `core/security.py permission_scope_query`: for a principal with built-in roles, exactly `build_scope_query`; for one with custom roles only, rows only where it holds the list's permission **by name** (no wildcard) through a custom role that applies, in the request's or its own organisation: an organisation role gives that organisation's rows, project roles those projects' rows. A platform role, a role without the permission, a revoked role and a disabled account give none. The filter always names the organisation, is never `{}` and never a top-level `$or`. Selecting a project still needs an explicit assignment (the tenant gate). `build_scope_query` itself is unchanged and its 63 call sites keep deny-all for custom-role users.
- **Opted in:** `GET /api/claims` only.
- **Tests.** `I/test_security_remediation_mongo.py::test_custom_role_holders_see_claims_only_where_they_hold_the_permission` (organisation role: A's claims, not B's; project role: its project only; another project refused; a role without the permission 403; a role of B 403; built-in unchanged; revocation 403; disabled 401) and `::test_the_custom_role_filter_never_widens_and_always_names_the_organisation`.
- **Next call sites** need, each: a passed permission, a fix to any key-overwriting or `$or`-replacing caller (`routers/tasks.py`, `routers/dashboard.py`, `if scope:` truthiness), and a real-MongoDB test. Not done.

## Mutation testing

18 mutants of the new controls (scratchpad script, not committed), each run against `T/test_role_grants.py`, `T/test_role_management_phase6.py` and `I/test_security_remediation_mongo.py`: organisation membership, request organisation, request project, platform role, project membership, scope passed through PolicyService (SEC-1); held-permission check, non-delegable judged on the resolved name, unknown permissions (SEC-2); foreign organisation, organisation-less role, disabled target, delegation on assignment (SEC-3); token-path refusal, `is_active` (SEC-4); organisation bound, project bound, wildcard (JG-7(c)). 17 killed. One (PolicyService scope) survived at first and is killed by an added test. The wildcard mutant was equivalent (a `*` entry never matches a name); the redundant code was removed and a test pins the behaviour.

## P-05: what must be read in production first

`scripts/claim_arbitration_readonly_readings.py` P-05 now reports `custom_role_compatibility` (counts only, no names or ids): custom roles; custom roles naming no organisation or project; roles named like a built-in role; users with custom roles only; users holding a custom role of another organisation; users holding a project role without the project; organisation users holding a role naming no organisation; disabled users; users `is_active: false` but not disabled. Each non-zero count is a set of users whose access changes at deployment. P-03 now reports `other_type` for the legacy `Other` claim decision (below).

## Legacy `Other` claims (read-only assessment)

P-03 `other_type`: total `other` claims; legacy ones with no `type_confirmed` (they read as confirmed today); provisional; confirmed; legacy by status; legacy with frozen evidence; legacy claims in organisations with a verified `other` notice period (these show a period as verified against the contract although nobody chose the type); organisations affected. No claim is changed. The owner decides, after the reading, whether legacy `other` claims become provisional.

## Found while testing, not fixed (outside this change)

- `GET /api/notifications/unread-count` answers 500 for a `system_service` account and, under the real-MongoDB harness, after a database swap (its service caches a database handle). Not investigated further.
- `GET /api/users/me` answers 422 for the seeded test personas (response model validation). Not investigated further.

## Review of 2026-11-19 (owner instructions and independent security review)

Owner instructions (second review) asked for the SEC-1 project boundary, production compatibility, adversarial SEC-2/3 testing, SEC-4 across tokens, WebSockets and Redis, the residual defects, and independent review. An independent reviewer (a separate agent given the code and the owner's properties, not this report) found three High and one Medium issue in the first implementation. Every finding below was reproduced by a failing test before it was fixed.

### Defects found and fixed

| Id | Severity | Found by | Defect | Fix | Regression test |
|---|---|---|---|---|---|
| DEF-1 | High | Owner question | `grant_applies` never compared a named project's organisation: an organisation role of A authorised a project of B when the request left the organisation out or named A with a project of B. | `PermissionService.resolve_request_scope` reads the project's organisation every time (never cached); a missing project or a contradictory pair resolves to an organisation no custom grant names. Used by every check, the cached path and `permission_scope_query`. | `test_an_organisation_role_never_reaches_a_project_of_another_organisation` (service, PolicyService, HTTP, both mismatch directions, unknown project, cached path); `test_a_cached_grant_never_crosses_the_project_boundary` (real Redis, re-parented project) |
| R-1 | High | Independent review | A custom role named like an unseeded built-in key (`legal_reviewer`, `contract_expert`) was treated as built-in, escaping SEC-1 scoping and the SEC-3 fence; the reserved-name check missed underscored keys. Introduced by this change (name matching added in SEC-1). | Built-in only by `is_system`, stored `scope: system` or a built-in `_id`; never by name. Every built-in key reserved in any spelling. | `test_r1_a_custom_role_named_like_a_built_in_key_is_still_a_custom_role`; `T/test_role_grants.py::test_a_name_never_makes_a_role_built_in` |
| R-2 | High | Independent review | Alias fan-out: a creator needed only one equivalent of a name, while holders pass every check the name satisfies (`projects:update` passes 23, including org-tier `dms.contract.applicability.manage`). | The creator must hold every check the name satisfies, in the role's scope. | `test_r2_an_alias_cannot_carry_permissions_its_creator_does_not_hold` |
| R-3 | High | Independent review | A custom project role counted for requests naming only its organisation, so its permissions reached the whole organisation (and could be delegated organisation-wide). | A project grant counts only for a request naming that project. | `test_r3_a_project_role_never_counts_at_organisation_level` |
| R-4 | Medium | Independent review | Permission-only gates (`require_permission`, no scope) counted a custom role in any organisation the user was listed in. | With nothing named, a custom grant counts only in the user's primary organisation (stored with the cache entry). | `test_r4_an_unscoped_check_counts_custom_roles_only_in_the_primary_organisation` |
| DEF-2 | Medium | This review | An open notifications WebSocket was never re-checked: disabling an account did not close it. | The socket re-checks the account on every message and at least every 30 s; closes 4401 (1013 if the store cannot be read). | `T/test_ws_notifications_auth.py::test_an_open_socket_closes_when_its_account_is_disabled` (both flags) |
| DEF-3 | Medium | This review | `PUT /api/users/{id}` silently dropped `organizations`: a user moved to another organisation stayed a member of the old one. Pre-existing. | `organizations` is applied when sent; a move that does not name the memberships kept is refused (422). | `test_revocation_and_membership_changes_reach_the_cache` |
| DEF-4 | Medium | This review | `organizations:read`, `orgs:view` and `reports:view` pass non-delegable billing/subscription checks through the alias contract, and were delegable. | A name is non-delegable when any check it satisfies is. | `test_no_spelling_of_a_privileged_permission_reaches_a_role` |
| L-1 | Low | Independent review | Second login route (`routers/users.py`) checked only `disabled`. | Shared active-account rule. | (token already refused; no new test) |
| L-2 | Low | Independent review | Development headers looked users up by string id only, so an ObjectId user's disabled flag was skipped. | Both forms looked up. | (dev-only path; no new test) |
| L-3 | Low | Independent review | A project role kept counting after its project became inactive. | Inactive project grants nothing. | `test_r3_...` (inactive project assertion) |
| RR-1 | Medium | Independent re-review | After R-4, a custom project role still passed permission-only gates (nothing named) across its primary organisation. | With nothing named, only organisation roles of the primary organisation count; a project role needs its project named. | `test_r3_...` (unscoped assertion); `T/test_role_grants.py` |
| RR-2 | Low | Independent re-review | Deactivating or deleting a project did not clear its members' cached grants (up to 1 h stale). | Both paths announce the change before the write and clear the members' cached grants after it, without signing them out (`complete_grant_change`). | `test_deactivating_a_project_clears_its_members_cached_grants` (real Redis) |

**Owner decision needed (DEF-4 root cause):** the alias declarations that make `organizations:read` / `reports:view` satisfy billing and subscription view checks. Changing them would change who sees billing pages; until decided, those names cannot be delegated.

### Residual defects investigated (item 6)

| Id | Classification | Finding |
|---|---|---|
| DEF-5 | Production data dependent | `GET /api/users/me` (422 "Invalid ObjectId format"), `GET /api/me` (401 "no longer active") and token refresh fail for users whose `_id` is not an ObjectId: `UserService._validate_user_id` accepts ObjectIds only. Seeded and API-created users get ObjectIds, so production is affected only if it holds string ids. P-05 now counts them (`users_with_a_non_objectid_id`). Not fixed; ticket for after the reading. |
| DEF-6 | Test-fixture (harness) | `/api/notifications*` 500 ("Event loop is closed") occurs only in the second test of a run: `dependencies.get_notification_service` keeps one service bound to the first database handle. Production runs one loop and one database, so it is not reached there. The earlier note that it was specific to service accounts was wrong. Not fixed; low. |
| Note | Pre-existing, Info | `POST /permissions/check` lets any authenticated user ask about any `user_id`; `get_effective_permission_names` also advertises the user's own `permissions` field, which no check reads. Not changed. |

### SEC-4 coverage, with and without Redis

`I/test_security_redis_mongo.py` (real Redis): access and refresh tokens refused after disabling (directly in the store, and through the users API, where re-enabling does not revive the ended sessions); Redis configured but unreachable, fail-closed (503, never 200) and fail-open (401 for a disabled account); no Redis (`is_active: false` refuses access and refresh); disabled service account and worker principal. WebSocket: connect refused (existing tests) and open sockets closed (DEF-2). CI now starts a disposable Redis (`redis:7.2-alpine`) for this module.

### Cache and concurrency

Real Redis: cached grants never cross the project boundary (including a project re-parented after caching); role edits, role revocation and membership moves clear the cache; concurrent valid, revoking and invalid assignments end consistent with the stored roles.

### Independent re-review

The reviewer re-read the fixed tree: findings 1 to 3 closed; finding 4 closed for organisations, with RR-1 (Medium) and RR-2 (Low) remaining, both now fixed above; no Critical or High hole introduced by the fixes. Its Info notes, carried as decisions or tickets: older built-in role documents stored under an ObjectId without `is_system` or `scope: system` are custom roles now and grant an organisation's users nothing (P-05 counts roles named like a built-in; audit before deployment); built-in roles are assigned without a delegation check (by design: built-in semantics preserved); `GET /api/claims` authorises before row filtering, so a holder of the permission only through a project role must select the project (functional, not a hole); `POST /permissions/check` lets any signed-in user query any user (pre-existing); membership rows written outside the application do not clear the cache (no application path writes them).

### Mutation testing (second pass)

32 mutants across SEC-1 to SEC-4, the boundary, the WebSocket re-check, DEF-3, DEF-4, R-1 to R-4 and RR-1/RR-2: 30 killed; 2 equivalent (an inactive project's marker can never match a resolved organisation; a project named without its organisation already fails the organisation comparison). The redundant line of the second was removed; the explicit inactive check is kept for clarity.

### Production compatibility (P-05)

P-05 `custom_role_compatibility.changes` classifies affected users, counts only and no identities: class (custom role of an organisation the user is not in; project role for a project not assigned; project role for a project of another organisation; role naming no organisation held by an organisation user; `is_active: false` not disabled), role type, users, organisations affected and users per organisation (no organisation named), the permission entries at stake, and the recommended re-grant for each class. Also: roles named like a built-in (now custom), users with a non-ObjectId id, custom roles carrying a name that passes a non-delegable check. **Recommendation:** read P-05 in production; for each non-zero class, an administrator re-grants the access properly scoped (the reading names the re-grant) before the code is deployed; nothing is migrated or re-granted automatically.

### JG-7(c) scope

Unchanged: opt-in on `GET /api/claims` only. One behaviour tightened by R-3: a custom project-role holder lists claims by selecting the project (without one, 403). No other list opts in.

### Legacy `Other` claims

Unchanged: no claim, notice decision or type is changed; P-03 reports the counts.

### Verdict per ticket (evidence-based; none production-approved)

| Ticket | Verdict |
|---|---|
| SEC-1 | Implemented and verified locally after DEF-1, R-1, R-3, R-4, RR-1, RR-2 and L-3 were fixed; independent re-review found no remaining Critical or High issue. Production enforcement depends on P-05. |
| SEC-2 | Implemented and verified after R-2 and DEF-4; DEF-4 root cause needs an owner decision. |
| SEC-3 | Implemented and verified; R-1 closed the built-in-name bypass of the fence; DEF-3 fixed. |
| SEC-4 | Implemented and verified with and without Redis; DEF-2 (open sockets) fixed. |
| JG-7(c) | Opt-in on the claims list only, verified; not expanded. |

## Production-dependent decisions (owner)

1. Read P-05 and P-03 in production (authorised read-only access).
2. For each non-zero P-05 count: accept the loss of access at deployment, or re-grant the roles correctly before it.
3. Authorised security review of SEC-1 to SEC-4 and JG-7(c).
4. Legacy `Other` claims: provisional or not.

## Final code hardening and security follow-ups (owner instructions, 2026-11-19, third pass)

Stages kept apart as before: **implemented** (code and its proof ran here), **tested** (real MongoDB, mutation), **CI-verified** (exact-head run), **security-approved** (authorised review; not given), **production-accepted** (not given). Nothing here changes stored users, roles, claims, production configuration or rollout settings.

### 1. 4B-05: deprecation is one transaction

Before: `SkillRegistry.deprecate` materialised a bundled-only version **before** the transaction, so a refused or failed deprecation (and any call on a standalone server, which answers 503) could leave a materialised copy behind, and two concurrent deprecations of a bundled version could both insert. Now the read, the materialisation, the status change and the audit event are all inside `run_in_transaction`; the production unique index `skill_version_unique` (migration `20261114_0001`) makes concurrent materialisations conflict, and the retried call reads the winner's result and is refused 409. Without transactions the call is refused 503 before any write.

Proof (`I/test_skill_publication_transactions_mongo.py`, real replica set, with the production unique index, and without it): materialisation in the transaction; a failure at the audit write rolls back the materialised copy and leaves a stored version published; 4 concurrent deprecations × 5 rounds × (bundled, stored) succeed once, write one document and one audit event; a forced interleaving (A reads, B commits, A must lose); repeats, a missing version (404) and a draft (409, nothing materialised); a standalone server (503, no write); 4 concurrent deprecations × 5 rounds with the unique index dropped (the review found the migration index is not created at start-up; the materialised copy now has a deterministic `_id`, `bundled:<key>@<version>`, so the `_id` index serialises them). Mutants: no deterministic `_id` (killed, 5 rounds); materialising outside the transaction (killed, 9 tests); removing either of the two overlapping status guards alone is equivalent (each covers the other), removing both is killed (13 tests).

### 2. `POST /permissions/check`

Callers: one, the client's `useHasPermission` (`client/src/services/enhanced-api.ts::checkUserPermission`), which sends only `{"permission"}` and reads `granted`; used by the Claim Bench for `dms.claim.strategy.read`. The response contract for that call is unchanged.

- **Self-check** (no `user_id`, or your own): any signed-in user, as before. It now answers with the gates' permission-level rule (`PolicyService.has_permission`), so an explicit-grant permission needs an explicit grant: before, a holder of a custom wildcard role was told `granted: true` for `dms.claim.strategy.read`, which every gate refuses.
- **Another user**: `roles:read` (held by Super Admin, organisation and project administrators; not by ordinary members) in a named `organization_id` or `project_id` through `PolicyService.authorize` (membership and subscription checked); Super Admin may omit the scope. Authority (`roles:read` anywhere) is checked before the named scope is read. A project is resolved to its own organisation; an unknown or mismatched project answers exactly like a refused scope (403, same body). A project administrator naming only an organisation is refused (organisation-wide scope required). The target must be the one user stored under that id (both id forms present: refused), a member of the named scope, and visible under the existing user-read rule (`AuthorizationService.check_user_access`). A missing and a hidden user get the same 404 and body, and the same membership reads. The target is evaluated as a request would see them (revoked role references dropped, role spellings normalised). The answer is the target's permission in that scope; a disabled account is `granted: false`, `account_active: false`. Rate limited (`scope="permission_check_other_user"`, 60/min). Every other-user check, allowed, refused, rate-limited or failed, writes `permission.check.other_user` (actor, target, permission, scope, result, reason, granted).

Proof (`I/test_permission_check_mongo.py`, real HTTP): self-check unchanged for every member; members without `roles:read` refused and audited; scope required; organisation administrator in scope, with project roles counting only in their project and the B role of a dual member not counting in A; no cross-tenant enumeration (other organisation 403; hidden vs missing identical 404; mismatched project 404; another organisation's project 403); project administrator confined to their project; organisation administrator naming a project the target is not on (404); Super Admin unscoped and scoped, a string-id target found as stored; audit sequence for each; and, from the review below, no project-existence oracle (every probe of a missing, foreign or mismatched project gives one identical answer per caller), a project administrator by membership row confined, a revoked superadmin role not reported, an id stored in both forms refused, rate-limited checks audited, the user-read rule kept. The original route fails 6 of the first 7 tests (the unchanged self-check passes). Mutants (13): no authority-first check, unresolved scope as 404, no organisation-wide requirement, raw target principal, first-match id lookup, unaudited limiter, no authorisation, no scope requirement, no organisation membership, no project membership, no user-read rule, no disabled-account rule, no Super Admin scope rule: all killed.

### 3. Advertised versus enforced permissions

Audit of `PermissionService.get_effective_permission_names` (the `permissions` list on login and `/api/users/me`, which the client uses to show and hide actions) against the evaluators the gates use. There are two kinds of gate: `require_permission` (Super Admin, else `user_has_permission`) and `PolicyService.authorize` (`has_permission`: the explicit-grant rule for `SUPERADMIN_EXCLUDED_PERMISSIONS`, and administrator widening, `dms.admin` passing `dms.*`). A list the client cannot attribute to a gate kind may only name what **both** grant.

Over-advertisement found and removed:

| What was advertised | Why it was wrong |
|---|---|
| The user document's own `permissions` field | No evaluator reads it. The two routes also fell back to it whenever the effective list was empty. |
| `users:create` (and its aliases) from a custom role | The check grants it only to the built-in administrator roles. |
| `["*"]` for a holder of a custom wildcard role | It claimed `users:create` and the explicit-grant permissions, which the gates refuse. Such a user is now listed from the catalogue. |

Kept, documented: `["*"]` for Super Admin (the client short-circuits Super Admin by role anyway); it does not cover the explicit-grant permissions, which the client asks the server about. Not advertised by design: the policy's administrator widening, because a `require_permission` gate does not apply it (under-advertisement, the safe direction; the self-check answers with the policy rule).

Proof (`I/test_effective_permissions_mongo.py`): for custom, dual-organisation, project, platform-held, wildcard and explicit-grant holders, and for every built-in persona, in four scopes, the advertised list equals the set of catalogue names both gate kinds grant; `/api/users/me` returns `[]` for a user whose only "permissions" are the stored field; a catalogue-id entry advertises the name, not the id. With real Redis (`I/test_security_redis_mongo.py`): after a role edit and a revocation through the API, the cached checks and the advertised list change together. Mutants (5): advertising the stored field, dropping the explicit rule, `*` for wildcard roles, the policy widening, `users:create` from a custom role: all killed. Existing tests that encoded the old advertisement (`T/test_role_permission_catalog_drift.py`, `T/test_role_soft_delete_contract.py`) were updated with a note.

### 4. DEF-4: compatibility assessment and separation proposal (no change made)

Owner decision: aliases and billing/subscription visibility unchanged; the three names stay non-delegable.

Observed (pinned by `I/test_def4_alias_compatibility_mongo.py`, real HTTP):

| Held name | Who holds it (built-in) | Billing/subscription checks it passes by alias |
|---|---|---|
| `organizations:read` | every tenant role: superadmin, orgadmin, orguser, contractmgr_org, projectadmin, projectuser | `billing.plan.view`, `billing.invoice.view`, `billing.invoice.download`, `subscription.history.view` (and `dms.contract.catalogue.browse`) |
| `orgs:view` | none built-in (legacy spelling) | same as `organizations:read` |
| `reports:view` | reporter | `subscription.usage.view` (and `dms.report.view`) |

Consequence today: an ordinary organisation or project user reads their organisation's billing records, billing review queue and subscription history (`/api/rbac-monetization/billing/{records,review-queue,history}/{org}`), always within their own organisation (another organisation: 403). None of the three names can be put on a role by a non-Super-Admin (403).

Proposal (for decision; not implemented):

1. Introduce view-only names that carry no billing meaning, for example `dms.organization.view` and `dms.report.view` (exists), and grant them to the built-in roles alongside the legacy names.
2. Give billing and subscription visibility its own names on the roles that should have it (proposed: orgadmin, and `contraclaim_billing_admin`); ordinary members would lose it.
3. Remove the billing/subscription members from the `organizations:read`/`orgs:view`/`reports:view` alias groups only after P-05 reports, per organisation, which custom roles hold these names and which users would lose billing visibility, and after approval. Until then the aliases stay and the names stay non-delegable.
4. Expected effect: ordinary members stop seeing billing records and subscription history; organisation administrators and billing administrators unchanged. The client's billing pages would need checking for how they gate (not assessed here).

### 5. DEF-5: string user ids (tests and design; no change made)

Reproduced with real login (`I/test_def5_string_user_ids_mongo.py`): a user stored under a string id signs in, but `/api/users/me` answers 422 and `/api/me` and `/api/refresh` 401 "no longer active". Tests: ObjectId users unaffected (pass); a string id never resolves to another user, including an ObjectId user with the same hex (pass); today's signature pinned (pass); three intended-behaviour tests that run the intended assertion and require it to fail today (`_still_def5`), so a fix turns them red until they are updated deliberately; ordinary tests, not `xfail`, because CI's `assert_suites_ran.py` counts an xfail as a skip (CI run 44519551894 failed on exactly that; the gate is unchanged).

Backwards-compatible design (for after P-05):

- One lookup helper used by `UserService` and the user routes: match `_id` as stored, `{"_id": {"$in": [raw, ObjectId(raw) if valid]}}`, with `limit 2`; **two matches is refused** (fail closed: a string id equal to another user's ObjectId hex), never the first one.
- Never rewrite stored ids; tokens keep naming the id as stored.
- Behind a setting (`ACCEPT_LEGACY_STRING_USER_IDS`, default off), turned on only if P-05 reports string ids, with the three intended-behaviour guards turned into plain assertions in the same change.
- `update_last_login`, `_validate_user_id` call sites and `validate_object_id` in `routers/users.py` move to the helper; everything else that reads users by id already tolerates both forms (`PermissionService`, `/permissions/check`).

### 6. DEF-6: notification service and the database handle (fixed)

`dependencies.get_notification_service` kept the first database handle for the life of the process. The test harness swaps handles (the reproduced 500), and so does production on `disconnect`/`connect`, after which the service would write through a closed client. Now the service (and the email service) is rebuilt when the handle object changes; with an unchanged handle it is the same object as before, and every service shares the one WebSocket connection manager. Proof: `T/test_notification_service_binding.py` (3) and `I/test_notifications_real_deps_mongo.py` (two databases in turn over real HTTP; fails with 500 on the old code).

### Independent review of the third pass

A separate reviewing agent (not the authorised security owner) read the diff end to end; no Critical or High findings.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium, introduced | Project-existence oracle: the scope was resolved before authority, so any member could tell a missing project (404) from another tenant's (403), and which organisation owns it | Fixed: authority first; unresolved scope answers like a refusal. Test and two mutants |
| 2 | Low-Medium, introduced | Timing: a missing user cost fewer reads than a hidden one | Fixed: the same membership reads and user-read rule run for a missing user (probe document); residual sub-query timing not eliminated |
| 3 | Low-Medium, pre-existing rule newly reachable | A project administrator assigned by membership row only (empty `users.projects`) passes `check_user_access` organisation-wide | Fixed in this route: organisation-wide answers need organisation-wide scope. The rule in `check_user_access` itself is unchanged (ticket below) |
| 4 | Low, introduced | Target evaluated from raw `roles`: a revoked superadmin role still reported authority | Fixed: canonical principal builder with revoked roles dropped |
| 5 | Low, conditional | `skill_version_unique` is created by migration only; without it concurrent materialisations duplicate | Fixed: deterministic `_id` |
| 6 | Low, DEF-5 class | Both id forms stored: visibility and evaluation could read different users | Fixed: ambiguous id refused (404) |
| 7 | Low, introduced | Limiter refusals and unexpected errors not audited | Fixed |
| 8 | Low, pre-existing | Super Admin advertised as `["*"]`, which implies the explicit-grant permissions | Kept and documented (the client short-circuits Super Admin by role; the explicit permissions are asked of the server) |
| 9 | Info | With the stored field no longer advertised, an empty effective list sends the client (`useRBAC.ts`) to its role-catalogue fallback, which ordinary users cannot read; `routers/users.py` still shows the stored field for other users' records | Not changed (UI-only; ticket below) |
| 10 | Info | A self-check naming a foreign organisation answers from the caller's own grants | Not changed; no foreign data involved |
| 11 | Info | `_refresh_skill` pointer outside the transaction; observability counter on retries; `services/scheduler.py` keeps start-up `email_service` methods (DEF-6 rebinding does not reach the digest jobs) | Not changed; ticket below for the scheduler |

New tickets (not fixed here): T-1 `AuthorizationService.check_user_access` should read project assignment from `project_memberships` as well as `users.projects` (finding 3, general rule); T-2 client `useRBAC` should trust an empty effective list instead of falling back to the role catalogue (finding 9); T-3 scheduler digest jobs should resolve the email service per run (finding 11).

**Re-review (same agent, after the fixes).**

Findings 1 to 7 are closed. Residuals and side effects:

| Item | Severity | Status |
|---|---|---|
| Timing residual of finding 1. A tenant administrator can tell an unresolvable project from another tenant's project by timing, because the latter goes on into `authorize`. Limited to administrators and 60 requests a minute. | Low | Documented |
| New bug from fix 4. A target whose stored email the user model rejects (none, or a local domain) answered 500. | Low | Fixed: the target principal is no longer a validated model. Test `test_a_target_without_a_valid_email_is_answered`; the raw-role mutant is still caught |
| Side effect of fix 3. The other-user check now serves built-in administrators, `orguser` holders and Super Admin. Holders of a custom `roles:read` role, at organisation or project level, are refused. | Info | Stricter than the contract; documented |
| Draft import and seed migration `20261114_0002` can still duplicate a version where `skill_version_unique` is absent. This predates the change. | Info | Ticket T-4: create the index at start-up |

This is not the authorised security review.

**CI.** CI run 44523611467 on `a43eb6a`: backend (8,414 unit; real-Mongo 79 modules, 1,063 passed, every module ran, none skipped), frontend, dependency scan and Docker green; secret scan red (same two findings). Run 44519551894 on `5053ca8` had failed only at the suites-ran gate, which counted the then-`xfail` DEF-5 tests as skips; fixed in `a43eb6a` without changing the gate. Stages: implemented, tested and CI-verified; **not** security-approved (authorised review outstanding) and **not** production-accepted.

## Owner decisions T-1 to T-4 (2026-11-19, fourth pass)

Stages as before. Nothing here changes stored users, roles, aliases or production configuration, and nothing creates a production index.

### T-2: client permissions fail closed (highest priority)

`client/src/hooks/useRBAC.ts`, rewritten:

- **No role-catalogue fallback.** An empty list returned by the server means "no permissions". Before, the hook downloaded the role catalogue and expanded roles itself, without scope.
- **No client alias expansion.** `can()` matches the server's effective list exactly. Before, the client's two-hop alias table passed checks the server refuses, for example `dms.claim.manage` → `projects:update` → `dms.task.manage` and `dms.keydate.manage`. The server list already contains every check a held name passes.
- **Three states**, each failing closed: `loading` (deny), `error` (deny, with the message; `ProtectedRoute` already shows sign-in), `ready` (the server's answer, possibly empty).
- **Invalidation.** On `auth-state-changed`, `profile-updated` and the new `tenant-scope-changed` (dispatched by `TenantContext` on every selection change), the hook forgets what it knew, denies, and re-reads `/me` with `force`. An answer to an older request never overwrites a newer one; nothing applies after unmount. The module-wide permission cache, keyed by roles only, is removed.
- **Super Admin and `*`.** They pass every check except the explicit-grant permissions (`EXPLICIT_GRANT_PERMISSIONS`), which need the name itself in the server list. The backend test `test_client_explicit_grant_permissions.py` keeps that list equal to `SUPERADMIN_EXCLUDED_PERMISSIONS`. The server's `["*"]` response contract is unchanged.

Proof:
- `useRBAC.test.ts`: 14 tests (server list only; authoritative empty; loading; error; no fan-out; Super Admin; wildcard; explicit grants; three invalidation events; account-switch race; unmount). All 14 fail on the old hook.
- `TenantContext.test.tsx`: the scope event fires.
- Mutants: explicit rule, stale-answer guard, clear-on-refresh, `force`, fan-out — all killed. The `status` gate is equivalent: loading and error states hold empty roles and permissions.
- Full client suite: 1,010 passed.

**Super Admin UI audit.** No client guard grants an explicit-grant permission through the Super Admin role or `*`:
- `ClaimBenchPage` asks the server (`useHasPermission`);
- the determination picker is filtered by the server;
- no Sidebar link uses an explicit-grant permission;
- the other `roles.includes("superadmin")` checks guard platform pages, which the backend restricts to Super Admin.

Every privilege is enforced at the backend route regardless.

### T-1: the user-read rule counts project memberships

`AuthorizationService.check_user_access` serves `GET/PUT/DELETE /api/users/{id}` and the other-user path of `/permissions/check`. For project-tier actors, each side's projects are now:
- `users.projects` plus active `project_memberships` rows (`status` active or unset);
- limited to existing, active projects of the actor's organisation.

The two sides must overlap. A project-tier actor with no such project reaches nobody; before, an empty `users.projects` skipped the test and gave organisation-wide reach. Organisation-tier rules are unchanged.

Proof (`I/test_user_access_project_memberships_mongo.py`, real HTTP):
- a membership-only project administrator reads only their project's users, both listed and membership-only;
- revoked rows, inactive projects, and another organisation's project in a row confer nothing;
- an unassigned project administrator reaches nobody;
- organisation-tier access is unchanged.

The old code fails 4 of 5. Mutants (inactive filter, organisation filter, membership rows, status filter, empty-skip): all killed.

### T-3: digest jobs resolve the email service when they run

`services/scheduler.py` no longer captures `email_service` methods at start-up. Each daily and weekly digest run asks `dependencies.get_email_service()`, which returns the service bound to the current database handle (DEF-6). Leader locks, job ids, triggers, `max_instances=1`, `coalesce=True` and the services' own retry and idempotency are unchanged.

Proof (`T/test_scheduler_email_binding.py`):
- no service is captured at start-up;
- a handle change (disconnect and connect) is followed by the next run;
- an unchanged handle keeps one service;
- the leader lock still gates both jobs;
- the schedule and options are unchanged.

The old scheduler fails 2 of 5. Limitation: unit-level, with the real dependency resolution and stand-in handles; no real-Mongo digest run.

### T-4: the skill-version unique index is verified, never created

`services/skills/version_index.py` reads the live `skill_version_unique` index on every call and compares it with the migration's definition (keys, unique, not partial). `T/test_skill_version_index_gate.py` asserts the definition equals migration `20261114_0001`'s entry.

`SkillRegistry.create_draft` (the import's upsert-or-insert) and `SkillRegistry.deprecate` refuse with 503 and write nothing when the index is missing, wrong or unreadable. No code path creates or repairs it; the gate itself is proven not to create it. The deterministic-`_id` safeguard in `deprecate` is kept and proven separately with the gate bypassed. No other writer inserts into `skill_versions`: `publish` and approvals update existing documents. No migration was added, so Gate 6 is unchanged.

Test databases now look like production after the migration:
- the real-route harness `_env` applies the migration's own `apply_indexes`;
- `tests/bench_fakes.FakeDB` reports the index.

Proof:
- real replica set: dropped, non-unique and wrong-key index are each refused, with no write and no index created;
- 5 rounds of concurrent materialisation behind a bypassed gate;
- unit gate tests (9).

Mutants: unique check, key check, fail-open on unreadable index, partial check, and each of the two gate calls removed — all killed.

### DEF-4 and Super Admin

- **DEF-4:** design and migration plan in `docs/DEF4_BILLING_PERMISSION_SEPARATION.md`. Production aliases are unchanged. Notable: `orgadmin` holds no billing permission explicitly today, so it must be granted before any alias is removed.
- **Super Admin:** the `*` contract is kept and never read as the explicit-grant permissions (T-2).
- **DEF-5:** unchanged; nothing enabled.

### Independent review of the fourth pass (agent; not the authorised security review)

| Finding | Severity | Resolution |
|---|---|---|
| T-2 remount loop | **Critical, introduced** | Fixed, with a regression test (`useRBAC.tenant-loop.test.tsx`, real hook and provider) that fails on the previous commit; the re-review confirmed it closed. |
| T-1 read the process database | Medium, introduced | Fixed: `AuthorizationService(db=None)` is injectable, and the unit test injects its own fake. |
| User list (`build_user_query`) still turned a project role into organisation-wide access | Medium, pre-existing | Fixed (see below). |
| `can()` used profile role names | Low, UI | Fixed: the server list decides alone. |
| Malformed index direction answered 500 | Low | Fixed: text and hashed directions answer 503. |
| DEF-4 design inaccurate in places | — | Corrected (see below). |
| Re-review: a failed background re-read showed the sign-in form | Low | Fixed: the last good answer is kept and the error recorded; a failed account change still denies. |
| Re-review: membership rows with an ObjectId `project_id` dropped from the user list | Low, fail-closed | Fixed, with a test. |

Details:

- **Remount loop.** `TenantProvider` announced its scope on mount. `useRBAC` cleared to loading, `ProtectedRoute` unmounted the layout, and the provider remounted. The reviewer reproduced over a million `/me` calls in 20 ms. Now:
  - only an account change (`auth-state-changed`) clears;
  - profile and scope changes re-read in the background, keeping the current answer;
  - the provider announces only real changes.
- **User list.** It now gets the same bound as the single-user read: users listed on, or holding an active membership row for, the actor's active projects in their organisation. No project means no users. Proof in `I/test_user_access_project_memberships_mongo.py`, including ObjectId-stored rows; the old list code fails it.
- **DEF-4 design corrections:**
  - routes scoped by role name rather than permission (`/subscriptions`, `/plan-settings`, `/plan-settings/effective-services`);
  - `/billing/summary`;
  - billing names are already non-delegable;
  - rollback is a release, not a toggle;
  - the wiring for `billing.record.view`;
  - step 4 now covers every route, with the open decisions named.

Still open, documented, not changed:

| Item | Severity |
|---|---|
| Consumers outside `can()` still check `roles.includes("superadmin")` for platform pages; the backend enforces | Low |
| `ProtectedRoute` shows the sign-in form when the first `/me` fails | Low |
| `session-api`'s shared profile cache can be overwritten by an older response | Low, pre-existing |
| The `skill_bindings` and `skills` unique indexes are not gated | Low, pre-existing |
| `bench_fakes` advertises an index it does not enforce | Low, test-only |
| The client alias helpers are now unused | Info |
| Admin-made role changes do not notify the affected user's open client; the server enforces immediately, and re-login is forced by the authority-change floor | Low |

**End-to-end evidence (mocked Playwright suites in CI).**

- **Run 44562598556 on `00a1224`:** 27 E2E failures. This was the remount loop; CI caught it independently of the review.
- **Runs 44564993286 (`0292c7a`) and 44566844854 (`e922566`):** after the loop fix, 15 failures remained in three specs (`letter-bench`, `contract-workflows`, `correspondence-linking`).
  - **Cause:** their mocked `/me` returned `roles: ["superadmin"]` without `permissions`.
  - **Why that broke them:** the real `/me` lists Super Admin as `["*"]` (`get_effective_permission_names`), and since T-2 the client decides from that list alone, no longer from role names.
  - **Fix:** the three mocks now return the server's contract (`permissions: ["*"]`).
- **Locally:** the full mocked set passes (78 passed; the 59 skipped are deployed-stack suites).

Backend in both runs: 8,431 unit tests and 1,070 real-MongoDB tests (80 modules) passed.

**CI (exact head).** CI run 44570828567 on `14b695b`: backend (8,431 unit; real-Mongo 80 modules, 1,070 passed, every module ran), frontend (1,014 unit; 78 mocked E2E passed), dependency scan and Docker green; secret scan red (same two findings). Fourth-pass stages: implemented, tested and CI-verified; **not** security-approved (authorised review outstanding) and **not** production-accepted.


## DEF-4 owner acceptance and security follow-ups (2026-11-20, sixth pass)

The owner instructions are restated in `docs/DEF4_BILLING_PERMISSION_SEPARATION.md` §11, which also holds the details, the changed flag-off matrix cells and the review table.

**Summary of security-relevant changes:**

- **M4 (`8a45f3b`): global pricing.**
  - Before: a tenant billing admin could edit every tenant's plans and add-ons, because `PolicyService` allowed `billing.plan.manage` unscoped.
  - Now: catalogue writes need Super Admin, or `platform.pricing.manage` held by name on a platform-scoped role. Such a role applies only to a user bound to no organisation.
  - No tenant can obtain it: `platform.*` is non-delegable, and only a Super Admin can put it on a role.
  - The advertised list and the client gating follow the same rule.
  - Every writer is audited with before/after.
- **L3 (`01cdb29`, `e633a0f`): subscription scope integrity.** Covered under DEF-4 §11.
- **Decision 4 (`5ccd918`, `8517f27`, `d0eb1db`): minimum disclosure.**
  - A caller without organisation-level standing gets the operational view only: status, boolean feature indicators and expiry.
  - With the flag off, the organisation financial routes are refused to such a caller, though the legacy alias still grants the name.
  - Raw override values never pass through the feature-status endpoint.
  - The tenant billing admin no longer sees every organisation's service state.
- **M1 (`10714db`): built-in roles are global.** Audit, P-05 reading and tests only. Five exposures are pinned (E1 to E5) and the holding boundaries have negative tests. The remediation is a separate, approved, High-priority workstream (`docs/M1_BUILT_IN_ROLE_SCOPING.md`); no migration has been run.

**Not changed, by instruction:**

- `BILLING_EXPLICIT_PERMISSIONS` stays off, and the aliases are unchanged.
- No production role, user or membership is changed.
- No deployment, no merge, no rollout.
- PR #114 stays a draft, and the skill packs stay unpublished.

**Open, Low:**

- unknown plan or add-on codes reseed defaults on read, unaudited (pre-existing);
- `has_permission("platform.pricing.manage")` ignores scope;
- the client billing-catalogue route also admits `system:admin` (visibility only);
- L3 silently drops other scope spellings instead of refusing them.

**Review and CI:** the independent re-review and CI on the exact head are recorded in the DEF-4 doc §11.


## DEF-4 security closure verification (2026-11-20, seventh pass)

Full record: `docs/DEF4_BILLING_PERMISSION_SEPARATION.md` §12.

**Verdicts:**

- **N1 holds.** Verified in both flag states, through the routes and through `PolicyService`.
- **`platform.pricing.manage` is consistent.** The six evaluators agree for eight personas, and only Super Admin or a platform role can write the global catalogue.

**Independent review of the complete DEF-4 work** found no Critical or High issue:

| Finding | Severity | Status |
|---|---|---|
| F1: `/plan-settings` failed open when no organisation resolved (cross-tenant, financial) | Medium | Fixed in `a8393ce` |
| F2: effective-services widening | Low | Fixed in `a8393ce` |
| F4: flag-on N1 inconsistency | Low | Fixed in `a8393ce` |
| F5: test gaps | — | Closed |
| F3: `package_id` changeable on `PUT` | Low/Medium, pre-existing | Owner decision |

**CI-observed failure:** the contract-lease failure was a test defect. It was reproduced deterministically and fixed in `9a47109`. **Not security approval.**


## F3 package scope (owner decision 2026-11-20, eighth pass)

Full record: `docs/DEF4_BILLING_PERMISSION_SEPARATION.md` §13.

**Why it mattered.** `package_id` is matched by entitlement resolution, so changing it on an ordinary update moved the entitlement. It is now protected scope alongside organisation and project:

- every caller is refused, Super Admin included;
- all spellings are refused, and explicit null is refused;
- the write is compare-and-set on all three fields.

**Writers audited:** only `update_subscription` can change it on an existing subscription.

**Not implemented:** a package transfer. It is designed in §13 and needs separate approval.

**CI on `652d4e6` (run 44733660126)** verified F1, F2, F4 and F5: green except Gitleaks.

**Remaining gaps (recorded, not changed):**

| Gap | Severity |
|---|---|
| Package not validated at subscription creation | Low, pre-existing |
| Entitlement lookup ignores `status` | Info, pre-existing |
| M1 | High workstream |
| Reseed-on-read | Low |
| `system:admin` on the client catalogue route | Low, visibility only |

**Not security approval.**

## Subscription security remediation (owner decisions 2026-11-20, ninth pass)

Full records: `docs/DEF4_BILLING_PERMISSION_SEPARATION.md` §14 (entitlement resolution) and §15 (gateway identity). Two remediations, reviewed separately. **Nothing here is security approval or production acceptance.**

### F3 completion evidence (recorded as given; not approval)

CI run 44765102151 on `f09d9be`: backend 8,435 passed; real MongoDB 1,132 passed across 87 modules, none skipped; frontend, dependency scan and Docker build/scan passed; **Gitleaks failed** on its 2 known findings (security-review dependency). F3 is implemented, locally tested and CI-verified; it is not security-approved and not production-accepted.

### Gateway subscription id: protected payment identity

**Commits:** `da322ae`, `7cabfdf`, `f64ac9f`; migration `20261124_0001` (Gate 6 fresh install 33/33, run `gate6-20261120t174311z-13219`, sandbox-CA caveat recorded in `docs/GATE_6_FRESH_INSTALL_EVIDENCE.md`). Not run against any production copy.

- **Writers audited:** ordinary `POST`/`PUT /subscriptions` (refused for every caller, every spelling, unknown keys 400); checkout (the only binder: refuses an id already bound, 409, and records provenance); the webhook (reads, never binds).
- **Webhook:** canonical provider allowlist (404 otherwise); signature; body-hash idempotency (unique index); exactly one checkout-bound subscription of this provider; customer and notes ownership; event order with a baseline; activation allowlist (`pending`, `trial`, `active`, `past_due`); payment dedupe (unique index); refused payments still recorded with a redacted payload.
- **Re-binding:** designed only (§15); not implemented.
- **Tests:** 26 real-Mongo (negative HTTP, cross-organisation, replay under a new event id, concurrency, mutation); 20 of 21 mutants killed, the survivor equivalent.
- **Independent reviews:** F1 to F6 and R1 to R4 fixed (F1, F3, F5, R1, R3, R4 introduced; F2, F4, F6, R2 pre-existing); R5 (migration blocks later migrations while duplicates exist) intended and documented.
- **Still open:** legacy bindings without provenance are trusted by the webhook (P-05 counts them; owner decision to classify and refuse unverified ones).

### Entitlement resolution: deterministic, status-aware selection

**Commits:** `5740b06`, `53c78e6`, `d37824e` (supersession withdrawn; latest activation governs), `b9ab8b3`, `b51de70`, `6fc0705`.

- **Rule:** package > project > organisation; eligible / restricted (archive read-only, offboarding export-only) / blocking (no fallback) / unknown and invalid period (deny) / not governing (pending, cancelled, expired trial, not started, ended: never shadow). A cancelled project subscription falls back to the organisation. Several governing rows: latest `governing_since` (stamped only on becoming governing); all unstamped: `ambiguous_subscriptions`, fail closed. The selected subscription and reason are in the policy audit.
- **Lifecycle guard:** plan, period and add-on changes only on an `active`/`pilot`/`trial` row that enforcement itself selects; compare-and-set on the decided state; 409 before the gateway otherwise.
- **Tests:** `test_entitlement_resolution_mongo.py`, 38 real-Mongo (competing and overlapping subscriptions, cancellations, trial expiry, archive/offboarding, package precedence, 10-round concurrency, stale writes, HTTP 409, P-05). Mutants: 17/17, 6/6, 9/9 and 7/8 (one equivalent) across the passes.
- **Three independent reviews** (findings, origin, resolution in §14): F1 to F8 (F1 High introduced, fixed by redesign; F7 display selector recorded); R1 to R4 (supersession harmful, withdrawn); N1 to N5 (N1 Medium introduced, fixed; N3 host clock and N4 older plan resurfacing recorded); R6-1 to R6-9 (R6-1/2/3/9 introduced, fixed; R6-4 pre-existing Medium, fixed; R6-5 to R6-8 pre-existing, open for owner decision).

### Coverage asked for in the review

| Area | Gateway identity | Entitlement resolution |
|---|---|---|
| Cross-tenant | Binding another organisation's id refused; webhook ownership checks; 409/404 without foreign data | Resolution filtered by organisation; guard and 409 limited to the row's own scope |
| Role combinations | Refusal for every caller incl. Super Admin; checkout under billing permission + step-up | Routes unchanged: billing permission + step-up; DEF-4 matrix suites |
| Revoked permissions | Existing DEF-4 and SEC suites (permission re-read per request) | Same; no new grant paths |
| Stale writes | Compare-and-set on scope and gateway id; checkout `DuplicateKeyError` | Compare-and-set on scope, gateway id and decided lifecycle state |
| Webhook replay | Event id and body hash (unique index), order baseline, payment dedupe | Activation stamps only from a non-governing state |

**Verification evidence (ninth pass):**

- **CI run 44786462934 on `51c192e`** (gateway code, entitlement to `d37824e`, Gate 6 pins 33): backend 8,436 passed, 1,278 skipped, 4 xfailed; exhibit confinement 20; real MongoDB "all 89 module(s) ran: 1183 test case(s)", 1,183 passed; frontend (lint, unit, mocked Playwright, build), dependency scan and Docker build/scan passed; Gitleaks failed on the 2 known findings.
- **CI run 44789094291 on `b51de70`** (all remediation code): backend 8,436 passed, 1,290 skipped, 4 xfailed; exhibit confinement 20; real MongoDB "all 89 module(s) ran: 1195 test case(s)", 1,195 passed; frontend, dependency scan and Docker build/scan passed; Gitleaks failed on the 2 known findings.
- **Local, quiet worktree at `6fc0705`** (real Redis on 6390, replica set on 27018): real MongoDB 89 CI modules, 1,196 passed, 0 failed, 0 skipped; full backend suite 9,616 passed, 375 skipped, 4 xfailed, 0 failed. `pre-commit run mypy --all-files` passed; ruff unchanged from the previous head on every touched file.
- **Final code, CI run 44791457632 on `0cb99f0`** (same code as `6fc0705`; later commits are docs-only): backend 8,436 passed, 1,291 skipped, 4 xfailed; exhibit confinement 20; real MongoDB "all 89 module(s) ran: 1196 test case(s)", 1,196 passed, none skipped; frontend (lint, unit, mocked Playwright, build), dependency scan and Docker build/scan passed; **Gitleaks failed** on the 2 known findings.
- CI success is not security approval.

### Remaining security findings (open)

| Finding | Severity | Origin | Owner |
|---|---|---|---|
| R6-5 trial conversion without payment | Low-Medium | Pre-existing | Owner decision |
| R6-6 reactivation ignores period; double billing | Low | Pre-existing | Owner decision (with R2 double billing) |
| R6-7 plan settings cancel without gateway cancel | Low | Pre-existing | Owner decision |
| R6-8 `PUT` changes plan fields unguarded | Info | Pre-existing | Owner decision |
| R6-3 residual: gateway moved, local write refused | Low | Introduced window (narrowed) | Re-billing design |
| N3 host-clock stamps | Low | Introduced | Recorded |
| N4 older plan resurfaces after cancelling the newest | Low | By design | Listing should mark the governing row (with F7) |
| Legacy gateway bindings without provenance | Medium until classified | Pre-existing | Owner decision after P-05 |
| Gitleaks (2 historical findings), picomatch R5 | - | Pre-existing | Authorised security review |
| M1 built-in role scope | High workstream | Pre-existing | Approved ticket |

### Production impact (nothing deployed, migrated or re-granted)

- Scopes with two or more unstamped governing rows deny (`ambiguous_subscriptions`) until resolved; P-05 `subscription_security_impact` counts them per organisation.
- A past-due or paused project still denies without falling back; rows with string or unreadable dates are governed by their real dates or denied.
- Plan, period and add-on changes on non-governing, blocked or restricted rows now answer 409.
- Migration `20261124_0001` raises (and blocks later migrations) while duplicate gateway ids, webhook bodies or payment records exist: read P-05 first.
- Gateway-bound subscriptions created through the old `PUT` remain trusted until classified.

## Subscription lifecycle security closure, R6-5 to R6-8 (owner decision 2026-11-21, tenth pass)

Full record: `docs/DEF4_BILLING_PERMISSION_SEPARATION.md` §16. **Nothing here is security approval or production acceptance.** Nothing was deployed, migrated, re-granted, rebound, refunded or cancelled at any gateway; no payment operation was run.

### Baseline (recorded accurately)

- **Final-code CI of the previous pass:** run 44791457632 on `0cb99f0` - backend 8,436 passed; real MongoDB 1,196 passed across 89 modules, none skipped; frontend, dependency scan and Docker passed; **Gitleaks failed** on its 2 known findings. Later docs-only heads (`11295e7`, `9e1a6bb`) carried the same code.
- **Gate 6:** fresh install 33/33 for migration `20261124_0001` (run `gate6-20261120t174311z-13219`, image with one sandbox proxy-CA layer, recorded in `docs/GATE_6_FRESH_INSTALL_EVIDENCE.md`). It is fresh-install evidence only, **not** production migration evidence. No migration was added in this pass (the new collections are unique by `_id`), so the 33-migration pin stands.

### Verdict per ticket (evidence: each defect reproduced on real MongoDB at the commit before its fix)

| Ticket | Commit(s) | Verdict |
|---|---|---|
| R6-8 `PUT /subscriptions/{id}` | `1b731f6`; sibling create route `53ed2a6` (F1) | **Fixed.** Allowlist only (`admin_note`, `cancellation_reason`; overrides Super Admin); every commercial, lifecycle, identity, scope and precedence field refused under any spelling; direct creation Super Admin only and never "paid" |
| R6-5 trial conversion | `4a3e6ce`; `53ed2a6` (F2-F4); `b7165f0` (R5-1..3); `5255dd5` (A3/A4); `918b41f` (R9-2/3/8/9) | **Fixed.** Running, governing trials only; priced plans through payment-confirmed checkout; free plans `not_billed`; no-charge conversion Super Admin with reason and audit; trials bounded, one per scope, never over a subscription |
| R6-6 reactivation | `58455e7`; `b7165f0` (R8-1, R6-2/3); `5255dd5` (B1, C6); `918b41f` (R9-7) | **Fixed.** Cancelled, in-period, gateway-confirmed, nothing else governing or pending; never a trial; unbilled or administratively cancelled ones Super Admin only; under the scope lease |
| R6-7 gateway-consistent cancellation | `5f95334`; `b7d59a9` (R7-1..5); `5255dd5` (C1-C5) | **Fixed.** Durable operation with idempotency key, scope lease with per-attempt fencing, gateway cancellation before local, review queue and retry, provider reconciliation |
| Gateway/database consistency (item 6) | `80186b7`; `b7d59a9` (G-1..3); `5255dd5` (A1/A2, B2/B3, C3); `918b41f` (R9-4/5/6/10) | **Fixed within scope.** Lifecycle changes are durable operations; unknown gateway outcomes listed; in-place plan change only where the gateway can make it; checkout serialised per scope. **Checkout replacement not implemented** (needs separate authorisation, §16.7) |
| Legacy bindings / P-05 | `a4cd266` | **Implemented, read-only;** counts not yet measured on production |

### Independent reviews (agents, not the authorised security review)

Review 7 (cold, given the code): 4 pre-existing payment bypasses (F1 Critical, F2-F4 High) and 13 introduced or adjacent defects. Review 8: 2 further High pre-existing paths (A1 in-place upgrade on Razorpay, A2 unbilled plans) and 11 others. Review 9: none High; 10 Medium/Low fixed, 2 Info recorded. Tables with origin (introduced / pre-existing) in DEF-4 §16.8-16.10.

### Coverage asked for

Real HTTP and real MongoDB throughout; concurrent requests (same and different keys, checkouts, trials, conversions, reactivations, plan settings); cross-tenant and revoked permissions (403); gateway failures, timeouts and unknown outcomes; signed-webhook replay; audit and history assertions; mutation testing per fix (all mutants killed except two recorded as equivalent).

**Exact-head CI for this pass:** run 44822992420 on `918b41f` (the final code; later commits `897fe22`, `8fe6e4b` and this one are docs only): backend 8,437 passed, 1,365 skipped, 4 xfailed; exhibit confinement 20; real MongoDB "all 99 module(s) ran: 1271 test case(s)", 1,271 passed, none skipped; frontend (lint, unit, mocked Playwright, build), dependency scan and Docker build/scan passed; **Gitleaks failed** on its 2 known findings. **Local, quiet worktree at `918b41f`:** real MongoDB 1,271 passed after the local Redis (which had stopped mid-run; 8 Redis-backed tests failed with connection refused) was restarted and `test_security_redis_mongo.py` rerun (11/11); full backend suite 9,683 passed + the same 8 re-run; frontend lint clean, 1,017 passed (3 skipped), build passed; mypy staged gate passed; ruff unchanged on every touched file. CI success is not security approval.

### Remaining payment-integrity risks (open)

| Risk | Severity | Owner |
|---|---|---|
| Checkout over a paid plan bills both until the old one is cancelled (warning returned; replacement design §16.7 not implemented) | Medium | Owner authorisation of the replacement workflow |
| Amount mismatch on renewal marks billing for review but does not stop service | Medium | Owner decision (webhook behaviour) |
| Legacy gateway bindings without provenance are still trusted by the webhook until classified | Medium until measured | P-05 then owner decision (§16.6) |
| Cancelled gateway-bound subscriptions without gateway evidence (pre-R6-7 plan settings) may still bill | Unknown until measured | P-05 then provider reconciliation |
| Razorpay cycle-end cancels are not recognised by reconciliation (R9-11) | Info | Adapter work |
| Gateway adapters have no request timeout (a hang beyond the 60 s lease is refused locally and listed, not prevented) | Low | Configuration |
| Gitleaks 2 historical findings; picomatch R5 | - | Authorised security review |

### Production impact still awaiting measurement (P-05 `payment_integrity_impact`, read-only)

Gateway bindings by class (verified checkout / verified reconciliation / conflicting / unverified); duplicate payment groups (block the migration's index); payments refused for review; overlapping governing scopes and gateway-bound overlaps; cancelled bound subscriptions without gateway evidence; governing rows without activation dates; operations needing review; conversions awaiting payment; no-charge conversions. Plus behaviour changes for tenants: in-place plan changes on Razorpay-bound subscriptions now 409 (checkout instead); unbilled plans changed only by Super Admin; one trial per scope.


## Payment integrity closure and M1 shadow preparation (owner instructions 2026-11-21, eleventh pass)

Full record: `docs/DEF4_BILLING_PERMISSION_SEPARATION.md` §17 and `docs/M1_BUILT_IN_ROLE_SCOPING.md` §8. **Nothing here is security approval or production acceptance.** No real payment provider was called; nothing was deployed, migrated, rebound, refunded or cancelled at any provider; no role was migrated or re-granted. `BILLING_EXPLICIT_PERMISSIONS=false`, Claim Bench rollout off, skill packs unpublished, Phase 5 blocked.

| Item | Flag (default) | Implemented | Locally verified | CI-verified (exact head) | Security-approved | Production-accepted |
|---|---|---|---|---|---|---|
| WS4 provider reliability (timeouts, outcome_unknown, durable checkout, cycle-end reading) | always on | Yes (`8b6733e`, `71402c3`) | Yes | Yes: run 44895171828 on `1b77419` (final code; Gitleaks red, known) | No | No |
| WS1 checkout replacement | `CHECKOUT_REPLACEMENT_ENABLED=false` | Yes, inactive (`f40a89f`, `71402c3`) | Yes | Yes: run 44895171828 on `1b77419` (final code; Gitleaks red, known) | No | No (activation needs owner approval; Razorpay scheduled start unverified) |
| WS2 payment amount mismatch policy | `PAYMENT_MISMATCH_POLICY=off` | Yes, inactive (`21ac55c`, `71402c3`) | Yes | Yes: run 44895171828 on `1b77419` (final code; Gitleaks red, known) | No | No (grace length and activation need owner approval) |
| WS3 P-05 legacy bindings and old cancellations | read-only | Yes (`9c64db2`, `71402c3`) | Yes | Yes: run 44895171828 on `1b77419` (final code; Gitleaks red, known) | No | Not run on production |
| WS5 M1 shadow evaluator | `BUILT_IN_ROLE_ASSIGNMENTS=off` (`on` refused) | Shadow only (`e661b92`, `71402c3`) | Yes | Yes: run 44895171828 on `1b77419` (final code; Gitleaks red, known) | No | No (M1 approval) |

### Verdict per workstream (each defect or gap reproduced on real MongoDB at the commit before its change)

| Workstream | Verdict |
|---|---|
| 4 Provider reliability | **Implemented (always on).** Bounded, classified provider calls; `outcome_unknown` reconciled with the same operation; no second create after a timeout under any key; abandoned checkouts never cancelled on an unreadable provider; R9-11 resolved by our own durable record of the confirmed cycle-end request and a read-only reconciliation |
| 1 Checkout replacement | **Implemented, inactive.** Exact source identity; scheduled start required (Razorpay unverified, so refused); source cancelled at cycle end only on the provider's confirmation of the target; no gap or overlap in entitlement; review on any disagreement; no refund |
| 2 Amount mismatch | **Implemented, inactive.** Verified initial activation; renewal financial review with a grace anchored at the first discrepancy; blocking after the grace; Super Admin settlement; nothing granted from unverified payment |
| 3 Legacy bindings and old cancellations | **Implemented (read-only reading).** Counts by organisation and category; remediation sequence proposed (DEF-4 §17.4), not executed |
| 5 M1 shadow evaluator | **Implemented, shadow only.** Responses unchanged; privacy-safe divergence counts; `on` refused |

### Independent reviews

Review 10 (cold, given the code): 17 findings - 1 High (R10-1, latent), 4 Medium (R10-2 to R10-4; R10-2 a pre-existing failure mode), 12 Low/Info. All fixed in `71402c3` except R10-17 (recorded). Further reviews: review 10 (cold) and re-reviews 11 to 14 - every finding reproduced and fixed (`71402c3`, `43ef3e0`, `fdef504`, `7420456`, `1b77419`); re-review 14 found nothing Medium or higher in a UTC deployment (DEF-4 §17.7-17.11).

### Findings by origin

- **Introduced by this pass and fixed:** R10-1, R10-3 to R10-16 (DEF-4 §17.7).
- **Pre-existing, fixed or closed here:** R10-2 (interrupted purchase); the unresolvable-price activation (closed only under `PAYMENT_MISMATCH_POLICY=enforce`); `subscription.authenticated` treated as an activation (R10-12); R9-11.
- **Residual (open):**

| Risk | Severity | Owner |
|---|---|---|
| Razorpay SDK absent from the backend requirements; the installed SDK needs `pkg_resources` | Blocks `PAYMENT_PROVIDER=razorpay` | Dependency change with review |
| Razorpay `start_at` (scheduled start) behaviour unverified; replacement refused for Razorpay until verified | - | Razorpay test mode, then a reviewed change |
| Razorpay answer to a second cycle-end cancel undocumented (sent to review) | Low | Provider confirmation |
| With every flag off, checkout over a paid Razorpay plan still bills both until the old one is cancelled (warning) | Medium | Owner activation of WS1 (after the Razorpay verification) |
| With the policy off, renewal mismatch marks review without a time limit; an unresolvable price still activates | Medium | Owner activation of WS2 with a grace length |
| Legacy bindings and pre-R6-7 cancellations unmeasured on production | Unknown until measured | P-05 + reconciliation, then the §17.4 sequence |
| M1: built-in roles reach every organisation of the user; shadow covers the policy gate only | High (pre-existing) | P-05, security review, owner approval |
| Gitleaks 2 historical findings; picomatch R5 | - | Authorised security review |

**CI:** exact-head run 44895171828 on `1b77419` (the final code; later commits are docs only): backend 8,485 passed, 1,461 skipped, 4 xfailed; exhibit confinement 20; real MongoDB "all 107 module(s) ran: 1367 test case(s)", 1,367 passed, none skipped; frontend (lint, unit, mocked Playwright, build), dependency scan and Docker build/scan passed; **Gitleaks failed** on its 2 known findings. **Local at `1b77419`:** backend unit suite 8,458 passed, 0 failed (135 skipped, 4 xfailed); payment, M1 and P-05 suites on real MongoDB 359 passed; mypy staged gate passed; ruff unchanged on every touched file (one pre-existing finding in `monetization_service.py`). CI success is not security approval.

## M1 remediation: built-in roles scoped to their assignment (2026-11-25)

**Authority.** The security owner's decision S-4 (recorded in the private review workspace) rejected M1 for production release and approved its remediation for implementation and verification. The owner directed the fix to start the same day.

**What changed.**
- **The flag.** Enforcement mode `BUILT_IN_ROLE_ASSIGNMENTS=on` is now implemented. The default stays `off`, and with the flag `off` or `shadow` every decision is unchanged.
- **The rule.** With `on`, a tenant built-in role counts only in the user's primary organisation, or where an explicit `role_assignments` row puts it.
- **Coverage.** The rule is applied at:
  - the permission grants;
  - the scope service's organisation-wide and membership checks;
  - billing standing;
  - checkout;
  - effective-services widening;
  - the `AuthorizationService` tiers;
  - role visibility;
  - search;
  - the perspective administrator check;
  - legacy reviewer keys.
- **Data.** Nothing is written, migrated or activated.

Full record: `docs/M1_BUILT_IN_ROLE_SCOPING.md` §9. Evidence: `tests/integration/test_m1_enforcement_mongo.py`, where M1-E1 to M1-E5 are refused with the flag `on`, and the primary organisation's answers are identical to the answers with the flag `off`.

**Status.**

| Implemented | CI-verified | Security-approved | Production-accepted |
|---|---|---|---|
| Yes, behind the flag (`e44fd79`; tests strengthened after mutation, see below) | Yes on `e44fd79` and `e2f7ded` (backend, real MongoDB, frontend, dependency scan, Docker); Gitleaks red on its 2 known findings only. Final commit: see the verification addendum | No: needs the security owner's review of this code | No: the M1 §5 plan (P-05, assignment decisions, grant before revoke, staging `shadow` then `on` with two deployed passes, release decision) is still open |

**Residual.**
- The shadow evaluator covers the policy gate only.
- The `AuthorizationService` tiers do not honour explicit assignment rows, so they fail closed.
- The letter-template creation path found in the audit was confirmed and fixed (addendum below).

### M1 verification addendum (owner instructions 2026-11-25, second round)

**Provisional acceptance.** The owner accepted the M1 implementation at `e44fd79` provisionally, subject to the verification below. **M1 closure: Pending. Production activation: Not approved.**

**New finding, fixed: cross-tenant letter-template creation (High, pre-existing).**
- **Confirmed through the real API at `e44fd79`.** An Organization Admin of A, with no membership in B, created an organisation template in B (200). A Project Admin created a template on its own project labelled with organisation B (200).
- **Fix (`e2f7ded`).** Before a template is stored, its project must belong to its organisation (for every caller), and the write rule of `check_letter_template_access` must pass for the new scope.
- **Tests.** `test_letter_template_tenant_isolation_mongo.py` (2) fail before the fix and pass after. They cover create, read, update, delete, list and a filter naming B; a Super Admin control reads B's templates.
- **Mutation.** 3 of 3 mutants on the fix are caught.
- The router has no duplicate, share or publish route.

**Rate-limit 429 (`test_m4_global_pricing_mongo`).** Not dismissed on isolation. Root cause: in-process rate-limit counters (no Redis) shared across modules for fixed seeded user ids. Reproduced by ordering two modules; fixed in the test environment only (`_env` clears the counters). Production limits unchanged.

**Local suites at `e2f7ded`.**
- Backend unit suite: 8,461 passed, 0 failed, 135 skipped (environment services absent), 4 xfailed.
- Real MongoDB, run as in CI: 1,381 passed, 0 failed, 0 skipped; all 109 modules ran.

**Pre-existing gap surfaced by the xfail review (for the security owner).** `decode_oidc_id_token` accepts an id_token with no `exp`. It is pinned by a strict xfail and is not changed here.

**Mutation testing: 24 mutants over every new authorisation check.**
- First valid run: 15 caught, 9 survived. The survivors were gaps in the tests, not the code. The most material: the M1 cache test never reached the cache, because no runtime Redis was configured. It now runs on a real Redis and asserts the cached entry and its mode.
- Tests were added or tightened for each survivor (M1 §9, "Mutation testing").
- **Final result at `8809a12`: 24 of 24 caught** (21 M1, 3 letter-template).

**Separate statuses.**

| Item | Status |
|---|---|
| Implementation | Done behind `BUILT_IN_ROLE_ASSIGNMENTS` (default `off`) |
| CI | Green except Gitleaks (2 known findings) on `e44fd79` and `e2f7ded`; final commit pending |
| Production evidence (P-05, R-PAY, P-01 to P-16) | Pending: no operator or host access |
| Staging passes | Pending: no staging owner or environment named |
| Security approval | Pending (security owner) |
| Legal approval | Pending (unchanged; JG-5(f)) |
| Owner acceptance | Provisional for the implementation; M1 closure Pending |

