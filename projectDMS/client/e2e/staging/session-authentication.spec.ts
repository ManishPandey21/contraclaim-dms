/**
 * Gate 3 bullet 1: login, logout, session refresh, and CSRF behaviour.
 *
 * Against a deployed stack, unmocked. Every contract asserted here is read from
 * the server's own source rather than guessed:
 *
 *   `GET  /api/csrf-token`  issues `cc_csrf_token` and returns it in the body
 *                           (`routers/auth.py:499`)
 *   `POST /api/login`       sets the auth cookie and a CSRF cookie
 *                           (`routers/auth.py:446`, `core/csrf.py:47`)
 *   `GET  /api/me`          401 without the cookie, 200 with it
 *   `POST /api/refresh`     re-issues the session
 *   `POST /api/logout`      clears both cookies
 *   unsafe + auth cookie    403 "Missing CSRF token" with no header, 403
 *                           "Invalid CSRF token" with a mismatched one
 *                           (`core/csrf.py:90`)
 *
 * The browser half uses only selectors an existing unmocked spec already proves
 * exist on the deployed login page (`login-responsive.spec.ts`).
 *
 * EXECUTED AGAINST A DEPLOYMENT in R-A8Q Stage B, 2026-09-08: 4/4 passed,
 * chromium, unmocked, over TLS, under `CONTRACLAIM_STAGING_E2E=1`. The refresh
 * half was measured past this file's own assertions - one `token_refresh` audit
 * row, its user resolving to the signed-in account, its `resource_id` equal to
 * the `session_id` in the reissued token. Gate 3 bullet 1 is ticked on that run.
 */
import { expect, test } from "@playwright/test";

import { BASE_URL, CREDENTIAL_VARS, requireStagingEnvironment } from "./staging-target";

const AUTH_COOKIE = process.env.E2E_AUTH_COOKIE_NAME?.trim() || "cc_access_token";
const CSRF_COOKIE = "cc_csrf_token";
const CSRF_HEADER = "x-csrf-token";

test.describe("Gate 3 bullet 1 - session and CSRF against a deployment", () => {
  test("an unauthenticated session is refused, and the login page is served", async ({
    page,
    request,
  }) => {
    requireStagingEnvironment();

    const me = await request.get("/api/me");
    expect(me.status()).toBe(401);

    await page.goto("/login");
    await expect(
      page.getByRole("heading", { name: "Welcome back to your contract record." })
    ).toBeVisible();
    await expect(page.getByLabel("Work email")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  });

  test("signing in through the form establishes a session", async ({ page, context }) => {
    const env = requireStagingEnvironment(...CREDENTIAL_VARS);

    await page.goto("/login");
    await page.getByLabel("Work email").fill(env.E2E_STAGING_EMAIL);
    await page.getByLabel("Password", { exact: true }).fill(env.E2E_STAGING_PASSWORD);
    await Promise.all([
      page.waitForResponse(
        (response) => response.url().includes("/api/login") && response.request().method() === "POST"
      ),
      page.getByRole("button", { name: "Sign in" }).click(),
    ]);

    await expect(page).not.toHaveURL(/\/login$/);

    const cookies = await context.cookies();
    const auth = cookies.find((cookie) => cookie.name === AUTH_COOKIE);
    expect(auth, `no ${AUTH_COOKIE} cookie after sign-in`).toBeTruthy();
    // The production posture, asserted where it is actually served from rather
    // than in the config file that claims it.
    expect(auth?.httpOnly).toBe(true);
    if (BASE_URL.startsWith("https://")) {
      expect(auth?.secure).toBe(true);
    }
  });

  test("an authenticated session reads its own user, refreshes, and logs out", async ({
    request,
  }) => {
    const env = requireStagingEnvironment(...CREDENTIAL_VARS);

    const csrf = await request.get("/api/csrf-token");
    expect(csrf.ok()).toBeTruthy();
    const csrfToken = (await csrf.json()).csrf_token as string;
    expect(csrfToken).toBeTruthy();

    const login = await request.post("/api/login", {
      headers: { [CSRF_HEADER]: csrfToken },
      data: { email: env.E2E_STAGING_EMAIL, password: env.E2E_STAGING_PASSWORD },
    });
    expect(login.ok(), `login failed with ${login.status()}`).toBeTruthy();

    const me = await request.get("/api/me");
    expect(me.status()).toBe(200);

    const cookies = await request.storageState().then((state) => state.cookies);
    const sessionCsrf = cookies.find((cookie) => cookie.name === CSRF_COOKIE)?.value;
    expect(sessionCsrf, "login did not issue a CSRF cookie").toBeTruthy();

    const refreshed = await request.post("/api/refresh", {
      headers: { [CSRF_HEADER]: sessionCsrf as string },
    });
    expect(refreshed.ok(), `refresh failed with ${refreshed.status()}`).toBeTruthy();

    // Still the same session afterwards - a refresh that logged the user out
    // would look identical in the response alone.
    expect((await request.get("/api/me")).status()).toBe(200);

    const loggedOut = await request.post("/api/logout", {
      headers: { [CSRF_HEADER]: sessionCsrf as string },
    });
    expect(loggedOut.ok()).toBeTruthy();
    expect((await request.get("/api/me")).status()).toBe(401);
  });

  test("a cookie-authenticated unsafe request without a CSRF header is refused", async ({
    request,
  }) => {
    const env = requireStagingEnvironment(...CREDENTIAL_VARS);

    const csrfToken = (await (await request.get("/api/csrf-token")).json()).csrf_token as string;
    const login = await request.post("/api/login", {
      headers: { [CSRF_HEADER]: csrfToken },
      data: { email: env.E2E_STAGING_EMAIL, password: env.E2E_STAGING_PASSWORD },
    });
    expect(login.ok()).toBeTruthy();

    const missing = await request.post("/api/refresh");
    expect(missing.status(), "an unsafe cookie request with no CSRF header was accepted").toBe(403);

    const mismatched = await request.post("/api/refresh", {
      headers: { [CSRF_HEADER]: "not-the-cookie-value" },
    });
    expect(mismatched.status(), "a mismatched CSRF token was accepted").toBe(403);

    // And the session is still usable: a rejected CSRF check must refuse the
    // request, not destroy the session behind it.
    expect((await request.get("/api/me")).status()).toBe(200);
  });
});
