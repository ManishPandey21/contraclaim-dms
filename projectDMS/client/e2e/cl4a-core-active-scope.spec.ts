/**
 * CL-4A: the navbar project bounds the core DMS modules - browser workflow
 * against a STATEFUL mock.
 *
 * User belongs to A1 and A2 (organisation admin, so the navbar project selector
 * is live). Select A1 -> Documents, Claims, IPC, Insurance, Bank Guarantees and
 * Key Dates each show their A1 record -> switch the navbar to A2 -> the A1 rows
 * leave every register and every request after the switch carries A2 (including
 * the Documents register, which reaches the API through `authenticatedFetch`) ->
 * direct A1 URLs are refused -> switch back to A1 -> access is restored.
 *
 * The mock applies the server's selection rules (`core/tenant_context.py`): a
 * list follows `X-Proj-Id` and refuses a filter naming another project, a
 * record-level request is 400 `selection_required` with nothing selected and
 * 403 `context_forbidden` outside the selection. It proves component, routing
 * and header behaviour, never a deployment: it is listed in MOCKED_SUITES, and
 * the real-stack staging run is still owed.
 */

import { expect, Page, Route, test } from "@playwright/test";

const ORG = { _id: "org-A", id: "org-A", name: "Aurora Engineering" };
const A1 = { _id: "proj-A1", id: "proj-A1", name: "Metro Package A1", organization_id: "org-A" };
const A2 = { _id: "proj-A2", id: "proj-A2", name: "Metro Package A2", organization_id: "org-A" };

type Row = Record<string, unknown> & { _id: string; id: string; project_id: string };
const row = (id: string, project: string, fields: Record<string, unknown>): Row => ({
  _id: id, id, organization_id: ORG.id, project_id: project, created_at: "2026-09-01T00:00:00", ...fields,
});
const document = (id: string, project: string, letterNo: string, subject: string) =>
  row(id, project, {
    filename: `${letterNo.replace(/\//g, "-")}.pdf`, filetype: "application/pdf", filesize: 1024, subject,
    letterNo, date: "2026-09-01T00:00:00", from: "Engineer", to: "Contractor", uploadType: "incoming",
    status: "active", tags: [], subTags: [], processing_status: "metadata_extracted",
    lifecycle_state: "active", createdBy: "seed-user", createdAt: "2026-09-01T12:00:00",
  });

/** One A1 and one A2 record per register; the label is what the register shows. */
const REGISTERS: Record<string, { rows: Row[]; label: (r: Row) => string }> = {
  documents: {
    rows: [
      document("65f0c0ffee0000000000c4a1", A1.id, "ENG/A1/001", "A1 site instruction"),
      document("65f0c0ffee0000000000c4a2", A2.id, "ENG/A2/001", "A2 depot notice"),
    ],
    label: (r) => String(r.letterNo),
  },
  claims: {
    rows: [
      row("claim-A1", A1.id, { title: "A1 prolongation claim", type: "eot", status: "draft", claim_ref: "CLM-A1" }),
      row("claim-A2", A2.id, { title: "A2 disruption claim", type: "other", status: "draft", claim_ref: "CLM-A2" }),
    ],
    label: (r) => String(r.title),
  },
  "ipc-bills": {
    rows: [
      row("ipc-A1", A1.id, { ipc_number: "IPC-A1-07", status: "draft", payment_status: "unpaid", currency: "INR" }),
      row("ipc-A2", A2.id, { ipc_number: "IPC-A2-03", status: "draft", payment_status: "unpaid", currency: "INR" }),
    ],
    label: (r) => String(r.ipc_number),
  },
  insurance: {
    rows: [
      row("ins-A1", A1.id, { policy_number: "POL-A1-CAR", insurance_type: "CAR", status: "active",
        date_of_issue: "2026-01-01T00:00:00", date_of_expiry: "2027-01-01T00:00:00" }),
      row("ins-A2", A2.id, { policy_number: "POL-A2-WC", insurance_type: "WC", status: "active",
        date_of_issue: "2026-01-01T00:00:00", date_of_expiry: "2027-01-01T00:00:00" }),
    ],
    label: (r) => String(r.policy_number),
  },
  "bank-guarantees": {
    rows: [
      row("bg-A1", A1.id, { bg_number: "BG-A1-PERF", bg_type: "performance", status: "valid", bg_amount: 1000,
        issue_date: "2026-01-01T00:00:00", expiry_date: "2027-01-01T00:00:00" }),
      row("bg-A2", A2.id, { bg_number: "BG-A2-ADV", bg_type: "advance", status: "valid", bg_amount: 500,
        issue_date: "2026-01-01T00:00:00", expiry_date: "2027-01-01T00:00:00" }),
    ],
    label: (r) => String(r.bg_number),
  },
  "key-dates": {
    rows: [
      row("kd-A1", A1.id, { title: "A1 station handover", milestone_ref: "KD-A1", status: "pending",
        contractual_week_number: 40, current_key_date: "2026-12-01T00:00:00" }),
      row("kd-A2", A2.id, { title: "A2 depot energisation", milestone_ref: "KD-A2", status: "pending",
        contractual_week_number: 30, current_key_date: "2026-11-01T00:00:00" }),
    ],
    label: (r) => String(r.title),
  },
};

