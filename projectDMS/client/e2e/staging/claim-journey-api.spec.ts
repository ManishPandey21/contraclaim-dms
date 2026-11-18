/**
 * Claim journey - DEPLOYED staging certification (plan 4B-44). Real HTTP, real
 * users of an internal canary tenant, the real model.
 *
 * NOT YET EXECUTED AGAINST A DEPLOYMENT. A mocked pass satisfies no staging
 * criterion: this file asserts nothing until it has run against a deployed stack
 * named by E2E_BASE_URL. Acceptance needs TWO CONSECUTIVE GREEN RUNS against the
 * same deployment (plan 4B-44), each with its build identifiers attached.
 *
 * It makes the same calls, in the same order, as the in-process journey test
 * `backend/rbac_backend/tests/integration/test_claim_journey_http_mongo.py`:
 *
 *   1. start      POST /api/claims (there is no /claims/start: 4B-20 is not built)
 *   2. heads      open the bench, add a head, test both heads (real model:
 *                 `degraded` must be false), decide, lock
 *   3. notice     record the notice requirement; a DIFFERENT person verifies it
 *   4. delay      a run-owned hindrance linked to H1; the delay view's net EOT
 *   5. quantum    one line on H2 with its record; the total in paise
 *   6. determination (Engineer project): author, propose, submit, approve by a
 *                 different reviewer; open the Letter Bench pre-filled from it
 *   7. submission (Contractor project): compose; every paragraph has a source
 *   8. escalation preview, run into a new case; the heads in the claim matrix;
 *                 a Statement of Claim whose Approve and Export are refused with
 *                 the plain reason; reviewer roles granted and the heads approved
 *                 over HTTP; where the path stops is asserted, not assumed.
 * Boundaries: a user of another organisation gets 403/404, an anonymous caller
 * 401, at several points.
 *
 * Known gaps the run measures rather than hides (reported in 4B-44):
 *   - no endpoint exposes the backend RELEASE_SHA (see "build identifiers").
 *
 * SETUP
 *   Backend: CLAIM_BENCH_ROLLOUT_MODE=canary with E2E_STAGING_ORG_ID in
 *   CLAIM_BENCH_CANARY_TENANT_IDS; LETTER_BENCH_ROLLOUT_MODE=canary with the same
 *   organisation in LETTER_BENCH_CANARY_TENANT_IDS (core/config.py). The Claims
 *   skill (indian-infra-claims) published. The organisation's plan includes claims,
 *   arbitration and drafting (claims_commercial_desk). Both projects' contracts
 *   ingested so clause 8.4 and the notice clause resolve in the clause index.
 *   Two projects dedicated to this certification (a notice requirement recorded
 *   here replaces the project's current one for claim type "eot").
 *
 * ENVIRONMENT (all required unless marked optional; no value is ever printed)
 *   E2E_BASE_URL                          the deployed stack (absent: every test skips)
 *   CONTRACLAIM_STAGING_E2E               optional switch: 1 = evidence run, a missing variable FAILS
 *   E2E_STAGING_HOST                      the one host fixtures may address (allowlist of one)
 *   E2E_PRODUCTION_HOSTS                  production hostnames, refused
 *   E2E_STAGING_ORG_ID                    the internal canary tenant
 *   E2E_CJ_CONTRACTOR_PROJECT_ID          project acting for the Contractor
 *   E2E_CJ_ENGINEER_PROJECT_ID            project acting for the Engineer
 *   E2E_CJ_NOTICE_DOCUMENT_ID             incoming letter in the Contractor project: the claim's notice
 *   E2E_CJ_RECORD_DOCUMENT_ID             outgoing letter/record in the Contractor project: the quantum record,
 *                                         also linked to the run's hindrance as the record behind H1's days
 *   E2E_CJ_NOTICE_CLAUSE_NO               notice clause number in the clause index (e.g. 20.1)
 *   E2E_CJ_NOTICE_QUOTE                   wording quoted from that clause, containing the period
 *   E2E_CJ_NOTICE_PERIOD_DAYS             the period in that wording (e.g. 28)
 *   E2E_CJ_ORGADMIN_EMAIL / _PASSWORD     org admin: claims, bench, notice, escalation, drafts, reviewer grants
 *   E2E_CJ_VERIFIER_EMAIL / _PASSWORD     second person, Contractor project: claim-manage + clause read; also
 *                                         the pleading approver (dms.arbitration.approve), not the drafter
 *   E2E_CJ_ENG_AUTHOR_EMAIL / _PASSWORD   Engineer project: dms.claim.determine by name
 *   E2E_CJ_ENG_REVIEWER_EMAIL / _PASSWORD Engineer project: dms.claim.determine by name, another person
 *   E2E_CJ_ENG_DRAFTER_EMAIL / _PASSWORD  Engineer project: drafting.request.create (client user)
 *   E2E_CJ_LEGAL_REVIEWER_EMAIL / _PASSWORD   Contractor project: dms.arbitration.approve (granted legal_reviewer here)
 *   E2E_CJ_QUANTUM_REVIEWER_EMAIL / _PASSWORD Contractor project: dms.arbitration.approve (granted quantum_reviewer here)
 *   E2E_CJ_FOREIGN_EMAIL / _PASSWORD      a user of another organisation
 *   E2E_CJ_FOREIGN_PROJECT_ID             that user's own project (its navbar selection)
 *   E2E_CJ_RELEASE_SHA                    optional: the deployed backend image's revision label, recorded
 *   E2E_RUN_ID, E2E_EVIDENCE_DIR, E2E_IGNORE_HTTPS_ERRORS   optional, as in fixtures.ts
 *
 * COST. Two head-test model calls, one determination proposal, one submission
 * composition, one Letter Bench analysis.
 *
 * RESIDUE. Run-owned (RUN_TAG in every title): the hindrance is archived in
 * teardown, the Engineer claim deleted, the Statement of Claim draft deleted. The
 * escalated Contractor claim and its arbitration case remain (no delete route for
 * a case, and escalation cannot be undone after a pleading version); their ids are
 * written to the evidence file for the runbook's manual removal. The reviewer
 * assignments granted here are revoked in teardown.
 */
