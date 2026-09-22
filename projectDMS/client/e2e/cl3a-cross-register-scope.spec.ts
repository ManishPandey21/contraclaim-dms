/**
 * CL-3A: one letter linked to a Variation and a Hindrance, and the navbar
 * selection bounding both registers - browser workflow against a STATEFUL mock.
 *
 * User belongs to A1 and A2 (organisation admin, so the navbar project selector
 * is live). Select A1 -> open Variation A1 -> link Letter L -> open Hindrance A1
 * -> link the same Letter L -> open Letter L -> Linked Records shows both ->
 * switch the navbar to A2 -> the A1 rows leave both registers, and the direct
 * Variation A1 / Hindrance A1 URLs are refused -> switch back to A1 -> both are
 * reachable again.
 *
 * The mock applies the server's selection rules (`core/tenant_context.py`): the
 * `X-Proj-Id` header bounds every Variation / Hindrance / relationship request
 * (403 `context_forbidden` outside it, 400 `selection_required` for a
 * record-level request with none), lists follow it, and the Document reverse
 * lookup hides Variation / Hindrance rows outside it. It proves component,
 * routing and header behaviour, never a deployment: it is listed in
 * MOCKED_SUITES, and the real-stack staging run is still owed.
 */

import { expect, Page, Route, test } from "@playwright/test";

const ORG = { _id: "org-A", id: "org-A", name: "Aurora Engineering" };
const A1 = { _id: "proj-A1", id: "proj-A1", name: "Metro Package A1", organization_id: "org-A" };
const A2 = { _id: "proj-A2", id: "proj-A2", name: "Metro Package A2", organization_id: "org-A" };

const LETTER = {
  _id: "65f0c0ffee0000000000aa01", organization_id: ORG.id, project_id: A1.id,
  filename: "ENG-VO-014.pdf", filetype: "application/pdf", filesize: 1024,
  subject: "Engineer instruction on added works", letterNo: "ENG/VO/014",
  date: "2026-09-01T00:00:00", from: "Engineer", to: "Contractor", uploadType: "incoming",
  status: "active", tags: [], subTags: [], processing_status: "metadata_extracted",
  lifecycle_state: "active", createdBy: "seed-user", createdAt: "2026-09-01T12:00:00",
};

const variation = (id: string, project: string, number: string) => ({
  _id: id, id, variation_number: number, variation_type: "positive", description: `Works ${number}`,
  status: "submitted", organization_id: ORG.id, project_id: project, contract_id: "primary",
  currency_amounts: [], linked_document_ids: [] as string[], submitted_amount: 1000,
  created_at: "2026-09-02T00:00:00",
});
const hindrance = (id: string, project: string, ref: string, title: string) => ({
  id, organization_id: ORG.id, project_id: project, event_type: "hindrance", hindrance_ref: ref, title,
  start_date: "2026-02-10T00:00:00", responsibility: "employer", critical_path_impact: false,
  status: "open", linked_document_ids: [], archived_at: null, timeline_sync_status: "synced",
  created_at: "2026-02-10T08:00:00",
});

const VARIATIONS = [variation("var-A1", A1.id, "VO-A1-001"), variation("var-A2", A2.id, "VO-A2-001")];
const HINDRANCES = [
  hindrance("hin-A1", A1.id, "HIN-0001", "A1 station access blocked"),
  hindrance("hin-A2", A2.id, "HIN-0001", "A2 depot crane stood down"),
];

type Link = {
  _id: string; organization_id: string; project_id: string; target_type: string; target_id: string;
  document_id: string; relationship_role: string; source: string; _revision: number;
  removed_at: string | null; frozen_at: null; created_at: string;
};

