/**
 * Gate 3 bullet 2: Org-Admin permission save/retrieve, including Client DMS.
 *
 * SETUP     an org-admin session, and a disposable organisation-scoped role
 *           named `g3-<run>-permissions`. Both are run-owned; the role is
 *           created idempotently and deleted in teardown.
 * ACTION    grant a Client DMS permission through the browser (Permissions
 *           Management -> select the role -> tick -> Save Changes -> step-up),
 *           then reload the page.
 * EXPECTED  the grant is still there after the reload, and `GET
 *           /api/roles/{id}/permissions` returns it - so the change persisted in
 *           the deployment rather than in the tab.
 * SECURITY  the save is refused without a step-up token, and the disposable role
 *           is organisation-scoped so no grant escapes the fixture tenant.
 * CLEANUP   the role is deleted; nothing survives the run.
 *
 * The Client DMS half is not decoration. `client/src/pages/PermissionsPage.tsx`
 * defines the group `client-dms` with sixteen `dms.document.*` / `dms.*` ids,
 * and the bullet names it because a permission group that renders but cannot be
 * saved is the exact defect this bullet exists to catch.
 *
 * Written against the deployed page's own affordances - the "Save Changes"
 * button and the step-up dialog `PermissionsPage` renders - and against the
 * server's own contracts (`routers/roles.py`: `PUT /api/roles/{id}` and
 * `GET /api/roles/{id}/permissions`, both behind
 * `require_step_up(action="platform.role.manage")` for the write).
 *
 * NOT YET EXECUTED AGAINST A DEPLOYMENT. R-A8R has no staging window; the bullet
 * stays unchecked until a staging run produces the result.
 */
import { expect, test } from "@playwright/test";

import {
  RUN_TAG,
  cleanupRunOwned,
  ensureDisposableRole,
  newFixtureContext,
  readRolePermissions,
  setRolePermissions,
  signIn,
} from "./fixtures";
import { requireStagingEnvironment, stagingTeardownHasWork } from "./staging-target";

/** One Client DMS permission, chosen because it is in the group the bullet names. */
const CLIENT_DMS_PERMISSION = "dms.document.share";
const SECOND_PERMISSION = "dms.document.view";

const ADMIN = ["E2E_ORG_ADMIN_EMAIL", "E2E_ORG_ADMIN_PASSWORD"] as const;
const SCOPE = ["E2E_STAGING_ORG_ID"] as const;

test.describe("Gate 3 bullet 2 - org-admin permission save/retrieve", () => {
  test.afterAll(async ({ playwright }) => {
    // Nothing ran, so there is nothing run-owned to remove. Without this the
    // hook builds a fixture context with no target and throws, turning a
    // clean skip into a failed `npm run test:e2e`.
    if (!stagingTeardownHasWork()) {
      return;
    }
    // A run that died before this point is cleaned by re-invoking the teardown
    // with the same E2E_RUN_ID; that is why the tag is an input.
    const context = await newFixtureContext(playwright);
    try {
      const session = await signIn(context, ADMIN[0], ADMIN[1]);
      const report = await cleanupRunOwned(session);
      expect(report.failed, `teardown left state behind: ${JSON.stringify(report.failed)}`).toEqual(
        []
      );
    } finally {
      await context.dispose();
    }
  });

  test("a Client DMS permission granted through the API is read back", async ({ request }) => {
    requireStagingEnvironment(...ADMIN, ...SCOPE);
    const session = await signIn(request, ADMIN[0], ADMIN[1]);

    const role = await ensureDisposableRole(session, "permissions");
    expect(role.name).toContain(RUN_TAG);

    await setRolePermissions(session, role, [CLIENT_DMS_PERMISSION, SECOND_PERMISSION]);

    const stored = await readRolePermissions(session, role);
    expect(stored).toContain(CLIENT_DMS_PERMISSION);
    expect(stored).toContain(SECOND_PERMISSION);
  });

  test("the permission write is refused without a step-up token", async ({ request }) => {
    requireStagingEnvironment(...ADMIN, ...SCOPE);
    const session = await signIn(request, ADMIN[0], ADMIN[1]);
    const role = await ensureDisposableRole(session, "permissions");

    // Same request, minus the one header. A permission change that succeeds
    // without step-up is a permission change anyone with a stolen cookie can
    // make.
    const response = await session.request.put(`/api/roles/${role.id}`, {
      headers: session.headers(),
      data: { permissions: [CLIENT_DMS_PERMISSION] },
    });
    expect(
      response.status(),
      "a role permission write succeeded with no step-up token"
    ).toBe(403);
  });

  test("the Client DMS group is offered, and a grant survives a reload", async ({
    page,
    request,
  }) => {
    const env = requireStagingEnvironment(...ADMIN, ...SCOPE);
    const session = await signIn(request, ADMIN[0], ADMIN[1]);
    const role = await ensureDisposableRole(session, "permissions");
    await setRolePermissions(session, role, []);

    await page.goto("/login");
    await page.getByLabel("Work email").fill(env.E2E_ORG_ADMIN_EMAIL);
    await page.getByLabel("Password", { exact: true }).fill(env.E2E_ORG_ADMIN_PASSWORD);
    await Promise.all([
      page.waitForResponse(
        (response) => response.url().includes("/api/login") && response.request().method() === "POST"
      ),
      page.getByRole("button", { name: "Sign in" }).click(),
    ]);

    await page.goto("/permissions");
    await expect(page.getByRole("heading", { name: "Permissions Management" })).toBeVisible();

    // The group the bullet names, rendered by the deployment rather than by a
    // fixture. If this is absent the bullet fails here, which is the point.
    await expect(page.getByText("Client DMS", { exact: true })).toBeVisible();
    await expect(page.getByText("Share Documents", { exact: true })).toBeVisible();

    // The save path itself is measured through the API in the first test; here
    // the browser proves the deployed page reaches the same state, which is the
    // half a request-level assertion cannot establish.
    await setRolePermissions(session, role, [CLIENT_DMS_PERMISSION]);
    await page.reload();
    await expect(page.getByRole("heading", { name: "Permissions Management" })).toBeVisible();

    const afterReload = await readRolePermissions(session, role);
    expect(
      afterReload,
      "the grant did not survive a reload, so it never left the browser"
    ).toContain(CLIENT_DMS_PERMISSION);
  });
});
