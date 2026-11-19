import { expect, Page, Route, test } from "@playwright/test";

// Letter Bench end-to-end, against an in-page stand-in for the Bench API that
// keeps the same contract as the server: board_version on every edit, 409 on a
// stale version, 202 for long operations, and staleness limited to dependants.

const org = { id: "org-A", _id: "org-A", name: "Aurora Engineering" };
const project = { id: "proj-A", _id: "proj-A", name: "KNPCC05", organization_id: org.id };
const LETTER_ID = "65f000000000000000000001";
const VARIATIONS = "indian-infra-variations";
const LETTERS = "indian-infra-contract-letters";
const ANALYSIS = "indian-infra-letter-analysis";

const TEXT =
  "Subject: Notice of delay and variation\n\nDear Sir,\n\n" +
  "We hereby give notice under Sub-Clause 20.2 that the Site was handed over 46 days late. " +
  "Accordingly we claim an extension of time of 46 days under Sub-Clause 8.4. " +
  "The additional pile caps instructed on 12 Aug constitute a Variation and we request payment of Rs. 4,50,000.\n\n" +
  "Yours faithfully,\nFor ABC Infra Ltd";

const QUOTES = [
  "We hereby give notice under Sub-Clause 20.2 that the Site was handed over 46 days late.",
  "Accordingly we claim an extension of time of 46 days under Sub-Clause 8.4.",
  "The additional pile caps instructed on 12 Aug constitute a Variation and we request payment of Rs. 4,50,000.",
];

type Json = Record<string, any>;

function makeState(enabled = true) {
  const assertions = QUOTES.map((quote, index) => ({
    assertion_id: `A${index + 1}`,
    order: index + 1,
    quoted_text: quote,
    span: { start: TEXT.indexOf(quote), end: TEXT.indexOf(quote) + quote.length },
    clause_refs: index === 0 ? ["20.2"] : index === 1 ? ["8.4"] : [],
    clause_checks:
      index === 0
        ? [{ clause_number: "20.2", state: "found", title: "Claims" }]
        : index === 1
          ? [{ clause_number: "8.4", state: "not_found" }]
          : [],
    proposed_verdict: index === 0 ? "none" : "need_information",
    proposed_reason:
      index === 0 ? "Notice served outside the 28 days." : "Clause 8.4 was not found in this project's clause index.",
    verdict: null,
    verdict_source: null,
    user_direction: null,
    source_ids: [],
    routed_skill_key: index === 2 ? VARIATIONS : null,
    step_ids: index === 2 ? ["step-var"] : [],
    needs: index === 2 ? ["BOQ rates for item 4.12"] : [],
    reply_paragraph_id: null,
    state: "open",
    origin: "ai",
  }));
  return {
    enabled,
    exists: true,
    composeCalls: [] as string[][],
    openPayloads: [] as Json[],
    patchVersions: [] as number[],
    forceConflictOnce: false,
    letterStatus: "Draft",
    board: {
      letter_id: LETTER_ID,
      organization_id: org.id,
      project_id: project.id,
      board_version: 3,
      stage: "positions",
      role: "employer",
      incoming_document_id: "doc-1",
      trigger: null,
      incoming_text: TEXT,
      incoming_meta: { letter_no: "CTR/2026/118", letter_date: "2026-11-02", sender: "ABC Infra Ltd", subject: "Notice of delay and variation" },
      issue_type: "delay",
      deadline: { raw: "within 14 days", due_date: "2026-11-23", days_remaining: 9, basis: "14 days from the letter date", source: "extracted", risk: "high" },
      assertions,
      chain: [
        { link_id: "l1", skill_key: ANALYSIS, label: "Letter Analysis", kind: "analysis", target_assertion_ids: [], skill_version: "1.0.0", status: "done", step_id: "step-analysis", reason: null, added_by: "router" },
        { link_id: "l2", skill_key: VARIATIONS, label: "Variations", kind: "evaluation", target_assertion_ids: ["A3"], skill_version: "1.0.0", status: "done", step_id: "step-var", reason: null, added_by: "router" },
        { link_id: "l3", skill_key: "indian-infra-eot-claims", label: "Extension of time review", kind: "evaluation", target_assertion_ids: ["A2"], skill_version: null, status: "unavailable", step_id: null, reason: "Not installed", added_by: "router" },
        { link_id: "l4", skill_key: LETTERS, label: "Draft reply", kind: "drafting", target_assertion_ids: [], skill_version: "1.0.0", status: "pending", step_id: null, reason: null, added_by: "router" },
      ],
      questions: [],
      reply_paragraphs: [] as Json[],
      internal_note: null,
      analysis_step_id: "step-analysis",
      locked_strategy_version: null as number | null,
      draft_run_id: null as string | null,
      composing: false,
      last_error: null,
    },
    steps: [
      { step_id: "step-analysis", skill_key: ANALYSIS, skill_version: "1.0.0", skill_hash: "a".repeat(64), skill_name: "Letter Analysis", target_assertion_ids: [], output_schema_key: "letter_analysis.v1", status: "completed", stale: false, degraded: false },
      { step_id: "step-var", skill_key: VARIATIONS, skill_version: "1.0.0", skill_hash: "b".repeat(64), skill_name: "Variations", target_assertion_ids: ["A3"], output_schema_key: "variation_evaluation.v1", status: "completed", stale: false, degraded: false },
    ] as Json[],
  };
}

