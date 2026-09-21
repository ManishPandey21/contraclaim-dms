import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import {
  ensureDisposableDocument,
  newFixtureContext,
  openProtectedPage,
  RUN_TAG,
  signIn,
  signInThroughBrowser,
  type FixtureDocument,
  type FixtureSession,
} from "./fixtures";
import {
  archiveRunOwned,
  createEntry,
  ensureEotSubmission,
  ensureKeyDate,
  ensureProgrammeMilestone,
  expectNoFailures,
  guardedSession,
  hindranceEnvironment,
  runTitle,
  writeEvidence,
  type RegisterEntry,
  type Target,
} from "./hindrance-fixtures";
import { stagingTeardownHasWork } from "./staging-target";

/**
 * Hindrance & Constraint Register — DEPLOYED staging certification (browser).
 *
 * Unlike `e2e/hindrance-register.spec.ts` nothing here is mocked: every request
 * reaches the staging backend. Run with 1 worker and 0 retries (the config
 * enforces both whenever `E2E_BASE_URL` is set).
 *
 * Accounts (seeded for the run, see docs/HINDRANCE_STAGING_CERTIFICATION_PLAN.md):
 *   E2E_HIN_ORGADMIN_*  orgadmin of the fixture organisation - seeds targets
 *   E2E_HIN_PA_AB_*     projectadmin assigned to run-owned projects A and B
 *   E2E_HIN_PA_B_*      projectadmin assigned to project B only
 *
 * `E2E_STAGING_PROJECT_ID` must equal `E2E_HIN_PROJECT_A_ID`: the shared
 * document fixture uploads into it.
 *
 * NOT YET EXECUTED AGAINST A DEPLOYMENT.
 */

test.describe.configure({ mode: "serial" });

const state: {
  env: Record<string, string>;
  seederContext?: APIRequestContext;
  seeder?: FixtureSession;
  document?: FixtureDocument;
  milestone?: Target;
  keyDate?: Target;
  eot?: Target;
  workflowEntryId?: string;
  projectAEntry?: RegisterEntry;
} = { env: {} };

async function selectProject(page: Page, projectName: string): Promise<void> {
  await page.getByLabel("Select project").selectOption({ label: projectName });
  await expect(page.getByLabel("Select project")).toHaveValue(/.+/);
}

async function openRegister(page: Page): Promise<void> {
  await openProtectedPage(page, "/hindrances", page.getByRole("heading", { name: "Hindrance & Constraint Register" }));
}

test.beforeAll(async ({ playwright }) => {
  state.env = hindranceEnvironment();
  if (!state.env.E2E_HIN_PROJECT_A_ID) return; // skipped (non-strict, no target)
  expect(process.env.E2E_STAGING_PROJECT_ID, "E2E_STAGING_PROJECT_ID must be project A").toBe(state.env.E2E_HIN_PROJECT_A_ID);
  state.seederContext = await newFixtureContext(playwright);
  state.seeder = guardedSession(await signIn(state.seederContext, "E2E_HIN_ORGADMIN_EMAIL", "E2E_HIN_ORGADMIN_PASSWORD"));
  const projectA = state.env.E2E_HIN_PROJECT_A_ID;
  state.document = await ensureDisposableDocument(state.seeder, "hin-site-diary");
  state.milestone = await ensureProgrammeMilestone(state.seeder, projectA, "ACT");
  state.keyDate = await ensureKeyDate(state.seeder, projectA, "KD");
  state.eot = await ensureEotSubmission(state.seeder, projectA, "EOT");
});

test.afterAll(async () => {
  if (!stagingTeardownHasWork() || !state.seeder) return;
  const report = await archiveRunOwned(state.seeder, [state.env.E2E_HIN_PROJECT_A_ID, state.env.E2E_HIN_PROJECT_B_ID]);
  writeEvidence("hindrance-browser-residue", {
    ...report,
    fixtures: {
      document_id: state.document?.id,
      programme_milestone_id: state.milestone?.id,
      key_date_id: state.keyDate?.id,
      eot_submission_id: state.eot?.id,
    },
  });
  await state.seederContext?.dispose();
  expectNoFailures(report);
});

