import { expect, test, type APIRequestContext } from "@playwright/test";

import { ensureDisposableDocument, newFixtureContext, RUN_TAG, signIn, type FixtureSession } from "./fixtures";
import {
  archiveRunOwned,
  createEntry,
  ensureEotSubmission,
  ensureKeyDate,
  ensureDocumentInProject,
  ensureProgrammeMilestone,
  entryBody,
  expectNoFailures,
  guardedSession,
  hindranceEnvironment,
  inProject,
  runTitle,
  writeEvidence,
  type Target,
} from "./hindrance-fixtures";
import { requireStagingEnvironment, stagingTeardownHasWork } from "./staging-target";

/**
 * Hindrance & Constraint Register — DEPLOYED staging certification (real HTTP).
 *
 * Stages 3 (RBAC matrix), 4 (isolation), 6 (document links), 7 (relationship
 * links), 8 (timeline), 9 (archive), 10 (generated references). Every expected
 * status is exact: a refusal is 403, never "not 2xx", because a 401 here would
 * be a masked 403 and a 422 would mean the request never reached authorization.
 *
 * Foreign-organisation fixtures are seeded by the runbook, not here: every write
 * this suite makes is held to `E2E_STAGING_ORG_ID` by `assertWriteIsTenantSafe`.
 *
 * NOT YET EXECUTED AGAINST A DEPLOYMENT.
 */

test.describe.configure({ mode: "serial" });

/** role key -> env prefix. Expected access comes from the release DEFAULT_ROLES (+ owner grant). */
const MATRIX = [
  { role: "superadmin", env: "E2E_HIN_SUPERADMIN", allowed: true },
  { role: "orgadmin", env: "E2E_HIN_ORGADMIN", allowed: true },
  { role: "projectadmin", env: "E2E_HIN_PA_AB", allowed: true },
  { role: "contractmgr_org", env: "E2E_HIN_CONTRACTMGR", allowed: true }, // owner-approved grant
  { role: "orguser", env: "E2E_HIN_ORGUSER", allowed: false },
  { role: "projectuser", env: "E2E_HIN_PROJECTUSER", allowed: false },
  { role: "doccontroller", env: "E2E_HIN_DOCCONTROLLER", allowed: false },
  { role: "reporter", env: "E2E_HIN_REPORTER", allowed: false },
  { role: "limited_user", env: "E2E_HIN_LIMITED", allowed: false },
] as const;

const FOREIGN = [
  "E2E_HIN_FOREIGN_ORGADMIN_EMAIL",
  "E2E_HIN_FOREIGN_ORGADMIN_PASSWORD",
  "E2E_HIN_FOREIGN_ORG_ID",
  "E2E_HIN_FOREIGN_PROJECT_ID",
  "E2E_HIN_FOREIGN_ENTRY_ID",
  "E2E_HIN_FOREIGN_DOCUMENT_ID",
  "E2E_HIN_FOREIGN_MILESTONE_ID",
  "E2E_HIN_FOREIGN_KEY_DATE_ID",
  "E2E_HIN_FOREIGN_EOT_ID",
] as const;

const contexts: APIRequestContext[] = [];
/** Suite-wide state shared by the serial tests. */
const suite: {
  env: Record<string, string>;
  foreign: Record<string, string>;
  /** The org admin with project A selected; `seederB` with project B. */
  seeder?: FixtureSession;
  seederB?: FixtureSession;
  documentId?: string;
  projectBDocumentId?: string;
  milestone?: Target;
  keyDate?: Target;
  eot?: Target;
  matrix: Record<string, Record<string, number>>;
} = { env: {}, foreign: {}, matrix: {} };

async function session(playwright: any, prefix: string): Promise<FixtureSession> {
  const request = await newFixtureContext(playwright);
  contexts.push(request);
  return guardedSession(await signIn(request, `${prefix}_EMAIL`, `${prefix}_PASSWORD`));
}

const reason = { reason: `certification ${RUN_TAG}` };