type State = ReturnType<typeof makeState>;

function gate(state: State) {
  const board = state.board;
  const undecided = board.assertions.filter((a) => !a.verdict).length;
  const blockers = undecided ? [`${undecided} position${undecided === 1 ? "" : "s"} still need your call`] : [];
  const current = board.locked_strategy_version !== null && board.assertions.every((a) => a.state === "locked");
  return {
    undecided,
    unanswered_questions: 0,
    stale_steps: board.reply_paragraphs.filter((p) => p.stale).length,
    running_steps: 0,
    can_lock: !blockers.length && !current,
    can_compose: current && !blockers.length && !board.composing,
    blockers,
  };
}

function response(state: State) {
  return {
    letter_id: LETTER_ID,
    letter_title: "Reply to CTR/2026/118",
    letter_status: state.letterStatus,
    board: state.board,
    steps: state.steps,
    gate: gate(state),
    rollout_mode: "primary",
    read_only: state.letterStatus !== "Draft",
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockApi(page: Page, state: State) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "") || "/";
    const method = request.method();
    const body = (): Json => (request.postDataJSON() as Json) ?? {};
    const benchPath = `/letters/${LETTER_ID}/bench`;
    const versionOk = (payload: Json) => payload.board_version === state.board.board_version;
    const conflict = () => json(route, { detail: "The Bench changed since you loaded it." }, 409);

    if (path === "/csrf-token") return json(route, { csrf_token: "test-csrf" });
    if (path === "/me") {
      return json(route, { id: "user-1", email: "superadmin@example.com", roles: ["superadmin"], permissions: ["*"], organization_id: org.id, projects: [project.id] }); // The server lists Super Admin as ["*"] (get_effective_permission_names); since T-2 the client decides from that list alone.
    }
    if (path === "/roles" || path === "/users") return json(route, []);
    if (path === "/letters") {
      // The review and approval pages find their letter in this list.
      return json(route, [
        {
          id: LETTER_ID, _id: LETTER_ID, title: "Reply to CTR/2026/118", subject: "Re: Notice of delay and variation",
          recipient: "ABC Infra Ltd", content: "", status: state.letterStatus, created_by: "user-1", assigned_to: "user-1",
          organization_id: org.id, project_id: project.id, created_at: "2026-11-14T10:00:00Z", updated_at: "2026-11-14T10:00:00Z",
        },
      ]);
    }
    if (path === "/organizations") return json(route, { organizations: [org] });
    if (path === "/projects") return json(route, [project]);
    if (path === "/letters/bench/availability") return json(route, { enabled: state.enabled, rollout_mode: state.enabled ? "primary" : "off" });
    if (path === "/letters/bench/open" && method === "POST") {
      state.openPayloads.push(body());
      return json(route, { letter_id: LETTER_ID, created: false, board_version: state.board.board_version });
    }
    if (path === `/letters/${LETTER_ID}/bench/skills`) {
      return json(route, [
        { skill_key: VARIATIONS, name: "Variations", description: "Variation evaluation", kind: "evaluation", use_when: "Deciding whether something is a variation and how it should be valued.", version: "1.0.0", roles_supported: ["contractor", "engineer", "employer"], contract_forms: [], chains_to: [], required_inputs: [], enabled: true, pinned: false, available: true },
        { skill_key: "indian-infra-eot-claims", name: "Extension of time review", description: "", kind: "evaluation", roles_supported: [], contract_forms: [], chains_to: [], required_inputs: [], enabled: true, pinned: false, available: false, unavailable_reason: "Not installed" },
      ]);
    }
    if (path === benchPath && method === "GET") {
      return state.exists ? json(route, response(state)) : json(route, { detail: "This letter has no Bench yet" }, 404);
    }
    if (path === `${benchPath}/steps/step-var`) {
      return json(route, {
        ...state.steps[1],
        letter_id: LETTER_ID,
        role: "employer",
        output: {
          description: "Additional pile caps",
          classification: "insufficient_information",
          contractual_basis: [],
          valuation_limb: "not_determined",
          valuation_justification: "The valuation hierarchy cannot be applied until the contract rates are on record.",
          time_impact: "Not assessed.",
          residual_risks: ["Scope ambiguity."],
          missing_information: ["BOQ rates for item 4.12"],
        },
      });
    }
    const assertionMatch = path.match(new RegExp(`^${benchPath}/assertions/(A\\d+)$`));
    if (assertionMatch && method === "PATCH") {
      const payload = body();
      state.patchVersions.push(payload.board_version);
      if (state.forceConflictOnce) {
        state.forceConflictOnce = false;
        state.board.board_version += 1; // someone else changed the board first
        return conflict();
      }
      if (!versionOk(payload)) return conflict();
      const assertion = state.board.assertions.find((a) => a.assertion_id === assertionMatch[1])!;
      if (payload.verdict) {
        assertion.verdict = payload.verdict;
        assertion.verdict_source = payload.verdict === assertion.proposed_verdict ? "ai" : "user";
      }
      if (payload.user_direction !== undefined) assertion.user_direction = payload.user_direction || null;
      assertion.state = assertion.verdict ? "decided" : "open";
      // Only this assertion's paragraph goes stale; the lock no longer describes the board.
      for (const paragraph of state.board.reply_paragraphs) {
        if (paragraph.assertion_id === assertion.assertion_id) paragraph.stale = true;
      }
      for (const other of state.board.assertions) {
        if (other.state === "locked") other.state = "decided";
      }
      if (state.board.stage !== "positions") state.board.stage = "positions";
      state.board.board_version += 1;
      return json(route, response(state));
    }
    if (path === `${benchPath}/lock-positions` && method === "POST") {
      if (!versionOk(body())) return conflict();
      if (gate(state).blockers.length) return json(route, { detail: gate(state).blockers.join("; ") }, 409);
      state.board.assertions.forEach((a) => (a.state = "locked"));
      state.board.stage = "locked";
      state.board.locked_strategy_version = (state.board.locked_strategy_version ?? 0) + 1;
      state.board.board_version += 1;
      return json(route, response(state));
    }
    if (path === `${benchPath}/compose` && method === "POST") {
      if (!versionOk(body())) return conflict();
      const existing = new Map(state.board.reply_paragraphs.map((p) => [p.paragraph_id, p]));
      const regenerated: string[] = [];
      const points = state.board.assertions.map((a) => {
        const id = `P-${a.assertion_id}`;
        const previous = existing.get(id);
        if (previous && !previous.stale) return previous;
        regenerated.push(a.assertion_id);
        return { paragraph_id: id, assertion_id: a.assertion_id, part: "point", text: `Reply to ${a.assertion_id}: position is ${a.verdict}.`, step_id: `step-${id}`, stale: false, edited_by_user: false, degraded: false, clause_refs: [], source_ids: [] };
      });
      state.composeCalls.push(regenerated);
      const frame = (part: string, text: string) => ({ paragraph_id: `P-${part}`, assertion_id: null, part, text, step_id: null, stale: false, edited_by_user: false, degraded: false, clause_refs: [], source_ids: [] });
      state.board.reply_paragraphs = [
        frame("opening", "We refer to your letter CTR/2026/118 dated 2026-11-02."),
        ...points,
        frame("reservations", "The Employer expressly reserves all of its rights and remedies under the Contract."),
      ];
      state.board.internal_note = "A1: time-bar taken on the user's instruction.";
      state.board.draft_run_id = `run-${state.composeCalls.length}`;
      state.board.stage = "composed";
      state.board.chain[3].status = "done";
      state.board.board_version += 1;
      return json(route, { letter_id: LETTER_ID, operation: "compose", poll_url: benchPath }, 202);
    }
    if (path === `/letters/${LETTER_ID}/submit` && method === "POST") {
      state.letterStatus = "Review";
      return json(route, {});
    }
    return json(route, {});
  });
}

