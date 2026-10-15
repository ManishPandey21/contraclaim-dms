/**
 * CL-2 correspondence linking, browser acceptance (stateful mock).
 *
 * Variation Register -> open Variation -> Link Correspondence -> search an
 * existing incoming letter -> link -> linked row appears -> open the letter ->
 * Linked Records shows the Variation -> navigate back -> unlink -> relationship
 * gone -> the letter remains.
 *
 * The network is mocked at `/api/**` with a small in-memory store that answers
 * in the shapes the real routers return (`Variation`, `DocumentRelationshipView`,
 * `DocumentListResponse`) and applies the server's own rules that matter here:
 * `uploadType=correspondence` matches incoming|outgoing only, a correspondence
 * role refuses a contract Document with 422, a re-link is idempotent, and unlink
 * soft-removes the relationship while the Document stays. It proves component
 * and routing behaviour, never a deployment: it is listed in MOCKED_SUITES and
 * excluded when E2E_BASE_URL targets staging.
 */

import { expect, Page, Route, test } from "@playwright/test";

const ORG = "org-A";
const PROJECT = "proj-A";

const INCOMING = {
  _id: "65f0c0ffee0000000000aa01", organization_id: ORG, project_id: PROJECT,
  filename: "ENG-VO-014.pdf", filetype: "application/pdf", filesize: 1024,
  subject: "Engineer instruction on added works", letterNo: "ENG/VO/014",
  date: "2026-09-01T00:00:00", from: "Engineer", to: "Contractor", uploadType: "incoming",
  status: "active", tags: [], subTags: [], processing_status: "metadata_extracted",
  lifecycle_state: "active", createdBy: "seed-user", createdAt: "2026-09-01T12:00:00",
};
const CONTRACT = {
  ...INCOMING, _id: "65f0c0ffee0000000000cc01", filename: "Particular-Conditions.pdf",
  subject: "Particular conditions", letterNo: "PC-1", uploadType: "contract",
};
// The production filename that pushed the Variation correspondence dialog past
// the viewport, plus one unbroken token: a name with no spaces has no soft wrap
// opportunity, which is the worst case for intrinsic width.
const LONG_OUTGOING = {
  ...INCOMING, _id: "65f0c0ffee0000000000aa02", uploadType: "outgoing",
  filename: "Kanpur-LET-JVTI-CPM-00550-E01-Variation statement no.01 for the Utility (Sewer & GRP Water Pipe line).pdf",
  letterNo: "Kanpur-LET-JVTI-CPM-00550-E01",
  subject: "Variation statement no.01 for the Utility (Sewer & GRP Water Pipe line) at Naveen Market and Bada Chauraha Metro Station.",
  from: "CONSULTING ENGINEERS JOINT VENTURE OF KANPUR METRO RAIL PROJECT", to: "GULERMAK-SAM INDIA KNPCC05",
};
const LONG_UNBROKEN = {
  ...INCOMING, _id: "65f0c0ffee0000000000aa03",
  filename: "Kanpur_LET_JVTI_CPM_00551_E01_Variation_statement_no02_for_the_Utility_Sewer_and_GRP_Water_Pipe_line_revised_final.pdf",
  letterNo: "Kanpur-LET-JVTI-CPM-00551-E01-REV-A-ANNEXURE-SCHEDULE-OF-RATES-AND-QUANTITIES",
  subject: "Variation statement no.02 for the Utility (Sewer & GRP Water Pipe line) at Naveen Market and Bada Chauraha Metro Station - revised.",
};
const VARIATION = {
  _id: "var-1", variation_number: "VO-001", variation_type: "positive", description: "Added drainage works",
  status: "submitted", organization_id: ORG, project_id: PROJECT, contract_id: "primary",
  currency_amounts: [], linked_document_ids: [] as string[], submitted_amount: 1000,
  created_at: "2026-09-02T00:00:00",
};

type Link = {
  _id: string; organization_id: string; project_id: string; target_type: string; target_id: string;
  document_id: string; relationship_role: string; source: string; _revision: number;
  removed_at: string | null; frozen_at: null; created_at: string;
};

