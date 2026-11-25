import { expect, Page, Route, test } from "@playwright/test";

/**
 * The claim journey, steps 1-8 (plan 4B-43; C-22 in the traceability table) - mocked `/api/**`.
 *
 * Mocked; proves component behaviour, not a deployment (excluded from deployed-stack runs in
 * playwright.config.ts). One test per step of
 *   Start -> Heads -> Notice -> Delay -> Quantum -> Determination | Submission -> Escalation -> Pleading
 * each asserting what the screen shows for that step, the request body of every mutation it
 * triggers, and what the screen does with the reply. The mocked server sends ObjectIds, UUIDs and
 * SHA-256 hashes in its records; every step checks that none of them is visible on the page.
 *
 * Step 6's Letter Bench half is in letter-bench.spec.ts ("claim journey step 6"); here step 6 only
 * shows that the determination's letter links point there.
 */

type Json = Record<string, any>;

const ORG = { _id: "65f0c0ffee0000000000a001", id: "65f0c0ffee0000000000a001", name: "Aurora Engineering" };
const PROJECT = {
  _id: "65f0c0ffee0000000000b001",
  id: "65f0c0ffee0000000000b001",
  name: "Metro Package A1",
  organization_id: ORG._id,
};
const ME = "65f0c0ffee0000000000c001";
const APPROVER = "65f0c0ffee0000000000c002";
const CLAIM_A = "65f0c0ffee000000000c1a01";
const CLAIM_B = "65f0c0ffee000000000c1b02";
const CLAIM_C = "65f0c0ffee000000000c1c03";
const CONTRACT_1 = "65f0c0ffee00000000c0de01";
const CONTRACT_2 = "65f0c0ffee00000000c0de02";
const REQUIREMENT_ID = "65f0c0ffee00000000e9e901";
const DETERMINATION_ID = "65f0c0ffee00000000de7e01";
const DOC_ID = "65f0c0ffee00000000d0c001";
const CASE_ID = "1b4e28ba-2fa1-11d2-883f-0016d3cca427";
const DRAFT_ID = "65f0c0ffee0000000000d001";
const STEP_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae8";
const HASH = "a1".repeat(32);
const OVERLAP_ID = "2026-03-20_2026-03-31";