test.beforeAll(async ({ playwright }) => {
  suite.env = hindranceEnvironment();
  suite.foreign = requireStagingEnvironment(...FOREIGN);
  requireStagingEnvironment(...MATRIX.flatMap((m) => [`${m.env}_EMAIL`, `${m.env}_PASSWORD`]));
  if (!suite.env.E2E_HIN_PROJECT_A_ID) return;
  expect(process.env.E2E_STAGING_PROJECT_ID, "E2E_STAGING_PROJECT_ID must be project A").toBe(suite.env.E2E_HIN_PROJECT_A_ID);
  const orgAdmin = await session(playwright, "E2E_HIN_ORGADMIN");
  const a = suite.env.E2E_HIN_PROJECT_A_ID;
  suite.seeder = inProject(orgAdmin, a);
  suite.seederB = inProject(orgAdmin, suite.env.E2E_HIN_PROJECT_B_ID);
  suite.documentId = (await ensureDisposableDocument(suite.seeder, "hin-api-doc")).id;
  suite.projectBDocumentId = await ensureDocumentInProject(suite.seeder, suite.env.E2E_HIN_PROJECT_B_ID, "hin-api-doc-b");
  suite.milestone = await ensureProgrammeMilestone(suite.seeder, a, "API-ACT");
  suite.keyDate = await ensureKeyDate(suite.seeder, a, "API-KD");
  suite.eot = await ensureEotSubmission(suite.seeder, a, "API-EOT");
});

test.afterAll(async () => {
  if (stagingTeardownHasWork() && suite.seeder) {
    const report = await archiveRunOwned(suite.seeder, [suite.env.E2E_HIN_PROJECT_A_ID, suite.env.E2E_HIN_PROJECT_B_ID]);
    writeEvidence("hindrance-api-residue", report);
    writeEvidence("hindrance-rbac-matrix", suite.matrix);
    expectNoFailures(report);
  }
  for (const request of contexts) await request.dispose();
});

// ---------------------------------------------------------------- Stage 10 --

test("references are server-generated, prefixed, project-scoped, unique and immutable", async () => {
  const a = suite.env.E2E_HIN_PROJECT_A_ID;
  const b = suite.env.E2E_HIN_PROJECT_B_ID;
  const before = await suite.seederB!.request.get("/api/hindrances", { params: { project_id: b, include_archived: "true" } });
  const bWasFresh = (await before.json()).total === 0;

  const hin = await createEntry(suite.seeder!, a, "ref-hin", "hindrance");
  const cns = await createEntry(suite.seeder!, a, "ref-cns", "constraint");
  const dly = await createEntry(suite.seeder!, a, "ref-dly", "delay_event");
  expect(hin.ref).toMatch(/^HIN-\d{4,}$/);
  expect(cns.ref).toMatch(/^CNS-\d{4,}$/);
  expect(dly.ref).toMatch(/^DLY-\d{4,}$/);
  const firstB = await createEntry(suite.seederB!, b, "ref-hin-b", "hindrance");
  if (bWasFresh) expect(firstB.ref, "project B's counter is not independent of A").toBe("HIN-0001");

  // A client cannot supply or change the reference.
  const supplied = await suite.seeder!.request.post("/api/hindrances", {
    headers: suite.seeder!.headers(),
    data: entryBody(a, "ref-supplied", { hindrance_ref: "HIN-9999" }),
  });
  expect(supplied.status()).toBe(422);
  const changed = await suite.seeder!.request.patch(`/api/hindrances/${hin.id}`, { headers: suite.seeder!.headers(), data: { hindrance_ref: "HIN-9999" } });
  expect(changed.status()).toBe(422);
  expect((await (await suite.seeder!.request.get(`/api/hindrances/${hin.id}`)).json()).hindrance_ref).toBe(hin.ref);

  // Concurrent creates never share a reference.
  const burst = await Promise.all(Array.from({ length: 10 }, (_, i) => createEntry(suite.seederB!, b, `ref-burst-${i}`)));
  const refs = burst.map((entry) => entry.ref);
  expect(new Set(refs).size).toBe(refs.length);
  writeEvidence("hindrance-refs", { a: [hin.ref, cns.ref, dly.ref], b_first: firstB.ref, b_was_fresh: bWasFresh, burst: refs });
});

