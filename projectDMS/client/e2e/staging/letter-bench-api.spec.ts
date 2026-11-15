/**
 * Letter Bench - DEPLOYED staging certification (real HTTP, real model, real
 * documents).
 *
 * What this measures that nothing else has. Every Letter Bench test before this
 * one ran against a fake language model, a stand-in `DraftContextBuilder` and
 * invented role data. This file runs the whole path - open, analyse, evaluate,
 * decide, lock, compose - against a deployed stack, and fails if the model did
 * not really answer:
 *
 *   REAL MODEL       the analysis step, every evaluation step and every reply
 *                    paragraph must be `degraded: false`. Degraded means the
 *                    deterministic fallback wrote the text because the model was
 *                    unavailable or its output was rejected; a green run on
 *                    fallback text would certify nothing.
 *   REAL DOCUMENT    the board is opened on a document the deployment ingested
 *   AUTHORITY        itself (`E2E_BENCH_DOCUMENT_ID`), in a project whose
 *                    contract is ingested. Every assertion must quote text that
 *                    is in that document, and cited clauses must resolve to
 *                    ledger sources.
 *   REAL ROLES       a user without drafting allocation is refused with 403, a
 *                    user in another organisation cannot open or read the board,
 *                    and an anonymous caller gets 401. Statuses are exact where
 *                    the server's contract is exact: a 401 for a signed-in user
 *                    would be a masked 403.
 *
 * SETUP (runbook, `docs/LETTER_BENCH_STAGING_CHECKS.md`)
 *   - staging backend with `LETTER_BENCH_ROLLOUT_MODE=canary` and the fixture
 *     organisation in `LETTER_BENCH_CANARY_TENANT_IDS`
 *   - `E2E_BENCH_DOCUMENT_ID`: an incoming letter, ingested through the normal
 *     upload path, in `E2E_STAGING_ORG_ID` / `E2E_STAGING_PROJECT_ID`, that has
 *     NOT been opened on the Bench before. Opening is idempotent per document
 *     and skill steps are cached on their input, so a reused document would not
 *     call the model again - the spec refuses one.
 *   - three accounts: a drafter with drafting allocation in the fixture project,
 *     a same-organisation user without it, and a user in another organisation.
 *
 * COST. This spends model budget: one analysis call, up to
 * `E2E_BENCH_MAX_EVALUATIONS` evaluation calls (default 3) and one drafting call
 * per assertion.
 *
 * RESIDUE. The run leaves one letter and its position board on staging, tied to
 * the document. There is no delete route for a board; the runbook says how to
 * remove the document afterwards.
 *
 * NOT YET EXECUTED AGAINST A DEPLOYMENT. Written from the service code
 * (`services/letter_drafting/bench.py`, `routers/letter_bench.py`); no checkbox
 * or readiness claim rests on it until staging has run it.
 */
import { expect, test, type APIRequestContext, type APIResponse } from "@playwright/test";

import {
  RUN_ID,
  RUN_TAG,
  assertTargetIsNotProduction,
  fixtureOrganizationId,
  fixtureProjectId,
  newFixtureContext,
  signIn,
  type FixtureSession,
} from "./fixtures";
import { guardedSession, inProject, writeEvidence } from "./hindrance-fixtures";
import { requireStagingEnvironment, stagingTeardownHasWork } from "./staging-target";

test.describe.configure({ mode: "serial" });

const DRAFTER = ["E2E_BENCH_DRAFTER_EMAIL", "E2E_BENCH_DRAFTER_PASSWORD"] as const;
const UNALLOCATED = ["E2E_BENCH_UNALLOCATED_EMAIL", "E2E_BENCH_UNALLOCATED_PASSWORD"] as const;
const FOREIGN = ["E2E_BENCH_FOREIGN_EMAIL", "E2E_BENCH_FOREIGN_PASSWORD"] as const;
const SCOPE = ["E2E_STAGING_ORG_ID", "E2E_STAGING_PROJECT_ID", "E2E_BENCH_DOCUMENT_ID"] as const;

const SYSTEM_SKILLS = [
  "indian-infra-contract-letters",
  "indian-infra-letter-analysis",
  "indian-infra-variations",
];
const ANALYSIS_SKILL = "indian-infra-letter-analysis";
const VERDICTS = ["entitlement", "partial", "none", "need_information"];