// Evidence discipline (CL-2 item 19): one worker, no retries - a pass on a
// retry is not evidence. Pinned here so the default CI config cannot relax it.
test.describe.configure({ mode: "serial", retries: 0 });

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockBackend(
  page: Page,
  { extraDocuments = [], linkedDocumentIds = [] }: { extraDocuments?: typeof INCOMING[]; linkedDocumentIds?: string[] } = {},
) {
  const documents = [INCOMING, CONTRACT, ...extraDocuments];
  let sequence = 0;
  const links: Link[] = linkedDocumentIds.map((documentId) => ({
    _id: `link-${++sequence}`, organization_id: ORG, project_id: PROJECT, target_type: "variation",
    target_id: VARIATION._id, document_id: documentId, relationship_role: "correspondence",
    source: "user", _revision: 1, removed_at: null, frozen_at: null, created_at: "2026-09-03T00:00:00",
  }));
  const active = () => links.filter((link) => link.removed_at === null);
  const view = (link: Link) => ({
    ...link,
    document: documents.find((doc) => doc._id === link.document_id) ?? null,
    target_label: VARIATION.variation_number,
    target_route: `/variations?variation_id=${VARIATION._id}`,
  });
  const presentedVariation = () => ({
    ...VARIATION, linked_document_ids: active().map((link) => link.document_id),
  });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = decodeURIComponent(url.pathname.replace(/^\/api/, "") || "/");
    const method = request.method();

    if (path === "/csrf-token") return json(route, { csrf_token: "test-csrf" });
    if (path === "/me") {
      return json(route, {
        id: "user-1", email: "superadmin@example.com", roles: ["superadmin"],
        organization_id: ORG, projects: [PROJECT],
      });
    }
    if (path === "/security-terms/status") return json(route, { requires_acceptance: false });
    if (path === "/roles") return json(route, []);
    if (path === "/organizations") return json(route, { organizations: [{ _id: ORG, id: ORG, name: "Aurora" }] });
    if (path === "/projects") return json(route, [{ _id: PROJECT, id: PROJECT, name: "Metro 01", organization_id: ORG }]);

    if (path === "/variations/summary") {
      return json(route, {
        original_contract_value: 0, total_submitted_amount: 1000, total_approved_amount: 0,
        cumulative_approved_variation: 0, revised_contract_value: 0, percentage_variation: 0,
        pending_variation_count: 1, approved_variation_count: 0, rejected_variation_count: 0,
      });
    }
    if (path === "/variations") return json(route, [presentedVariation()]);
    if (path === `/variations/${VARIATION._id}`) return json(route, presentedVariation());

    if (path === `/entities/variation/${VARIATION._id}/document-links` && method === "GET") {
      return json(route, { links: active().map(view) });
    }
    if (path === `/entities/variation/${VARIATION._id}/document-links:batch` && method === "POST") {
      const body = request.postDataJSON() as { links: Array<{ document_id: string; relationship_role: string }> };
      const created: Link[] = [];
      for (const item of body.links) {
        const document = documents.find((doc) => doc._id === item.document_id);
        if (!document) return json(route, { detail: "Document not found" }, 404);
        if (["correspondence", "payment_correspondence"].includes(item.relationship_role)
          && !["incoming", "outgoing"].includes(document.uploadType.toLowerCase())) {
          return json(route, { detail: "Only incoming or outgoing correspondence can be linked as correspondence; use supporting_document for other Documents" }, 422);
        }
        const existing = active().find((link) => link.document_id === item.document_id
          && link.relationship_role === item.relationship_role);
        if (existing) { created.push(existing); continue; }
        const link: Link = {
          _id: `link-${++sequence}`, organization_id: ORG, project_id: PROJECT, target_type: "variation",
          target_id: VARIATION._id, document_id: item.document_id, relationship_role: item.relationship_role,
          source: "user", _revision: 1, removed_at: null, frozen_at: null, created_at: new Date().toISOString(),
        };
        links.push(link);
        created.push(link);
      }
      return json(route, { links: created.map(view) }, 201);
    }
    const remove = path.match(/^\/document-links\/([^/]+):remove$/);
    if (remove && method === "POST") {
      const link = links.find((item) => item._id === remove[1]);
      const body = request.postDataJSON() as { expected_revision: number };
      if (!link || link.removed_at !== null) return json(route, { detail: "Document relationship is already removed" }, 409);
      if (link._revision !== body.expected_revision) return json(route, { detail: "Document relationship was modified" }, 409);
      link.removed_at = new Date().toISOString();
      link._revision += 1;
      return json(route, { link: view(link) });
    }

    if (path === "/document-search") {
      const q = (url.searchParams.get("q") || "").toLowerCase();
      const uploadType = (url.searchParams.get("uploadType") || "").toLowerCase();
      const found = documents.filter((doc) => {
        const type = doc.uploadType.toLowerCase();
        if (uploadType === "correspondence" && !["incoming", "outgoing"].includes(type)) return false;
        if (uploadType && uploadType !== "correspondence" && type !== uploadType) return false;
        return !q || [doc.filename, doc.subject, doc.letterNo, doc.from, doc.to]
          .some((value) => String(value || "").toLowerCase().includes(q));
      });
      return json(route, { documents: found, total: found.length, skip: 0, limit: 25 });
    }

    const entityLinks = path.match(/^\/documents\/([^/]+)\/entity-links$/);
    if (entityLinks) {
      return json(route, { links: active().filter((link) => link.document_id === entityLinks[1]).map(view) });
    }
    const linkTargets = path.match(/^\/documents\/([^/]+)\/link-targets$/);
    if (linkTargets) return json(route, { document_id: linkTargets[1], target_type: url.searchParams.get("target_type"), targets: [] });
    const documentRow = path.match(/^\/documents\/([^/]+)$/);
    if (documentRow && method === "GET") {
      const document = documents.find((doc) => doc._id === documentRow[1]);
      return document ? json(route, document) : json(route, { detail: "Document not found" }, 404);
    }

    if (path.endsWith("/processing-status")) return json(route, { status: "completed" });
    if (/(^|\/)(users|notifications|tags|subtags|parties|letters|references|linked-documents)(\/|$)/.test(path)) {
      return json(route, []);
    }
    return json(route, {});
  });

  return {
    documents,
    activeLinks: () => active(),
    allLinks: () => links,
  };
}