test("an allowed role records, links, reloads, archives and restores an entry", async ({ page }) => {
  const title = runTitle("workflow");
  await signInThroughBrowser(page, "E2E_HIN_PA_AB_EMAIL", "E2E_HIN_PA_AB_PASSWORD");
  await openRegister(page);
  await selectProject(page, state.env.E2E_HIN_PROJECT_A_NAME);

  // 3-4. create; the reference is server-generated.
  await page.getByRole("button", { name: /Record entry/ }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Title *").fill(title);
  await dialog.getByLabel("Start / occurrence date *").fill("2026-02-10");
  await dialog.getByLabel("Category").selectOption("site_access");
  const [created] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith("/api/hindrances") && r.request().method() === "POST"),
    dialog.getByRole("button", { name: "Record entry" }).click(),
  ]);
  expect(created.status()).toBe(201);
  const body = await created.json();
  const ref = String(body.hindrance_ref);
  state.workflowEntryId = String(body.id ?? body._id);
  expect(ref).toMatch(/^HIN-\d{4,}$/);
  const row = page.getByRole("row", { name: new RegExp(title) });
  await expect(row).toContainText(ref);

  // 5-6. detail, edit.
  await page.getByRole("link", { name: ref }).click();
  await expect(page.getByRole("heading", { name: new RegExp(title) })).toBeVisible();
  await page.getByRole("button", { name: /Edit/ }).click();
  await page.getByRole("dialog").getByLabel("Location").fill(`${RUN_TAG} north gate`);
  await page.getByRole("dialog").getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText(`${RUN_TAG} north gate`)).toBeVisible();

  // 7. existing document, through the canonical entity_document_links panel.
  await page.getByLabel("Search Documents").fill(state.document!.letterNo);
  await page.getByRole("button", { name: new RegExp(`^Link .*${state.document!.letterNo}`) }).first().click();
  await expect(page.getByRole("link", { name: new RegExp(state.document!.letterNo) }).first()).toBeVisible();

  // 8-10. programme milestone, key date, EOT submission.
  for (const [button, target] of [
    ["Link programme activity", state.milestone!],
    ["Link key date", state.keyDate!],
    ["Link EOT submission", state.eot!],
  ] as const) {
    await page.getByRole("button", { name: button }).click();
    await page.getByRole("button", { name: `Link ${target.label}` }).click();
    await expect(page.getByText(target.label, { exact: true }).first()).toBeVisible();
  }

  // 11-12. reload proves persistence.
  await page.reload();
  await expect(page.getByText(`${RUN_TAG} north gate`)).toBeVisible();
  for (const target of [state.milestone!, state.keyDate!, state.eot!]) {
    await expect(page.getByText(target.label, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByRole("link", { name: new RegExp(state.document!.letterNo) }).first()).toBeVisible();

  // 13. timeline: the detail reports "shown", and the Contract Timeline lists the entry.
  await expect(page.getByText("shown", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Open timeline" }).click();
  await expect(page.getByText(title).first()).toBeVisible();
  await page.goBack();

  // 14-16. archive (reason required), hidden by default, shown with include archived.
  await page.getByRole("button", { name: /Archive/ }).click();
  const confirm = page.getByRole("alertdialog");
  await expect(confirm.getByRole("button", { name: "Archive" })).toBeDisabled();
  await confirm.getByLabel("Reason *").fill(`certification ${RUN_TAG}`);
  await confirm.getByRole("button", { name: "Archive" }).click();
  await expect(page.getByText(/Archived entries are read-only/)).toBeVisible();
  await expect(page.getByRole("button", { name: /Edit/ })).toHaveCount(0);
  await openRegister(page);
  await expect(page.getByRole("row", { name: new RegExp(title) })).toHaveCount(0);
  await page.getByLabel("Include archived").check();
  await expect(page.getByRole("row", { name: new RegExp(title) })).toBeVisible();

  // 17-18. restore, active again.
  await page.getByRole("link", { name: ref }).click();
  await page.getByRole("button", { name: /Restore/ }).click();
  await page.getByRole("alertdialog").getByLabel("Reason *").fill(`certification ${RUN_TAG}`);
  await page.getByRole("alertdialog").getByRole("button", { name: "Restore" }).click();
  await expect(page.getByRole("button", { name: /Edit/ })).toBeVisible();
  await openRegister(page);
  await page.getByLabel("Include archived").uncheck();
  await expect(page.getByRole("row", { name: new RegExp(title) })).toBeVisible();
});

test("a project admin of another project is refused the entry", async ({ page, playwright }) => {
  expect(state.workflowEntryId, "the workflow test did not create an entry").toBeTruthy();
  const id = state.workflowEntryId!;
  await signInThroughBrowser(page, "E2E_HIN_PA_B_EMAIL", "E2E_HIN_PA_B_PASSWORD");
  await openRegister(page);
  await expect(page.getByText(runTitle("workflow"))).toHaveCount(0);
  await page.goto(`/hindrances/${id}`);
  await expect(page.getByText("You do not have access to this register entry.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Edit/ })).toHaveCount(0);

  // The API refuses too, with 403 - not a masked 401, not a 200.
  const request = await newFixtureContext(playwright);
  const foreign = guardedSession(await signIn(request, "E2E_HIN_PA_B_EMAIL", "E2E_HIN_PA_B_PASSWORD"));
  expect((await foreign.request.get(`/api/hindrances/${id}`)).status()).toBe(403);
  const patch = await foreign.request.patch(`/api/hindrances/${id}`, { headers: foreign.headers(), data: { title: "x" } });
  expect(patch.status()).toBe(403);
  await request.dispose();
});

test("switching project in the navbar re-scopes the register and targets new records", async ({ page, playwright }) => {
  const projectA = state.env.E2E_HIN_PROJECT_A_ID;
  const request = await newFixtureContext(playwright);
  const admin = guardedSession(await signIn(request, "E2E_HIN_PA_AB_EMAIL", "E2E_HIN_PA_AB_PASSWORD"));
  state.projectAEntry = await createEntry(admin, projectA, "navbar-A");
  await request.dispose();

  await signInThroughBrowser(page, "E2E_HIN_PA_AB_EMAIL", "E2E_HIN_PA_AB_PASSWORD");
  await openRegister(page);
  await selectProject(page, state.env.E2E_HIN_PROJECT_A_NAME);
  await expect(page.getByRole("row", { name: new RegExp(runTitle("navbar-A")) })).toBeVisible();

  await selectProject(page, state.env.E2E_HIN_PROJECT_B_NAME);
  await expect(page.getByRole("row", { name: new RegExp(runTitle("navbar-A")) })).toHaveCount(0);

  // Create lands in the newly selected project.
  await page.getByRole("button", { name: /Record entry/ }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Title *").fill(runTitle("navbar-B"));
  await dialog.getByLabel("Start / occurrence date *").fill("2026-02-11");
  const [created] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith("/api/hindrances") && r.request().method() === "POST"),
    dialog.getByRole("button", { name: "Record entry" }).click(),
  ]);
  expect(created.status()).toBe(201);
  expect((await created.json()).project_id).toBe(state.env.E2E_HIN_PROJECT_B_ID);
  await expect(page.getByRole("row", { name: new RegExp(runTitle("navbar-B")) })).toBeVisible();
  await expect(page.getByRole("row", { name: new RegExp(runTitle("navbar-A")) })).toHaveCount(0);

  // House policy: selection scopes collections, membership gates a direct fetch.
  // This user is a member of A, so the old detail URL opens the A record under
  // its own project; a non-member is refused (previous test). Recorded, not assumed.
  await page.goto(`/hindrances/${state.projectAEntry.id}`);
  await expect(page.getByRole("heading", { name: new RegExp(runTitle("navbar-A")) })).toBeVisible();
  writeEvidence("hindrance-navbar-direct-url", {
    selected_project: state.env.E2E_HIN_PROJECT_B_ID,
    fetched_record_project: state.projectAEntry.project_id,
    outcome: "opened (member of the record's project)",
  });
});

/**
 * Timeline fault, UI half (plan section 8). Runs only in the runbook's disarmed
 * phase, after the API spec's armed phase left a failed TLFAIL entry: the detail
 * page must show the failure and the "Retry timeline sync" button, and the
 * button must repair it.
 */
test("a failed timeline projection shows a retry button that repairs it", async ({ page, playwright }) => {
  test.skip((process.env.E2E_HIN_TIMELINE_FAULT ?? "").trim() !== "disarmed", "runs only in the runbook's disarmed phase");
  const request = await newFixtureContext(playwright);
  const admin = guardedSession(await signIn(request, "E2E_HIN_PA_AB_EMAIL", "E2E_HIN_PA_AB_PASSWORD"));
  const listed = await admin.request.get("/api/hindrances", { params: { project_id: state.env.E2E_HIN_PROJECT_A_ID, q: runTitle("TLFAIL") } });
  const [entry] = ((await listed.json()).items as any[]).filter((row) => row.title === runTitle("TLFAIL"));
  await request.dispose();
  expect(entry, "the armed phase left no TLFAIL entry").toBeTruthy();
  expect(entry.timeline_sync_status).toBe("failed");

  await signInThroughBrowser(page, "E2E_HIN_PA_AB_EMAIL", "E2E_HIN_PA_AB_PASSWORD");
  await openProtectedPage(page, `/hindrances/${entry.id}`, page.getByRole("heading", { name: new RegExp(runTitle("TLFAIL")) }));
  await expect(page.getByText("not synced", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Retry timeline sync/ }).click();
  await expect(page.getByText("shown", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Retry timeline sync/ })).toHaveCount(0);
});
