# Plan Settings Page Implementation

Date: 2026-05-18

## Goal

Create a Plan Settings page for assigning DMS and Drafting service plans at organization or project level. Organization-level plans cascade to projects by default. Project-level plan selections override organization-level plans.

The Documents page must enable `Request Draft` only when the document's effective organization/project service state includes Drafting.

## Implemented Scope

### Backend

- Reuses existing `plans` and `subscriptions` collections.
- Adds a standardized `no_service_override` plan for explicit service opt-out.
- Adds plan settings APIs under `/api/rbac-monetization`:
  - `GET /plan-settings`
  - `GET /plan-settings/effective-services`
  - `PUT /plan-settings/scope`
- Adds effective service calculation:
  - project subscription wins first;
  - otherwise organization subscription is inherited;
  - no subscription means DMS and Drafting are disabled.
- Merges plan features with subscription `entitlement_overrides`.
- Enforces Drafting entitlement in `POST /documents/{id}/request-draft`.

### Frontend

- Adds `/plan-settings` route.
- Adds `Plan Settings` sidebar entry for `subscription.entitlement.manage` users and `superadmin`.
- Adds a Plan Settings page that lists organizations and associated projects.
- Supports organization plan selection.
- Supports project-level `Inherit Organization Plan`, `No Service`, and explicit plan override.
- Shows effective DMS and Drafting badges from backend effective feature state.
- Disables `Request Draft` in `DocumentsPage.tsx` when Drafting is inactive or entitlement state is still loading.

## Effective Entitlement Rules

1. If a project has an active project-level subscription, that project subscription controls effective services.
2. If a project has no active project-level subscription, the organization-level subscription controls effective services.
3. If neither project nor organization has an active subscription, both DMS and Drafting are disabled.
4. `no_service_override` disables both DMS and Drafting.
5. Subscription overrides win over plan default features.

## Acceptance Checks

- Organization-level DMS plan disables Drafting for inherited projects.
- Organization-level Drafting plan enables Drafting for inherited projects.
- Project-level DMS plan overrides organization-level Drafting and disables Drafting for that project.
- Project-level Drafting plan overrides organization-level DMS and enables Drafting for that project.
- Project-level `No Service` disables DMS and Drafting for that project.
- Backend draft request returns `403` if Drafting is inactive.
- Frontend hides/enables `Request Draft` according to effective Drafting state.