// ----------------------------------------------------------------- Stage 3 --

test("the RBAC matrix grants exactly the roles the release and the owner approved", async ({ playwright }) => {
  const a = suite.env.E2E_HIN_PROJECT_A_ID;
  const existing = await createEntry(suite.seeder!, a, "rbac-target");

  for (const persona of MATRIX) {
    const who = inProject(await session(playwright, persona.env), a);
    const row: Record<string, number> = {};
    const post = (url: string, data: unknown) => who.request.post(url, { headers: who.headers(), data });
    row.list = (await who.request.get("/api/hindrances", { params: { project_id: a } })).status();
    row.view = (await who.request.get(`/api/hindrances/${existing.id}`)).status();
    row.compat_list = (await who.request.get("/api/delay-events", { params: { project_id: a } })).status();
    const created = await post("/api/hindrances", entryBody(a, `rbac-${persona.role}`));
    row.create = created.status();
    const own = created.ok() ? String((await created.json()).id) : existing.id;
    row.edit = (await who.request.patch(`/api/hindrances/${own}`, { headers: who.headers(), data: { location: RUN_TAG } })).status();
    row.document_link = (
      await post(`/api/entities/delay_event/${own}/document-links:batch`, { links: [{ document_id: suite.documentId, relationship_role: "site_record" }] })
    ).status();
    row.relationship_link = (await post(`/api/hindrances/${own}/links`, { target_type: "key_date", target_id: suite.keyDate!.id })).status();
    row.archive = (await post(`/api/hindrances/${own}/archive`, reason)).status();
    row.restore = (await post(`/api/hindrances/${own}/restore`, reason)).status();
    suite.matrix[persona.role] = row;

    if (persona.allowed) {
      expect(row, persona.role).toEqual({
        list: 200, view: 200, compat_list: 200, create: 201, edit: 200,
        document_link: 201, relationship_link: 201, archive: 200, restore: 200,
      });
    } else {
      // Least privilege: every operation refused, exactly 403.
      for (const [operation, status] of Object.entries(row)) expect(status, `${persona.role} ${operation}`).toBe(403);
    }
  }
});

test("a direct URL is authorized independently of menu visibility", async ({ playwright }) => {
  const who = inProject(await session(playwright, "E2E_HIN_PROJECTUSER"), suite.env.E2E_HIN_PROJECT_A_ID);
  const entry = await createEntry(suite.seeder!, suite.env.E2E_HIN_PROJECT_A_ID, "direct-url");
  for (const url of [`/api/hindrances/${entry.id}`, `/api/hindrances/${entry.id}/history`, `/api/hindrances/${entry.id}/links`, `/api/delay-events/${entry.id}`]) {
    expect((await who.request.get(url)).status(), url).toBe(403);
  }
});

// ----------------------------------------------------------------- Stage 4 --

