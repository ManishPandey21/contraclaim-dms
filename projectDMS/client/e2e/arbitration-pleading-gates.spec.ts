import { expect, Page, Route, test } from "@playwright/test";

/**
 * Pleading approval gates in the case workspace (plan 4B-40, B4) - mocked `/api/**`.
 *
 * Proves component behaviour, not a deployment (excluded from deployed-stack runs in
 * playwright.config.ts). What it checks, as the plan's proof for 4B-40 asks:
 *   - the approval-gate stepper names the gate the workflow is at and who acts there;
 *   - no raw id, hash or internal node name is visible anywhere on the page, although
 *     the mocked server sends ObjectIds, UUIDs and SHA-256 hashes in its records.
 */

const ORG = { _id: "65f0c0ffee0000000000a001", id: "65f0c0ffee0000000000a001", name: "Aurora Engineering" };
const PROJECT = {
  _id: "65f0c0ffee0000000000b001",
  id: "65f0c0ffee0000000000b001",
  name: "Metro Package A1",
  organization_id: ORG._id,
};
const CASE_ID = "1b4e28ba-2fa1-11d2-883f-0016d3cca427";
const DRAFT_ID = "65f0c0ffee0000000000d001";
const HASH = "a1".repeat(32);

const CASE = {
  _id: CASE_ID,
  organization_id: ORG._id,
  project_id: PROJECT._id,
  title: "ABC Infra v. Metro Authority",
  party_perspective: "claimant",
  status: "matrix_preparation",
  readiness_score: 40,
  readiness_blockers: [],
};

const WORKFLOW = {
  run_id: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  case_id: CASE_ID,
  draft_id: DRAFT_ID,
  pleading_type: "statement_of_claim",
  engine: "langgraph_v1",
  rollout_mode: "canary",
  status: "awaiting_plan_approval",
  current_node: "plan_approval_gate",
  next_action: "approve_plan",
  state_version: 5,
  progress: 60,
  blockers: [],
  required_human_role: "senior_legal_approver",
  fallback_available: true,
  matrix_revision_hash: HASH,
  readiness_artifact_hash: HASH,
  plan_hash: HASH,
  approval_receipt_ids: {
    document_selection: "65f0c0ffee0000000000e001",
    matrix_review: "65f0c0ffee0000000000e002",
    readiness: "65f0c0ffee0000000000e003",
  },
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api/, "") || "/";
    if (path === "/csrf-token") return json(route, { csrf_token: "test-csrf" });
    if (path === "/me" || path === "/users/me") {
      return json(route, {
        id: "65f0c0ffee0000000000c001",
        email: "counsel@example.test",
        roles: ["orgadmin"],
        permissions: ["dms.arbitration.view", "dms.arbitration.edit", "dms.arbitration.create", "dms.document.view"],
        organization_id: ORG._id,
        projects: [PROJECT._id],
      });
    }
    if (path === "/security-terms/status") return json(route, { requires_acceptance: false });
    if (path === "/organizations") return json(route, { organizations: [ORG] });
    if (path === "/projects") return json(route, [PROJECT]);
    if (path === `/arbitration/cases/${CASE_ID}`) return json(route, CASE);
    if (path === `/arbitration/cases/${CASE_ID}/dashboard`) {
      return json(route, {
        case: CASE,
        matrix_counts: { "claim-matrix": 2 },
        approved_counts: { "claim-matrix": 2 },
        drafts: [
          { _id: DRAFT_ID, case_id: CASE_ID, title: "Statement of Claim", draft_type: "statement_of_claim", status: "draft" },
        ],
        readiness: { case_id: CASE_ID, readiness_score: 40, status: "blocked", blockers: [], checks: [] },
        latest_agent_runs: [],
      });
    }
    if (path === `/arbitration/cases/${CASE_ID}/workflows`) return json(route, [WORKFLOW]);
    if (path.startsWith(`/arbitration/cases/${CASE_ID}/workflows/${WORKFLOW.run_id}/events`)) {
      return json(route, [{ _id: "65f0c0ffee0000000000f001", event_type: "plan_ready", created_at: "2026-11-16T10:00:00Z" }]);
    }
    if (path === `/arbitration/cases/${CASE_ID}/workflows/${WORKFLOW.run_id}`) return json(route, WORKFLOW);
    if (route.request().method() === "GET") return json(route, []);
    return json(route, {});
  });
}

test.describe("pleading approval gates (4B-40)", () => {
  test("the stepper names the current gate and who acts, and no raw id is shown", async ({ page }) => {
    await mockApi(page);
    await page.goto(`/arbitration/cases/${CASE_ID}`);

    const gates = page.getByRole("region", { name: "Approval gates" });
    await expect(gates).toBeVisible();
    await expect(gates.getByRole("status")).toHaveText(
      "Now: Approve the pleading plan. Who acts: a senior legal approver.",
    );
    const steps = gates.getByRole("listitem");
    await expect(steps).toHaveCount(8);
    await expect(steps.nth(4)).toHaveAttribute("aria-current", "step");
    await expect(steps.nth(0)).toContainText("Done");
    await expect(steps.nth(7)).toContainText("Not yet");

    const text = (await page.locator("body").innerText()).toLowerCase();
    expect(text).not.toMatch(/\b[0-9a-f]{24}\b/); // ObjectId
    expect(text).not.toMatch(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/); // UUID
    expect(text).not.toMatch(/\b[0-9a-f]{40,}\b/); // content hash
    expect(text).not.toMatch(/plan_approval_gate|approve_plan|langgraph_v1|senior_legal_approver/);
    // The project is named, not given as its id.
    await expect(page.getByText("Selected document IDs")).toHaveCount(0);
  });
});
