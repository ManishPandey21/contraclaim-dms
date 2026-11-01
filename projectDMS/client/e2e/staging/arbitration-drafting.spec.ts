/**
 * Gate 3 bullet 7: arbitration draft create, generate, edit, save version,
 * approve/return, export DOCX/PDF.
 *
 * SETUP     an author session inside `E2E_STAGING_ORG_ID`, an approver session
 *           for the separation-of-duties half, and one disposable draft whose
 *           title carries the run tag.
 * ACTION    create -> generate -> edit -> save a manual version -> return for
 *           revision -> approve -> export DOCX and PDF.
 * EXPECTED  each step changes server state that the next step can read back.
 *           Generation produces sections; the manual version is retrievable by
 *           its number; the return and the approval move `status`; both exports
 *           return real bytes with the right magic number.
 * SECURITY  author-approver separation is asserted, not assumed: the author's
 *           own approval must be refused with 409 before the approver's is
 *           accepted. Approving your own pleading is the control this workflow
 *           exists to enforce.
 * CLEANUP   the draft is enumerated by run tag and deleted by id.
 *
 * **Why this bullet is measurable at all, and why it costs nothing.**
 * R-A8S opened with a proposal to withdraw it, on the basis that the arbitration
 * engine is deliberately not primary. Re-derived from the source, that is true
 * of the LangGraph *workflow* engine and false of the *drafting* surface this
 * bullet names: `ArbitrationDraftingService.generate` picks its generator from
 * `ARBITRATION_DRAFT_MODE` (default `deterministic`) and never consults
 * `ArbitrationEnginePolicy`, and under the shipped configuration that policy
 * returns `arbitration_v2` for every request anyway. So the whole chain runs on
 * the primary production path, with no model call and no rollout flag touched.
 * `backend/.../test_release_gate_specification.py` re-derives that on every run.
 *
 * NOT YET EXECUTED AGAINST A DEPLOYMENT. R-A8S is an offline phase; the bullet
 * stays unchecked until a staging run produces the result.
 */
import { expect, test } from "@playwright/test";

import {
  RUN_TAG,
  cleanupRunOwned,
  ensureDisposableArbitrationDraft,
  newFixtureContext,
  signIn,
} from "./fixtures";
import { requireStagingEnvironment, stagingTeardownHasWork } from "./staging-target";

const AUTHOR = ["E2E_STAGING_EMAIL", "E2E_STAGING_PASSWORD"] as const;
const APPROVER = ["E2E_APPROVER_EMAIL", "E2E_APPROVER_PASSWORD"] as const;
const SCOPE = ["E2E_STAGING_ORG_ID", "E2E_STAGING_PROJECT_ID"] as const;

/** `PK\x03\x04` and `%PDF`. A 200 with an HTML error page is not an export. */
const DOCX_MAGIC = Buffer.from([0x50, 0x4b, 0x03, 0x04]);
const PDF_MAGIC = Buffer.from("%PDF", "utf-8");