test("no foreign organisation, project, record, document or target leaks", async ({ playwright }) => {
  const f = suite.foreign;
  const own = await createEntry(suite.seeder!, suite.env.E2E_HIN_PROJECT_A_ID, "isolation");
  // The foreign org admin works under its own organisation's selection.
  const outsider = inProject(
    await session(playwright, "E2E_HIN_FOREIGN_ORGADMIN"),
    f.E2E_HIN_FOREIGN_PROJECT_ID,
    f.E2E_HIN_FOREIGN_ORG_ID,
  );
  const results: Record<string, number> = {};

  // A foreign org admin reaching into this org.
  results.foreign_org_detail = (await outsider.request.get(`/api/hindrances/${own.id}`)).status();
  results.foreign_org_patch = (await outsider.request.patch(`/api/hindrances/${own.id}`, { headers: outsider.headers(), data: { title: "x" } })).status();
  results.foreign_org_list_project = (await outsider.request.get("/api/hindrances", { params: { project_id: suite.env.E2E_HIN_PROJECT_A_ID } })).status();
  results.foreign_reverse_key_date = (await outsider.request.get(`/api/hindrances/affecting/key_date/${suite.keyDate!.id}`)).status();
  results.foreign_reverse_document = (await outsider.request.get(`/api/documents/${suite.documentId}/entity-links`)).status();
  for (const [key, value] of Object.entries(results)) expect(value, key).toBe(403);

  // This org's admin reaching into the foreign org.
  const admin = suite.seeder!;
  const post = (url: string, data: unknown) => admin.request.post(url, { headers: admin.headers(), data });
  const inbound: Record<string, number> = {
    foreign_record: (await admin.request.get(`/api/hindrances/${f.E2E_HIN_FOREIGN_ENTRY_ID}`)).status(),
    foreign_project_create: (await post("/api/hindrances", { project_id: f.E2E_HIN_FOREIGN_PROJECT_ID, title: runTitle("x"), start_date: "2026-02-10T00:00:00" })).status(),
    foreign_document: (await post(`/api/entities/delay_event/${own.id}/document-links:batch`, { links: [{ document_id: f.E2E_HIN_FOREIGN_DOCUMENT_ID, relationship_role: "site_record" }] })).status(),
    foreign_milestone: (await post(`/api/hindrances/${own.id}/links`, { target_type: "programme_milestone", target_id: f.E2E_HIN_FOREIGN_MILESTONE_ID })).status(),
    foreign_key_date: (await post(`/api/hindrances/${own.id}/links`, { target_type: "key_date", target_id: f.E2E_HIN_FOREIGN_KEY_DATE_ID })).status(),
    foreign_eot: (await post(`/api/hindrances/${own.id}/links`, { target_type: "eot_submission", target_id: f.E2E_HIN_FOREIGN_EOT_ID })).status(),
  };
  for (const [key, value] of Object.entries(inbound)) expect(value, key).toBe(403);

  // Foreign project inside the same organisation: a project-B-only admin on an A record.
  const projectB = inProject(await session(playwright, "E2E_HIN_PA_B"), suite.env.E2E_HIN_PROJECT_B_ID);
  expect((await projectB.request.get(`/api/hindrances/${own.id}`)).status()).toBe(403);
  expect((await projectB.request.get(`/api/hindrances/affecting/key_date/${suite.keyDate!.id}`)).status()).toBe(403);
  const listed = await projectB.request.get("/api/hindrances");
  expect(listed.status()).toBe(200);
  expect(((await listed.json()).items as any[]).some((row) => row.project_id === suite.env.E2E_HIN_PROJECT_A_ID)).toBe(false);
  writeEvidence("hindrance-isolation", { outbound: results, inbound });
});

// ------------------------------------------------ Active project scope (API) --

test("the selected project bounds every record-level request (CASE A/B/C)", async ({ playwright }) => {
  const a = suite.env.E2E_HIN_PROJECT_A_ID;
  const b = suite.env.E2E_HIN_PROJECT_B_ID;
  const entry = await createEntry(suite.seeder!, a, "active-scope");
  const code = async (response: any) => (await response.json())?.detail?.code;
  const memberOfBoth = await session(playwright, "E2E_HIN_PA_AB");
  const nonMember = await session(playwright, "E2E_HIN_PA_B");

  // CASE A: member of A and B, A selected -> allowed.
  const underA = inProject(memberOfBoth, a);
  expect((await underA.request.get(`/api/hindrances/${entry.id}`)).status()).toBe(200);

  // CASE B: same member, B selected -> refused, reads and writes.
  const underB = inProject(memberOfBoth, b);
  const read = await underB.request.get(`/api/hindrances/${entry.id}`);
  const write = await underB.request.patch(`/api/hindrances/${entry.id}`, { headers: underB.headers(), data: { title: "moved" } });
  const compat = await underB.request.get(`/api/delay-events/${entry.id}`);
  expect([read.status(), write.status(), compat.status()]).toEqual([403, 403, 403]);
  expect([await code(read), await code(write), await code(compat)]).toEqual(["context_forbidden", "context_forbidden", "context_forbidden"]);

  // No selection at all -> 400 selection_required for the record; the list stays bounded.
  const unselected = await memberOfBoth.request.get(`/api/hindrances/${entry.id}`);
  expect(unselected.status()).toBe(400);
  expect(await code(unselected)).toBe("selection_required");
  const bounded = await memberOfBoth.request.get("/api/hindrances");
  expect(bounded.status()).toBe(200);

  // CASE C: not a member of A -> refused whatever it selects.
  const cSelectsA = await inProject(nonMember, a).request.get(`/api/hindrances/${entry.id}`);
  const cSelectsB = await inProject(nonMember, b).request.get(`/api/hindrances/${entry.id}`);
  expect([cSelectsA.status(), cSelectsB.status()]).toEqual([403, 403]);

  expect((await (await suite.seeder!.request.get(`/api/hindrances/${entry.id}`)).json()).title).toContain("active-scope");
  writeEvidence("hindrance-active-scope-api", {
    case_a: 200,
    case_b: { read: read.status(), write: write.status(), compat: compat.status() },
    no_selection: unselected.status(),
    case_c: { selects_a: cSelectsA.status(), selects_b: cSelectsB.status() },
  });
});