// Evidence discipline: one worker, no retries - a pass on a retry is not evidence.
test.describe.configure({ mode: "serial", retries: 0, timeout: 120_000 });

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockBackend(page: Page) {
  const links: Link[] = [];
  const requests: Array<{ method: string; path: string; project: string }> = [];
  let sequence = 0;
  const active = () => links.filter((link) => link.removed_at === null);
  const target = (type: string, id: string) =>
    type === "variation" ? VARIATIONS.find((row) => row._id === id) : HINDRANCES.find((row) => row.id === id);
  const view = (link: Link) => {
    const row = target(link.target_type, link.target_id) as Record<string, string> | undefined;
    return {
      ...link,
      document: link.document_id === LETTER._id ? LETTER : null,
      target_label: link.target_type === "variation" ? row?.variation_number : row?.hindrance_ref,
      target_route: link.target_type === "variation"
        ? `/variations?variation_id=${link.target_id}` : `/hindrances/${link.target_id}`,
    };
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = decodeURIComponent(url.pathname.replace(/^\/api/, "") || "/");
    const method = request.method();
    const body = request.postDataJSON?.() ?? null;
    const selected = request.headers()["x-proj-id"] || "";
    const scoped = /^\/(variations|hindrances|entities\/(variation|delay_event))/.test(path);
    if (scoped) requests.push({ method, path, project: selected });
    const refuse = () => json(route, { detail: { code: "context_forbidden", message: "not in the selected project" } }, 403);
    const selectFirst = () => json(route, { detail: { code: "selection_required", message: "select a project" } }, 400);
    const holdRecord = (row: { project_id: string } | undefined) => {
      if (!selected) return selectFirst();
      if (!row) return json(route, { detail: "Not found" }, 404);
      if (row.project_id !== selected) return refuse();
      return null;
    };

    if (path === "/csrf-token") return json(route, { csrf_token: "test-csrf" });
    if (path === "/me" || path === "/users/me") {
      return json(route, {
        id: "u-orgadmin-a", email: "orgadmin@example.test", roles: ["orgadmin"],
        permissions: ["dms.variation.view", "dms.variation.edit", "dms.hindrance.view", "dms.hindrance.edit",
          "dms.hindrance.create", "dms.document.view"],
        organization_id: ORG.id, projects: [A1.id, A2.id],
      });
    }
    if (path === "/security-terms/status") return json(route, { requires_acceptance: false });
    if (path === "/roles") return json(route, []);
    if (path === "/organizations") return json(route, { organizations: [ORG] });
    if (path === "/projects") return json(route, [A1, A2]);

    // ---- Variation register -------------------------------------------
    if (path === "/variations/summary") {
      return json(route, {
        original_contract_value: 0, total_submitted_amount: 0, total_approved_amount: 0,
        cumulative_approved_variation: 0, revised_contract_value: 0, percentage_variation: 0,
        pending_variation_count: 0, approved_variation_count: 0, rejected_variation_count: 0,
      });
    }
    if (path === "/variations" && method === "GET") {
      const filter = url.searchParams.get("project_id");
      if (selected && filter && filter !== selected) return refuse();
      return json(route, VARIATIONS.filter((row) => !selected || row.project_id === selected)
        .map((row) => ({ ...row, linked_document_ids: active().filter((l) => l.target_id === row._id).map((l) => l.document_id) })));
    }
    const variationRow = path.match(/^\/variations\/([^/]+)$/);
    if (variationRow) {
      const row = VARIATIONS.find((candidate) => candidate._id === variationRow[1]);
      return holdRecord(row) ?? json(route, row);
    }

    // ---- Hindrance register -------------------------------------------
    if (path === "/hindrances" && method === "GET") {
      const items = HINDRANCES.filter((row) => !selected || row.project_id === selected);
      return json(route, { items, total: items.length, skip: 0, limit: 25 });
    }
    const hindranceRow = path.match(/^\/hindrances\/([^/]+)(\/.*)?$/);
    if (hindranceRow && hindranceRow[1] !== "affecting") {
      const row = HINDRANCES.find((candidate) => candidate.id === hindranceRow[1]);
      const refused = holdRecord(row);
      if (refused) return refused;
      const tail = hindranceRow[2] || "";
      if (!tail) return json(route, row);
      if (tail === "/history") return json(route, { entries: [] });
      if (tail === "/links") return json(route, { links: [] });
      return json(route, {});
    }
    if (path === "/programme-milestones" || path === "/key-dates") return json(route, []);
    if (path === "/key-dates/workflow") return json(route, { project_id: selected, baseline_status: "draft", submissions: [], determinations: [] });

    // ---- canonical relationships ----------------------------------------
    const forward = path.match(/^\/entities\/(variation|delay_event)\/([^/]+)\/document-links(:batch)?$/);
    if (forward) {
      const [, type, id, batch] = forward;
      const row = target(type, id) as { project_id: string } | undefined;
      const refused = holdRecord(row);
      if (refused) return refused;
      if (batch && method === "POST") {
        const created: Link[] = [];
        for (const item of body.links as Array<{ document_id: string; relationship_role: string }>) {
          const existing = active().find((link) => link.target_type === type && link.target_id === id
            && link.document_id === item.document_id && link.relationship_role === item.relationship_role);
          if (existing) { created.push(existing); continue; }
          const link: Link = {
            _id: `link-${++sequence}`, organization_id: ORG.id, project_id: row!.project_id, target_type: type,
            target_id: id, document_id: item.document_id, relationship_role: item.relationship_role,
            source: "user", _revision: 1, removed_at: null, frozen_at: null, created_at: new Date().toISOString(),
          };
          links.push(link);
          created.push(link);
        }
        return json(route, { links: created.map(view) }, 201);
      }
      return json(route, { links: active().filter((link) => link.target_type === type && link.target_id === id).map(view) });
    }
    if (path === "/document-search") {
      const uploadType = (url.searchParams.get("uploadType") || "").toLowerCase();
      const found = uploadType && !["correspondence", "incoming"].includes(uploadType) ? [] : [LETTER];
      return json(route, { documents: found, total: found.length, skip: 0, limit: 25 });
    }
    const entityLinks = path.match(/^\/documents\/([^/]+)\/entity-links$/);
    if (entityLinks) {
      // Variation / Hindrance rows outside the selection are never revealed.
      return json(route, {
        links: active().filter((link) => link.document_id === entityLinks[1]
          && (!selected || link.project_id === selected)).map(view),
      });
    }
    const linkTargets = path.match(/^\/documents\/([^/]+)\/link-targets$/);
    if (linkTargets) return json(route, { document_id: linkTargets[1], target_type: url.searchParams.get("target_type"), targets: [] });
    const documentRow = path.match(/^\/documents\/([^/]+)$/);
    if (documentRow && method === "GET") {
      return documentRow[1] === LETTER._id ? json(route, LETTER) : json(route, { detail: "Document not found" }, 404);
    }
    if (path.endsWith("/processing-status")) return json(route, { status: "completed" });
    if (/(^|\/)(users|notifications|tags|subtags|parties|letters|references|linked-documents)(\/|$)/.test(path)) {
      return json(route, []);
    }
    return json(route, method === "GET" ? [] : {});
  });

  return { links: () => active(), requests };
}