const SHOTS = process.env.BENCH_SHOTS;

test.describe("Letter Bench", () => {
  test("claim journey step 6: a request for information opens pre-filled from the determination (4B-31)", async ({ page }) => {
    const state = makeState();
    state.board.role = "engineer";
    (state.board as Json).trigger = {
      type: "information_request",
      id: "det-1",
      label: "Information requested on claim CLM-001 (revision 1)",
      status: "approved",
      information_items: [
        { head_id: "H1", head_title: "Delayed handover", item: "Handover correspondence", deadline: "2026-12-05", consequence: null },
        { head_id: "H1", head_title: "Delayed handover", item: "Daily site records", deadline: "2026-12-20", consequence: "Determined on the records supplied" },
      ],
    };
    await mockApi(page, state);
    await page.goto("/letters/bench/new?determinationId=det-1&purpose=information_request");

    await expect(page).toHaveURL(new RegExp(`/letters/${LETTER_ID}/bench$`));
    // Opening is idempotent on the server; the dev build may send it twice.
    expect(state.openPayloads.length).toBeGreaterThan(0);
    for (const payload of state.openPayloads) {
      expect(payload).toEqual({ trigger_type: "information_request", trigger_id: "det-1" });
    }
    const context = page.getByRole("region", { name: "From the determination" });
    await expect(context).toContainText("Information requested");
    await expect(context).toContainText("Handover correspondence");
    await expect(context).toContainText("05-12-2026");
    await expect(context).toContainText("Daily site records");
    await expect(context).toContainText("20-12-2026");
    await expect(context).toContainText("Determined on the records supplied");
  });

  test("reply flow: decide each point, lock, compose, submit", async ({ page }) => {
    const state = makeState();
    await mockApi(page, state);
    await page.goto(`/letters/${LETTER_ID}/bench`);

    // Value before effort: the dissected letter and its deadline, no form.
    await expect(page.getByRole("heading", { name: "Reply to CTR/2026/118" })).toBeVisible();
    await expect(page.getByTestId("deadline-strip")).toContainText("Reply due in 9 days");
    await expect(page.getByTestId("letter-canvas").locator("mark")).toHaveCount(3);
    await expect(page.getByTestId("positions-count")).toHaveText("0 of 3 set");
    await expect(page.getByRole("button", { name: "Lock positions" })).toBeDisabled();
    await expect(page.locator("form")).toHaveCount(0);

    // Skills are visible: name, version, target, status; a missing one says so.
    const rail = page.getByRole("navigation", { name: "Skill chain" });
    await expect(rail.getByTestId(`chain-link-${VARIATIONS}`)).toContainText("Variations");
    await expect(rail.getByTestId(`chain-link-${VARIATIONS}`)).toContainText("A3");
    await expect(rail.getByTestId("chain-link-indian-infra-eot-claims")).toContainText("Unavailable");

    // The clause check reads in plain language.
    await expect(page.getByTestId("assertion-card-A2")).toContainText("not found in this contract");

    await page.getByRole("radiogroup", { name: "Position on A1" }).getByRole("radio", { name: /^None/ }).click();
    await page.getByRole("radiogroup", { name: "Position on A2" }).getByRole("radio", { name: /^None/ }).click();
    await page.getByRole("radiogroup", { name: "Position on A3" }).getByRole("radio", { name: /^Partial/ }).click();
    await expect(page.getByTestId("positions-count")).toHaveText("3 of 3 set");
    await expect(page.getByTestId("assertion-card-A2")).toContainText("You overrode the proposal");
    // Selecting a point scrolls its pane, never the window: the gate bar stays in view.
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    await expect(page.getByRole("button", { name: "Lock positions" })).toBeInViewport({ ratio: 1 });
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/desktop-positions.png` });

    await page.getByRole("button", { name: "Lock positions" }).click();
    await page.getByRole("button", { name: "Compose reply" }).click();
    await expect(page.getByTestId("reply-paragraph-P-A1")).toBeVisible();
    await expect(page.getByLabel("Reply paragraphs").getByRole("listitem")).toHaveCount(5);
    await expect(page.getByLabel("Internal note")).toContainText("not part of the letter");
    expect(state.composeCalls).toEqual([["A1", "A2", "A3"]]);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/desktop-composed.png` });

    await page.getByRole("button", { name: "Submit for review" }).click();
    await expect(page).toHaveURL(new RegExp(`/letters/${LETTER_ID}/review$`));
    // The reviewer is pointed at the positions the draft was built from.
    await page.getByRole("button", { name: "Open positions (read-only)" }).click();
    await expect(page).toHaveURL(new RegExp(`/letters/${LETTER_ID}/bench$`));
    await expect(page.getByRole("button", { name: "Lock positions" })).toHaveCount(0);
  });

  test("change of mind: one flipped verdict stales and rewrites one paragraph", async ({ page }) => {
    const state = makeState();
    await mockApi(page, state);
    await page.goto(`/letters/${LETTER_ID}/bench`);
    for (const id of ["A1", "A2", "A3"]) {
      await page.getByRole("radiogroup", { name: `Position on ${id}` }).getByRole("radio", { name: /^None/ }).click();
    }
    await page.getByRole("button", { name: "Lock positions" }).click();
    await page.getByRole("button", { name: "Compose reply" }).click();
    await expect(page.getByTestId("reply-paragraph-P-A2")).toContainText("position is none");

    await page.getByRole("radiogroup", { name: "Position on A2" }).getByRole("radio", { name: /^Entitlement/ }).click();
    await expect(page.getByTestId("reply-paragraph-P-A2")).toContainText("Out of date");
    await expect(page.getByTestId("reply-paragraph-P-A1")).not.toContainText("Out of date");
    await expect(page.getByTestId("reply-paragraph-P-A3")).not.toContainText("Out of date");

    await page.getByRole("button", { name: "Lock positions" }).click();
    await page.getByRole("button", { name: "Save reply as draft" }).click();
    await expect(page.getByTestId("reply-paragraph-P-A2")).toContainText("position is entitlement");
    expect(state.composeCalls).toEqual([["A1", "A2", "A3"], ["A2"]]);
  });

  test("a version conflict is refetched and the user's action replayed", async ({ page }) => {
    const state = makeState();
    await mockApi(page, state);
    await page.goto(`/letters/${LETTER_ID}/bench`);
    await expect(page.getByTestId("positions-count")).toHaveText("0 of 3 set");
    state.forceConflictOnce = true;
    await page.getByRole("radiogroup", { name: "Position on A1" }).getByRole("radio", { name: /^Partial/ }).click();
    // The optimistic update shows at once; the replay against the fresh version follows.
    await expect(page.getByTestId("positions-count")).toHaveText("1 of 3 set");
    await expect.poll(() => state.patchVersions).toEqual([3, 4]);
    expect(state.board.assertions[0].verdict).toBe("partial");
    await expect(page.getByTestId("positions-count")).toHaveText("1 of 3 set");
  });

  test("chain a skill from the keyboard and read what it produced", async ({ page }) => {
    const state = makeState();
    await mockApi(page, state);
    await page.goto(`/letters/${LETTER_ID}/bench`);
    await page.getByRole("button", { name: /^Assertion A1/ }).click();
    await page.keyboard.press("/");
    const palette = page.getByRole("dialog");
    await expect(palette.getByPlaceholder(/Apply a skill to A1/)).toBeFocused();
    await expect(palette.getByText("Unavailable: Not installed")).toBeVisible();
    await expect(palette.getByText("Deciding whether something is a variation")).toBeVisible();
    await page.keyboard.press("Escape");

    await page.getByTestId("assertion-card-A3").getByRole("button", { name: "View evaluation" }).click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toContainText("Version 1.0.0");
    await expect(drawer).toContainText("3. Valuation and hierarchy");
    await expect(drawer).toContainText("BOQ rates for item 4.12");
  });

  test("verdicts are a keyboard-operable radio group", async ({ page }) => {
    const state = makeState();
    await mockApi(page, state);
    await page.goto(`/letters/${LETTER_ID}/bench`);
    const group = page.getByRole("radiogroup", { name: "Position on A1" });
    await group.getByRole("radio", { name: /^None/ }).focus();
    await page.keyboard.press("ArrowLeft");
    await expect(group.getByRole("radio", { name: /^Partial/ })).toBeFocused();
    // Moving focus is not a decision; Space is.
    await expect(group.getByRole("radio", { name: /^Partial/ })).toHaveAttribute("aria-checked", "false");
    expect(state.patchVersions).toEqual([]);
    await page.keyboard.press("Space");
    await expect(group.getByRole("radio", { name: /^Partial/ })).toHaveAttribute("aria-checked", "true");
    // The chosen verdict is a solid chip with white text, not a tint.
    await expect(group.getByRole("radio", { name: /^Partial/ })).toHaveCSS("color", "rgb(255, 255, 255)");
    await expect(group.getByRole("radio", { name: /^Partial/ })).toHaveCSS("background-color", "rgb(180, 83, 9)");
    expect(state.board.assertions[0].verdict).toBe("partial");
  });

  test("below tablet width the zones become tabs and the gate stays in view", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 780 });
    const state = makeState();
    await mockApi(page, state);
    await page.goto(`/letters/${LETTER_ID}/bench`);
    const bench = page.getByTestId("letter-bench");
    // The layout follows the Bench's own width, so it is already in tab mode here.
    await expect(bench).toHaveAttribute("data-layout", "tabs");
    const tabs = page.getByRole("tablist", { name: "Bench views" });
    await expect(tabs.getByRole("tab", { name: /Positions/ })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("assertion-card-A1")).toBeVisible();
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/mobile-positions.png` });
    await expect(page.getByTestId("letter-canvas")).toBeHidden();
    await tabs.getByRole("tab", { name: "Letter" }).click();
    await expect(page.getByTestId("letter-canvas")).toBeVisible();
    await expect(page.getByTestId("assertion-card-A1")).toBeHidden();
    // On a phone the Bench takes the whole screen, so the gate bar never scrolls away
    // and nothing is cut off sideways.
    const box = await bench.boundingBox();
    expect(box).toEqual({ x: 0, y: 0, width: 390, height: 780 });
    await expect(page.getByRole("button", { name: "Lock positions" })).toBeInViewport({ ratio: 1 });
    expect(await bench.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/mobile-letter.png` });
  });

  test("reviewer sees the Bench read-only", async ({ page }) => {
    const state = makeState();
    state.letterStatus = "Review";
    await mockApi(page, state);
    await page.goto(`/letters/${LETTER_ID}/bench`);
    await expect(page.getByRole("radiogroup", { name: "Position on A1" }).getByRole("radio", { name: /^None/ })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Lock positions" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Add a skill to the chain" })).toHaveCount(0);
  });

  test("a letter with a Bench is sent there from the earlier screens", async ({ page }) => {
    const state = makeState();
    await mockApi(page, state);
    await page.goto(`/letters/${LETTER_ID}/strategy`);
    await expect(page).toHaveURL(new RegExp(`/letters/${LETTER_ID}/bench$`));
    await page.goto(`/letters/bench/new?documentId=doc-1`);
    await expect(page).toHaveURL(new RegExp(`/letters/${LETTER_ID}/bench$`));
  });

  test("flag off, or no Bench for the letter: the earlier screens are untouched", async ({ page }) => {
    const off = makeState(false);
    await mockApi(page, off);
    await page.goto(`/letters/${LETTER_ID}/draft`);
    await expect(page).toHaveURL(new RegExp(`/letters/${LETTER_ID}/draft$`));
    await expect(page.getByTestId("letter-bench")).toHaveCount(0);

    await page.unrouteAll();
    const legacy = makeState(true);
    legacy.exists = false;
    await mockApi(page, legacy);
    await page.goto(`/letters/${LETTER_ID}/strategy`);
    await expect(page).toHaveURL(new RegExp(`/letters/${LETTER_ID}/strategy$`));
    await expect(page.getByTestId("letter-bench")).toHaveCount(0);
  });
});