import { expect, test, type APIRequestContext, type APIResponse } from "@playwright/test";

import { RUN_ID, RUN_TAG, assertTargetIsNotProduction, fixtureOrganizationId, newFixtureContext, signIn, type FixtureSession } from "./fixtures";
import { archiveRunOwned, entryBody, guardedSession, inProject, writeEvidence } from "./hindrance-fixtures";
import { requireStagingEnvironment, stagingTeardownHasWork } from "./staging-target";

test.describe.configure({ mode: "serial" });

const SCOPE = [
  "E2E_STAGING_HOST",
  "E2E_PRODUCTION_HOSTS",
  "E2E_STAGING_ORG_ID",
  "E2E_CJ_CONTRACTOR_PROJECT_ID",
  "E2E_CJ_ENGINEER_PROJECT_ID",
  "E2E_CJ_NOTICE_DOCUMENT_ID",
  "E2E_CJ_RECORD_DOCUMENT_ID",
  "E2E_CJ_NOTICE_CLAUSE_NO",
  "E2E_CJ_NOTICE_QUOTE",
  "E2E_CJ_NOTICE_PERIOD_DAYS",
  "E2E_CJ_FOREIGN_PROJECT_ID",
] as const;
const PEOPLE = [
  "ORGADMIN",
  "VERIFIER",
  "ENG_AUTHOR",
  "ENG_REVIEWER",
  "ENG_DRAFTER",
  "LEGAL_REVIEWER",
  "QUANTUM_REVIEWER",
  "FOREIGN",
] as const;
type Person = (typeof PEOPLE)[number];
const CREDENTIALS = PEOPLE.flatMap((who) => [`E2E_CJ_${who}_EMAIL`, `E2E_CJ_${who}_PASSWORD`]);
const ELEMENTS = ["event", "notice", "contractual_basis", "causation", "quantum"];

const contexts: APIRequestContext[] = [];
const suite: {
  env: Record<string, string>;
  sessions: Record<string, FixtureSession>;
  userIds: Partial<Record<Person, string>>;
  claimId?: string;
  engineerClaimId?: string;
  hindranceId?: string;
  determinationId?: string;
  caseId?: string;
  draftId?: string;
  matrix: any[];
  assignmentIds: string[];
  evidence: Record<string, unknown>;
} = { env: {}, sessions: {}, userIds: {}, matrix: [], assignmentIds: [], evidence: { run: RUN_ID } };

/** Every test names the whole environment: a missing value skips (or, under the switch, fails). */
function environment(): Record<string, string> {
  return requireStagingEnvironment(...SCOPE, ...CREDENTIALS);
}

function contractor(): string {
  return suite.env.E2E_CJ_CONTRACTOR_PROJECT_ID;
}

function engineer(): string {
  return suite.env.E2E_CJ_ENGINEER_PROJECT_ID;
}

