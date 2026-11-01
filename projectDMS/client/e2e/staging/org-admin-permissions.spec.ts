/**
 * Gate 3 bullet 2: Org-Admin permission save/retrieve, including Client DMS.
 *
 * SETUP     an org-admin session, and a disposable organisation-scoped role
 *           named `g3-<run>-permissions`. Both are run-owned; the role is
 *           created idempotently and deleted in teardown.
 * ACTION    grant a Client DMS permission through the browser (Permissions
 *           Management -> the Permissions tab -> select the role -> tick ->
 *           Save Changes -> step-up), then reload the page.
 * EXPECTED  the grant is still ticked after the reload, and `GET
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
 * R-A8W Stage B ran this file against a deployment twice: 7/9 each time, and
 * the bullet stayed unticked. Run 1 met the mandatory Security Terms screen (a
 * freshly seeded org admin has not accepted the active terms). Run 2 met
 * F-A8W-B4: `PermissionsPage` opens on `<Tabs defaultValue="roles">` and renders
 * the permission groups only in the `permissions` tab, and this spec asserted
 * "Client DMS" without selecting it. R-A8X accepts the terms through the page a
 * user would use, selects the tab, and makes the save itself a browser action.
 * An offline edit earns nothing; the bullet stays unchecked until staging runs it.
 */
import { expect, test, type Page } from "@playwright/test";

import {
  RUN_TAG,
  cleanupRunOwned,
  ensureDisposableRole,
  newFixtureContext,
  openProtectedPage,
  readRolePermissions,
  setRolePermissions,
  signIn,
  signInThroughBrowser,
} from "./fixtures";
import { requireStagingEnvironment, stagingTeardownHasWork } from "./staging-target";

/** One Client DMS permission, chosen because it is in the group the bullet names. */
const CLIENT_DMS_PERMISSION = "dms.document.share";
const SECOND_PERMISSION = "dms.document.view";

const ADMIN = ["E2E_ORG_ADMIN_EMAIL", "E2E_ORG_ADMIN_PASSWORD"] as const;
const SCOPE = ["E2E_STAGING_ORG_ID"] as const;

test.describe("Gate 3 bullet 2 - org-admin permission save/retrieve", () => {
  // The three bullet-2 tests share one run-owned role, and each worker runs its
  // own afterAll cleanup. In parallel they reset, grant and delete that role under
  // each other (`playwright.config.ts` sets fullyParallel). Serial, they cannot.
  //
  // Scoped to THIS block (R-A9A, F-A8Z-B1). R-A8Z configured serial mode for the
  // whole file, so one failed bullet-2 browser test skipped all eight Gate 4
  // tests - measured as "8 skipped", i.e. never executed. The Gate 4 tests must
  // run whatever happens here. Both blocks clean up by run tag, so a deployed-stack
  // run uses one worker (`playwright.config.ts`), or one block's teardown could
  // remove the other's run-owned role mid-test.
  test.describe.configure({ mode: "serial" });

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

  test("the Client DMS group is offered, and a grant saved in the browser survives a reload", async ({
    page,
    request,
  }) => {
    const env = requireStagingEnvironment(...ADMIN, ...SCOPE);
    const session = await signIn(request, ADMIN[0], ADMIN[1]);
    const role = await ensureDisposableRole(session, "permissions");
    await setRolePermissions(session, role, []);
    expect(await readRolePermissions(session, role), "the fixture role did not start empty").toEqual([]);

    await signInThroughBrowser(page, ADMIN[0], ADMIN[1]);
    const heading = page.getByRole("heading", { name: "Permissions Management" });
    // The matrix loads each role's permissions asynchronously. Asserting or
    // clicking before this role's load lands lets a late fetch reset the tick.
    const loaded = rolePermissionsLoaded(page, role.id);
    await openProtectedPage(page, "/permissions", heading);
    await loaded;

    // F-A8W-B4. The page opens on the Roles tab; the permission groups exist
    // only in the Permissions tab. Asserting the group without selecting the
    // tab measured the default tab, not the product.
    const shareCheckbox = await openRoleInPermissionMatrix(page, role.name);

    // The group the bullet names, rendered by the deployment rather than by a
    // fixture. If this is absent the bullet fails here, which is the point.
    await expect(page.getByRole("heading", { name: "Client DMS", exact: true })).toBeVisible();
    await expect(shareCheckbox, "the fixture role already holds the Client DMS grant").not.toBeChecked();

    // The save is the browser's own: tick, Save Changes, step-up dialog. The
    // page saves only the selected role, so no other role is written.
    await shareCheckbox.click();
    await expect(shareCheckbox).toBeChecked();
    await page.getByRole("button", { name: "Save Changes" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("heading", { name: "Confirm permission changes" })).toBeVisible();
    await dialog.getByLabel("Password", { exact: true }).fill(env.E2E_ORG_ADMIN_PASSWORD);
    const [saved] = await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes(`/api/roles/${role.id}`) && response.request().method() === "PUT"
      ),
      dialog.getByRole("button", { name: "Verify" }).click(),
    ]);
    expect(saved.status(), "the browser save was refused").toBe(200);

    expect(
      await readRolePermissions(session, role),
      "the server does not hold what the browser saved"
    ).toEqual([CLIENT_DMS_PERMISSION]);

    const reloaded = rolePermissionsLoaded(page, role.id);
    await page.reload();
    await openProtectedPage(page, "/permissions", heading);
    await reloaded;
    const afterReload = await openRoleInPermissionMatrix(page, role.name);
    await expect(
      afterReload,
      "the grant did not survive a reload, so it never left the browser"
    ).toBeChecked();
  });
});