test.describe("Gate 3 bullet 7 - arbitration drafting and approval chain", () => {
  test.afterAll(async ({ playwright }) => {
    // Nothing ran, so there is nothing run-owned to remove. Without this the
    // hook builds a fixture context with no target and throws, turning a
    // clean skip into a failed `npm run test:e2e`.
    if (!stagingTeardownHasWork()) {
      return;
    }
    const context = await newFixtureContext(playwright);
    try {
      const session = await signIn(context, AUTHOR[0], AUTHOR[1]);
      const report = await cleanupRunOwned(session);
      expect(
        report.failed,
        `teardown left arbitration state behind: ${JSON.stringify(report.failed)}`
      ).toEqual([]);
    } finally {
      await context.dispose();
    }
  });

  test("create, generate, edit and save a version", async ({ request }) => {
    requireStagingEnvironment(...AUTHOR, ...SCOPE);
    const author = await signIn(request, AUTHOR[0], AUTHOR[1]);

    const draft = await ensureDisposableArbitrationDraft(author, "b7");
    expect(draft.title).toContain(RUN_TAG);

    // --- generate ---------------------------------------------------------- #
    const generated = await author.request.post(
      `/api/arbitration/drafts/${draft.id}/generate`,
      { headers: author.headers(), data: {} }
    );
    expect(
      generated.status(),
      `generation returned ${generated.status()} for a deterministic-mode draft`
    ).toBe(200);
    const detail = await generated.json();
    const sections: any[] = detail.sections ?? detail.draft?.sections ?? [];
    expect(
      sections.length,
      "generation returned no sections; the chain answered 200 without completing"
    ).toBeGreaterThan(0);

    // --- edit --------------------------------------------------------------- #
    const editedRelief = `Extension of time of 35 days. Edited by ${RUN_TAG}.`;
    const edited = await author.request.patch(`/api/arbitration/drafts/${draft.id}`, {
      headers: author.headers(),
      data: { relief_sought: editedRelief },
    });
    expect(edited.status(), "the draft could not be edited").toBe(200);

    const reread = await author.request.get(`/api/arbitration/drafts/${draft.id}`);
    expect(reread.status()).toBe(200);
    expect(
      (await reread.json()).relief_sought,
      "the edit did not survive a re-read, so nothing was persisted"
    ).toBe(editedRelief);

    // --- save version ------------------------------------------------------- #
    const markdown = `# ${draft.title}\n\nManual version saved by ${RUN_TAG}.\n`;
    const saved = await author.request.post(
      `/api/arbitration/drafts/${draft.id}/versions`,
      { headers: author.headers(), data: { full_markdown: markdown } }
    );
    expect(saved.status(), "a manual version could not be saved").toBe(201);
    const version = (await saved.json()).version;
    expect(version, "the saved version has no version number").toBeTruthy();

    const fetched = await author.request.get(
      `/api/arbitration/drafts/${draft.id}/versions/${version}`
    );
    expect(fetched.status(), "the saved version could not be read back").toBe(200);
    expect((await fetched.json()).full_markdown).toContain(RUN_TAG);
  });

  test("the author cannot approve their own draft", async ({ request }) => {
    requireStagingEnvironment(...AUTHOR, ...SCOPE);
    const author = await signIn(request, AUTHOR[0], AUTHOR[1]);
    const draft = await ensureDisposableArbitrationDraft(author, "b7");

    const selfApproval = await author.request.post(
      `/api/arbitration/drafts/${draft.id}/approve`,
      { headers: author.headers() }
    );
    expect(
      selfApproval.status(),
      "the author's own approval was accepted; author-approver separation is the " +
        "control this workflow exists to enforce"
    ).toBe(409);
  });

  test("return for revision, then approve, then export DOCX and PDF", async ({
    request,
    playwright,
  }) => {
    requireStagingEnvironment(...AUTHOR, ...APPROVER, ...SCOPE);
    const author = await signIn(request, AUTHOR[0], AUTHOR[1]);
    const draft = await ensureDisposableArbitrationDraft(author, "b7");

    const approverContext = await newFixtureContext(playwright);
    try {
      const approver = await signIn(approverContext, APPROVER[0], APPROVER[1]);

      // --- return for revision --------------------------------------------- #
      const returned = await approver.request.post(
        `/api/arbitration/drafts/${draft.id}/return-for-revision`,
        {
          headers: approver.headers(),
          data: { reason: `Returned by ${RUN_TAG} for the Gate 3 measurement.` },
        }
      );
      expect(returned.status(), "the draft could not be returned for revision").toBe(200);
      expect(
        (await returned.json()).status,
        "returning for revision did not move the draft out of review"
      ).toBe("draft");

      // --- approve ---------------------------------------------------------- #
      const approved = await approver.request.post(
        `/api/arbitration/drafts/${draft.id}/approve`,
        { headers: approver.headers() }
      );
      expect(approved.status(), "the approver could not approve the draft").toBe(200);
      expect((await approved.json()).status).toBe("approved");
    } finally {
      await approverContext.dispose();
    }

    // --- export ------------------------------------------------------------- #
    const docx = await author.request.get(
      `/api/arbitration/drafts/${draft.id}/export/docx`
    );
    expect(docx.status(), "the DOCX export failed").toBe(200);
    const docxBytes = await docx.body();
    expect(
      docxBytes.subarray(0, 4).equals(DOCX_MAGIC),
      "the DOCX export returned 200 without a DOCX; a 200 carrying an error page " +
        "is the failure this magic-number check exists for"
    ).toBe(true);

    const pdf = await author.request.get(`/api/arbitration/drafts/${draft.id}/export/pdf`);
    expect(pdf.status(), "the PDF export failed").toBe(200);
    const pdfBytes = await pdf.body();
    expect(
      pdfBytes.subarray(0, 4).equals(PDF_MAGIC),
      "the PDF export returned 200 without a PDF"
    ).toBe(true);
  });

  test("the draft is reachable in the browser at its own route", async ({
    page,
    request,
  }) => {
    const env = requireStagingEnvironment(...AUTHOR, ...SCOPE);
    const author = await signIn(request, AUTHOR[0], AUTHOR[1]);
    const draft = await ensureDisposableArbitrationDraft(author, "b7");

    await page.goto("/login");
    await page.getByLabel("Work email").fill(env.E2E_STAGING_EMAIL);
    await page.getByLabel("Password", { exact: true }).fill(env.E2E_STAGING_PASSWORD);
    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes("/api/login") && response.request().method() === "POST"
      ),
      page.getByRole("button", { name: "Sign in" }).click(),
    ]);

    await page.goto(`/arbitration/drafts/${draft.id}`);
    await expect(page.getByText(draft.title)).toBeVisible();
  });
});
