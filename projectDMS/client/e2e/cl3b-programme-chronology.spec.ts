/**
 * CL-3B: one letter linked to a Programme Milestone and a Chronology event, both
 * bounded by the navbar selection - browser workflow against a STATEFUL mock.
 *
 * Organisation admin of A1 and A2. Select A1 -> open Programme A1 -> link Letter L
 * -> open Chronology A1 at its event (deep link) -> link the same Letter L -> open
 * Letter L -> Linked Records shows both -> switch the navbar to A2 -> the A1
 * records are refused and leave the Letter's Linked Records -> switch back to A1
 * -> both are reachable -> unlink the Programme link -> the Chronology link stays.
 *
 * The mock applies the server's selection rules (`core/tenant_context.py`): the
 * `X-Proj-Id` header bounds every Programme / Chronology / relationship request
 * (403 `context_forbidden` outside it, 400 `selection_required` for a
 * record-level request with none) and the Document reverse lookup hides their
 * rows outside it. It proves component, routing and header behaviour, never a
 * deployment: it is listed in MOCKED_SUITES, and the real-stack staging run is
 * still owed (staging plan §15).
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

const milestone = (id: string, project: string, ref: string, title: string) => ({
  _id: id, organization_id: ORG.id, project_id: project, milestone_ref: ref, title,
  milestone_type: "programme_activity", status: "in_progress", planned_date: "2026-03-01T00:00:00",
  linked_document_ids: [], metadata: {},
});
const chronology = (id: string, project: string, title: string) => ({
  _id: id, organization_id: ORG.id, project_id: project, title, chronology_type: "eot_delay",
  party_perspective: "claimant", status: "review", selected_source_ids: [], selected_source_types: [],
  settings: {}, summary_counts: {},
});
const chronologyEvent = (id: string, chronologyId: string, project: string, title: string) => ({
  _id: id, chronology_id: chronologyId, organization_id: ORG.id, project_id: project, title,
  event_date: "2026-02-10T00:00:00", date_type: "exact", verification_status: "verified",
  event_classification: "notice", supports_party: "claimant", impact_type: "time", pleading_use: "none",
  contract_clauses: [], issue_tags: [], claim_heads: [], related_document_ids: [], related_event_ids: [],
  event_link_ids: [], source_spans: [],
});

const MILESTONES = [milestone("pm-A1", A1.id, "PM-110", "Pier P4 piling"), milestone("pm-A2", A2.id, "PM-210", "Viaduct span")];
const CHRONOLOGIES = [chronology("chr-A1", A1.id, "Station S2 delay chronology"), chronology("chr-A2", A2.id, "Depot chronology")];
const EVENTS = [
  chronologyEvent("ev-A1", "chr-A1", A1.id, "Engineer instruction received"),
  chronologyEvent("ev-A2", "chr-A2", A2.id, "Depot handover"),
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
    type === "programme_milestone" ? MILESTONES.find((row) => row._id === id) : EVENTS.find((row) => row._id === id);
  const view = (link: Link) => {
    const row = target(link.target_type, link.target_id) as Record<string, string> | undefined;
    return {
      ...link,
      document: link.document_id === LETTER._id ? LETTER : null,
      target_label: link.target_type === "programme_milestone"
        ? `${row?.milestone_ref} · ${row?.title}` : `2026-02-10 · ${row?.title}`,
      target_route: link.target_type === "programme_milestone"
        ? `/programme-milestones/${link.target_id}` : `/chronology/${row?.chronology_id}?event_id=${link.target_id}`,
    };
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = decodeURIComponent(url.pathname.replace(/^\/api/, "") || "/");
    const method = request.method();
    const body = request.postDataJSON?.() ?? null;
    const selected = request.headers()["x-proj-id"] || "";
    const scoped = /^\/(programme-milestones|chronologies|entities\/(programme_milestone|chronology_event))/.test(path);
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
        permissions: ["dms.evidence_graph.view", "dms.evidence_graph.manage", "dms.chronology.view",
          "dms.chronology.edit", "dms.document.view"],
        organization_id: ORG.id, projects: [A1.id, A2.id],
      });
    }
    if (path === "/security-terms/status") return json(route, { requires_acceptance: false });
    if (path === "/roles") return json(route, []);
    if (path === "/organizations") return json(route, { organizations: [ORG] });
    if (path === "/projects") return json(route, [A1, A2]);

    // ---- Programme Milestones ------------------------------------------
    const milestoneRow = path.match(/^\/programme-milestones\/([^/]+)$/);
    if (milestoneRow) {
      const row = MILESTONES.find((candidate) => candidate._id === milestoneRow[1]);
      return holdRecord(row) ?? json(route, row);
    }

    // ---- Chronology -----------------------------------------------------
    if (path === "/chronologies" && method === "GET") {
      return json(route, CHRONOLOGIES.filter((row) => !selected || row.project_id === selected));
    }
    const chronologyRow = path.match(/^\/chronologies\/([^/]+)(\/.*)?$/);
    if (chronologyRow) {
      const row = CHRONOLOGIES.find((candidate) => candidate._id === chronologyRow[1]);
      const refused = holdRecord(row);
      if (refused) return refused;
      const tail = chronologyRow[2] || "";
      if (tail === "/events") return json(route, EVENTS.filter((event) => event.chronology_id === row!._id));
      if (tail === "/pleading-context") return json(route, { chronology_id: row!._id, source_ledger: [], missing_evidence: [], summary: {} });
      return json(route, row);
    }

    // ---- canonical relationships ----------------------------------------
    const forward = path.match(/^\/entities\/(programme_milestone|chronology_event)\/([^/]+)\/document-links(:batch)?$/);
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
    const removal = path.match(/^\/document-links\/([^/]+):remove$/);
    if (removal && method === "POST") {
      const link = active().find((candidate) => candidate._id === removal[1]);
      if (!link) return json(route, { detail: "Document relationship not found" }, 404);
      const refused = holdRecord(target(link.target_type, link.target_id) as { project_id: string } | undefined);
      if (refused) return refused;
      link.removed_at = new Date().toISOString();
      link._revision += 1;
      return json(route, { link: view(link) });
    }
    if (path === "/document-search") {
      const uploadType = (url.searchParams.get("uploadType") || "").toLowerCase();
      const found = uploadType && !["correspondence", "incoming"].includes(uploadType) ? [] : [LETTER];
      return json(route, { documents: found, total: found.length, skip: 0, limit: 25 });
    }
    const entityLinks = path.match(/^\/documents\/([^/]+)\/entity-links$/);
    if (entityLinks) {
      // Programme / Chronology rows outside the selection are never revealed.
      return json(route, {
        links: active().filter((link) => link.document_id === entityLinks[1]
          && (!selected || link.project_id === selected)).map(view),
      });
    }
    const targetTypes = path.match(/^\/documents\/([^/]+)\/link-target-types$/);
    if (targetTypes) {
      // Selection-bound types only while the selection is the Letter's project.
      return json(route, {
        document_id: targetTypes[1],
        target_types: selected === LETTER.project_id ? ["programme_milestone", "chronology_event"] : [],
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

async function linkLetter(page: Page) {
  await page.getByLabel("Search Documents").fill("ENG");
  await page.getByRole("button", { name: "Search" }).first().click();
  await page.getByRole("button", { name: "Link ENG-VO-014.pdf" }).click();
}

test("one letter across Programme and Chronology, bounded by the navbar selection", async ({ page }) => {
  const store = await mockBackend(page);

  // Select A1, open Programme A1, link Letter L.
  await page.goto("/programme-milestones/pm-A1");
  await selectProject(page, A1.name);
  await expect(page.getByRole("heading", { name: /Pier P4 piling/ })).toBeVisible();
  await linkLetter(page);
  await expect(page.getByTestId("linked-document")).toContainText("ENG-VO-014.pdf");

  // Open Chronology A1 at its event (the deep link opens its Documents), link the same Letter L.
  await page.goto("/chronology/chr-A1?event_id=ev-A1");
  const event = page.locator('[data-event-id="ev-A1"]');
  await expect(event).toContainText("Engineer instruction received");
  await expect(event.getByRole("button", { name: "Hide documents" })).toBeVisible();
  await linkLetter(page);
  await expect(event.getByTestId("linked-document")).toContainText("ENG-VO-014.pdf");
  expect(store.links().map((link) => `${link.target_type}:${link.target_id}`).sort())
    .toEqual(["chronology_event:ev-A1", "programme_milestone:pm-A1"]);

  // Open Letter L: Linked Records shows both, with their deep links.
  await page.goto(`/documentviewer/${LETTER._id}`);
  await page.getByRole("tab", { name: "Records" }).click();
  const records = page.getByTestId("linked-record");
  await expect(records).toHaveCount(2);
  await expect(records.filter({ hasText: "PM-110" })).toContainText("Programme milestone · Progress evidence");
  await expect(records.filter({ hasText: "PM-110" })).toHaveAttribute("href", "/programme-milestones/pm-A1");
  await expect(records.filter({ hasText: "Engineer instruction received" })).toContainText("Chronology event · Correspondence");
  await expect(records.filter({ hasText: "Engineer instruction received" }))
    .toHaveAttribute("href", "/chronology/chr-A1?event_id=ev-A1");
  await expect(page.getByRole("button", { name: /Link to Record/ })).toBeVisible();

  // Switch the navbar to A2: the A1 records are refused and leave the letter.
  const switchedAt = store.requests.length;
  await selectProject(page, A2.name);
  // The switch remounts the routed page (MainLayout), back on its default tab.
  await page.getByRole("tab", { name: "Records" }).click();
  await expect(page.getByText("No linked records.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Link to Record/ })).toHaveCount(0);
  await page.goto("/programme-milestones/pm-A1");
  await expect(page.getByText("This programme milestone is not available in the project selected in the navbar.")).toBeVisible();
  await expect(page.getByRole("heading", { name: /Pier P4 piling/ })).toHaveCount(0);
  await page.goto("/chronology/chr-A1?event_id=ev-A1");
  await expect(page.getByRole("button", { name: /Depot chronology/ })).toBeVisible();
  await expect(page.locator('[data-event-id="ev-A1"]')).toHaveCount(0);
  const afterSwitch = store.requests.slice(switchedAt);
  expect(afterSwitch.length).toBeGreaterThan(0);
  expect(afterSwitch.every((request) => request.project === A2.id)).toBe(true);

  // Switch back to A1: both are reachable again.
  await selectProject(page, A1.name);
  await page.goto("/chronology/chr-A1?event_id=ev-A1");
  await expect(page.locator('[data-event-id="ev-A1"]').getByTestId("linked-document")).toContainText("ENG-VO-014.pdf");
  await page.goto("/programme-milestones/pm-A1");
  await expect(page.getByRole("heading", { name: /Pier P4 piling/ })).toBeVisible();

  // Unlink the Programme link only: the Chronology link remains.
  await page.getByRole("button", { name: "Unlink ENG-VO-014.pdf" }).click();
  await expect(page.getByText("No linked Documents yet.")).toBeVisible();
  expect(store.links().map((link) => `${link.target_type}:${link.target_id}`)).toEqual(["chronology_event:ev-A1"]);
  await page.goto(`/documentviewer/${LETTER._id}`);
  await page.getByRole("tab", { name: "Records" }).click();
  await expect(page.getByTestId("linked-record")).toHaveCount(1);
  await expect(page.getByTestId("linked-record")).toContainText("Chronology event");
});