const SUMMARIES: Record<string, unknown> = {
  "/ipc-bills/summary": {
    total_ipcs: 0, base_currency: "INR", total_claimed_base: 0, total_approved_base: 0,
    total_net_payable_base: 0, total_paid_base: 0, total_balance_payable_base: 0, percent_of_contract_billed: 0,
  },
  "/insurance/summary": { total: 0, active: 0, expiring_soon: 0, expired: 0, missing: 0 },
  "/insurance/alerts": [],
  "/bank-guarantees/summary": {
    total: 0, total_bg_amount: 0, valid: 0, extension_required: 0, expiring_45: 0, expiring_30: 0, expired: 0,
  },
  "/bank-guarantees/alerts": [],
  "/key-dates/dashboard": { total: 0, achieved: 0, overdue: 0, due_30: 0, eot_under_review: 0, eot_approved: 0 },
};

// Evidence discipline: one worker, no retries - a pass on a retry is not evidence.
test.describe.configure({ mode: "serial", retries: 0, timeout: 180_000 });

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockBackend(page: Page) {
  const requests: Array<{ method: string; path: string; project: string }> = [];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = decodeURIComponent(url.pathname.replace(/^\/api/, "") || "/");
    const method = request.method();
    const selected = request.headers()["x-proj-id"] || "";
    const register = Object.keys(REGISTERS).find((name) => path === `/${name}` || path.startsWith(`/${name}/`));
    if (register || path === "/dashboard/stats") requests.push({ method, path, project: selected });
    const refuse = () => json(route, { detail: { code: "context_forbidden", message: "This record is not available in the project selected in the navbar." } }, 403);
    const selectFirst = () => json(route, { detail: { code: "selection_required", message: "Select a project in the navbar to continue." } }, 400);

    if (path === "/csrf-token") return json(route, { csrf_token: "test-csrf" });
    if (path === "/me" || path === "/users/me") {
      return json(route, {
        id: "u-orgadmin-a", email: "orgadmin@example.test", roles: ["orgadmin"],
        permissions: ["dms.document.view", "dms.claim.view", "dms.claim.edit", "dms.ipc.view", "dms.ipc.edit",
          "dms.insurance.view", "dms.insurance.edit", "dms.bg.view", "dms.bg.edit", "dms.keydate.view",
          "dms.keydate.edit"],
        organization_id: ORG.id, projects: [A1.id, A2.id],
      });
    }
    if (path === "/security-terms/status") return json(route, { requires_acceptance: false });
    if (path === "/roles") return json(route, []);
    if (path === "/organizations") return json(route, { organizations: [ORG] });
    if (path === "/projects") return json(route, [A1, A2]);
    if (path in SUMMARIES) return json(route, SUMMARIES[path]);
    if (path === "/insurance/types") return json(route, []);
    if (path === "/key-dates/workflow") {
      return json(route, { project_id: selected, baseline_status: "draft", submissions: [], determinations: [] });
    }

    if (register) {
      const { rows } = REGISTERS[register];
      const list = path === `/${register}`;
      if (list && method === "GET") {
        const filter = url.searchParams.get("project_id");
        if (selected && filter && filter !== selected) return refuse();
        const visible = rows.filter((candidate) => (selected ? candidate.project_id === selected : true));
        return register === "documents"
          ? json(route, { documents: visible, total: visible.length })
          : json(route, visible);
      }
      const [, , id, tail] = path.split("/");
      const record = rows.find((candidate) => candidate.id === id);
      if (record || /^[\w-]+$/.test(id || "")) {
        if (!selected) return selectFirst();
        if (!record) return json(route, { detail: "Not found" }, 404);
        if (record.project_id !== selected) return refuse();
        if (!tail) return json(route, record);
        return json(route, method === "GET" ? [] : {});
      }
    }
    if (path === "/dashboard/stats") return json(route, {});
    if (/(^|\/)(users|notifications|tags|subtags|parties|letters|references|linked-documents|sla|tasks)(\/|$)/.test(path)) {
      return json(route, []);
    }
    return json(route, method === "GET" ? [] : {});
  });

  return { requests };
}