// ----------------------------------------------------------------- Stage 6 --

test("documents link through entity_document_links, unlink softly and audit", async () => {
  const admin = suite.seeder!;
  const entry = await createEntry(admin, suite.env.E2E_HIN_PROJECT_A_ID, "doc-links");
  const target = `/api/entities/delay_event/${entry.id}/document-links`;
  const linked = await admin.request.post(`${target}:batch`, {
    headers: admin.headers(),
    data: { links: [{ document_id: suite.documentId, relationship_role: "site_record" }] },
  });
  expect(linked.status()).toBe(201);
  const [link] = (await (await admin.request.get(target)).json()).links;
  expect(link.document_id).toBe(suite.documentId);
  const linkId = String(link.id ?? link._id);

  const reverse = (await (await admin.request.get(`/api/documents/${suite.documentId}/entity-links`)).json()).links as any[];
  expect(reverse.some((row) => row.target_type === "delay_event" && row.target_id === entry.id)).toBe(true);

  // No legacy array write: the register row itself does not carry the document.
  expect((await (await admin.request.get(`/api/hindrances/${entry.id}`)).json()).linked_document_ids ?? []).toEqual([]);

  // Same-organisation document from project B: refused, like the foreign-org one in Stage 4.
  const crossProject = await admin.request.post(`${target}:batch`, {
    headers: admin.headers(),
    data: { links: [{ document_id: suite.projectBDocumentId, relationship_role: "site_record" }] },
  });
  expect(crossProject.status(), "a project-B document linked to a project-A entry").toBe(403);

  const revision = Number(link.revision ?? link._revision ?? 1);
  const removed = await admin.request.post(`/api/document-links/${linkId}:remove`, {
    headers: admin.headers(),
    data: { ...reason, expected_revision: revision },
  });
  expect(removed.status()).toBe(200);
  expect(((await (await admin.request.get(target)).json()).links as any[]).filter((row) => !row.removed_at)).toEqual([]);
  expect((await admin.request.get(`/api/documents/${suite.documentId}`)).status(), "source document did not survive unlink").toBe(200);
  // Audit: no read API exposes `document_relationship.linked/unlinked`, so the runbook
  // reads them from the staging audit_events for exactly these ids (plan §8).
  writeEvidence("hindrance-document-link-audit-keys", { link_id: linkId, delay_event_id: entry.id, document_id: suite.documentId });
});

// ----------------------------------------------------------------- Stage 7 --