/** A signed-in person (one sign-in per person). Never prints the credential, only its variable name. */
async function signedIn(playwright: any, who: Person): Promise<FixtureSession> {
  if (!suite.sessions[who]) {
    const request = await newFixtureContext(playwright);
    contexts.push(request);
    suite.sessions[who] = guardedSession(await signIn(request, `E2E_CJ_${who}_EMAIL`, `E2E_CJ_${who}_PASSWORD`));
    const me = await suite.sessions[who].request.get("/api/users/me");
    expect(me.status(), `GET /api/users/me as ${who}`).toBe(200);
    const body = await me.json();
    suite.userIds[who] = String(body.id ?? body._id);
  }
  return suite.sessions[who];
}

/** The person with a navbar selection in the canary tenant, as the browser sends it. */
async function as(playwright: any, who: Person, project: string): Promise<FixtureSession> {
  return inProject(await signedIn(playwright, who), project);
}

/** The user of another organisation, under its own organisation's selection. */
async function outsider(playwright: any): Promise<FixtureSession> {
  const session = await signedIn(playwright, "FOREIGN");
  const scope = { "X-Proj-Id": suite.env.E2E_CJ_FOREIGN_PROJECT_ID };
  return {
    ...session,
    headers: (extra: Record<string, string> = {}) => session.headers({ ...scope, ...extra }),
    request: new Proxy(session.request, {
      get(target, property, receiver) {
        const value = Reflect.get(target, property, receiver);
        if (typeof value !== "function") return value;
        if (!["get", "post", "put", "patch", "delete"].includes(String(property))) return value.bind(target);
        return (url: string, options: Record<string, any> = {}) =>
          value.call(target, url, { ...options, headers: { ...scope, ...(options.headers ?? {}) } });
      },
    }),
  };
}

/** Status and a short server detail only. */
async function detail(response: APIResponse): Promise<string> {
  const text = await response.text().catch(() => "");
  return `${response.status()} ${text.slice(0, 300)}`;
}

