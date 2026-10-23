import { expect, Page, Route, test } from "@playwright/test";

/**
 * Hindrance & Constraint Register — browser workflow against a STATEFUL mock
 * of `/api/**`.
 *
 * This proves the UI drives the canonical register API correctly (paths,
 * payloads, PATCH semantics, archive, relationships) and renders what comes
 * back. It is a mocked suite, so it proves component behaviour, not a
 * deployment: `playwright.config.ts` excludes it from deployed-stack runs.
 * There is no deployed-stack spec for the register yet; staging evidence for it
 * is still to be written and captured.
 *
 * The mock enforces the scope rules the backend enforces, so the browser
 * really sees a refusal: entries belong to one project, a user assigned to
 * another project gets 403 on read and write, and - since the active-scope
 * decision (2026-09-22, `core/tenant_context.py`) - the navbar selection sent as
 * `X-Proj-Id` bounds every request: a record in another project is 403
 * `context_forbidden`, a record-level request with no selection is 400
 * `selection_required`, and the list follows the selection.
 */

const ORG = { _id: "org-A", id: "org-A", name: "Aurora Engineering" };
const PROJECT_A1 = { _id: "proj-A1", id: "proj-A1", name: "Metro Package A1", organization_id: "org-A" };
const PROJECT_A2 = { _id: "proj-A2", id: "proj-A2", name: "Metro Package A2", organization_id: "org-A" };

type Actor = { id: string; roles: string[]; projects: string[]; permissions: string[] };

const PROJECT_ADMIN: Actor = {
  id: "u-projadmin-a1",
  roles: ["projectadmin"],
  projects: ["proj-A1"],
  permissions: [
    "dms.hindrance.view",
    "dms.hindrance.create",
    "dms.hindrance.edit",
    "dms.hindrance.archive",
    "dms.document.view",
    "dms.keydate.view",
    "dms.evidence_graph.view",
  ],
};
const FOREIGN_PROJECT_USER: Actor = {
  id: "u-projuser-a2",
  roles: ["projectuser"],
  projects: ["proj-A2"],
  permissions: ["dms.hindrance.view", "dms.hindrance.edit", "dms.document.view"],
};

/** An organisation admin: org-wide, so the navbar project selector is live. */
const ORG_ADMIN: Actor = {
  id: "u-orgadmin-a",
  roles: ["orgadmin"],
  projects: ["proj-A1", "proj-A2"],
  permissions: PROJECT_ADMIN.permissions,
};

type Entry = Record<string, any>;