test("milestone, key date and EOT links are same-scope, soft-removed and never duplicated", async () => {
  const admin = suite.seeder!;
  const entry = await createEntry(admin, suite.env.E2E_HIN_PROJECT_A_ID, "rel-links");
  const evidence: Record<string, unknown> = {};
  for (const [type, target] of [
    ["programme_milestone", suite.milestone!],
    ["key_date", suite.keyDate!],
    ["eot_submission", suite.eot!],
  ] as const) {
    const body = { target_type: type, target_id: target.id };
    const first = await admin.request.post(`/api/hindrances/${entry.id}/links`, { headers: admin.headers(), data: body });
    expect(first.status(), type).toBe(201);
    // A repeat is idempotent: 200 with the SAME active link, never a second one.
    const duplicate = await admin.request.post(`/api/hindrances/${entry.id}/links`, { headers: admin.headers(), data: body });
    expect(duplicate.status(), `${type} duplicate`).toBe(200);
    const firstBody = await first.json();
    const duplicateBody = await duplicate.json();
    expect(String(duplicateBody.id ?? duplicateBody._id)).toBe(String(firstBody.id ?? firstBody._id));
    const active = ((await (await admin.request.get(`/api/hindrances/${entry.id}/links`)).json()).links as any[]).filter(
      (row) => row.target_type === type && !row.removed_at,
    );
    expect(active, `${type} active links`).toHaveLength(1);
    const affecting = (await (await admin.request.get(`/api/hindrances/affecting/${type}/${target.id}`)).json()).items as any[];
    expect(affecting.some((row) => String(row.hindrance?.id ?? row.hindrance?._id) === entry.id), `${type} reverse`).toBe(true);
    const linkId = String(firstBody.id ?? firstBody._id);
    const removed = await admin.request.post(`/api/hindrances/${entry.id}/links/${linkId}/remove`, { headers: admin.headers(), data: reason });
    expect(removed.status()).toBe(200);
    expect((await removed.json()).removed_at).toBeTruthy();
    const relinked = await admin.request.post(`/api/hindrances/${entry.id}/links`, { headers: admin.headers(), data: body });
    expect(relinked.status(), `${type} relink after soft removal`).toBe(201);
    evidence[type] = { first: first.status(), duplicate: duplicate.status(), removed: removed.status(), relinked: relinked.status() };
  }
  writeEvidence("hindrance-relationship-links", evidence);
});

// ----------------------------------------------------------------- Stage 8 --

async function timelineEvents(admin: FixtureSession, entryId: string): Promise<any[]> {
  const response = await admin.request.get("/api/project-events", { params: { project_id: suite.env.E2E_HIN_PROJECT_A_ID, limit: 1000 } });
  expect(response.status()).toBe(200);
  return ((await response.json()) as any[]).filter((row) => row.source_entity_type === "delay_event" && row.source_entity_id === entryId);
}

test("one timeline event per entry, updated in place", async () => {
  const admin = suite.seeder!;
  const entry = await createEntry(admin, suite.env.E2E_HIN_PROJECT_A_ID, "timeline");
  const [first, ...extra] = await timelineEvents(admin, entry.id);
  expect(extra).toEqual([]);
  expect(first).toBeTruthy();
  // The edit alone must update the projection - no manual re-sync.
  const edited = await admin.request.patch(`/api/hindrances/${entry.id}`, { headers: admin.headers(), data: { title: runTitle("timeline-renamed") } });
  expect(edited.status()).toBe(200);
  expect((await edited.json()).timeline_sync_status).toBe("synced");
  const after = await timelineEvents(admin, entry.id);
  expect(after).toHaveLength(1);
  expect(String(after[0]._id ?? after[0].id)).toBe(String(first._id ?? first.id));
  expect(after[0].title).toContain("timeline-renamed");
});

/**
 * Failure injection, two invocations around a runbook step (plan §8):
 *   armed:    staging-only project_events validator rejects this run's TLFAIL title
 *   disarmed: validator restored; retry must repair idempotently
 */