/** How long one model-backed operation may take before the run fails. */
const OPERATION_TIMEOUT_MS = Number(process.env.E2E_BENCH_TIMEOUT_MS ?? 300_000);
const POLL_MS = 3_000;
const MAX_EVALUATIONS = Number(process.env.E2E_BENCH_MAX_EVALUATIONS ?? 3);

const contexts: APIRequestContext[] = [];
const suite: {
  env: Record<string, string>;
  drafter?: FixtureSession;
  letterId?: string;
  evidence: Record<string, unknown>;
} = { env: {}, evidence: { run: RUN_ID } };

async function session(playwright: any, vars: readonly [string, string]): Promise<FixtureSession> {
  const request = await newFixtureContext(playwright);
  contexts.push(request);
  const signedIn = guardedSession(await signIn(request, vars[0], vars[1]));
  return inProject(signedIn, fixtureProjectId());
}

function drafter(): FixtureSession {
  if (!suite.drafter) throw new Error("the drafter session was not established");
  return suite.drafter;
}

function letterId(): string {
  if (!suite.letterId) throw new Error("no board was opened; an earlier test failed");
  return suite.letterId;
}

/** Status and a short server detail only. A board body can carry letter text. */
async function detail(response: APIResponse): Promise<string> {
  const text = await response.text().catch(() => "");
  return `${response.status()} ${text.slice(0, 200)}`;
}

async function readBench(who: FixtureSession): Promise<any> {
  const response = await who.request.get(`/api/letters/${letterId()}/bench`);
  expect(response.status(), `GET bench: ${await detail(response)}`).toBe(200);
  return response.json();
}