class RegisterState {
  /** Every register request as the backend would see it: method, path, selected project. */
  requests: Array<{ method: string; path: string; project: string }> = [];
  entries: Entry[] = [];
  links: Entry[] = [];
  documentLinks: Entry[] = [];
  patches: Array<Record<string, unknown>> = [];
  seq = 0;
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

const document = {
  _id: "doc-A1",
  filename: "Site diary 10-Feb.pdf",
  subject: "Police barricading at S2",
  status: "approved",
  organization_id: "org-A",
  project_id: "proj-A1",
  createdAt: "2026-02-10T09:00:00Z",
};

async function mockApi(page: Page, actor: Actor, state: RegisterState) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "") || "/";
    const method = request.method();
    const body = request.postDataJSON?.() ?? null;
    const inScope = (entry: Entry) => actor.projects.includes(entry.project_id);
    const selected = request.headers()["x-proj-id"] || "";
    const registerPath = path.startsWith("/hindrances") || path.startsWith("/entities/delay_event");
    if (registerPath) state.requests.push({ method, path, project: selected });
    const refuse = () => json(route, { detail: { code: "context_forbidden", message: "not available in the selected project" } }, 403);
    const selectFirst = () => json(route, { detail: { code: "selection_required", message: "select a project" } }, 400);
    if (registerPath && selected && !actor.projects.includes(selected)) return refuse();

    if (path === "/csrf-token") return json(route, { csrf_token: "test-csrf" });
    if (path === "/me" || path === "/users/me") {
      return json(route, {
        id: actor.id,
        email: `${actor.id}@example.test`,
        roles: actor.roles,
        permissions: actor.permissions,
        organization_id: ORG.id,
        projects: actor.projects,
      });
    }
    if (path === "/security-terms/status") return json(route, { requires_acceptance: false });
    if (path === "/organizations") return json(route, { organizations: [ORG] });
    if (path === "/projects") {
      return json(route, [PROJECT_A1, PROJECT_A2].filter((project) => actor.projects.includes(project._id)));
    }

    // ---- register ------------------------------------------------------
    if (path === "/hindrances" && method === "GET") {
      const includeArchived = url.searchParams.get("include_archived") === "true";
      const projectId = url.searchParams.get("project_id");
      if (projectId && !actor.projects.includes(projectId)) return json(route, { detail: "Not authorized" }, 403);
      if (selected && projectId && projectId !== selected) return refuse();
      const items = state.entries.filter(
        (entry) => inScope(entry) && (!selected || entry.project_id === selected) && (includeArchived || !entry.archived_at),
      );
      return json(route, { items, total: items.length, skip: 0, limit: 25 });
    }
    if (path === "/hindrances" && method === "POST") {
      if (!selected) return selectFirst();
      if (body.project_id !== selected) return refuse();
      if (!actor.projects.includes(body.project_id)) return json(route, { detail: "Not authorized" }, 403);
      state.seq += 1;
      const entry = {
        ...body,
        id: `h-${state.seq}`,
        organization_id: ORG.id,
        hindrance_ref: `HIN-${String(state.seq).padStart(4, "0")}`,
        status: body.status ?? "open",
        linked_document_ids: [],
        archived_at: null,
        timeline_sync_status: "synced",
        created_at: "2026-02-10T08:00:00",
        created_by: actor.id,
      };
      state.entries.push(entry);
      return json(route, entry, 201);
    }
    const item = path.match(/^\/hindrances\/([^/]+)(\/.*)?$/);
    if (item && item[1] !== "affecting") {
      if (!selected) return selectFirst();
      const entry = state.entries.find((candidate) => candidate.id === item[1]);
      if (!entry) return json(route, { detail: "Register entry not found" }, 404);
      if (entry.project_id !== selected) return refuse();
      if (!inScope(entry)) return json(route, { detail: "Not authorized: scope_denied" }, 403);
      const tail = item[2] || "";
      if (!tail && method === "GET") return json(route, entry);
      if (!tail && method === "PATCH") {
        if (entry.archived_at) return json(route, { detail: "Archived register entries are read-only" }, 409);
        state.patches.push(body);
        Object.assign(entry, body, { updated_at: "2026-02-11T08:00:00", updated_by: actor.id });
        return json(route, entry);
      }
      if (tail === "/archive") {
        Object.assign(entry, { archived_at: "2026-02-12T08:00:00", archived_by: actor.id, archive_reason: body.reason });
        return json(route, entry);
      }
      if (tail === "/restore") {
        Object.assign(entry, { archived_at: null, archived_by: null, archive_reason: null });
        return json(route, entry);
      }
      if (tail === "/history") return json(route, { entries: [] });
      if (tail === "/links" && method === "GET") {
        return json(route, { links: state.links.filter((link) => link.delay_event_id === entry.id && !link.removed_at) });
      }
      if (tail === "/links" && method === "POST") {
        const labels: Record<string, { label: string; title: string }> = {
          "act-A1": { label: "ACT-110", title: "Pier P4 piling" },
          "kd-A1": { label: "KD-03", title: "Access to Station S2" },
        };
        const link = {
          id: `link-${state.links.length + 1}`,
          delay_event_id: entry.id,
          organization_id: ORG.id,
          project_id: entry.project_id,
          target_type: body.target_type,
          target_id: body.target_id,
          relationship_role: body.target_type === "key_date" ? "impacts_key_date" : "affects_activity",
          revision: 1,
          target: { ...labels[body.target_id], status: "planned", route: body.target_type === "key_date" ? `/key-dates/${body.target_id}` : null },
          target_available: true,
          target_restricted: false,
        };
        state.links.push(link);
        return json(route, link, 201);
      }
    }

    // ---- relationship targets -----------------------------------------
    if (path === "/programme-milestones") {
      return json(route, [{ _id: "act-A1", milestone_ref: "ACT-110", title: "Pier P4 piling", milestone_type: "programme_activity", status: "in_progress", project_id: "proj-A1" }]);
    }
    if (path === "/key-dates") {
      return json(route, [{ _id: "kd-A1", id: "kd-A1", milestone_ref: "KD-03", title: "Access to Station S2", contractual_week_number: 12, project_id: "proj-A1", linked_document_ids: [], linked_letter_ids: [] }]);
    }
    if (path === "/key-dates/workflow") return json(route, { project_id: "proj-A1", baseline_status: "draft", submissions: [], determinations: [] });

    // ---- canonical document relationships --------------------------------
    const documentLinks = path.match(/^\/entities\/delay_event\/([^/]+)\/document-links(:batch)?$/);
    if (documentLinks) {
      if (!selected) return selectFirst();
      const entry = state.entries.find((candidate) => candidate.id === documentLinks[1]);
      if (entry && entry.project_id !== selected) return refuse();
      if (!entry || !inScope(entry)) return json(route, { detail: "Not authorized" }, 403);
      if (documentLinks[2] && method === "POST") {
        const created = body.links.map((link: any, index: number) => ({
          _id: `dl-${state.documentLinks.length + index + 1}`,
          document_id: link.document_id,
          relationship_role: link.relationship_role,
          _revision: 1,
          target_type: "delay_event",
          target_id: entry.id,
          document,
        }));
        state.documentLinks.push(...created);
        return json(route, { links: created }, 201);
      }
      return json(route, { links: state.documentLinks.filter((link) => link.target_id === entry.id) });
    }
    if (path === "/document-search") return json(route, { documents: [document] });

    // Shell noise (notifications, dashboards…) answers empty.
    return json(route, method === "GET" ? [] : {});
  });
}