test("a timeline projection failure is visible, audited and repaired by retry", async () => {
  const phase = (process.env.E2E_HIN_TIMELINE_FAULT ?? "").trim();
  test.skip(!phase, "timeline fault injection runs only inside the runbook step (E2E_HIN_TIMELINE_FAULT)");
  const admin = suite.seeder!;
  const title = runTitle("TLFAIL");
  if (phase === "armed") {
    const created = await admin.request.post("/api/hindrances", {
      headers: admin.headers(),
      data: entryBody(suite.env.E2E_HIN_PROJECT_A_ID, "TLFAIL"),
    });
    expect(created.status()).toBe(201);
    const body = await created.json();
    expect(body.timeline_sync_status).toBe("failed");
    const history = JSON.stringify(await (await admin.request.get(`/api/hindrances/${body.id}/history`)).json());
    expect(history).toContain("timeline_sync_failed");
    writeEvidence("hindrance-timeline-fault-armed", { id: body.id, status: body.timeline_sync_status });
    return;
  }
  expect(phase).toBe("disarmed");
  const listed = await admin.request.get("/api/hindrances", { params: { project_id: suite.env.E2E_HIN_PROJECT_A_ID, q: title } });
  const [entry] = ((await listed.json()).items as any[]).filter((row) => row.title === title);
  // The browser spec's disarmed phase repairs it through the "Retry timeline sync"
  // button first (plan §8); either way a retry now must be synced and idempotent.
  expect(entry, "the armed phase left no TLFAIL entry").toBeTruthy();
  const retried = await admin.request.post(`/api/hindrances/${entry.id}/timeline-sync`, { headers: admin.headers() });
  expect((await retried.json()).timeline_sync_status).toBe("synced");
  const again = await admin.request.post(`/api/hindrances/${entry.id}/timeline-sync`, { headers: admin.headers() });
  expect((await again.json()).timeline_event_id).toBe((await retried.json()).timeline_event_id);
  expect(await timelineEvents(admin, entry.id)).toHaveLength(1);
});

// ----------------------------------------------------------------- Stage 9 --

test("archive requires a reason, retains everything, blocks edits and restores", async () => {
  const admin = suite.seeder!;
  const a = suite.env.E2E_HIN_PROJECT_A_ID;
  const entry = await createEntry(admin, a, "archive");
  const post = (url: string, data: unknown) => admin.request.post(url, { headers: admin.headers(), data });
  expect((await post(`/api/entities/delay_event/${entry.id}/document-links:batch`, { links: [{ document_id: suite.documentId, relationship_role: "site_record" }] })).status()).toBe(201);
  expect((await post(`/api/hindrances/${entry.id}/links`, { target_type: "key_date", target_id: suite.keyDate!.id })).status()).toBe(201);

  expect((await post(`/api/hindrances/${entry.id}/archive`, {})).status()).toBe(422);
  expect((await post(`/api/hindrances/${entry.id}/archive`, reason)).status()).toBe(200);

  const fetched = await admin.request.get(`/api/hindrances/${entry.id}`);
  expect(fetched.status()).toBe(200);
  expect((await fetched.json()).archived_at).toBeTruthy();
  const inDefault = (await (await admin.request.get("/api/hindrances", { params: { project_id: a, q: runTitle("archive") } })).json()).items as any[];
  expect(inDefault.some((row) => row.id === entry.id)).toBe(false);
  const withArchived = (await (await admin.request.get("/api/hindrances", { params: { project_id: a, q: runTitle("archive"), include_archived: "true" } })).json()).items as any[];
  expect(withArchived.some((row) => row.id === entry.id)).toBe(true);
  expect((await admin.request.patch(`/api/hindrances/${entry.id}`, { headers: admin.headers(), data: { title: "x" } })).status()).toBe(409);
  expect(((await (await admin.request.get(`/api/hindrances/${entry.id}/links`)).json()).links as any[]).filter((l) => !l.removed_at)).toHaveLength(1);
  expect(((await (await admin.request.get(`/api/entities/delay_event/${entry.id}/document-links`)).json()).links as any[]).length).toBe(1);
  expect(JSON.stringify(await (await admin.request.get(`/api/hindrances/${entry.id}/history`)).json())).toContain("archived");

  expect((await post(`/api/hindrances/${entry.id}/restore`, reason)).status()).toBe(200);
  expect((await (await admin.request.get(`/api/hindrances/${entry.id}`)).json()).archived_at).toBeFalsy();
});
