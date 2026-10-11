# Sign-Up Flow Redirect Audit

Date: 2026-05-20

## Issue

After a user creation or sign-up-style onboarding action, the UI could land on the public landing page (`/`) instead of a clear authenticated destination or login recovery path.

## Flow Traced

```text
RegisterPage / UsersPage
-> enhancedApi.createOrganization/createProject/createUser
-> EnhancedApiService.request()
-> ensureValidToken()
-> backend /api/refresh or original API request
-> 401/refresh failure
-> frontend session cleanup redirect
-> previous behavior: logoutAndRedirect("/")
-> public LandingPage
```

## Root Cause

The frontend treated failed session refresh during authenticated API calls as a redirect to `/`. In this application `/` is the public landing page, so a missing, expired, or rejected auth cookie during the post-sign-up/user-creation flow looked like a successful sign-up followed by an unexpected public landing redirect.

The backend user creation and auth endpoints are separate flows:

- `POST /api/users` creates users and requires an authenticated actor with `users:create`.
- `POST /api/login` establishes the browser session cookie.
- `GET /api/me` verifies the active session.

Because user creation does not automatically authenticate the newly created user, the correct recovery destination on session loss is the login page, not the public landing page.

## Fix

Session-expiry redirects now use `redirectToLoginAfterSessionExpiry()`, which clears legacy session storage and routes to `/login`.

Updated areas:

- `client/src/services/auth.ts`
- `client/src/services/enhanced-api.ts`
- `client/src/services/api.ts`
- `client/src/services/dashboard-api.ts`
- `client/src/pages/ProjectsPage.tsx`

## Expected Behavior After Fix

1. Authenticated registration/user-creation actions continue normally when the session is valid.
2. If the session expires during the action, the app redirects to `/login`.
3. The app no longer silently routes expired post-sign-up flows to `/`.
4. Public landing remains available only through explicit navigation or logout.

## Regression Coverage

Added/updated tests:

- `client/src/tests/base_url.spec.ts`
  - Verifies expired user creation redirects through the login-session-expiry path instead of the public landing page.
- `client/src/pages/__tests__/ProjectsPage.organization-filter.test.tsx`
  - Mocks `/me` so route tests do not make live backend calls.