async function selectProject(page: Page, name: string) {
  await page.getByLabel("Select project").selectOption({ label: name });
}

/** The register page for each module, and a direct URL to its A1 record. */
const PAGES: Array<{ register: string; url: string; direct: string; refused: RegExp }> = [
  { register: "documents", url: "/documents", direct: "/documentviewer/65f0c0ffee0000000000c4a1",
    refused: /not available in the project selected in the navbar/ },
  { register: "claims", url: "/claims", direct: "/claims/claim-A1",
    refused: /This claim is not in the project selected in the navbar/ },
  { register: "ipc-bills", url: "/ipc-bills", direct: "/ipc-bills?ipc_id=ipc-A1",
    refused: /This IPC is not in the project selected in the navbar/ },
  { register: "insurance", url: "/insurance", direct: "/insurance?insurance_id=ins-A1",
    refused: /This insurance policy is not in the project selected in the navbar/ },
  { register: "bank-guarantees", url: "/bank-guarantees", direct: "", refused: /^$/ },
  { register: "key-dates", url: "/key-dates", direct: "/key-dates/kd-A1",
    refused: /This key date is not in the project selected in the navbar/ },
];

const label = (register: string, project: string) =>
  REGISTERS[register].label(REGISTERS[register].rows.find((r) => r.project_id === project)!);

test("the navbar project bounds Documents, Claims, IPC, Insurance, BG and Key Dates", async ({ page }) => {
  const store = await mockBackend(page);

  // Selected A1: every register shows its A1 record and none of A2's.
  await page.goto("/documents");
  await selectProject(page, A1.name);
  for (const { register, url } of PAGES) {
    await page.goto(url);
    await expect(page.getByText(label(register, A1.id), { exact: true }).first()).toBeVisible();
    await expect(page.getByText(label(register, A2.id), { exact: true })).toHaveCount(0);
  }

  // Switch to A2: the A1 rows disappear, and every request after the switch carries A2.
  const switchedAt = store.requests.length;
  await selectProject(page, A2.name);
  for (const { register, url } of PAGES) {
    await page.goto(url);
    await expect(page.getByText(label(register, A2.id), { exact: true }).first()).toBeVisible();
    await expect(page.getByText(label(register, A1.id), { exact: true })).toHaveCount(0);
  }
  const afterSwitch = store.requests.slice(switchedAt);
  expect(new Set(afterSwitch.map((request) => request.path.split("/")[1]))).toEqual(
    new Set(PAGES.map(({ register }) => register)),
  );
  expect(afterSwitch.filter((request) => request.project !== A2.id)).toEqual([]);

  // Direct A1 URLs under A2: refused, and nothing of the A1 record renders.
  for (const { register, direct, refused } of PAGES) {
    if (!direct) continue;
    await page.goto(direct);
    await expect(page.getByText(refused).first()).toBeVisible();
    await expect(page.getByText(label(register, A1.id), { exact: true })).toHaveCount(0);
  }

  // Switch back to A1: access is restored.
  await page.goto("/documents");
  await selectProject(page, A1.name);
  await page.goto("/claims/claim-A1");
  await expect(page.getByText(label("claims", A1.id)).first()).toBeVisible();
  await page.goto("/key-dates/kd-A1");
  await expect(page.getByText(label("key-dates", A1.id)).first()).toBeVisible();
  await page.goto(`/documentviewer/${REGISTERS.documents.rows[0].id}`);
  await expect(page.getByText(/not available in the project selected/)).toHaveCount(0);
  for (const { register, url } of PAGES) {
    await page.goto(url);
    await expect(page.getByText(label(register, A1.id), { exact: true }).first()).toBeVisible();
  }
});