/**
 * Select the Permissions tab, narrow the matrix to one role, and return the
 * checkbox for the Client DMS "Share Documents" permission in that role's column.
 */
function rolePermissionsLoaded(page: Page, roleId: string) {
  return page.waitForResponse(
    (response) =>
      response.url().includes(`/api/roles/${roleId}/permissions`) &&
      response.request().method() === "GET" &&
      response.ok()
  );
}

async function openRoleInPermissionMatrix(page: Page, roleName: string) {
  await page.getByRole("tab", { name: "Permissions", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Permissions", exact: true })).toHaveAttribute(
    "data-state",
    "active"
  );
  // Scoped to the tab panel: the navbar's TenantScopeBar renders native
  // selects (role combobox) whenever the tenant has more than one project.
  const matrix = page.getByRole("tabpanel", { name: "Permissions" });
  await matrix.getByRole("combobox").click();
  await page.getByRole("option", { name: roleName, exact: true }).click();
  const row = matrix.getByRole("row").filter({ has: page.getByRole("cell", { name: "Share Documents", exact: true }) });
  await expect(row).toHaveCount(1);
  const checkbox = row.getByRole("checkbox");
  await expect(checkbox).toHaveCount(1);
  return checkbox;
}

/**
 * R-A8W. The bullet above is "save/retrieve"; Gate 4 bullet 8 is "Org-Admin
 * permissions are validated", and a permission that saves correctly but also
 * reaches another tenant is not validated. So the same run measures the other
 * half: an org admin is allowed inside its organisation and refused outside it.
 *
 * READ-ONLY against the foreign tenant. `E2E_FOREIGN_ORG_ID` and
 * `E2E_FOREIGN_PROJECT_ID` name an organisation and a project the org admin does
 * NOT belong to; nothing here writes to them, so the fixture rule that every
 * write names `E2E_STAGING_ORG_ID` still holds.
 *
 * A refusal may be 403 or 404. A 401 is a FAILURE: the client turns 401 into a
 * forced logout, so a scope refusal that arrives as 401 logs a legitimate user
 * out of their own tenant. A 200 carrying a foreign row is a leak. A 200 with an
 * empty list is only accepted where the endpoint is a collection read, because
 * "the gate answers membership, the scope query answers row visibility".
 */
const FOREIGN = ["E2E_FOREIGN_ORG_ID", "E2E_FOREIGN_PROJECT_ID"] as const;
/** A user and a role that belong to the foreign organisation, for direct-id refusals. */
const FOREIGN_OBJECTS = ["E2E_FOREIGN_USER_ID", "E2E_FOREIGN_ROLE_ID"] as const;
const REFUSALS = [403, 404];

/**
 * The staging environment, plus the one property every Gate 4 refusal rests on:
 * the "foreign" fixtures are not the fixture tenant itself. With
 * `E2E_FOREIGN_ORG_ID` equal to `E2E_STAGING_ORG_ID`, the list rows pass because
 * the own id is filtered out as "foreign" and the refusal rows measure the
 * admin's own tenant.
 */
function requireForeignTenant(...names: string[]): Record<string, string> {
  const env = requireStagingEnvironment(...names);
  if (env.E2E_FOREIGN_ORG_ID !== undefined) {
    expect(
      env.E2E_FOREIGN_ORG_ID,
      "E2E_FOREIGN_ORG_ID names the fixture organisation itself, so no foreign tenant is measured"
    ).not.toBe(env.E2E_STAGING_ORG_ID);
  }
  if (env.E2E_FOREIGN_PROJECT_ID !== undefined && env.E2E_STAGING_PROJECT_ID !== undefined) {
    expect(env.E2E_FOREIGN_PROJECT_ID, "E2E_FOREIGN_PROJECT_ID names the fixture project itself").not.toBe(
      env.E2E_STAGING_PROJECT_ID
    );
  }
  return env;
}

function rowsOf(body: unknown, ...keys: string[]): any[] {
  if (Array.isArray(body)) {
    return body;
  }
  for (const key of keys) {
    const value = (body as Record<string, unknown> | null)?.[key];
    if (Array.isArray(value)) {
      return value;
    }
  }
  return [];
}

function organisationOf(row: any): string {
  return String(row?.organization_id ?? row?.organizationId ?? "").trim();
}

function idOf(row: any): string {
  return String(row?._id ?? row?.id ?? "").trim();
}

/** Every object anywhere in a JSON body that belongs to, or is, a foreign object. */
function foreignObjectsIn(body: unknown, foreignOrg: string, foreignProject: string): unknown[] {
  const found: unknown[] = [];
  const walk = (node: unknown) => {
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (node && typeof node === "object") {
      const row = node as Record<string, unknown>;
      const org = organisationOf(row);
      const id = idOf(row);
      const project = String(row.project_id ?? row.projectId ?? "").trim();
      const memberships = [row.organizations, row.projects]
        .filter(Array.isArray)
        .flat()
        .map((value) => String(value).trim());
      if (
        org === foreignOrg ||
        project === foreignProject ||
        id === foreignOrg ||
        id === foreignProject ||
        memberships.includes(foreignOrg) ||
        memberships.includes(foreignProject)
      ) {
        found.push(node);
      }
      Object.values(row).forEach(walk);
    }
  };
  walk(body);
  return found;
}

test.describe("Gate 4 bullet 8 - org-admin is allowed in its tenant and refused outside it", () => {
  test.afterAll(async ({ playwright }) => {
    // Row 13's own-role control creates one run-owned role in the fixture
    // organisation. The same run tag and teardown as bullet 2; safe to call twice.
    if (!stagingTeardownHasWork()) {
      return;
    }
    const context = await newFixtureContext(playwright);
    try {
      const session = await signIn(context, ADMIN[0], ADMIN[1]);
      const report = await cleanupRunOwned(session);
      expect(report.failed, `teardown left state behind: ${JSON.stringify(report.failed)}`).toEqual([]);
    } finally {
      await context.dispose();
    }
  });

  test("the organisation list holds the org admin's own organisation and no other", async ({
    request,
  }) => {
    const env = requireForeignTenant(...ADMIN, ...SCOPE, ...FOREIGN);
    await signIn(request, ADMIN[0], ADMIN[1]);

    const response = await request.get("/api/organizations");
    expect(response.status(), "an org admin could not list organisations at all").toBe(200);
    const rows = rowsOf(await response.json(), "organizations", "items", "data");
    const ids = rows.map(idOf);

    expect(ids, "the org admin's own organisation is not visible to it").toContain(
      env.E2E_STAGING_ORG_ID
    );
    expect(ids, "a foreign organisation is listed to an org admin").not.toContain(
      env.E2E_FOREIGN_ORG_ID
    );
    expect(
      ids.filter((id) => id !== env.E2E_STAGING_ORG_ID),
      "the organisation list reaches beyond the org admin's tenant"
    ).toEqual([]);
  });

  test("a foreign organisation addressed directly is refused", async ({ request }) => {
    const env = requireForeignTenant(...ADMIN, ...SCOPE, ...FOREIGN);
    await signIn(request, ADMIN[0], ADMIN[1]);

    const own = await request.get(`/api/organizations/${env.E2E_STAGING_ORG_ID}`);
    expect(own.status(), "the org admin was refused its own organisation").toBe(200);

    const foreign = await request.get(`/api/organizations/${env.E2E_FOREIGN_ORG_ID}`);
    // Exactly 403 for every direct-id refusal (R-A9A review): an id that does not
    // exist answers 404, so accepting 404 would pass on a wrong or stale fixture
    // id while measuring nothing. 403 proves the object exists AND was refused.
    expect(
      foreign.status(),
      `a direct read of a foreign organisation returned ${foreign.status()}; 200 is a leak, 401 a forced logout, 404 a missing fixture`
    ).toBe(403);
  });

  test("every visible project belongs to the org admin's organisation", async ({ request }) => {
    const env = requireForeignTenant(...ADMIN, ...SCOPE, ...FOREIGN);
    await signIn(request, ADMIN[0], ADMIN[1]);

    const response = await request.get("/api/projects");
    expect(response.status(), "an org admin could not list projects in its own tenant").toBe(200);
    const rows = rowsOf(await response.json(), "projects", "items", "data");

    // Non-empty, or "no foreign project" would be a pass that measured nothing.
    expect(rows.length, "the org admin sees no project at all").toBeGreaterThan(0);
    expect(
      rows.filter((row) => organisationOf(row) !== env.E2E_STAGING_ORG_ID).map(idOf),
      "projects outside the org admin's organisation are visible to it"
    ).toEqual([]);
  });

  test("a foreign project, or a foreign-organisation filter, leaks nothing", async ({ request }) => {
    const env = requireForeignTenant(...ADMIN, ...SCOPE, ...FOREIGN, "E2E_STAGING_PROJECT_ID");
    await signIn(request, ADMIN[0], ADMIN[1]);

    // CONTROL: the same read of the org admin's own project succeeds, so the
    // refusal below is the foreign-project check and not a missing permission.
    const own = await request.get(`/api/projects/${env.E2E_STAGING_PROJECT_ID}`);
    expect(own.status(), "CONTROL: the org admin was refused its own project by id").toBe(200);

    const direct = await request.get(`/api/projects/${env.E2E_FOREIGN_PROJECT_ID}`);
    expect(
      direct.status(),
      `a direct read of a foreign project returned ${direct.status()}; 200 is a leak, 401 a forced logout, 404 a missing fixture`
    ).toBe(403);

    // The selector: asking for another organisation's projects by parameter.
    const filtered = await request.get("/api/projects", {
      params: { organization_id: env.E2E_FOREIGN_ORG_ID },
    });
    expect(filtered.status(), "a scope refusal arrived as 401").not.toBe(401);
    if (filtered.status() === 200) {
      const leaked = foreignObjectsIn(
        await filtered.json(),
        env.E2E_FOREIGN_ORG_ID,
        env.E2E_FOREIGN_PROJECT_ID
      );
      expect(leaked, "selecting a foreign organisation returned its projects").toEqual([]);
    } else {
      expect(REFUSALS).toContain(filtered.status());
    }
  });

  test("roles and users of a foreign organisation are not visible", async ({ request }) => {
    const env = requireForeignTenant(...ADMIN, ...SCOPE, ...FOREIGN);
    await signIn(request, ADMIN[0], ADMIN[1]);

    const roles = await request.get("/api/roles");
    expect(roles.status(), "an org admin could not list roles in its own tenant").toBe(200);
    const foreignRoles = rowsOf(await roles.json(), "roles").filter((row) => {
      const org = organisationOf(row);
      return org !== "" && org !== env.E2E_STAGING_ORG_ID;
    });
    expect(foreignRoles.map(idOf), "roles scoped to another organisation are visible").toEqual([]);

    // CONTROL: the unfiltered user list answers, so a refusal of the foreign
    // filter below is the selector check and not a missing users:read.
    const ownUsers = await request.get("/api/users");
    expect(ownUsers.status(), "CONTROL: the org admin could not list users in its own tenant").toBe(200);

    const users = await request.get("/api/users", {
      params: { organization_id: env.E2E_FOREIGN_ORG_ID },
    });
    expect(users.status(), "a scope refusal arrived as 401").not.toBe(401);
    if (users.status() === 200) {
      const leaked = rowsOf(await users.json(), "users", "items", "data").filter(
        (row) => organisationOf(row) === env.E2E_FOREIGN_ORG_ID
      );
      expect(leaked.map(idOf), "an org admin can list another organisation's users").toEqual([]);
    } else {
      expect(REFUSALS).toContain(users.status());
    }
  });

  test("a foreign user and a foreign role addressed directly are refused", async ({ request }) => {
    const env = requireForeignTenant(...ADMIN, ...SCOPE, ...FOREIGN, ...FOREIGN_OBJECTS);
    const session = await signIn(request, ADMIN[0], ADMIN[1]);

    // CONTROLS: the same two routes answer 200 for an object in the org admin's
    // own organisation - its own user record, and a run-owned organisation role
    // (removed by this block's teardown) - so the refusals below are the tenant
    // check and not a missing users:read / roles:read or a role-visibility rule.
    const me = await (await session.request.get("/api/users/me")).json();
    const ownUser = await session.request.get(`/api/users/${String(me._id ?? me.id)}`);
    expect(ownUser.status(), "CONTROL: the org admin was refused its own user record by id").toBe(200);
    const ownRole = await ensureDisposableRole(session, "g4-own-role-control");
    const ownRoleRead = await session.request.get(`/api/roles/${ownRole.id}`);
    expect(ownRoleRead.status(), "CONTROL: the org admin was refused its own organisation's role by id").toBe(200);

    for (const path of [`/api/users/${env.E2E_FOREIGN_USER_ID}`, `/api/roles/${env.E2E_FOREIGN_ROLE_ID}`]) {
      const response = await request.get(path);
      // Exactly 403: both routes answer 404 for an id that does not exist
      // (`routers/users.py` and `routers/roles.py` look the object up before the
      // tenant check), so 404 would pass on a wrong fixture id.
      expect(
        response.status(),
        `GET ${path} for a foreign object returned ${response.status()}; 200 is a leak, 401 a forced logout, 404 a missing fixture, 500 a masked refusal`
      ).toBe(403);
    }
  });

  test("the org admin cannot move itself into a foreign organisation", async ({ request }) => {
    // The write the R-A8X review traced: PUT /api/users/{own id} with a foreign
    // organization_id was accepted and moved the admin into the foreign tenant.
    // The only write here targets the admin's own record; if the refusal is
    // missing, the read-back fails the test and names the moved account.
    const env = requireForeignTenant(...ADMIN, ...SCOPE, ...FOREIGN);
    const session = await signIn(request, ADMIN[0], ADMIN[1]);

    const before = await session.request.get("/api/users/me");
    expect(before.status()).toBe(200);
    const me = await before.json();
    const myId = String(me._id ?? me.id);
    expect(String(me.organization_id ?? me.organizationId)).toBe(env.E2E_STAGING_ORG_ID);

    const moved = await session.request.put(`/api/users/${myId}`, {
      headers: session.headers(),
      data: { organization_id: env.E2E_FOREIGN_ORG_ID },
    });
    expect(moved.status(), `moving the org admin into a foreign organisation returned ${moved.status()}`).toBe(403);

    const after = await (await session.request.get("/api/users/me")).json();
    expect(
      String(after.organization_id ?? after.organizationId),
      `the org admin ${myId} now belongs to another organisation`
    ).toBe(env.E2E_STAGING_ORG_ID);
  });

  test("the org admin can update its own project, and cannot move it into a foreign organisation", async ({
    request,
  }) => {
    // Gate 4 b8 row 12 (R-A9A, F-A8Z-B2). R-A8Z measured this by hand with a
    // probe account whose `users.roles` held a role NAME; permission lookup
    // resolves role ids, so that account held no permission and was refused its
    // own project - the re-parent refusal it recorded measured the gate, not the
    // foreign-organisation check. The org admin's default role carries
    // `dms.dashboard.view` and `dms.project.manage`, the names
    // `routers/projects.py` checks, so the same account is the control.
    //
    // OWN-ORG CONTROL must pass first: read and an unchanged update of the org
    // admin's own project. Only then is a 403 on the re-parent the property.
    // The only writes target the fixture project, with its own name and
    // organisation; if the refusal were missing, the read-back names the move.
    const env = requireForeignTenant(...ADMIN, ...SCOPE, ...FOREIGN, "E2E_STAGING_PROJECT_ID");
    const session = await signIn(request, ADMIN[0], ADMIN[1]);
    const path = `/api/projects/${env.E2E_STAGING_PROJECT_ID}`;

    const before = await session.request.get(path);
    expect(
      before.status(),
      "CONTROL: the org admin was refused a read of its own project, so a refusal below would measure the permission gate"
    ).toBe(200);
    const project = await before.json();
    expect(organisationOf(project), "the fixture project is not in the fixture organisation").toBe(
      env.E2E_STAGING_ORG_ID
    );

    const control = await session.request.put(path, {
      headers: session.headers(),
      data: { name: project.name, organization_id: env.E2E_STAGING_ORG_ID },
    });
    expect(
      control.status(),
      "CONTROL: an unchanged update of the org admin's own project was refused, so the re-parent refusal would measure nothing"
    ).toBe(200);

    const moved = await session.request.put(path, {
      headers: session.headers(),
      data: { name: project.name, organization_id: env.E2E_FOREIGN_ORG_ID },
    });
    // Exactly 403: the router refuses a re-parent before it looks the target
    // organisation up, so a 400/404 here is the existence oracle R-A8X closed.
    expect(moved.status(), `moving the fixture project into a foreign organisation returned ${moved.status()}`).toBe(
      403
    );
    // Pins the refusal to the re-parent branch, the only body-dependent 403 on this route.
    expect(await moved.text()).toContain("Moving a project to another organization requires a superadmin");

    const after = await session.request.get(path);
    expect(after.status()).toBe(200);
    const stored = await after.json();
    expect(
      organisationOf(stored),
      `project ${env.E2E_STAGING_PROJECT_ID} now belongs to another organisation`
    ).toBe(env.E2E_STAGING_ORG_ID);
    expect(stored.name, "the control update changed the fixture project's name").toBe(project.name);
  });

  test("navigating the admin pages surfaces no foreign data and no forced logout", async ({
    page,
  }) => {
    const env = requireForeignTenant(...ADMIN, ...SCOPE, ...FOREIGN);

    await signInThroughBrowser(page, ADMIN[0], ADMIN[1]);
    // Accept the mandatory terms before measuring, or every page below is the
    // terms screen and "no foreign object" is true of a page that loaded nothing.
    await openProtectedPage(page, "/permissions", page.getByRole("heading", { name: "Permissions Management" }));

    const unauthorised: string[] = [];
    const leaks: string[] = [];
    // Per page, not per test (R-A9A review): the navbar's TenantContext lists
    // organisations and projects on every mount, so one shared set let
    // /organizations and /projects "make their call" on the navbar's alone.
    let observed = new Set<string>();
    const parsing: Promise<void>[] = [];
    let measured = 0;
    page.on("response", (response) => {
      const url = response.url();
      if (!url.includes("/api/")) {
        return;
      }
      observed.add(new URL(url).pathname);
      if (response.status() === 401) {
        unauthorised.push(`${response.request().method()} ${new URL(url).pathname}`);
      }
      if (!(response.headers()["content-type"] ?? "").includes("application/json")) {
        return;
      }
      // Collected and awaited below, so a leak in the last page's body cannot
      // arrive after the assertion that says there was none.
      parsing.push(
        (async () => {
          try {
            const body = await response.json();
            measured += 1;
            if (foreignObjectsIn(body, env.E2E_FOREIGN_ORG_ID, env.E2E_FOREIGN_PROJECT_ID).length > 0) {
              leaks.push(new URL(url).pathname);
            }
          } catch {
            // A body that is gone or not JSON carries no row to leak.
          }
        })()
      );
    });

    // Each page must stay on itself, render ITS page (its own h1, not the
    // "Access unavailable" card `ProtectedRoute` shows at the same URL), and make
    // its own API call after navigating - or "no foreign object" is true of a page
    // that loaded nothing.
    const pages: Array<[string, string, RegExp]> = [
      ["/organizations", "/api/organizations", /^Organizations$/],
      ["/projects", "/api/projects", /^Projects( - .+)?$/],
      ["/users", "/api/users", /^Users Management$/],
      ["/permissions", "/api/roles", /^Permissions Management$/],
    ];
    for (const [path, api, title] of pages) {
      observed = new Set<string>();
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      expect(new URL(page.url()).pathname, `visiting ${path} did not stay on ${path}`).toBe(path);
      await expect(page.getByRole("heading", { level: 1, name: title }), `${path} did not render its page`).toBeVisible();
      await expect(page.getByText("Access unavailable"), `${path} rendered the access-denied card`).toHaveCount(0);
      expect(
        [...observed].some((seen) => seen.startsWith(api)),
        `${path} made no ${api} call after navigating, so its data was never measured`
      ).toBe(true);
    }

    await Promise.allSettled(parsing);
    expect(measured, "no JSON API response was observed, so nothing was measured").toBeGreaterThan(0);
    expect(unauthorised, "an admin page answered 401, which the client turns into a forced logout").toEqual(
      []
    );
    expect(leaks, "an admin page received another organisation's objects").toEqual([]);
  });
});