/** Set E2E_SCREENSHOT_DIR to keep review screenshots of the workflow. */
async function shot(page: Page, name: string) {
  const directory = process.env.E2E_SCREENSHOT_DIR;
  if (directory) await page.screenshot({ path: `${directory}/${name}.png`, fullPage: true });
}

async function openRegister(page: Page) {
  await page.goto("/hindrances");
  await expect(page.getByRole("heading", { name: "Hindrance & Constraint Register" })).toBeVisible();
  // Reachable from the application sidebar, not only by URL.
  await expect(page.locator("aside").getByRole("link", { name: "Hindrance & Constraint Register" })).toHaveAttribute("href", "/hindrances");
}

test.describe("Hindrance & Constraint Register workflow", () => {
  // Measured 27-40 s per workflow on a dev host against the 45 s default: too close for CI.
  test.describe.configure({ timeout: 90_000 });
  test("a project admin records, edits, links, reloads and archives an entry", async ({ page }) => {
    const state = new RegisterState();
    await mockApi(page, PROJECT_ADMIN, state);
    await openRegister(page);

    // 1. Create.
    await page.getByRole("button", { name: /Record entry/ }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Title *").fill("Site access blocked at Station S2");
    await dialog.getByLabel("Start / occurrence date *").fill("2026-02-10");
    await dialog.getByLabel("Category").selectOption("site_access");
    await dialog.getByLabel("Affected party").fill("Contractor");
    await dialog.getByRole("button", { name: "Record entry" }).click();

    // 2. It appears in the register.
    const row = page.getByRole("row", { name: /HIN-0001/ });
    await expect(row).toBeVisible();
    await expect(row).toContainText("Site access");
    await expect(row).toContainText("Contractor");
    await shot(page, "01-register");

    // 3. Edit — only the changed field is sent.
    await page.getByRole("link", { name: "HIN-0001" }).click();
    await expect(page.getByRole("heading", { name: /Site access blocked at Station S2/ })).toBeVisible();
    await page.getByRole("button", { name: /Edit/ }).click();
    await page.getByRole("dialog").getByLabel("Location").fill("Station S2 north gate");
    await page.getByRole("dialog").getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Station S2 north gate")).toBeVisible();
    expect(state.patches).toEqual([{ location: "Station S2 north gate" }]);

    // 4. Link an existing document through the canonical relationship API.
    await page.getByLabel("Search Documents").fill("diary");
    await page.getByRole("button", { name: "Link Site diary 10-Feb.pdf" }).click();
    const linkedDocument = page.getByRole("link", { name: /^Site diary 10-Feb\.pdf .*Site record$/ });
    await expect(linkedDocument).toHaveAttribute("href", "/documentviewer/doc-A1");

    // 5. Link an activity.
    await page.getByRole("button", { name: "Link programme activity" }).click();
    await page.getByRole("button", { name: "Link ACT-110" }).click();
    await expect(page.getByText("ACT-110", { exact: true })).toBeVisible();

    // 6. Link a key date.
    await page.getByRole("button", { name: "Link key date" }).click();
    await page.getByRole("button", { name: "Link KD-03" }).click();
    await expect(page.getByRole("link", { name: /KD-03/ })).toHaveAttribute("href", "/key-dates/kd-A1");

    // 7. Reload: everything is still there because it came from the API.
    await page.reload();
    await expect(page.getByText("Station S2 north gate")).toBeVisible();
    await expect(page.getByText("ACT-110", { exact: true })).toBeVisible();
    await expect(page.getByText("KD-03", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: /^Site diary 10-Feb\.pdf .*Site record$/ })).toBeVisible();
    await shot(page, "02-detail");

    // 9. Archive, with a reason.
    await page.getByRole("button", { name: /Archive/ }).click();
    const confirm = page.getByRole("alertdialog");
    await expect(confirm.getByRole("button", { name: "Archive" })).toBeDisabled();
    await confirm.getByLabel("Reason *").fill("Raised in error");
    await confirm.getByRole("button", { name: "Archive" }).click();
    await expect(page.getByText(/Archived entries are read-only/)).toBeVisible();
    await expect(page.getByRole("button", { name: /Edit/ })).toHaveCount(0);

    // 10. Hidden from the default register, visible with the archived filter.
    await openRegister(page);
    await expect(page.getByText("No hindrances or constraints recorded yet.")).toBeVisible();
    await page.getByLabel("Include archived").check();
    await expect(page.getByRole("row", { name: /HIN-0001/ })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("aside button").first().click(); // collapse the app sidebar
    await shot(page, "03-register-mobile");
  });

  test("a user assigned to another project cannot read or update the entry", async ({ page }) => {
    const state = new RegisterState();
    state.seq = 1;
    state.entries.push({
      id: "h-1",
      organization_id: "org-A",
      project_id: "proj-A1",
      event_type: "hindrance",
      hindrance_ref: "HIN-0001",
      title: "Site access blocked at Station S2",
      start_date: "2026-02-10T00:00:00",
      responsibility: "employer",
      critical_path_impact: false,
      status: "open",
      linked_document_ids: [],
      archived_at: null,
      created_at: "2026-02-10T08:00:00",
    });
    await mockApi(page, FOREIGN_PROJECT_USER, state);

    // 8a. Not in their register.
    await openRegister(page);
    await expect(page.getByText("No hindrances or constraints recorded yet.")).toBeVisible();

    // 8b. A crafted URL is refused, not rendered.
    await page.goto("/hindrances/h-1");
    await expect(page.getByText("This register entry is not available in the project selected in the navbar.")).toBeVisible();
    await expect(page.getByRole("button", { name: /Edit/ })).toHaveCount(0);

    // 8c. A direct API write from the page's own session is refused too: under the
    // user's own selection (403) and with no selection at all (400).
    const statuses = await page.evaluate(async () => {
      const write = (headers: Record<string, string>) =>
        fetch("/api/hindrances/h-1", {
          method: "PATCH",
          headers: { "Content-Type": "application/json", ...headers },
          body: JSON.stringify({ title: "Hijacked" }),
        }).then((response) => response.status);
      // Since CL-4A the page's fetch wrapper adds the navbar selection to every API
      // request; an explicitly empty header is how a request states "nothing selected".
      return [await write({ "X-Proj-Id": "proj-A2" }), await write({ "X-Proj-Id": "", "X-Org-Id": "" })];
    });
    expect(statuses).toEqual([403, 400]);
    expect(state.entries[0].title).toBe("Site access blocked at Station S2");
  });

  test("switching the navbar project re-scopes the register, its requests and its detail pages", async ({ page }) => {
    const state = new RegisterState();
    state.seq = 2;
    const entry = (id: string, project: string, ref: string, title: string) => ({
      id,
      organization_id: "org-A",
      project_id: project,
      event_type: "hindrance",
      hindrance_ref: ref,
      title,
      start_date: "2026-02-10T00:00:00",
      responsibility: "employer",
      critical_path_impact: false,
      status: "open",
      linked_document_ids: [],
      archived_at: null,
      timeline_sync_status: "synced",
      created_at: "2026-02-10T08:00:00",
    });
    state.entries.push(entry("h-1", "proj-A1", "HIN-0001", "A1 station access"), entry("h-2", "proj-A2", "HIN-0001", "A2 depot crane"));
    await mockApi(page, ORG_ADMIN, state);

    // A1 selected: only A1 rows; the A1 detail opens.
    await openRegister(page);
    await page.getByLabel("Select project").selectOption({ label: PROJECT_A1.name });
    await expect(page.getByRole("row", { name: /A1 station access/ })).toBeVisible();
    await expect(page.getByRole("row", { name: /A2 depot crane/ })).toHaveCount(0);
    await page.goto("/hindrances/h-1");
    await expect(page.getByRole("heading", { name: /A1 station access/ })).toBeVisible();

    // Switch to A2: the A1 row goes at once, and every later request carries A2.
    await openRegister(page);
    const switchedAt = state.requests.length;
    await page.getByLabel("Select project").selectOption({ label: PROJECT_A2.name });
    await expect(page.getByRole("row", { name: /A1 station access/ })).toHaveCount(0);
    await expect(page.getByRole("row", { name: /A2 depot crane/ })).toBeVisible();
    const afterSwitch = state.requests.slice(switchedAt);
    expect(afterSwitch.length).toBeGreaterThan(0);
    expect(afterSwitch.every((request) => request.project === "proj-A2")).toBe(true);

    // The old A1 detail URL is refused under A2, and nothing of it renders.
    await page.goto("/hindrances/h-1");
    await expect(page.getByText("This register entry is not available in the project selected in the navbar.")).toBeVisible();
    await expect(page.getByRole("heading", { name: /A1 station access/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Edit/ })).toHaveCount(0);

    // Create under A2 lands in A2.
    await openRegister(page);
    await page.getByRole("button", { name: /Record entry/ }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Title *").fill("A2 new constraint");
    await dialog.getByLabel("Start / occurrence date *").fill("2026-02-12");
    await dialog.getByRole("button", { name: "Record entry" }).click();
    await expect(page.getByRole("row", { name: /A2 new constraint/ })).toBeVisible();
    const created = state.entries.find((candidate) => candidate.title === "A2 new constraint");
    expect(created?.project_id).toBe("proj-A2");
    const post = state.requests.find((request) => request.method === "POST" && request.path === "/hindrances");
    expect(post?.project).toBe("proj-A2");

    // Switch back to A1: access to the A1 entry is restored.
    await page.getByLabel("Select project").selectOption({ label: PROJECT_A1.name });
    await expect(page.getByRole("row", { name: /A1 station access/ })).toBeVisible();
    await page.goto("/hindrances/h-1");
    await expect(page.getByRole("heading", { name: /A1 station access/ })).toBeVisible();
  });
});