async function ok(response: APIResponse, what: string, status = 200): Promise<any> {
  expect(response.status(), `${what}: ${await detail(response)}`).toBe(status);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

function refusedAcrossTenant(response: APIResponse, what: string): void {
  // 200 is a leak, 401 a masked refusal, 5xx an unhandled path.
  expect([403, 404], what).toContain(response.status());
}

async function anonymous(playwright: any): Promise<APIRequestContext> {
  const request = await newFixtureContext(playwright);
  contexts.push(request);
  return request;
}

function post(who: FixtureSession, url: string, data: unknown = {}): Promise<APIResponse> {
  return who.request.post(url, { headers: who.headers(), data });
}

async function bench(who: FixtureSession, claimId: string): Promise<any> {
  return ok(await who.request.get(`/api/claims/${claimId}/bench`), "GET bench");
}

async function boardVersion(who: FixtureSession, claimId: string): Promise<number> {
  return Number((await bench(who, claimId)).bench.board_version);
}

async function decideAndLock(who: FixtureSession, claimId: string): Promise<any> {
  let board = await bench(who, claimId);
  for (const head of board.bench.heads.filter((h: any) => h.test_stale)) {
    // A test against facts that have since changed is not a basis for a lock (D-22): test again.
    board = await ok(await post(who, `/api/claims/${claimId}/bench/heads/${head.head_id}/test`, { board_version: board.bench.board_version }), `re-test ${head.head_id}`);
  }
  for (const head of board.bench.heads) {
    for (const key of ELEMENTS) {
      if (head.elements[key]?.state) continue;
      const response = await who.request.put(`/api/claims/${claimId}/bench/heads/${head.head_id}/elements/${key}`, {
        headers: who.headers(),
        data: { board_version: board.bench.board_version, state: head.elements[key]?.proposed_state ?? "weak" },
      });
      board = await ok(response, `decide ${head.head_id}.${key}`);
    }
    if (!head.classification) {
      const response = await who.request.patch(`/api/claims/${claimId}/bench/heads/${head.head_id}`, {
        headers: who.headers(),
        data: { board_version: board.bench.board_version, classification: head.proposed_classification ?? "compensable" },
      });
      board = await ok(response, `classify ${head.head_id}`);
    }
  }
  expect(board.gate.blockers, "the lock gate still has blockers").toEqual([]);
  board = await ok(await post(who, `/api/claims/${claimId}/bench/lock`, { board_version: board.bench.board_version }), "lock");
  expect(board.bench.stage).toBe("locked");
  return board;
}

test.beforeAll(async () => {
  suite.env = environment();
  if (!stagingTeardownHasWork()) return;
  assertTargetIsNotProduction();
});

test.afterAll(async ({ playwright }) => {
  if (stagingTeardownHasWork() && suite.env.E2E_CJ_CONTRACTOR_PROJECT_ID) {
    const admin = await as(playwright, "ORGADMIN", contractor());
    const residue: Record<string, unknown> = { run: RUN_ID, failures: [] as string[] };
    const failures = residue.failures as string[];
    for (const id of suite.assignmentIds) {
      const revoked = await post(admin, `/api/reviewer-assignments/${id}/revoke`, { reason: `certification teardown ${RUN_ID}` });
      if (!revoked.ok()) failures.push(`revoke ${id}: ${revoked.status()}`);
    }
    if (suite.draftId) {
      const removed = await admin.request.delete(`/api/arbitration/drafts/${suite.draftId}`, { headers: admin.headers() });
      if (!removed.ok()) failures.push(`draft ${suite.draftId}: ${removed.status()}`);
    }
    if (suite.engineerClaimId) {
      const eng = await as(playwright, "ORGADMIN", engineer());
      const removed = await eng.request.delete(`/api/claims/${suite.engineerClaimId}`, { headers: eng.headers() });
      if (!removed.ok()) failures.push(`engineer claim ${suite.engineerClaimId}: ${removed.status()}`);
    }
    residue.hindrances = await archiveRunOwned(admin, [contractor()]);
    residue.left_for_manual_removal = { contractor_claim: suite.claimId, arbitration_case: suite.caseId };
    writeEvidence("claim-journey-residue", residue);
    writeEvidence("claim-journey", suite.evidence);
  }
  await Promise.all(contexts.map((context) => context.dispose()));
});

test.describe("Claim journey - deployed certification", () => {
  test("build identifiers are recorded", async ({ playwright }, testInfo) => {
    environment();
    const request = await anonymous(playwright);
    const ids: Record<string, unknown> = { run: RUN_ID, release_sha_supplied: process.env.E2E_CJ_RELEASE_SHA?.trim() || null };
    // No endpoint exposes RELEASE_SHA (Dockerfile ENV + image label only): recorded as such, a 4B-44 gap.
    for (const path of ["/health/live", "/api/health/live"]) {
      const response = await request.get(path);
      if (response.ok()) {
        const body = await response.json().catch(() => ({}));
        ids.health_path = path;
        ids.backend_release_sha = body.release_sha ?? body.release ?? body.version ?? "not exposed by the backend";
        break;
      }
    }
    const index = await request.get("/");
    expect(index.status(), "GET / (the client shell)").toBe(200);
    const html = await index.text();
    ids.client_assets = Array.from(html.matchAll(/\/assets\/([\w.-]+\.(?:js|css))/g), (match) => match[1]);
    expect((ids.client_assets as string[]).length, "no hashed client asset in the shell").toBeGreaterThan(0);
    console.log(`4B-44 build identifiers: ${JSON.stringify(ids)}`);
    await testInfo.attach("build-identifiers", { body: JSON.stringify(ids, null, 2), contentType: "application/json" });
    suite.evidence.build = ids;
  });

  test("the Claim Bench is on, in canary, for the tenant; anonymous is refused", async ({ playwright }) => {
    environment();
    const admin = await as(playwright, "ORGADMIN", contractor());
    const availability = await ok(await admin.request.get("/api/claims/bench/availability"), "availability");
    expect(availability.enabled, "add the tenant to CLAIM_BENCH_CANARY_TENANT_IDS").toBe(true);
    expect(availability.rollout_mode).toBe("canary");
    const letters = await ok(await admin.request.get("/api/letters/bench/availability"), "letter availability");
    expect(letters.enabled, "add the tenant to LETTER_BENCH_CANARY_TENANT_IDS").toBe(true);
    const anon = await anonymous(playwright);
    expect((await anon.get("/api/claims/bench/availability")).status()).toBe(401);
  });

  test("1. start: a claim is created", async ({ playwright }) => {
    environment();
    const admin = await as(playwright, "ORGADMIN", contractor());
    for (const [project, capacity] of [[contractor(), "contractor"], [engineer(), "engineer"]] as const) {
      const scoped = await as(playwright, "ORGADMIN", project);
      const state = await ok(await scoped.request.get(`/api/projects/${project}/contractual-capacity`), "capacity");
      if (state.value !== capacity) {
        await ok(
          await scoped.request.patch(`/api/projects/${project}/contractual-capacity`, {
            headers: scoped.headers(),
            data: { contractual_capacity: capacity, reason: `Certification ${RUN_TAG}: this project acts for the ${capacity}.` },
          }),
          `set ${capacity}`
        );
      }
    }
    const created = await ok(
      await post(admin, "/api/claims", {
        title: `${RUN_TAG} Delayed handover of Site`,
        type: "eot",
        status: "notified",
        description: "Right of way for chainage 12+000 to 18+500 not handed over.",
        event_date: "2026-03-02T00:00:00",
        awareness_date: "2026-03-02T00:00:00",
        notice_date: "2026-03-20T00:00:00",
        amount_claimed: 1250000.5,
        eot_days_claimed: 60,
        contract_clauses: ["Sub-Clause 8.4", suite.env.E2E_CJ_NOTICE_CLAUSE_NO],
        project_id: contractor(),
        organization_id: fixtureOrganizationId(),
      }),
      "POST /api/claims",
      201
    );
    suite.claimId = String(created.id ?? created._id);
    expect(created.project_id).toBe(contractor());
    const anon = await anonymous(playwright);
    expect((await anon.get(`/api/claims/${suite.claimId}`)).status()).toBe(401);
    const foreign = await outsider(playwright);
    refusedAcrossTenant(await foreign.request.get(`/api/claims/${suite.claimId}`), "foreign read of the claim");
  });

  test("2. heads: open, add, test with the real model, decide, lock", async ({ playwright }) => {
    environment();
    const admin = await as(playwright, "ORGADMIN", contractor());
    const claimId = suite.claimId!;
    let board = await ok(await post(admin, `/api/claims/${claimId}/bench/open`), "open bench");
    expect(board.bench.heads.map((h: any) => h.head_id)).toEqual(["H1"]);
    board = await ok(
      await post(admin, `/api/claims/${claimId}/bench/heads`, {
        board_version: board.bench.board_version,
        title: `${RUN_TAG} Idling of plant during the hold`,
        kind: "cost",
        event_summary: "Plant stood idle while access was withheld.",
        clause_refs: ["8.4"],
        amount_claimed: 500000,
      }),
      "add head"
    );
    for (const headId of ["H1", "H2"]) {
      board = await ok(await post(admin, `/api/claims/${claimId}/bench/heads/${headId}/test`, { board_version: board.bench.board_version }), `test ${headId}`);
    }
    const steps: unknown[] = [];
    for (const head of board.bench.heads) {
      const step = await ok(await admin.request.get(`/api/claims/${claimId}/bench/steps/${head.step_id}`), "step");
      expect(step.degraded, `${head.head_id}: DEGRADED - record checks wrote it, not the model (${step.error ?? ""})`).toBe(false);
      expect(step.role).toBe("contractor");
      steps.push({ head: head.head_id, skill_version: step.skill_version, model: step.model });
    }
    const foreign = await outsider(playwright);
    refusedAcrossTenant(await foreign.request.get(`/api/claims/${claimId}/bench`), "foreign read of the bench");
    const locked = await decideAndLock(admin, claimId);
    suite.evidence.heads = { steps, locked_version: locked.bench.board_version };
  });

  test("3. notice: recorded by one person, verified by another", async ({ playwright }) => {
    environment();
    const admin = await as(playwright, "ORGADMIN", contractor());
    const verifier = await as(playwright, "VERIFIER", contractor());
    const requirement = await ok(
      await post(admin, "/api/claims/notice/requirements", {
        project_id: contractor(),
        claim_type: "eot",
        period_days: Number(suite.env.E2E_CJ_NOTICE_PERIOD_DAYS),
        trigger: "awareness",
        clause_no: suite.env.E2E_CJ_NOTICE_CLAUSE_NO,
        quote: suite.env.E2E_CJ_NOTICE_QUOTE,
      }),
      "record notice",
      201
    );
    const id = String(requirement.id ?? requirement._id);
    expect(requirement.origin, "the clause did not resolve in the clause index").toBe("clause_index");
    const own = await post(admin, `/api/claims/notice/requirements/${id}/verify`);
    expect(own.status(), await detail(own)).toBe(403);
    const verified = await ok(await post(verifier, `/api/claims/notice/requirements/${id}/verify`), "verify");
    expect(verified.verification).toBe("verified");
    expect(verified.verified_by).not.toBe(verified.created_by);
    const anon = await anonymous(playwright);
    expect((await anon.post(`/api/claims/notice/requirements/${id}/verify`)).status()).toBe(401);
  });

  test("4. delay: a hindrance linked to H1 and the net EOT", async ({ playwright }) => {
    environment();
    const admin = await as(playwright, "ORGADMIN", contractor());
    const claimId = suite.claimId!;
    const hindrance = await ok(
      await post(
        admin,
        "/api/hindrances",
        entryBody(contractor(), "cj-row", {
          start_date: "2026-03-02T00:00:00",
          end_date: "2026-04-30T00:00:00",
          responsibility: "employer",
          critical_path_impact: true,
        })
      ),
      "create hindrance",
      201
    );
    suite.hindranceId = String(hindrance.id ?? hindrance._id);
    // JG-4: the record behind the hindrance, linked on the register's own entry; escalation
    // carries it as the evidence for H1's days.
    await ok(
      await post(admin, `/api/entities/delay_event/${suite.hindranceId}/document-links:batch`, {
        links: [{ document_id: suite.env.E2E_CJ_RECORD_DOCUMENT_ID, relationship_role: "supporting_document" }],
      }),
      "link the record to the hindrance",
      201
    );
    const link = { target_type: "hindrance", target_id: suite.hindranceId };
    // D-22 (JG-3, fixed): the board is locked, so the link is refused until someone who may
    // manage the claim reopens it with a reason, which is audited.
    const refused = await post(admin, `/api/claims/${claimId}/bench/heads/H1/delay/links`, {
      board_version: await boardVersion(admin, claimId),
      ...link,
    });
    expect(refused.status(), `link on a locked board: ${await detail(refused)}`).toBe(409);
    expect((await refused.json()).detail?.code).toBe("claim_board_locked");
    expect((await bench(admin, claimId)).bench.stage).toBe("locked");
    await ok(
      await post(admin, `/api/claims/${claimId}/bench/unlock`, {
        board_version: await boardVersion(admin, claimId),
        reason: `${RUN_TAG} the hindrance register now records the access delay`,
      }),
      "reopen the board with a reason"
    );
    let delay = await ok(
      await post(admin, `/api/claims/${claimId}/bench/heads/H1/delay/links`, {
        board_version: await boardVersion(admin, claimId),
        ...link,
      }),
      "link hindrance"
    );
    suite.evidence.stage_after_link = (await bench(admin, claimId)).bench.stage;
    delay = await ok(
      await admin.request.put(`/api/claims/${claimId}/bench/heads/H1/delay/settings`, {
        headers: admin.headers(),
        data: { board_version: await boardVersion(admin, claimId), method: "as_planned_vs_as_built" },
      }),
      "delay method"
    );
    const event = delay.events[0];
    await ok(
      await admin.request.put(`/api/claims/${claimId}/bench/heads/H1/delay/events/${event.event_id}`, {
        headers: admin.headers(),
        data: { board_version: await boardVersion(admin, claimId), risk_owner: "employer", criticality: "critical" },
      }),
      "decide event"
    );
    const view = await ok(await admin.request.get(`/api/claims/${claimId}/bench/heads/H1/delay`), "delay view");
    expect(view.blockers).toEqual([]);
    expect(view.net.settled).toBe(true);
    expect(view.net.days).toBe(view.employer_critical_days);
    expect(view.net.days).toBeGreaterThan(0);
    expect(view.net_table.reduce((sum: number, row: any) => sum + row.days, 0)).toBe(view.net.days);
    suite.evidence.net_eot_days = view.net.days;
  });

  test("5. quantum: a line with its record and the total in paise", async ({ playwright }) => {
    environment();
    const admin = await as(playwright, "ORGADMIN", contractor());
    const claimId = suite.claimId!;
    await ok(
      await post(admin, `/api/entities/claim/${claimId}/document-links:batch`, {
        links: [
          { document_id: suite.env.E2E_CJ_RECORD_DOCUMENT_ID, relationship_role: "correspondence" },
          { document_id: suite.env.E2E_CJ_NOTICE_DOCUMENT_ID, relationship_role: "notice" },
        ],
      }),
      "link documents",
      201
    );
    const quantum = await ok(
      await post(admin, `/api/claims/${claimId}/bench/heads/H2/quantum/lines`, {
        board_version: await boardVersion(admin, claimId),
        category: "direct",
        description: `${RUN_TAG} Hire of idle excavator, 12 to 30 March`,
        amount: "500000.25",
        records: [{ document_id: suite.env.E2E_CJ_RECORD_DOCUMENT_ID, role: "support" }],
      }),
      "add quantum line"
    );
    expect(quantum.claimed_total_paise).toBe(50000025);
    expect(quantum.supported_total_paise).toBe(50000025);
    const anon = await anonymous(playwright);
    expect((await anon.get(`/api/claims/${claimId}/bench/heads/H2/quantum`)).status()).toBe(401);
    await decideAndLock(admin, claimId);
    suite.evidence.quantum_total_paise = quantum.claimed_total_paise;
  });

  test("6. determination (Engineer project): four eyes, then the Letter Bench pre-filled", async ({ playwright }) => {
    environment();
    const admin = await as(playwright, "ORGADMIN", engineer());
    const author = await as(playwright, "ENG_AUTHOR", engineer());
    const reviewer = await as(playwright, "ENG_REVIEWER", engineer());
    const received = await ok(
      await post(admin, "/api/claims", {
        title: `${RUN_TAG} Contractor's claim: delayed handover of Site`,
        type: "eot",
        status: "notified",
        event_date: "2026-03-02T00:00:00",
        notice_date: "2026-03-20T00:00:00",
        amount_claimed: 1250000.5,
        eot_days_claimed: 60,
        contract_clauses: ["8.4"],
        project_id: engineer(),
        organization_id: fixtureOrganizationId(),
      }),
      "engineer claim",
      201
    );
    suite.engineerClaimId = String(received.id ?? received._id);
    await ok(await post(admin, `/api/claims/${suite.engineerClaimId}/bench/open`), "open engineer bench");
    const base = `/api/claims/${suite.engineerClaimId}/determinations`;
    const listed = await ok(await author.request.get(base), "list determinations");
    expect(listed.perspective).toBe("engineer");
    expect(listed.can_author).toBe(true);
    let view = await ok(await post(author, base, { origin: "authored" }), "author");
    let det = view.determination;
    view = await ok(await post(author, `${base}/${det._id}/propose`, { version: det.version }), "propose");
    for (const finding of view.determination.heads) {
      view = await ok(
        await author.request.put(`${base}/${det._id}/heads/${finding.head_id}`, {
          headers: author.headers(),
          data: {
            version: view.determination.version,
            result: "partial",
            days: 10,
            amount: 1250000.5,
            findings: "Access was given ten days late; the rest is not shown.",
          },
        }),
        "finding"
      );
    }
    det = view.determination;
    det = (await ok(await post(author, `${base}/${det._id}/submit`, { version: det.version, reviewer_id: suite.userIds.ENG_REVIEWER }), "submit")).determination;
    expect(det.status).toBe("in_review");
    const own = await post(author, `${base}/${det._id}/approve`, { version: det.version });
    expect(own.status(), await detail(own)).toBe(403);
    const approved = await ok(await post(reviewer, `${base}/${det._id}/approve`, { version: det.version }), "approve");
    expect(approved.determination.status).toBe("approved");
    expect(approved.determination.decided_by).toBe(suite.userIds.ENG_REVIEWER);
    suite.determinationId = det._id;

    const drafter = await as(playwright, "ENG_DRAFTER", engineer());
    const opened = await ok(await post(drafter, "/api/letters/bench/open", { trigger_type: "determination", trigger_id: det._id }), "open Letter Bench");
    expect(opened.created).toBe(true);
    // JG-2 (fixed): the client drafter who opened it reads it, read-only.
    const letter = await ok(await drafter.request.get(`/api/letters/${opened.letter_id}/bench`), "opener's read of the Letter Bench");
    expect(letter.read_only).toBe(true);
    const text = JSON.stringify(letter);
    expect(text).toContain("10 days extension of time");
    expect(text).toContain("INR 12,50,000.50");
    const foreign = await outsider(playwright);
    refusedAcrossTenant(await post(foreign, "/api/letters/bench/open", { trigger_type: "determination", trigger_id: det._id }), "foreign open");
    suite.evidence.determination = { id: det._id, letter_id: opened.letter_id, drafter_read: 200 };
  });

  test("7. submission (Contractor project): composed with provenance", async ({ playwright }) => {
    environment();
    const admin = await as(playwright, "ORGADMIN", contractor());
    const submission = await ok(await post(admin, `/api/claims/${suite.claimId}/submission/compose`, {}), "compose");
    const paragraphs = submission.sections.flatMap((view: any) => view.section.paragraphs);
    expect(paragraphs.length).toBeGreaterThan(0);
    expect(paragraphs.filter((p: any) => p.sources.length === 0).map((p: any) => p.text.slice(0, 60))).toEqual([]);
    const kinds = new Set(paragraphs.flatMap((p: any) => p.sources.map((s: any) => s.kind)));
    for (const kind of ["head", "quantum", "delay"]) expect(kinds.has(kind), `no paragraph cites ${kind}`).toBe(true);
    suite.evidence.submission = { paragraphs: paragraphs.length, degraded: submission.sections.some((v: any) => v.section.degraded) };
  });

  test("8. escalation into a case, and the Statement of Claim gated", async ({ playwright }) => {
    environment();
    const admin = await as(playwright, "ORGADMIN", contractor());
    const approver = await as(playwright, "VERIFIER", contractor());
    const claimId = suite.claimId!;
    const preview = await ok(await post(admin, "/api/claims/escalation/preview", { claim_ids: [claimId] }), "preview");
    expect(preview.can_escalate, JSON.stringify(preview.blockers)).toBe(true);
    const anon = await anonymous(playwright);
    expect((await anon.post("/api/claims/escalation/preview", { data: { claim_ids: [claimId] } })).status()).toBe(401);
    const foreign = await outsider(playwright);
    refusedAcrossTenant(await post(foreign, "/api/claims/escalation/preview", { claim_ids: [claimId] }), "foreign preview");
    const run = await ok(
      await post(admin, "/api/claims/escalation/run", {
        claim_ids: [claimId],
        new_case: { title: `${RUN_TAG} ABC Infra v. Authority` },
        idempotency_key: `cj-${RUN_ID}`.replace(/[^A-Za-z0-9_.:-]/g, "-"),
      }),
      "escalate",
      201
    );
    suite.caseId = run.case_id;
    const cases = `/api/arbitration/cases/${run.case_id}`;
    suite.matrix = await ok(await admin.request.get(`${cases}/claim-matrix`), "claim matrix");
    expect(suite.matrix.length).toBe(2);
    expect(suite.matrix.every((row: any) => row.approval_status === "needs_review")).toBe(true);

    const draft = await ok(
      await post(admin, "/api/arbitration/drafts", {
        case_id: run.case_id,
        organization_id: fixtureOrganizationId(),
        project_id: contractor(),
        draft_type: "statement_of_claim",
        party_role: "claimant",
        title: `${RUN_TAG} Statement of Claim`,
      }),
      "create draft",
      201
    );
    suite.draftId = String(draft.id ?? draft._id);
    const drafts = `/api/arbitration/drafts/${suite.draftId}`;
    const pleading = { full_markdown: "# STATEMENT OF CLAIM\n\n1. The Respondent delayed access to the Site.\n" };
    await ok(await post(admin, `${drafts}/versions`, pleading), "version 1", 201);
    const gate = (await ok(await approver.request.get(`${drafts}/citations`), "gates")).action_gates;
    expect(gate.approve.allowed).toBe(false);
    const approve = await post(approver, `${drafts}/approve`);
    expect(approve.status(), await detail(approve)).toBe(409);
    const refusal = (await approve.json()).detail;
    expect(refusal.code).toBe("draft_action_refused");
    expect(new Set(refusal.detail.reasons.map((r: any) => r.code))).toEqual(new Set(gate.approve.reasons.map((r: any) => r.code)));
    const exported = await approver.request.get(`${drafts}/export/docx`);
    expect(exported.status(), await detail(exported)).toBe(409);

    // Reviewer roles granted over HTTP, the heads approved by two different reviewers.
    for (const [who, role] of [["LEGAL_REVIEWER", "legal_reviewer"], ["QUANTUM_REVIEWER", "quantum_reviewer"]] as const) {
      await as(playwright, who, contractor());
      const granted = await ok(
        await post(admin, "/api/reviewer-assignments", {
          user_id: suite.userIds[who],
          role_key: role,
          scope_type: "project",
          organization_id: fixtureOrganizationId(),
          project_id: contractor(),
          reason: `Certification ${RUN_TAG}`,
        }),
        `grant ${role}`,
        201
      );
      suite.assignmentIds.push(String(granted.id ?? granted._id));
    }
    for (const row of suite.matrix) {
      for (const [who, role] of [["LEGAL_REVIEWER", "legal"], ["QUANTUM_REVIEWER", "quantum"]] as const) {
        const reviewer = await as(playwright, who, contractor());
        await ok(await post(reviewer, `${cases}/claim-matrix/${row._id ?? row.id}/review`, { action: "approve", reviewer_role: role }), `${role} review`);
      }
    }
    await ok(await post(admin, `${drafts}/versions`, pleading), "version 2", 201);
    const versions = await ok(await admin.request.get(`${drafts}/versions`), "versions");
    const latest = versions.reduce((a: any, b: any) => (a.version > b.version ? a : b));
    const blockers: string[] = latest.structured_output?.approval_blockers ?? [];
    expect(blockers.some((b) => b.includes("awaiting approval") || b.includes("none is approved"))).toBe(false);
    // JG-4 (fixed): every pleaded head carries what it needs, including the record behind H1's days.
    expect(latest.structured_output?.pleaded_head_gaps ?? []).toEqual([]);
    const after = (await ok(await approver.request.get(`${drafts}/citations`), "gates after review")).action_gates;
    expect(after.approve.allowed, "a hand-written version without citations cannot pass validation").toBe(false);
    expect(after.approve.checked_on_action).toEqual(["Case readiness approval", "Plan approval", "Legal review approval"]);
    const readiness = await ok(await admin.request.get(`${cases}/readiness`, { params: { draft_id: suite.draftId } }), "readiness");
    suite.evidence.pleading = {
      case_id: run.case_id,
      blockers,
      head_gaps: latest.structured_output?.pleaded_head_gaps ?? [],
      readiness_open: readiness.blockers.map((b: any) => b.check_key),
    };
  });
});
