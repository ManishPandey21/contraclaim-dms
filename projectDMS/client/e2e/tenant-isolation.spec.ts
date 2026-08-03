/**
 * Two-user tenant-isolation tests.
 *
 * These run against a REAL backend with REAL logins. Nothing here is mocked:
 * mocking the API would only prove the frontend renders what it is handed,
 * which is precisely the claim these tests exist to falsify.
 *
 * Skipped unless RUN_TENANT_ISOLATION_E2E=1 and both accounts are supplied,
 * because they need seeded data that CI does not carry.
 *
 *   Required environment
 *     RUN_TENANT_ISOLATION_E2E=1
 *     E2E_BASE_URL                  default http://127.0.0.1:5173
 *     E2E_USER_A_EMAIL / _PASSWORD  a user in organisation A
 *     E2E_USER_B_EMAIL / _PASSWORD  a user in a DIFFERENT organisation B
 *
 *   Optional -- enables the direct-object-reference tests. Set these to ids
 *   that belong to user B only.
 *     E2E_B_ORG_ID, E2E_B_PROJECT_ID, E2E_B_DOCUMENT_ID, E2E_B_LETTER_ID
 *
 *   Run
 *     RUN_TENANT_ISOLATION_E2E=1 \
 *     E2E_USER_A_EMAIL=a@example.com E2E_USER_A_PASSWORD=... \
 *     E2E_USER_B_EMAIL=b@example.com E2E_USER_B_PASSWORD=... \
 *     npx playwright test e2e/tenant-isolation.spec.ts
 */

import { APIRequestContext, Browser, BrowserContext, Page, expect, test } from "@playwright/test";

const ENABLED = process.env.RUN_TENANT_ISOLATION_E2E === "1";
const BASE_URL = process.env.E2E_BASE_URL || "http://127.0.0.1:5173";

const USER_A = {
  email: process.env.E2E_USER_A_EMAIL || "",
  password: process.env.E2E_USER_A_PASSWORD || "",
};
const USER_B = {
  email: process.env.E2E_USER_B_EMAIL || "",
  password: process.env.E2E_USER_B_PASSWORD || "",
};

const B_ORG_ID = process.env.E2E_B_ORG_ID || "";
const B_PROJECT_ID = process.env.E2E_B_PROJECT_ID || "";
const B_DOCUMENT_ID = process.env.E2E_B_DOCUMENT_ID || "";
const B_LETTER_ID = process.env.E2E_B_LETTER_ID || "";

const HAVE_CREDENTIALS = Boolean(USER_A.email && USER_A.password && USER_B.email && USER_B.password);

test.skip(
  !ENABLED || !HAVE_CREDENTIALS,
  "Set RUN_TENANT_ISOLATION_E2E=1 and both E2E_USER_A_* / E2E_USER_B_* credentials to run two-user isolation tests.",
);

/** Statuses that constitute a safe refusal. 200 is never acceptable here. */
const REFUSED = [400, 401, 403, 404, 409, 422];

async function login(page: Page, user: { email: string; password: string }) {
  await page.goto(`${BASE_URL}/login`);
  await page.getByLabel(/^work email$/i).fill(user.email);
  await page.getByLabel(/^password$/i).fill(user.password);
  await page.getByRole("button", { name: /^sign in$/i }).click();

  // A first-time account is held at the security-terms gate before the app
  // shell renders. Accept it so the tenant assertions can reach the header;
  // an account that has already accepted skips straight past this.
  const acceptTerms = page.getByRole("button", { name: /accept and continue/i });
  if (await acceptTerms.isVisible({ timeout: 10_000 }).catch(() => false)) {
    // The consent checkbox stays inert until the terms have been scrolled to
    // the end, so scroll the region first rather than clicking a dead control.
    const terms = page.getByRole("region", { name: /scrollable security terms/i });
    await terms.evaluate((el) => el.scrollTo(0, el.scrollHeight)).catch(() => {});
    const consent = page.getByRole("checkbox", { name: /i have read and agree/i });
    await consent.click();
    await expect(acceptTerms).toBeEnabled({ timeout: 10_000 });
    await acceptTerms.click();
  }

  // The header scope bar only renders once the session and tenant scope resolve.
  await expect(page.getByLabel("Selected organisation and project")).toBeVisible({
    timeout: 30_000,
  });
}

async function signedInContext(browser: Browser, user: { email: string; password: string }) {
  // A fresh context per user: separate cookies and storage, so neither can
  // inherit the other's session.
  const context = await browser.newContext();
  const page = await context.newPage();
  await login(page, user);
  return { context, page };
}

/** Read the organisation names the header actually offers this user. */
async function visibleOrganisations(page: Page): Promise<string[]> {
  const select = page.getByLabel("Select organisation");
  if ((await select.count()) === 0) {
    const locked = page.getByLabel("Organisation");
    return [(await locked.innerText()).trim()];
  }
  return (await select.locator("option").allInnerTexts()).map((text) => text.trim());
}

async function apiStatus(request: APIRequestContext, url: string): Promise<number> {
  const response = await request.get(url, { failOnStatusCode: false });
  return response.status();
}