/** The page must show no database id, UUID or content hash (plan 4.3, "Errors"). */
async function expectNoRawIds(page: Page) {
  const text = (await page.locator("body").innerText()).toLowerCase();
  expect(text).not.toMatch(/\b[0-9a-f]{24}\b/); // ObjectId
  expect(text).not.toMatch(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/); // UUID
  expect(text).not.toMatch(/\b[0-9a-f]{40,}\b/); // content hash
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

// --------------------------------------------------------------------------- fixtures

const ELEMENTS = ["event", "notice", "contractual_basis", "causation", "quantum"] as const;

const head = (decided: readonly string[]): Json => ({
  head_id: "H1",
  title: "Delayed handover of Site",
  kind: "time_and_cost",
  event_summary: "Site handed over 146 days late.",
  clause_refs: ["8.4"],
  amount_claimed: 184500000,
  eot_days_claimed: 146,
  elements: Object.fromEntries(
    ELEMENTS.map((key) => [
      key,
      decided.includes(key)
        ? { state: "supported", source_ids: [DOC_ID], decided_by: ME, proposed_state: "supported", proposed_by: "record_check" }
        : { source_ids: [DOC_ID] },
    ]),
  ),
  classification: "compensable",
  residual_risks: [],
  missing_information: [],
  step_id: STEP_ID,
  test_stale: false,
  tested_at: "2026-11-10T09:00:00Z",
});

const noticeAssessment = (verified: boolean): Json => ({
  basis: "awareness",
  basis_date: "2026-03-05T00:00:00",
  period_days: 28,
  period_source: verified ? "contract_verified" : "contract_unverified",
  period_verified: verified,
  clause_no: "20.1",
  requirement_id: REQUIREMENT_ID,
  due_date: "2026-04-02T00:00:00",
  served_on: "2026-04-14T00:00:00",
  served_late_by_days: 12,
  state: "served_late",
});

const requirement = (verified: boolean): Json => ({
  _id: REQUIREMENT_ID,
  organization_id: ORG._id,
  project_id: PROJECT._id,
  claim_type: "eot",
  period_days: 28,
  trigger: "awareness",
  clause_no: "20.1",
  clause_title: "Contractor's Claims",
  quote: "The Contractor shall give notice within 28 days after becoming aware of the event.",
  document_type: "GCC",
  origin: "clause_index",
  verification: verified ? "verified" : "extracted_unverified",
  status: "current",
  created_by: APPROVER,
  created_at: "2026-11-01T10:00:00Z",
  verified_by: verified ? ME : null,
  verified_at: verified ? "2026-11-18T10:00:00Z" : null,
});

const bench = (over: Json = {}): Json => ({
  claim_id: CLAIM_A,
  claim_ref: "CLM-001",
  title: "Delayed handover of Site",
  claim_type: "eot",
  status: "submitted",
  organization_id: ORG._id,
  project_id: PROJECT._id,
  exists: true,
  bench: { board_version: 3, stage: "heads", heads: [head(["event", "notice", "contractual_basis", "causation"])] },
  gate: {
    undecided_elements: 1,
    unclassified_heads: 0,
    stale_tests: 0,
    blockers: ["1 element still needs your call"],
    can_lock: false,
  },
  steps: [],
  notice_assessment: noticeAssessment(false),
  notice_requirement: requirement(false),
  perspective: "contractor",
  skill: { skill_key: "indian-infra-claims", available: false, reason: "Awaiting legal approval" },
  read_only: false,
  arbitration_case_id: null,
  rollout_mode: "primary",
  ...over,
});

const event = (over: Json): Json => ({
  event_id: "65f0c0ffee00000000e7e701",
  link_id: "65f0c0ffee00000000117701",
  label: "Hindrance HR-E1",
  title: "Site not handed over",
  start_date: "2026-03-01T00:00:00",
  end_date: "2026-03-31T00:00:00",
  open_ended: false,
  register_responsibility: "employer",
  register_critical: true,
  revision_current: true,
  counted: true,
  days: 31,
  decision: { risk_owner: "employer", criticality: "critical" },
  ...over,
});

const EMPLOYER_EVENT = "65f0c0ffee00000000e7e701";
const CONTRACTOR_EVENT = "65f0c0ffee00000000e7e702";

const delay = (settled: boolean): Json => ({
  head_id: "H1",
  method: "as_planned_vs_as_built",
  as_of_date: null,
  unavailable_methods: {},
  include_neutral: false,
  events: [
    event({}),
    event({
      event_id: CONTRACTOR_EVENT,
      link_id: "65f0c0ffee00000000117702",
      label: "Hindrance HR-C1",
      title: "Late mobilisation",
      start_date: "2026-03-20T00:00:00",
      end_date: "2026-04-10T00:00:00",
      days: 22,
      decision: { risk_owner: "contractor", criticality: "critical" },
    }),
  ],
  overlaps: [
    {
      overlap_id: OVERLAP_ID,
      start_date: "2026-03-20T00:00:00",
      end_date: "2026-03-31T00:00:00",
      days: 12,
      kind: "employer_contractor",
      employer_event_ids: [EMPLOYER_EVENT],
      contractor_event_ids: [CONTRACTOR_EVENT],
      finding: settled
        ? {
            treatment: "eot_granted_no_cost",
            days_to_eot: 12,
            basis: "Clause 8.4: concurrent delay gives time without cost.",
            decided_by: ME,
            decided_at: "2026-11-18T10:00:00Z",
            event_ids: [EMPLOYER_EVENT, CONTRACTOR_EVENT],
          }
        : null,
      days_to_eot: settled ? 12 : null,
    },
  ],
  records: [],
  employer_critical_days: 31,
  contractor_critical_days: 22,
  neutral_critical_days: 0,
  contractor_only_days: 10,
  net: {
    low: settled ? 31 : 19,
    high: 31,
    days: settled ? 31 : null,
    settled,
    previously_granted: 0,
    employer_only_days: 19,
    overlap_days_to_eot: settled ? 12 : 0,
    neutral_days_included: 0,
  },
  net_table: settled
    ? [
        { key: "employer_only", label: "Employer delay with no Contractor delay on the same day", days: 19, up_to: 0 },
        { key: "overlap_time", label: "Overlap given time", days: 12, up_to: 0, overlap_id: OVERLAP_ID, treatment: "eot_granted_no_cost" },
      ]
    : [
        { key: "employer_only", label: "Employer delay with no Contractor delay on the same day", days: 19, up_to: 0 },
        { key: "undecided", label: "Overlaps with no decided treatment: counted only in the higher figure", days: 0, up_to: 12 },
      ],
  compensable_periods: [{ start_date: "2026-03-01T00:00:00", end_date: "2026-03-19T00:00:00", days: 19 }],
  blockers: settled ? [] : ["H1: 1 overlap between Employer and Contractor delay need a decided treatment"],
  stale_links: 0,
});

const QUANTUM = {
  head_id: "H1",
  currency: "INR",
  lines: [
    {
      line: {
        line_id: "Q1",
        category: "prolongation",
        description: "Site overheads",
        amount: 4500000,
        amount_paise: 450000000,
        period_start: "2026-03-15T00:00:00",
        period_end: "2026-03-25T00:00:00",
        formula: "none",
        records: [{ document_id: DOC_ID, role: "support" }],
      },
      locked: true,
      lock_reasons: [
        "6 of the 11 days in this period are not in a period decided as compensable in the delay assessment.",
      ],
    },
    {
      line: {
        line_id: "Q2",
        category: "overhead_formula",
        description: "Head-office overheads",
        amount: 500000,
        amount_paise: 50000000,
        formula: "hudson",
        formula_inputs: { overhead_percentage: { value: "10", document_id: DOC_ID } },
        records: [{ document_id: DOC_ID, role: "support" }],
      },
      locked: false,
      lock_reasons: [],
      calculated: true,
      calculation_note: "Hudson: 10% of the contract sum over the contract period, times 31 days.",
    },
  ],
  calculated_total_paise: 50000000,
  claimed_total_paise: 500000000,
  supported_total_paise: 50000000,
  locked_total_paise: 450000000,
  claimed_total: 5000000,
  supported_total: 500000,
  locked_total: 4500000,
  locked_count: 1,
};

const DETERMINATIONS = {
  claim_id: CLAIM_A,
  project_id: PROJECT._id,
  perspective: "engineer",
  can_author: true,
  items: [
    {
      determination: {
        _id: DETERMINATION_ID,
        claim_id: CLAIM_A,
        origin: "authored",
        revision: 1,
        issued_by: "engineer",
        status: "approved",
        heads: [
          {
            head_id: "H1",
            head_title: "Delayed handover of Site",
            result: "partial",
            days: 31,
            amount: 500000,
            findings: "Time is due for the employer delay; cost only for the period with proof.",
            residual_risks: [],
            information_required: [
              { item: "Handover correspondence", deadline: "2026-12-05T00:00:00", consequence: null },
              { item: "Daily site records", deadline: "2026-12-20T00:00:00", consequence: "Determined on the records supplied" },
            ],
            decided_by: ME,
          },
        ],
        reference: "ENG/DET/014",
        determination_date: "2026-11-12T00:00:00",
        reviewer_id: APPROVER,
        created_by: ME,
        created_at: "2026-11-10T00:00:00Z",
        decided_by: APPROVER,
        decided_at: "2026-11-12T00:00:00Z",
        history: [],
        version: 4,
      },
      figures: [{ head_id: "H1", days_claimed: 146, amount_claimed: 5000000, amount_supported: 500000, amount_locked: 4500000 }],
      board_changed: false,
      editable: false,
      blockers: [],
    },
  ],
};

const SUBMISSION = {
  claim_id: CLAIM_A,
  exists: true,
  composed_at: "2026-11-15T00:00:00Z",
  sections: [
    {
      section: {
        key: "facts",
        title: "Statement of facts",
        paragraphs: [
          {
            text: "The Site was to be handed over on 1 March 2026 and was handed over on 25 July 2026.",
            sources: [
              { kind: "document", ref: DOC_ID, label: "Letter CTR/2026/118 dated 05-03-2026, revision 2" },
              { kind: "head", ref: "H1", label: "H1 Delayed handover of Site" },
            ],
            edited: true,
          },
        ],
        internal_notes: ["The handover minutes are not yet linked to the claim."],
        produced_by: "record_composition",
        degraded: false,
        composed_at: "2026-11-15T00:00:00Z",
        edited_by: ME,
        edited_at: "2026-11-16T00:00:00Z",
      },
      stale: false,
    },
  ],
  evidence: [
    { number: "C-1", document_id: DOC_ID, title: "Letter on the late handover", reference: "CTR/2026/118", date: "2026-03-05T00:00:00" },
    { number: "C-2", document_id: "65f0c0ffee00000000d0c002", title: "Restricted item", reference: null, date: null },
  ],
  can_compose: true,
  stale_sections: 0,
  approved_for_issue: false,
  can_export: true,
  restricted_items: 1,
  version: 2,
};

const claimRow = (id: string, over: Json = {}): Json => ({
  _id: id,
  claim_ref: "CLM-001",
  type: "eot",
  title: "Delayed handover of Site",
  status: "submitted",
  contract_clauses: ["8.4"],
  linked_document_ids: [DOC_ID],
  linked_letter_ids: [],
  allowed_status_transitions: [],
  project_id: PROJECT._id,
  organization_id: ORG._id,
  currency: "INR",
  amount_claimed: 5000000,
  contract_id: CONTRACT_1,
  contract_label: "Main civil works contract",
  ...over,
});

// The case workspace, as in arbitration-pleading-gates.spec.ts.
const CASE = {
  _id: CASE_ID,
  organization_id: ORG._id,
  project_id: PROJECT._id,
  title: "CLM-001 Delayed handover and others",
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
  approval_receipt_ids: { document_selection: "65f0c0ffee0000000000e001" },
};

// --------------------------------------------------------------------------- the mocked server

interface Call {
  method: string;
  path: string;
  body: any;
}

interface Server {
  claims: Json[];
  bench: Json;
  delay: Json;
  undoRefusals: number;
  calls: Call[];
}

function makeServer(over: Partial<Server> = {}): Server {
  return {
    claims: [claimRow(CLAIM_A)],
    bench: bench(),
    delay: delay(false),
    undoRefusals: 0,
    calls: [],
    ...over,
  };
}

const callsTo = (server: Server, method: string, path: string) =>
  server.calls.filter((call) => call.method === method && call.path === path);

async function mockApi(page: Page, server: Server) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "") || "/";
    const method = request.method();
    let body: any = null;
    try {
      body = request.postDataJSON();
    } catch {
      body = request.postData();
    }
    if (method !== "GET") server.calls.push({ method, path, body });
    const claimBase = `/claims/${CLAIM_A}`;

    if (path === "/csrf-token") return json(route, { csrf_token: "test-csrf" });
    if (path === "/me" || path === "/users/me") {
      return json(route, {
        id: ME,
        email: "claims@example.test",
        roles: ["orgadmin"],
        permissions: [
          "dms.claim.view",
          "dms.claim.edit",
          "dms.claim.manage",
          "dms.claim.determine",
          "dms.document.view",
          "dms.arbitration.view",
          "dms.arbitration.edit",
        ],
        organization_id: ORG._id,
        projects: [PROJECT._id],
      });
    }
    if (path === "/permissions/check") return json(route, { granted: true });
    if (path === "/security-terms/status") return json(route, { requires_acceptance: false });
    if (path === "/organizations") return json(route, { organizations: [ORG] });
    if (path === "/projects") return json(route, [PROJECT]);
    if (path === "/users") {
      return json(route, [
        { _id: ME, first_name: "Asha", last_name: "Rao", email: "claims@example.test" },
        { _id: APPROVER, first_name: "Vikram", last_name: "Mehta", email: "approver@example.test" },
      ]);
    }

    // Register and start.
    if (path === "/claims/bench/availability") return json(route, { enabled: true, rollout_mode: "primary" });
    if (path === "/claims/contract-options") {
      return json(route, {
        options: [
          { contract_id: CONTRACT_1, label: "Main civil works contract" },
          { contract_id: CONTRACT_2, label: "Systems and signalling contract" },
        ],
        selected: null,
        must_choose: true,
      });
    }
    if (path === "/claims" && method === "GET") return json(route, server.claims);
    if (path === "/claims/start" && method === "POST") {
      // 4B-20: the claim and its board with the first head. The mocked board of CLAIM_A answers for it.
      const started = claimRow(CLAIM_A, { title: "Access to the depot land was given late", status: "draft" });
      return json(route, { claim: started, head_id: "H1", board_version: 0 }, 201);
    }
    if (path === "/claims" && method === "POST") {
      const created = claimRow("65f0c0ffee000000000c1d04", {
        ...body,
        claim_ref: null,
        status: "draft",
        contract_label: "Systems and signalling contract",
        amount_claimed: null,
      });
      server.claims = [...server.claims, created];
      return json(route, created, 201);
    }

    // Notice.
    if (path === "/claims/notice/candidates") {
      return json(route, {
        project_id: PROJECT._id,
        claim_type: "eot",
        clause_index_available: true,
        candidates: [
          {
            period_days: 28,
            trigger: "awareness",
            clause_no: "20.1",
            clause_title: "Contractor's Claims",
            quote: "The Contractor shall give notice within 28 days after becoming aware of the event.",
            document_id: DOC_ID,
            document_type: "GCC",
            clause_uid: "65f0c0ffee00000000c1a501",
            modifies_general_conditions: false,
            cited_by_claim: true,
          },
        ],
        current: server.bench.notice_requirement,
      });
    }
    if (path === `/claims/notice/requirements/${REQUIREMENT_ID}/verify`) {
      server.bench = { ...server.bench, notice_assessment: noticeAssessment(true), notice_requirement: requirement(true) };
      return json(route, requirement(true));
    }

    // The board.
    if (path === `${claimBase}/bench` && method === "GET") return json(route, server.bench);
    if (path === `${claimBase}/bench/heads/H1/elements/quantum`) {
      server.bench = bench({
        bench: { board_version: 4, stage: "heads", heads: [head(ELEMENTS)] },
        gate: { undecided_elements: 0, unclassified_heads: 0, stale_tests: 0, blockers: [], can_lock: true },
      });
      return json(route, server.bench);
    }
    if (path === `${claimBase}/bench/lock`) {
      server.bench = bench({
        bench: { board_version: 5, stage: "locked", heads: [head(ELEMENTS)], locked_by: ME, locked_at: "2026-11-18T10:00:00Z" },
        gate: { undecided_elements: 0, unclassified_heads: 0, stale_tests: 0, blockers: [], can_lock: false },
      });
      return json(route, server.bench);
    }
    if (path === `${claimBase}/bench/unlock`) {
      server.bench = bench({
        bench: { board_version: 6, stage: "heads", heads: [head(ELEMENTS)] },
        gate: { undecided_elements: 0, unclassified_heads: 0, stale_tests: 0, blockers: [], can_lock: true },
      });
      return json(route, server.bench);
    }
    if (path === `${claimBase}/bench/heads/H1/delay`) return json(route, server.delay);
    if (path === `${claimBase}/bench/heads/H1/delay/overlaps/${OVERLAP_ID}`) {
      server.delay = delay(true);
      return json(route, server.delay);
    }
    if (path === `${claimBase}/bench/heads/H1/quantum`) return json(route, QUANTUM);
    if (path === `${claimBase}/determinations`) return json(route, DETERMINATIONS);
    if (path === `${claimBase}/submission`) return json(route, SUBMISSION);

    // Escalation.
    if (path === "/claims/escalation/preview") {
      const carry = (id: string, ref: string, over: Json) => ({
        claim_id: id,
        claim_ref: ref,
        title: `Claim ${ref}`,
        status: "submitted",
        has_board: true,
        already_in_case_id: null,
        rows: { "claim-matrix": 1, "quantum-annexures": 1 },
        weak: [],
        not_carried: ["Internal analysis is not carried."],
        status_after: "disputed",
        ...over,
      });
      return json(route, {
        project_id: PROJECT._id,
        claims: [
          carry(CLAIM_A, "CLM-001", { board_locked: false, weak: ["H1: notice served 12 days late"] }),
          carry(CLAIM_B, "CLM-002", { board_locked: true }),
        ],
        totals: { "claim-matrix": 2, "quantum-annexures": 2 },
        blockers: [],
        can_escalate: true,
      });
    }
    if (path === "/claims/escalation/run") {
      server.claims = server.claims.map((claim) =>
        body.claim_ids.includes(claim._id) ? { ...claim, status: "disputed", evidence_frozen_at: "2026-11-18T10:00:00Z" } : claim,
      );
      return json(route, {
        case_id: CASE_ID,
        case_title: body.new_case.title,
        created_case: true,
        rows_created: { "claim-matrix": 2, "quantum-annexures": 2 },
        rows_already_present: 0,
        claims: [],
      });
    }
    if (path === "/claims/escalation/undo") {
      if (server.undoRefusals > 0) {
        server.undoRefusals -= 1;
        return json(
          route,
          { detail: { code: "case_has_pleading", user_message: "The case already has a pleading version, so this cannot be undone." } },
          409,
        );
      }
      server.bench = { ...server.bench, arbitration_case_id: null };
      return json(route, {
        claim_id: CLAIM_A,
        case_id: CASE_ID,
        status: "submitted",
        rows_archived: 2,
        freeze_lifted: true,
        case_discarded: false,
      });
    }

    // The case workspace (step 8).
    if (path === "/arbitration/cases" && method === "GET") return json(route, [CASE]);
    if (path === `/arbitration/cases/${CASE_ID}`) return json(route, CASE);
    if (path === `/arbitration/cases/${CASE_ID}/dashboard`) {
      return json(route, {
        case: CASE,
        matrix_counts: { "claim-matrix": 2 },
        approved_counts: { "claim-matrix": 2 },
        drafts: [{ _id: DRAFT_ID, case_id: CASE_ID, title: "Statement of Claim", draft_type: "statement_of_claim", status: "draft" }],
        readiness: { case_id: CASE_ID, readiness_score: 40, status: "blocked", blockers: [], checks: [] },
        latest_agent_runs: [],
      });
    }
    if (path === `/arbitration/cases/${CASE_ID}/workflows`) return json(route, [WORKFLOW]);
    if (path.startsWith(`/arbitration/cases/${CASE_ID}/workflows/${WORKFLOW.run_id}/events`)) {
      return json(route, [{ _id: "65f0c0ffee0000000000f001", event_type: "plan_ready", created_at: "2026-11-16T10:00:00Z" }]);
    }
    if (path === `/arbitration/cases/${CASE_ID}/workflows/${WORKFLOW.run_id}`) return json(route, WORKFLOW);

    if (method === "GET") return json(route, []);
    return json(route, {});
  });
}