test("links an existing incoming letter to a Variation and unlinks it without deleting the letter", async ({ page }) => {
  const store = await mockBackend(page);

  // Variation Register -> open the Variation's correspondence
  await page.goto("/variations");
  await expect(page.getByRole("heading", { name: "Variation Register" })).toBeVisible();
  await page.getByRole("button", { name: "Correspondence for VO-001" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Correspondence — VO-001")).toBeVisible();
  await expect(dialog.getByText("No linked Documents yet.")).toBeVisible();

  // Search existing correspondence: only incoming/outgoing is offered
  await dialog.getByLabel("Search Documents").fill("ENG/VO");
  await dialog.getByRole("button", { name: "Search" }).click();
  await expect(dialog.getByTestId("document-search-result")).toHaveCount(1);
  await expect(dialog.getByTestId("document-search-result")).toContainText("ENG/VO/014");
  await expect(dialog.getByTestId("document-search-result")).toContainText("Incoming");
  await expect(dialog.getByText("Particular-Conditions.pdf")).toHaveCount(0);

  // Link -> linked row appears
  await dialog.getByRole("button", { name: "Link ENG-VO-014.pdf" }).click();
  const linkedRow = dialog.getByTestId("linked-document");
  await expect(linkedRow).toHaveCount(1);
  await expect(linkedRow).toContainText("ENG-VO-014.pdf");
  await expect(linkedRow).toContainText("Correspondence");
  expect(store.activeLinks()).toHaveLength(1);

  // Open the letter -> Linked Records shows the Variation
  await linkedRow.getByRole("link").click();
  await expect(page).toHaveURL(new RegExp(`/documentviewer/${INCOMING._id}`));
  await page.getByRole("tab", { name: "Records" }).click();
  const record = page.getByTestId("linked-record");
  await expect(record).toHaveCount(1);
  await expect(record).toContainText("VO-001");
  await expect(record).toContainText("Variation · Correspondence");
  await expect(record).toHaveAttribute("href", `/variations?variation_id=${VARIATION._id}`);

  // Navigate back through the reverse deep link -> the Variation opens
  await record.click();
  await expect(page).toHaveURL(/\/variations\?variation_id=var-1/);
  const reopened = page.getByRole("dialog");
  await expect(reopened.getByText("Correspondence — VO-001")).toBeVisible();
  await expect(reopened.getByTestId("linked-document")).toHaveCount(1);

  // Unlink -> relationship gone
  await reopened.getByRole("button", { name: "Unlink ENG-VO-014.pdf" }).click();
  await expect(reopened.getByText("No linked Documents yet.")).toBeVisible();
  expect(store.activeLinks()).toHaveLength(0);
  expect(store.allLinks()[0].removed_at).not.toBeNull();

  // The letter remains, and its Linked Records no longer shows the Variation
  expect(store.documents.some((doc) => doc._id === INCOMING._id)).toBe(true);
  await page.goto(`/documentviewer/${INCOMING._id}`);
  await page.getByRole("tab", { name: "Records" }).click();
  await expect(page.getByText("No linked records.")).toBeVisible();
});

/**
 * Layout regression: at production desktop widths the dialog's content grew to
 * the min-content width of a long, unwrapped filename and of a five-column
 * filter grid that could not shrink, and spilled past the dialog's right edge
 * and the viewport. Assert geometry, not classes: the dialog sits inside the
 * viewport, every descendant sits inside the dialog, the page gains no
 * horizontal scroll, and the controls a user needs are on screen.
 */
const VIEWPORTS = [
  { width: 1920, height: 1080 },
  { width: 1600, height: 900 },
  { width: 1366, height: 768 },
  { width: 1280, height: 720 },
  { width: 1024, height: 768 },
  { width: 390, height: 844 },
];

for (const viewport of VIEWPORTS) {
  test(`correspondence dialog stays inside a ${viewport.width}x${viewport.height} viewport with long filenames`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockBackend(page, {
      extraDocuments: [LONG_OUTGOING, LONG_UNBROKEN],
      linkedDocumentIds: [LONG_OUTGOING._id],
    });

    if (viewport.width >= 768) {
      await page.goto("/variations");
      await page.getByRole("button", { name: "Correspondence for VO-001" }).click();
    } else {
      // On a phone the register table's row actions are not reachable (a page
      // layout matter outside this dialog); the deep link opens the same dialog.
      await page.goto(`/variations?variation_id=${VARIATION._id}`);
    }
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByTestId("linked-document")).toHaveCount(1);
    await expect(dialog.getByTestId("linked-document")).toContainText("Kanpur-LET-JVTI-CPM-00550-E01");

    await dialog.getByLabel("Search Documents").fill("Kanpur");
    await dialog.getByRole("button", { name: "Search" }).click();
    await expect(dialog.getByTestId("document-search-result")).toHaveCount(2);

    const overflow = await dialog.evaluate((root) => {
      const box = root.getBoundingClientRect();
      const outside = Array.from(root.querySelectorAll<HTMLElement>("*"))
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          if (rect.width === 0 && rect.height === 0) return false;
          if (element.closest(".sr-only")) return false;
          return rect.left < box.left - 1 || rect.right > box.right + 1;
        })
        .map((element) => `${element.tagName.toLowerCase()}.${element.className}`.slice(0, 120));
      return {
        dialog: { left: box.left, right: box.right, top: box.top, bottom: box.bottom },
        viewport: { width: window.innerWidth, height: window.innerHeight },
        pageScrollWidth: document.documentElement.scrollWidth,
        dialogScrollWidth: root.scrollWidth,
        dialogClientWidth: root.clientWidth,
        outside,
      };
    });

    expect(overflow.outside, "descendants outside the dialog").toEqual([]);
    expect(overflow.dialog.left).toBeGreaterThanOrEqual(0);
    expect(overflow.dialog.right).toBeLessThanOrEqual(overflow.viewport.width);
    expect(overflow.dialog.top).toBeGreaterThanOrEqual(0);
    expect(overflow.dialog.bottom).toBeLessThanOrEqual(overflow.viewport.height);
    expect(overflow.dialogScrollWidth).toBeLessThanOrEqual(overflow.dialogClientWidth);
    expect(overflow.pageScrollWidth).toBeLessThanOrEqual(overflow.viewport.width);

    // The full name stays reachable when the row clamps it.
    await expect(dialog.getByTestId("linked-document").getByRole("link")).toHaveAttribute("title", LONG_OUTGOING.filename);
    for (const control of [
      dialog.getByLabel("Relationship role"),
      dialog.getByLabel("Search Documents"),
      dialog.getByRole("button", { name: "Search" }),
      dialog.getByLabel("Direction"),
      dialog.getByLabel("Letter number"),
      dialog.getByLabel("Subject"),
      dialog.getByRole("button", { name: "Close" }),
    ]) {
      await expect(control).toBeInViewport({ ratio: 1 });
    }
    // Result actions stay reachable (the body scrolls, the dialog does not grow).
    const lastLink = dialog.getByRole("button", { name: `Link ${LONG_UNBROKEN.filename}` });
    await lastLink.scrollIntoViewIfNeeded();
    await expect(lastLink).toBeInViewport({ ratio: 1 });
    await expect(dialog.getByTestId("document-search-result").first()).toContainText("Already linked");

    await page.screenshot({ path: test.info().outputPath(`dialog-${viewport.width}x${viewport.height}.png`) });

    // Function is unchanged: link the second long letter, then close.
    await lastLink.click();
    await expect(dialog.getByTestId("linked-document")).toHaveCount(2);
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });
}