/**
 * An API session authenticated purely over HTTP.
 *
 * The API assertions must not depend on the UI: the deployed frontend may lag
 * the branch under test, and tenant isolation is enforced by the server
 * regardless of what the client renders. Binding baseURL to E2E_BASE_URL here
 * also avoids inheriting the config's dev-server baseURL, which would silently
 * point these checks at localhost instead of the target deployment.
 */
async function apiSession(
  playwright: typeof import("@playwright/test").request,
  user: { email: string; password: string },
): Promise<APIRequestContext> {
  const context = await playwright.newContext({ baseURL: BASE_URL });
  const masked = `${user.email.split("@")[0]}***`;

  // /api/login, not /login -- the latter is a frontend route that returns the
  // SPA shell with HTTP 200 for any input, including a nonexistent user.
  const response = await context.post("/api/login", {
    data: { email: user.email, password: user.password },
    failOnStatusCode: false,
  });
  expect(response.status(), `login failed for ${masked}`).toBe(200);

  // Prove the session is real before any isolation assertion runs. Without this
  // an unauthenticated context would make every cross-tenant check pass on a
  // 401 -- a vacuous pass that looks identical to enforcement.
  const me = await context.get("/api/me", { failOnStatusCode: false });
  expect(me.status(), `session not established for ${masked}`).toBe(200);
  const profile = await me.json();
  expect(String(profile.email || "").toLowerCase()).toBe(user.email.toLowerCase());

  return context;
}

test.describe("two-user tenant isolation", () => {
  let a: { context: BrowserContext; page: Page };
  let b: { context: BrowserContext; page: Page };

  test.beforeAll(async ({ browser }) => {
    a = await signedInContext(browser, USER_A);
    b = await signedInContext(browser, USER_B);
  });

  test.afterAll(async () => {
    await a?.context.close();
    await b?.context.close();
  });

  test("each user sees only their own organisation in the header", async () => {
    const orgsA = await visibleOrganisations(a.page);
    const orgsB = await visibleOrganisations(b.page);

    const meaningful = (names: string[]) =>
      names.filter((name) => name && !/^all organisations$/i.test(name));

    const overlap = meaningful(orgsA).filter((name) => meaningful(orgsB).includes(name));
    expect(overlap, `user A sees ${orgsA.join(", ")}; user B sees ${orgsB.join(", ")}`).toEqual([]);
  });

  test("user A cannot reach user B's project through a direct URL", async () => {
    test.skip(!B_PROJECT_ID, "Set E2E_B_PROJECT_ID to run the direct-URL test.");

    await a.page.goto(`${BASE_URL}/documents?project_id=${B_PROJECT_ID}`);
    await a.page.waitForLoadState("networkidle");

    // The project must not become the active context, whatever the URL asked for.
    const activeProject = await a.page.getByLabel("Selected organisation and project").innerText();
    expect(activeProject).not.toContain(B_PROJECT_ID);
  });

  test("forged local-storage context does not grant access", async () => {
    test.skip(!B_ORG_ID || !B_PROJECT_ID, "Set E2E_B_ORG_ID and E2E_B_PROJECT_ID.");

    await a.page.evaluate(
      ([org, project]) => {
        window.localStorage.setItem("org_id", org);
        window.localStorage.setItem("proj_id", project);
      },
      [B_ORG_ID, B_PROJECT_ID],
    );
    await a.page.reload();
    await expect(a.page.getByLabel("Selected organisation and project")).toBeVisible({
      timeout: 30_000,
    });

    // TenantContext revalidates persisted ids against the profile, so the
    // forged pair must be discarded rather than restored.
    const storedOrg = await a.page.evaluate(() => window.localStorage.getItem("org_id"));
    expect(storedOrg).not.toBe(B_ORG_ID);
  });

  test("context survives a refresh and stays the user's own", async () => {
    const before = await a.page.getByLabel("Selected organisation and project").innerText();
    await a.page.reload();
    await expect(a.page.getByLabel("Selected organisation and project")).toBeVisible({
      timeout: 30_000,
    });
    const after = await a.page.getByLabel("Selected organisation and project").innerText();
    expect(after.trim()).toBe(before.trim());
  });

  test("logging in as the other user does not inherit the previous context", async ({ browser }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    try {
      await login(page, USER_A);
      const asA = await page.getByLabel("Selected organisation and project").innerText();

      // Same browser context: log out, then in as B. B must not see A's scope.
      await page.evaluate(() => window.localStorage.clear());
      await login(page, USER_B);
      const asB = await page.getByLabel("Selected organisation and project").innerText();

      expect(asB.trim()).not.toBe(asA.trim());
    } finally {
      await context.close();
    }
  });
});