/** Poll the board until `done` holds, failing loudly on a recorded error. */
async function waitForBoard(
  who: FixtureSession,
  what: string,
  done: (bench: any) => boolean
): Promise<any> {
  const deadline = Date.now() + OPERATION_TIMEOUT_MS;
  let bench = await readBench(who);
  while (!done(bench)) {
    if (Date.now() > deadline) {
      throw new Error(
        `${what} did not finish in ${OPERATION_TIMEOUT_MS} ms ` +
          `(stage=${bench.board.stage}, composing=${bench.board.composing}, ` +
          `running=${bench.gate.running_steps}, last_error=${bench.board.last_error ?? "none"})`
      );
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    bench = await readBench(who);
  }
  return bench;
}

function squash(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

test.beforeAll(async ({ playwright }) => {
  suite.env = requireStagingEnvironment(...SCOPE, ...DRAFTER, ...UNALLOCATED, ...FOREIGN);
  if (!stagingTeardownHasWork()) return;
  assertTargetIsNotProduction();
  suite.drafter = await session(playwright, DRAFTER);
});

test.afterAll(async () => {
  if (stagingTeardownHasWork()) {
    writeEvidence("letter-bench", suite.evidence);
  }
  await Promise.all(contexts.map((context) => context.dispose()));
});

test.describe("Letter Bench - deployed certification", () => {
  test("the Bench is switched on for the fixture organisation", async () => {
    requireStagingEnvironment(...SCOPE, ...DRAFTER);
    const response = await drafter().request.get("/api/letters/bench/availability", {
      params: { organization_id: fixtureOrganizationId() },
    });
    expect(response.status(), await detail(response)).toBe(200);
    const body = await response.json();
    expect(
      body.enabled,
      "set LETTER_BENCH_ROLLOUT_MODE=canary and add the fixture organisation to LETTER_BENCH_CANARY_TENANT_IDS"
    ).toBe(true);
    // Canary, not primary: this run is the evidence primary would need.
    expect(body.rollout_mode).toBe("canary");
    suite.evidence.rollout_mode = body.rollout_mode;
  });

  test("an anonymous caller is refused before anything is read", async ({ playwright }) => {
    requireStagingEnvironment(...SCOPE);
    const anonymous = await newFixtureContext(playwright);
    contexts.push(anonymous);
    const response = await anonymous.get("/api/letters/bench/availability");
    expect(response.status()).toBe(401);
  });

  test("a user without drafting allocation cannot open the Bench on the document", async ({ playwright }) => {
    requireStagingEnvironment(...SCOPE, ...UNALLOCATED);
    const unallocated = await session(playwright, UNALLOCATED);
    const response = await unallocated.request.post("/api/letters/bench/open", {
      headers: unallocated.headers(),
      data: {
        incoming_document_id: suite.env.E2E_BENCH_DOCUMENT_ID,
        organization_id: fixtureOrganizationId(),
        project_id: fixtureProjectId(),
      },
    });
    // 403 exactly. 401 would be a masked 403 (the client logs the user out on
    // it), 404 would mean the document was hidden rather than the action
    // refused, and 200 is the defect this test exists for.
    expect(response.status(), await detail(response)).toBe(403);
    suite.evidence.unallocated_open = response.status();
  });

  test("the drafter opens the Bench and a second open returns the same board", async () => {
    requireStagingEnvironment(...SCOPE, ...DRAFTER);
    const open = () =>
      drafter().request.post("/api/letters/bench/open", {
        headers: drafter().headers(),
        data: {
          incoming_document_id: suite.env.E2E_BENCH_DOCUMENT_ID,
          organization_id: fixtureOrganizationId(),
          project_id: fixtureProjectId(),
        },
      });

    const first = await open();
    expect(first.status(), await detail(first)).toBe(200);
    const opened = await first.json();
    expect(
      opened.created,
      "E2E_BENCH_DOCUMENT_ID already has a board. Skill steps are cached on their input, so a " +
        "reused document would not call the model. Ingest a new letter for each certification run."
    ).toBe(true);
    suite.letterId = String(opened.letter_id);

    const second = await open();
    expect(second.status(), await detail(second)).toBe(200);
    const again = await second.json();
    expect(again.created).toBe(false);
    expect(String(again.letter_id)).toBe(suite.letterId);
    suite.evidence.letter_id = suite.letterId;
  });

  test("the analysis is written by the model, from the document itself", async () => {
    requireStagingEnvironment(...SCOPE, ...DRAFTER);
    const bench = await waitForBoard(
      drafter(),
      "the analysis",
      (b) => b.board.stage !== "dissecting" && b.gate.running_steps === 0
    );
    const board = bench.board;

    expect(board.last_error, "the analysis recorded an error").toBeFalsy();
    expect(board.model_blocked_reason, "the guardrail refused to send this letter to the model").toBeFalsy();
    expect(board.organization_id).toBe(fixtureOrganizationId());
    expect(board.incoming_document_id).toBe(suite.env.E2E_BENCH_DOCUMENT_ID);

    const text = squash(String(board.incoming_text ?? ""));
    expect(
      text.length,
      "the document has no extracted text; ingest it through the normal upload path first"
    ).toBeGreaterThan(200);

    expect(board.assertions.length, "the analysis found no assertions").toBeGreaterThan(0);
    const unquoted = board.assertions
      .filter((assertion: any) => !text.includes(squash(String(assertion.quoted_text))))
      .map((assertion: any) => assertion.assertion_id);
    expect(unquoted, "assertions that quote text which is not in the document").toEqual([]);

    const summary = bench.steps.find((step: any) => step.step_id === board.analysis_step_id);
    expect(summary, "the board names no analysis step").toBeTruthy();
    expect(summary.skill_key).toBe(ANALYSIS_SKILL);
    expect(summary.skill_version).toBe("1.0.0");
    expect(summary.status).toBe("completed");
    expect(
      summary.degraded,
      "the analysis is DEGRADED: the deterministic fallback wrote it, not the model"
    ).toBe(false);

    const stepResponse = await drafter().request.get(
      `/api/letters/${letterId()}/bench/steps/${summary.step_id}`
    );
    expect(stepResponse.status(), await detail(stepResponse)).toBe(200);
    const step = await stepResponse.json();
    expect(String(step.model ?? ""), "the step records no model name").not.toBe("");
    expect(Array.isArray(step.output?.summary_lines) && step.output.summary_lines.length).toBeTruthy();

    suite.evidence.analysis = {
      model: step.model,
      skill_hash: summary.skill_hash,
      assertions: board.assertions.length,
      with_clause_refs: board.assertions.filter((a: any) => (a.clause_refs ?? []).length > 0).length,
      proposed: board.assertions.filter((a: any) => a.proposed_verdict).length,
      text_characters: text.length,
    };
  });

  test("the three system skills are offered at 1.0.0", async () => {
    requireStagingEnvironment(...SCOPE, ...DRAFTER);
    const response = await drafter().request.get(`/api/letters/${letterId()}/bench/skills`);
    expect(response.status(), await detail(response)).toBe(200);
    const skills: any[] = await response.json();
    for (const key of SYSTEM_SKILLS) {
      const skill = skills.find((row) => row.skill_key === key);
      expect(skill, `${key} is not offered on this letter`).toBeTruthy();
      expect(skill.version, `${key} version`).toBe("1.0.0");
    }
    // Names and versions only: the method text must not be listed to drafters.
    expect(JSON.stringify(skills)).not.toContain("skill_md");
  });

  test("the same-organisation user without allocation cannot read the board", async ({ playwright }) => {
    requireStagingEnvironment(...SCOPE, ...UNALLOCATED);
    const unallocated = await session(playwright, UNALLOCATED);
    const response = await unallocated.request.get(`/api/letters/${letterId()}/bench`);
    expect(response.status(), await detail(response)).toBe(403);
    suite.evidence.unallocated_read = response.status();
  });

  test("a user in another organisation can neither read nor open it", async ({ playwright }) => {
    requireStagingEnvironment(...SCOPE, ...FOREIGN);
    const request = await newFixtureContext(playwright);
    contexts.push(request);
    // Signed in under their own organisation: no fixture scope headers.
    const foreign = guardedSession(await signIn(request, FOREIGN[0], FOREIGN[1]));

    const read = await foreign.request.get(`/api/letters/${letterId()}/bench`);
    // Refusal or non-disclosure are both acceptable across a tenant boundary.
    // 200 is a leak, 401 is a masked refusal, 5xx is an unhandled path.
    expect([403, 404], `foreign read: ${await detail(read)}`).toContain(read.status());

    const open = await foreign.request.post("/api/letters/bench/open", {
      headers: foreign.headers(),
      data: { incoming_document_id: suite.env.E2E_BENCH_DOCUMENT_ID },
    });
    expect([403, 404], `foreign open: ${await detail(open)}`).toContain(open.status());
    suite.evidence.foreign = { read: read.status(), open: open.status() };
  });

  test("routed evaluations are written by the model", async () => {
    requireStagingEnvironment(...SCOPE, ...DRAFTER);
    let bench = await readBench(drafter());
    const links = bench.board.chain
      .filter((link: any) => link.kind === "evaluation" && link.target_assertion_ids.length > 0)
      .filter((link: any) => link.status !== "unavailable")
      .slice(0, MAX_EVALUATIONS);

    const results: unknown[] = [];
    for (const link of links) {
      const start = await drafter().request.post(`/api/letters/${letterId()}/bench/steps`, {
        headers: drafter().headers(),
        data: { skill_key: link.skill_key, target_assertion_ids: link.target_assertion_ids },
      });
      expect(start.status(), `run ${link.skill_key}: ${await detail(start)}`).toBe(202);
      bench = await waitForBoard(drafter(), `evaluation ${link.skill_key}`, (b) => b.gate.running_steps === 0);

      const after = bench.board.chain.find((row: any) => row.link_id === link.link_id);
      expect(after?.status, `${link.skill_key}: ${after?.reason ?? "no reason recorded"}`).toBe("done");
      const step = bench.steps.find((row: any) => row.step_id === after.step_id);
      expect(step?.status).toBe("completed");
      expect(step?.degraded, `${link.skill_key} is DEGRADED: fallback output, not the model`).toBe(false);
      results.push({ skill_key: link.skill_key, targets: link.target_assertion_ids.length, degraded: step.degraded });
    }
    // A letter with nothing to evaluate is a legitimate analysis outcome, not a
    // failure - but it is recorded, so a reviewer can ask for a richer letter.
    suite.evidence.evaluations = results;
  });

  test("positions are decided, locked, and the reply is composed by the model", async () => {
    requireStagingEnvironment(...SCOPE, ...DRAFTER);
    let bench = await readBench(drafter());

    for (const assertion of bench.board.assertions) {
      if (assertion.verdict) continue;
      const verdict = VERDICTS.includes(assertion.proposed_verdict) ? assertion.proposed_verdict : "need_information";
      const response = await drafter().request.patch(
        `/api/letters/${letterId()}/bench/assertions/${assertion.assertion_id}`,
        { headers: drafter().headers(), data: { board_version: bench.board.board_version, verdict } }
      );
      expect(response.status(), `decide ${assertion.assertion_id}: ${await detail(response)}`).toBe(200);
      bench = await response.json();
    }

    for (const item of bench.board.questions) {
      if (!item.question?.required || String(item.answer ?? "").trim()) continue;
      const response = await drafter().request.put(`/api/letters/${letterId()}/bench/questions`, {
        headers: drafter().headers(),
        data: {
          board_version: bench.board.board_version,
          question_id: item.question.question_id,
          answer: `Staging certification ${RUN_TAG}: no further instruction; proceed on the record.`,
        },
      });
      expect(response.status(), `answer question: ${await detail(response)}`).toBe(200);
      bench = await response.json();
    }

    // An evaluation run can leave the gate waiting; read it fresh before locking.
    bench = await waitForBoard(drafter(), "the board to settle", (b) => b.gate.running_steps === 0);
    expect(bench.gate.blockers, "the gate still has blockers").toEqual([]);

    const lock = await drafter().request.post(`/api/letters/${letterId()}/bench/lock-positions`, {
      headers: drafter().headers(),
      data: { board_version: bench.board.board_version },
    });
    expect(lock.status(), `lock positions: ${await detail(lock)}`).toBe(200);
    bench = await lock.json();
    expect(bench.board.stage).toBe("locked");
    expect(bench.board.locked_strategy_version, "no strategy version was accepted").not.toBeNull();
    expect(bench.gate.can_compose).toBe(true);

    const compose = await drafter().request.post(`/api/letters/${letterId()}/bench/compose`, {
      headers: drafter().headers(),
      data: { board_version: bench.board.board_version },
    });
    expect(compose.status(), `compose: ${await detail(compose)}`).toBe(202);
    bench = await waitForBoard(drafter(), "composing the reply", (b) => b.board.composing === false);

    const board = bench.board;
    expect(board.last_error, "composing recorded an error").toBeFalsy();
    expect(board.stage).toBe("composed");
    expect(board.draft_run_id, "no drafting run was created").toBeTruthy();

    const points = board.reply_paragraphs.filter((paragraph: any) => paragraph.part === "point");
    const answered = new Set(points.map((paragraph: any) => paragraph.assertion_id));
    const unanswered = board.assertions
      .map((assertion: any) => assertion.assertion_id)
      .filter((id: string) => !answered.has(id));
    expect(unanswered, "assertions with no reply paragraph").toEqual([]);

    const degraded = points.filter((paragraph: any) => paragraph.degraded).map((p: any) => p.paragraph_id);
    expect(degraded, "reply paragraphs written by the fallback template, not the model").toEqual([]);
    const empty = points.filter((paragraph: any) => !String(paragraph.text).trim()).map((p: any) => p.paragraph_id);
    expect(empty, "empty reply paragraphs").toEqual([]);

    // Document authority: a clause the reply relies on must trace to a ledger
    // source. Clauses the letter cites that the contract does not contain are a
    // legitimate finding, so they are recorded rather than failed - but a run in
    // which NOTHING resolves means the contract was not reachable at all.
    const citing = board.assertions.filter((assertion: any) => (assertion.clause_refs ?? []).length > 0);
    const resolved = citing.filter((assertion: any) => (assertion.source_ids ?? []).length > 0);
    if (citing.length > 0) {
      expect(
        resolved.length,
        "no cited clause resolved to a contract source: is the project's contract ingested?"
      ).toBeGreaterThan(0);
    }

    suite.evidence.compose = {
      draft_run_id: board.draft_run_id,
      strategy_version: board.locked_strategy_version,
      paragraphs: points.length,
      assertions_citing_clauses: citing.length,
      assertions_with_sources: resolved.length,
      unresolved_clauses: citing
        .filter((assertion: any) => (assertion.source_ids ?? []).length === 0)
        .flatMap((assertion: any) => assertion.clause_refs),
    };
  });

  test("the unallocated user still cannot change a decided position", async ({ playwright }) => {
    requireStagingEnvironment(...SCOPE, ...UNALLOCATED);
    const bench = await readBench(drafter());
    const target = bench.board.assertions[0];
    const unallocated = await session(playwright, UNALLOCATED);
    const response = await unallocated.request.patch(
      `/api/letters/${letterId()}/bench/assertions/${target.assertion_id}`,
      { headers: unallocated.headers(), data: { board_version: bench.board.board_version, verdict: "entitlement" } }
    );
    expect(response.status(), await detail(response)).toBe(403);
    const after = await readBench(drafter());
    expect(after.board.board_version, "a refused write changed the board").toBe(bench.board.board_version);
  });
});