// --------------------------------------------------------------------------- the journey

test.describe("claim journey (4B-43)", () => {
  test("step 1, Start: three questions start the claim and open its board", async ({ page }) => {
    const server = makeServer();
    await mockApi(page, server);
    await page.goto("/claims");
    await expect(page.getByRole("heading", { name: "Claim Register" })).toBeVisible();

    await page.getByRole("button", { name: "New Claim" }).click();
    const dialog = page.getByRole("dialog", { name: "Start a claim" });
    await expect(dialog.getByText("Three questions.", { exact: false })).toBeVisible();
    const start = dialog.getByRole("button", { name: "Start claim" });
    await expect(start).toBeDisabled();
    await dialog.getByLabel("What happened?").fill("Access to the depot land was given late. Piling could not start.");
    await dialog.getByLabel("When did you become aware of it?").fill("2026-03-05");
    // The project has two contracts: one must be chosen, and nothing is guessed.
    await expect(dialog.getByText("To start the claim, choose the contract.")).toBeVisible();
    await expect(start).toBeDisabled();
    await dialog.getByLabel("Contract", { exact: true }).selectOption({ label: "Systems and signalling contract" });
    await expectNoRawIds(page);

    await start.click();
    const posted = callsTo(server, "POST", "/claims/start");
    await expect.poll(() => posted.length).toBe(1);
    expect(posted[0].body).toEqual({
      project_id: PROJECT._id,
      what_happened: "Access to the depot land was given late. Piling could not start.",
      aware_on: "2026-03-05",
      contract_id: CONTRACT_2,
    });
    expect(callsTo(server, "POST", "/claims")).toHaveLength(0);
    // The new claim opens on its board.
    await expect(page).toHaveURL(new RegExp(`/claims/${CLAIM_A}$`));
    await expect(page.getByRole("navigation", { name: "Heads of claim" })).toBeVisible();
    await expectNoRawIds(page);
  });

  test("step 1, More details: the full form, with the answers carried over, files the claim in the register", async ({ page }) => {
    const server = makeServer();
    await mockApi(page, server);
    await page.goto("/claims");
    await expect(page.getByRole("row", { name: /Delayed handover of Site/ })).toBeVisible();

    await page.getByRole("button", { name: "New Claim" }).click();
    const questions = page.getByRole("dialog", { name: "Start a claim" });
    await questions.getByLabel("What happened?").fill("Access to the depot land was given late.");
    await questions.getByLabel("When did you become aware of it?").fill("2026-03-05");
    await questions.getByRole("button", { name: "More details" }).click();
    await expect(questions).toBeHidden();

    const dialog = page.getByRole("dialog", { name: "New Claim" });
    await expect(dialog.getByLabel("Became aware on")).toHaveValue("2026-03-05");
    await dialog.getByPlaceholder("e.g. EOT — monsoon delay").fill("Late access to the depot land");
    await dialog.getByRole("combobox").first().click();
    await page.getByRole("option", { name: "Extension of Time" }).click();
    await dialog.getByLabel("Event date").fill("2026-03-02");
    await dialog.getByLabel("Contract clauses relied on").fill("8.4, 20.1");
    const contract = dialog.getByLabel("Contract", { exact: true });
    await expect(contract).toHaveValue("");
    await expect(dialog.getByText("This project has more than one contract.", { exact: false })).toBeVisible();
    await contract.selectOption({ label: "Systems and signalling contract" });
    // The project is the navbar's, shown by name.
    await expect(dialog.getByText("Metro Package A1")).toBeVisible();
    await expectNoRawIds(page);

    await dialog.getByRole("button", { name: "Create claim" }).click();
    await expect(dialog).toBeHidden();
    const posted = callsTo(server, "POST", "/claims");
    expect(posted).toHaveLength(1);
    expect(posted[0].body).toEqual({
      title: "Late access to the depot land",
      type: "eot",
      description: "Access to the depot land was given late.",
      currency: "INR",
      event_date: "2026-03-02T00:00:00Z",
      awareness_date: "2026-03-05T00:00:00Z",
      contract_clauses: ["8.4", "20.1"],
      project_id: PROJECT._id,
      contract_id: CONTRACT_2,
      status: "draft",
    });
    await expect(page.getByText("Claim created")).toBeVisible();
    const row = page.getByRole("row", { name: /Late access to the depot land/ });
    await expect(row).toContainText("Systems and signalling contract");
    await expect(row).toContainText("Draft");
    await expectNoRawIds(page);
  });

  test("step 2, Heads: the last call is sent with the board version, then the board locks and reopens with a reason", async ({ page }) => {
    const server = makeServer();
    await mockApi(page, server);
    await page.goto(`/claims/${CLAIM_A}`);

    await expect(page.getByRole("heading", { name: /Delayed handover of Site/, level: 1 })).toBeVisible();
    const heads = page.getByRole("navigation", { name: "Heads of claim" });
    await expect(heads).toContainText("4 of 5 decided");
    await expect(page.getByText("1 element still needs your call")).toBeVisible();
    await expect(page.getByRole("button", { name: "Lock the board" })).toBeDisabled();
    await expectNoRawIds(page);

    await page.getByRole("group", { name: "Your call on Quantum" }).getByRole("button", { name: "Supported" }).click();
    await expect(page.getByText("Every element and head has your call.")).toBeVisible();
    expect(callsTo(server, "PUT", `/claims/${CLAIM_A}/bench/heads/H1/elements/quantum`).map((c) => c.body)).toEqual([
      { board_version: 3, state: "supported" },
    ]);
    await expect(heads).toContainText("Decided");

    await page.getByRole("button", { name: "Lock the board" }).click();
    await expect(page.getByRole("heading", { name: "Board locked" })).toBeVisible();
    expect(callsTo(server, "POST", `/claims/${CLAIM_A}/bench/lock`).map((c) => c.body)).toEqual([{ board_version: 4 }]);
    await expect(page.getByLabel("Head of claim")).toBeDisabled();

    await page.getByRole("button", { name: "Reopen the board" }).click();
    const confirm = page.getByRole("button", { name: "Reopen", exact: true });
    await expect(confirm).toBeDisabled();
    await page.getByLabel("Why is the board being reopened?").fill("Engineer changed the event date");
    await confirm.click();
    await expect(page.getByRole("heading", { name: "Before the board can be locked" })).toBeVisible();
    expect(callsTo(server, "POST", `/claims/${CLAIM_A}/bench/unlock`).map((c) => c.body)).toEqual([
      { board_version: 5, reason: "Engineer changed the event date" },
    ]);
    await expect(page.getByLabel("Head of claim")).toBeEnabled();
    await expectNoRawIds(page);
  });

  test("step 3, Notice: the register's pill and strip, the clock card, and verifying the period against its clause", async ({ page }) => {
    const server = makeServer({
      claims: [
        claimRow(CLAIM_A, { notice_assessment: noticeAssessment(false) }),
        claimRow(CLAIM_B, {
          claim_ref: "CLM-002",
          title: "Withheld drawings",
          notice_assessment: { ...noticeAssessment(false), served_on: null, served_late_by_days: null, days_remaining: -3, state: "overdue" },
        }),
      ],
    });
    await mockApi(page, server);
    await page.goto("/claims");

    const strip = page.getByRole("status").filter({ hasText: "overdue" });
    await expect(strip).toContainText("1 notice overdue");
    await expect(strip).toContainText("1 served after the period");
    await expect(page.getByRole("row", { name: /CLM-001/ })).toContainText("Served 12 days late");
    await expect(page.getByRole("row", { name: /CLM-002/ })).toContainText("Notice 3 days overdue");
    await expectNoRawIds(page);

    await page.goto(`/claims/${CLAIM_A}`);
    const clock = page.locator("section", { has: page.getByRole("heading", { name: /Notice clock/ }) });
    await expect(clock).toContainText("Served 12 days late");
    await expect(clock).toContainText(
      "Notice served 12 days after the 28-day period read from clause 20.1, not yet verified, counted from the awareness date.",
    );
    await expect(clock).toContainText("Counted from 05-03-2026; period ends 02-04-2026.");
    await expect(clock).toContainText("Period source: clause 20.1 (GCC), not yet verified by a person");

    await clock.getByRole("button", { name: "Review notice period" }).click();
    const dialog = page.getByRole("dialog", { name: "Notice period for this type of claim" });
    await expect(dialog).toContainText("28 days from becoming aware of the event, clause 20.1 (GCC).");
    // The quote is shown beside the clause it is read from.
    await expect(dialog.locator("blockquote").first()).toHaveText(
      "The Contractor shall give notice within 28 days after becoming aware of the event.",
    );
    await expect(dialog).toContainText("Matched to the clause in this project's clause index.");
    await expect(dialog).toContainText("It must be verified by someone other than the person who recorded it");
    await expect(dialog.getByText("Cited by this claim")).toBeVisible();
    await expectNoRawIds(page);

    await dialog.getByRole("button", { name: "I have checked it against the contract" }).click();
    await expect(dialog).toContainText("Verified against the clause on 18-11-2026.");
    expect(callsTo(server, "POST", `/claims/notice/requirements/${REQUIREMENT_ID}/verify`)).toHaveLength(1);
    await page.keyboard.press("Escape");
    await expect(clock).toContainText("verified 18-11-2026");
    await expect(clock).toContainText("Notice served 12 days after the 28-day period in clause 20.1, counted from the awareness date.");
    await expectNoRawIds(page);
  });

  test("step 4, Delay: the net-EOT table adds up, and a recorded overlap treatment settles the range", async ({ page }) => {
    const server = makeServer();
    await mockApi(page, server);
    await page.goto(`/claims/${CLAIM_A}`);
    await page.getByRole("tablist", { name: "Parts of head H1" }).getByRole("tab", { name: "Delay" }).click();

    const net = page.locator("section", { has: page.getByRole("heading", { name: "Extension of time on the decisions so far" }) });
    await expect(net).toContainText("19 to 31 days");
    await expect(net).toContainText("Shown as a range because an overlap has no decided treatment.");
    const table = page.getByRole("table", { name: "How the extension is made up" });
    await expect(table.getByRole("columnheader")).toHaveText(["Component", "Days", "Up to"]);
    const rows = table.getByRole("row");
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(1)).toHaveText(/Employer delay with no Contractor delay on the same day\s*19/);
    await expect(rows.nth(2)).toHaveText(/Overlaps with no decided treatment.*0\s*\+12/);
    await expect(rows.nth(3)).toHaveText(/Extension of time\s*19\s*31/);
    // The timeline bars carry their labels.
    const timeline = page.getByRole("figure", { name: "Delay events by risk owner" });
    await expect(timeline).toContainText("Hindrance HR-E1");
    await expect(timeline).toContainText("Hindrance HR-C1");
    await expect(page.getByText("Treatment not decided")).toBeVisible();
    await expectNoRawIds(page);

    await page.getByLabel("Treatment", { exact: true }).selectOption("eot_granted_no_cost");
    const record = page.getByRole("button", { name: "Record treatment" });
    await expect(record).toBeDisabled();
    await page.getByLabel(/^Basis: the clause/).fill("Clause 8.4: concurrent delay gives time without cost.");
    await record.click();

    await expect(net.getByText("31 days", { exact: true })).toBeVisible();
    await expect(net).not.toContainText("Shown as a range");
    expect(callsTo(server, "PUT", `/claims/${CLAIM_A}/bench/heads/H1/delay/overlaps/${OVERLAP_ID}`).map((c) => c.body)).toEqual([
      { board_version: 3, treatment: "eot_granted_no_cost", basis: "Clause 8.4: concurrent delay gives time without cost." },
    ]);
    await expect(table.getByRole("row").nth(2)).toHaveText(/O1: Time, no cost\s*12/);
    await expect(table.getByRole("row").last()).toHaveText(/Extension of time\s*31/);
    await expect(page.getByText("Time, no cost · 12 days to the extension")).toBeVisible();
    await expectNoRawIds(page);
  });

  test("step 5, Quantum: totals in rupees and lakh, a lock says what is missing, a formula line is marked as a calculation", async ({ page }) => {
    const server = makeServer();
    await mockApi(page, server);
    await page.goto(`/claims/${CLAIM_A}`);
    await page.getByRole("tablist", { name: "Parts of head H1" }).getByRole("tab", { name: "Quantum" }).click();

    const totals = page.locator("section", { has: page.getByRole("heading", { name: "Build-up of this head" }) });
    await expect(totals.locator("dl > div")).toHaveText([
      /Claimed\s*₹50,00,000\s*₹50 L/,
      /Supported\s*₹5,00,000\s*₹5 L/,
      /Locked\s*₹45,00,000\s*₹45 L/,
    ]);
    await expect(totals).toContainText(
      "Of the amount claimed, ₹5,00,000 is calculated by a formula. A calculation is not a finding that the amount is recoverable.",
    );
    const lines = page.locator("section", { has: page.getByRole("heading", { name: "Lines (2)" }) });
    const locked = lines.getByRole("listitem").filter({ hasText: "Site overheads" }).first();
    await expect(locked.getByLabel("Locked")).toBeVisible();
    await expect(locked).toContainText("6 of the 11 days in this period are not in a period decided as compensable");
    await expect(locked).toContainText("Q1 · Prolongation · 15-03-2026 to 25-03-2026 · 1 record");
    const formula = lines.getByRole("listitem").filter({ hasText: "Head-office overheads" }).first();
    await expect(formula.getByLabel("Supported")).toBeVisible();
    await expect(formula).toContainText("Hudson");
    await expect(formula).toContainText("Hudson: 10% of the contract sum over the contract period, times 31 days.");
    await expectNoRawIds(page);
  });

  test("step 6, Determination: the approved findings, and letters that open the Letter Bench from this determination", async ({ page }) => {
    const server = makeServer({ bench: bench({ perspective: "engineer" }) });
    await mockApi(page, server);
    await page.goto(`/claims/${CLAIM_A}`);

    // An Engineer's workspace opens on the determination.
    const tabs = page.getByRole("tablist", { name: "Claim workspace" });
    await expect(tabs.getByRole("tab", { name: "Determination" })).toHaveAttribute("aria-selected", "true");
    const card = page.getByRole("article", { name: "Determination revision 1" });
    await expect(card).toContainText("Determination by the Engineer");
    await expect(card).toContainText("ENG/DET/014 · dated 12-11-2026");
    await expect(card).toContainText("approved by Vikram Mehta on 12-11-2026");
    await expect(card).toContainText("Handover correspondence — by 05-12-2026");
    await expect(card).toContainText("Daily site records — by 20-12-2026 — if not supplied: Determined on the records supplied");

    const issue = card.getByRole("link", { name: "Issue determination letter" });
    await expect(issue).toHaveAttribute(
      "href",
      `/letters/bench/new?determinationId=${DETERMINATION_ID}&purpose=determination`,
    );
    const ask = card.getByRole("link", { name: "Request information" });
    await expect(ask).toHaveAttribute(
      "href",
      `/letters/bench/new?determinationId=${DETERMINATION_ID}&purpose=information_request`,
    );
    await expect(card).toContainText("Opens the Letter Bench with the findings, the items asked for and their deadlines.");
    await expectNoRawIds(page);
  });

  test("step 6, Submission: paragraphs show where they come from, hand edits are marked, restricted evidence is not named", async ({ page }) => {
    const server = makeServer();
    await mockApi(page, server);
    await page.goto(`/claims/${CLAIM_A}`);
    await page.getByRole("tablist", { name: "Claim workspace" }).getByRole("tab", { name: "Submission" }).click();

    const doc = page.getByRole("article", { name: "Claim submission" });
    await expect(doc.getByRole("heading", { name: "1. Statement of facts" })).toBeVisible();
    await expect(doc).toContainText("Edited by hand");
    const paragraph = doc.locator("details").first();
    await expect(paragraph.locator("summary")).toContainText("(edited)");
    await expect(paragraph.locator("summary")).toContainText("2 sources");
    await paragraph.locator("summary").click();
    await expect(paragraph.getByRole("list", { name: "Where this paragraph comes from" }).getByRole("listitem")).toHaveText([
      "Document: Letter CTR/2026/118 dated 05-03-2026, revision 2",
      "Head of claim: H1 Delayed handover of Site",
    ]);
    // Notes to the reader stay apart from the document's text.
    await expect(doc).toContainText("For you, not in the document");
    await expect(page.getByText(/1 item of this claim's evidence is restricted for you and shown as "Restricted item"/)).toBeVisible();
    const schedule = doc.locator("table");
    await expect(schedule.getByRole("row").nth(1)).toHaveText(/C-1\s*05-03-2026\s*CTR\/2026\/118\s*Letter on the late handover/);
    await expect(schedule.getByRole("row").nth(2)).toHaveText(/C-2\s*Undated\s*Restricted item/);
    // A viewer who cannot see every item cannot edit the text.
    await expect(doc.getByRole("button", { name: "Edit" })).toHaveCount(0);
    await expectNoRawIds(page);
  });

  test("step 7, Escalation: several claims from the register, weak heads acknowledged, then undone with a reason", async ({ page }) => {
    const server = makeServer({
      claims: [
        claimRow(CLAIM_A),
        claimRow(CLAIM_B, { claim_ref: "CLM-002", title: "Withheld drawings", status: "notified" }),
        claimRow(CLAIM_C, { claim_ref: "CLM-003", title: "Settled variation", status: "closed" }),
      ],
      undoRefusals: 1,
    });
    await mockApi(page, server);
    await page.goto("/claims");

    // A closed claim cannot be chosen.
    await expect(page.getByRole("row", { name: /CLM-003/ }).getByRole("checkbox")).toHaveCount(0);
    await page.getByRole("checkbox", { name: "Select CLM-001 for escalation" }).check();
    await page.getByRole("checkbox", { name: "Select CLM-002 for escalation" }).check();
    const selected = page.getByRole("region", { name: "Selected claims" });
    await expect(selected).toContainText("2 claims selected");
    await selected.getByRole("button", { name: "Escalate selected to arbitration" }).click();

    const dialog = page.getByRole("dialog", { name: "Escalate to arbitration" });
    await expect(dialog.getByRole("region", { name: "Claim CLM-001" })).toContainText("Carried, but weak on the record");
    await expect(dialog.getByRole("region", { name: "Claim CLM-001" })).toContainText("H1: notice served 12 days late");
    await expect(dialog).toContainText("Claim matrix (heads of claim)");
    await expect(dialog).toContainText("CLM-002: the claim's status becomes “disputed”.");
    expect(callsTo(server, "POST", "/claims/escalation/preview")[0].body).toEqual({ claim_ids: [CLAIM_A, CLAIM_B] });
    await expect(dialog.getByLabel("Case title")).toHaveValue("Delayed handover of Site and others");
    await expectNoRawIds(page);

    const escalate = dialog.getByRole("button", { name: "Escalate", exact: true });
    await expect(escalate).toBeDisabled();
    await dialog.getByRole("checkbox", { name: /The board of CLM-001 is not locked/ }).check();
    await dialog.getByLabel("Case title").fill("CLM-001 Delayed handover and others");
    await escalate.click();

    await expect.poll(() => callsTo(server, "POST", "/claims/escalation/run").length).toBe(1);
    const run = callsTo(server, "POST", "/claims/escalation/run")[0].body;
    expect(run).toEqual({
      claim_ids: [CLAIM_A, CLAIM_B],
      new_case: { title: "CLM-001 Delayed handover and others", party_perspective: "claimant" },
      idempotency_key: expect.stringMatching(/^esc-/),
      acknowledged_weak: [CLAIM_A],
    });
    // The selection clears and the register reloads, while the dialog stays open on its result
    // (found by this suite: it used to unmount with the selection, so no confirmation was seen).
    await expect(selected).toBeHidden();
    await expect(dialog).toContainText("Case created");
    await expect(dialog.getByRole("link", { name: "Open the case" })).toHaveAttribute("href", `/arbitration/cases/${CASE_ID}`);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("row", { name: /CLM-001/ })).toContainText("Disputed");
    await expect(page.getByRole("checkbox", { name: "Select CLM-001 for escalation" })).toHaveCount(0);

    // Undo, from the claim: refused once by the server, which says why; then done.
    server.bench = bench({ arbitration_case_id: CASE_ID, status: "disputed" });
    await page.goto(`/claims/${CLAIM_A}`);
    await page.getByRole("button", { name: "Undo escalation" }).click();
    const undo = page.getByRole("dialog", { name: "Undo the escalation" });
    const confirm = undo.getByRole("button", { name: "Undo escalation" });
    await expect(confirm).toBeDisabled();
    await undo.getByLabel("Why is the escalation being undone?").fill("Escalated before the board was locked");
    await confirm.click();
    await expect(undo.getByRole("alert")).toHaveText("The case already has a pleading version, so this cannot be undone.");
    await confirm.click();
    await expect(undo).toBeHidden();
    await expect(page.getByText("Escalation undone. Its rows in the case are archived.")).toBeVisible();
    expect(callsTo(server, "POST", "/claims/escalation/undo").map((c) => c.body)).toEqual([
      { claim_id: CLAIM_A, case_id: CASE_ID, reason: "Escalated before the board was locked" },
      { claim_id: CLAIM_A, case_id: CASE_ID, reason: "Escalated before the board was locked" },
    ]);
    await expect(page.getByRole("button", { name: "Escalate to arbitration" })).toBeVisible();
    await expectNoRawIds(page);
  });

  test("step 8, Pleading: from the claim to its case, where the gate stepper names the gate and who acts", async ({ page }) => {
    const server = makeServer({ bench: bench({ arbitration_case_id: CASE_ID, status: "disputed" }) });
    await mockApi(page, server);
    await page.goto(`/claims/${CLAIM_A}`);
    const toCase = page.getByRole("link", { name: "Open the arbitration case" });
    await expect(toCase).toHaveAttribute("href", `/arbitration/cases/${CASE_ID}`);
    await page.goto(`/arbitration/cases/${CASE_ID}`);

    const gates = page.getByRole("region", { name: "Approval gates" });
    // The first load of the case workspace on a cold Vite dev server can make Vite re-optimise
    // its dependencies, which fails the lazy chunk once (the ErrorBoundary's chunk-load message,
    // "A newer version of the app is available"). That is a dev-server artefact; one reload
    // clears it. Any other failure, or a second one, still fails the test.
    const staleChunk = page.getByText("A newer version of the app is available.");
    await expect(gates.or(staleChunk)).toBeVisible();
    if (await staleChunk.isVisible()) await page.reload();
    await expect(gates).toBeVisible();
    await expect(gates.getByRole("status")).toHaveText("Now: Approve the pleading plan. Who acts: a senior legal approver.");
    const steps = gates.getByRole("listitem");
    await expect(steps).toHaveCount(8);
    await expect(steps.nth(4)).toHaveAttribute("aria-current", "step");
    await expectNoRawIds(page);
  });
});