test.describe("project-scoped narrowing is visible to the user", () => {
  test("changing the project does not leave the previous project's rows on screen", async ({
    browser,
  }) => {
    const { context, page } = await signedInContext(browser, USER_A);
    try {
      const projectSelect = page.getByLabel("Select project");
      test.skip(
        (await projectSelect.count()) === 0,
        "User A has no project selector (single or locked project).",
      );

      const options = await projectSelect.locator("option").all();
      test.skip(options.length < 3, "Needs at least two projects plus the All Projects option.");

      await page.goto(`${BASE_URL}/documents`);
      await page.waitForLoadState("networkidle");

      const firstValue = await options[1].getAttribute("value");
      const secondValue = await options[2].getAttribute("value");

      await projectSelect.selectOption(firstValue!);
      await page.waitForLoadState("networkidle");
      const firstBody = await page.locator("main").innerText();

      await projectSelect.selectOption(secondValue!);
      // The routed page remounts on the tenant key, so the previous project's
      // content must not still be on screen once the switch settles.
      await page.waitForLoadState("networkidle");
      const secondBody = await page.locator("main").innerText();

      expect(secondBody).not.toBe(firstBody);
    } finally {
      await context.close();
    }
  });
});

/**
 * Server-side isolation, asserted over HTTP with no UI involvement.
 *
 * These are the security-critical checks: the API enforces tenancy whatever the
 * client renders, and the deployed frontend may lag the branch under test.
 */
test.describe("two-user API isolation", () => {
  let aApi: APIRequestContext;

  test.beforeAll(async ({ playwright }) => {
    aApi = await apiSession(playwright.request, USER_A);
  });

  test.afterAll(async () => {
    await aApi?.dispose();
  });

  /**
   * Positive control. Every other assertion in this block is satisfied by an
   * empty result or a 4xx, so a session that simply cannot read anything --
   * wrong role id, missing permission, expired cookie -- would make them all
   * pass while proving nothing. This test fails in exactly that situation.
   */
  test("the session can read its OWN organisation's data", async () => {
    const response = await aApi.get("/api/projects", { failOnStatusCode: false });
    expect(response.status(), "positive control: own projects must be readable").toBe(200);
    const body = await response.json();
    const rows = Array.isArray(body) ? body : body?.items || [];
    expect(
      rows.length,
      "session returned no data at all -- the isolation assertions below would pass vacuously",
    ).toBeGreaterThan(0);
  });

  test("letters are not returned for the other tenant's organisation", async () => {
    test.skip(!B_ORG_ID, "Set E2E_B_ORG_ID.");
    const response = await aApi.get(`/api/letters?organization_id=${B_ORG_ID}`, {
      failOnStatusCode: false,
    });
    if (response.status() === 200) {
      const body = await response.json();
      const rows = Array.isArray(body) ? body : body?.items || body?.results || [];
      expect(rows, "cross-organisation letters must not be returned").toHaveLength(0);
    } else {
      expect(REFUSED).toContain(response.status());
    }
  });

  test("copied document and letter ids do not resolve", async () => {
    test.skip(!B_DOCUMENT_ID && !B_LETTER_ID, "Set E2E_B_DOCUMENT_ID and/or E2E_B_LETTER_ID.");
    if (B_DOCUMENT_ID) {
      expect(REFUSED).toContain(await apiStatus(aApi, `/api/documents/${B_DOCUMENT_ID}`));
    }
    if (B_LETTER_ID) {
      expect(REFUSED).toContain(await apiStatus(aApi, `/api/letters/${B_LETTER_ID}`));
    }
  });

  test("file download by foreign document id is refused", async () => {
    test.skip(!B_DOCUMENT_ID, "Set E2E_B_DOCUMENT_ID.");
    expect(REFUSED).toContain(await apiStatus(aApi, `/api/documents/${B_DOCUMENT_ID}/download`));
  });

  test("search returns nothing carrying the other organisation", async () => {
    test.skip(!B_ORG_ID, "Set E2E_B_ORG_ID.");
    const response = await aApi.get(
      `/api/search/documents?q=${encodeURIComponent("a")}&organizations=${B_ORG_ID}`,
      { failOnStatusCode: false },
    );
    if (response.status() === 200) {
      const results = (await response.json())?.results || [];
      for (const row of results) {
        expect(String(row.organization_id || "")).not.toBe(B_ORG_ID);
      }
    } else {
      expect(REFUSED).toContain(response.status());
    }
  });

  test("projects listing never exposes the other organisation", async () => {
    test.skip(!B_ORG_ID, "Set E2E_B_ORG_ID.");
    const response = await aApi.get(`/api/projects?organization_id=${B_ORG_ID}`, {
      failOnStatusCode: false,
    });
    if (response.status() === 200) {
      const body = await response.json();
      const rows = Array.isArray(body) ? body : body?.items || [];
      for (const row of rows) {
        expect(String(row.organization_id || "")).not.toBe(B_ORG_ID);
      }
    } else {
      expect(REFUSED).toContain(response.status());
    }
  });

  test("tasks are not returned for the other tenant's organisation", async () => {
    test.skip(!B_ORG_ID, "Set E2E_B_ORG_ID.");
    const response = await aApi.get(`/api/tasks?organization_id=${B_ORG_ID}`, {
      failOnStatusCode: false,
    });
    if (response.status() === 200) {
      const body = await response.json();
      const rows = Array.isArray(body) ? body : body?.items || [];
      expect(rows).toHaveLength(0);
    } else {
      expect(REFUSED).toContain(response.status());
    }
  });
});