async function selectProject(page: Page, name: string) {
  await page.getByLabel("Select project").selectOption({ label: name });
}

test("one letter across Variation and Hindrance, bounded by the navbar selection", async ({ page }) => {
  const store = await mockBackend(page);

  // Select A1, open Variation A1, link Letter L.
  await page.goto("/variations");
  await selectProject(page, A1.name);
  await expect(page.getByTestId("variation-scope")).toContainText(A1.name);
  await expect(page.getByRole("cell", { name: "VO-A2-001", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Correspondence for VO-A1-001" }).click();
  const variationDialog = page.getByRole("dialog");
  await variationDialog.getByLabel("Search Documents").fill("ENG/VO");
  await variationDialog.getByRole("button", { name: "Search" }).click();
  await variationDialog.getByRole("button", { name: "Link ENG-VO-014.pdf" }).click();
  await expect(variationDialog.getByTestId("linked-document")).toContainText("ENG-VO-014.pdf");
  await page.keyboard.press("Escape");

  // Open Hindrance A1, link the same Letter L.
  await page.goto("/hindrances/hin-A1");
  await expect(page.getByRole("heading", { name: /A1 station access blocked/ })).toBeVisible();
  await page.getByLabel("Search Documents").fill("ENG");
  await page.getByRole("button", { name: "Search" }).first().click();
  await page.getByRole("button", { name: "Link ENG-VO-014.pdf" }).click();
  await expect(page.getByRole("link", { name: /ENG-VO-014\.pdf/ }).first()).toBeVisible();
  expect(store.links().map((link) => `${link.target_type}:${link.target_id}`).sort())
    .toEqual(["delay_event:hin-A1", "variation:var-A1"]);

  // Open Letter L: Linked Records shows Variation A1 and Hindrance A1.
  await page.goto(`/documentviewer/${LETTER._id}`);
  await page.getByRole("tab", { name: "Records" }).click();
  const records = page.getByTestId("linked-record");
  await expect(records).toHaveCount(2);
  await expect(records.filter({ hasText: "VO-A1-001" })).toContainText("Variation · Correspondence");
  await expect(records.filter({ hasText: "HIN-0001" })).toContainText("Hindrance / Constraint");
  await expect(records.filter({ hasText: "HIN-0001" })).toHaveAttribute("href", "/hindrances/hin-A1");

  // Switch the navbar to A2: A1 records leave both registers' list state.
  await page.goto("/variations");
  const switchedAt = store.requests.length;
  await selectProject(page, A2.name);
  await expect(page.getByRole("cell", { name: "VO-A2-001", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "VO-A1-001", exact: true })).toHaveCount(0);
  await page.goto("/hindrances");
  await expect(page.getByRole("row", { name: /A2 depot crane/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /A1 station access/ })).toHaveCount(0);
  const afterSwitch = store.requests.slice(switchedAt);
  expect(afterSwitch.length).toBeGreaterThan(0);
  expect(afterSwitch.every((request) => request.project === A2.id)).toBe(true);

  // Direct Variation A1 URL: refused, nothing of it renders.
  await page.goto("/variations?variation_id=var-A1");
  await expect(page.getByText("This variation is not in the project selected in the navbar.")).toBeVisible();
  await expect(page.getByText(/Correspondence — VO-A1-001/)).toHaveCount(0);

  // Direct Hindrance A1 URL: refused.
  await page.goto("/hindrances/hin-A1");
  await expect(page.getByText("This register entry is not available in the project selected in the navbar.")).toBeVisible();
  await expect(page.getByRole("heading", { name: /A1 station access blocked/ })).toHaveCount(0);

  // Letter L under A2 reveals neither A1 record.
  await page.goto(`/documentviewer/${LETTER._id}`);
  await page.getByRole("tab", { name: "Records" }).click();
  await expect(page.getByText("No linked records.")).toBeVisible();

  // Switch back to A1: both are reachable again.
  await selectProject(page, A1.name);
  await page.goto("/variations?variation_id=var-A1");
  await expect(page.getByText(/Correspondence — VO-A1-001/)).toBeVisible();
  await page.goto("/hindrances/hin-A1");
  await expect(page.getByRole("heading", { name: /A1 station access blocked/ })).toBeVisible();
});
