/**
 * Run-owned staging fixtures for the Hindrance & Constraint Register certification.
 *
 * Same four rules as `fixtures.ts` - run-owned, idempotent, deterministically
 * cleanable, tenant-safe - with one difference forced by the register: staging
 * data may be ARCHIVED but never deleted. So "cleanup" here archives every
 * run-owned register entry, soft-removes every active relationship link it made,
 * and writes the residual ids to the evidence directory. Nothing is deleted.
 *
 * The EOT link needs an EOT submission, and an EOT submission needs a FROZEN key
 * date baseline, which cannot be unfrozen. That is why the certification runs in
 * two run-owned projects (`E2E_HIN_PROJECT_A_ID` / `_B_ID`) created for the run,
 * never in a shared staging project.
 *
 * NOT YET EXECUTED AGAINST A DEPLOYMENT (offline preparation, awaiting R-A9I and
 * an owner-granted maintenance window).
 */
import * as fs from "node:fs";
import * as path from "node:path";

import { expect } from "@playwright/test";

import {
  assertTargetIsNotProduction,
  assertWriteIsTenantSafe,
  fixtureOrganizationId,
  isRunOwned,
  RUN_ID,
  RUN_TAG,
  type FixtureSession,
} from "./fixtures";
import { requireStagingEnvironment } from "./staging-target";

/** The two run-owned projects and the accounts the certification is measured with. */
export const HINDRANCE_ENV = [
  "E2E_HIN_PROJECT_A_ID",
  "E2E_HIN_PROJECT_A_NAME",
  "E2E_HIN_PROJECT_B_ID",
  "E2E_HIN_PROJECT_B_NAME",
  "E2E_HIN_ORGADMIN_EMAIL",
  "E2E_HIN_ORGADMIN_PASSWORD",
  "E2E_HIN_PA_AB_EMAIL",
  "E2E_HIN_PA_AB_PASSWORD",
  "E2E_HIN_PA_B_EMAIL",
  "E2E_HIN_PA_B_PASSWORD",
] as const;

export function hindranceEnvironment(): Record<string, string> {
  const env = requireStagingEnvironment(...HINDRANCE_ENV);
  for (const key of ["E2E_HIN_PROJECT_A_NAME", "E2E_HIN_PROJECT_B_NAME"]) {
    if (env[key] && !isRunOwned(env[key])) {
      throw new Error(`${key} must carry the run tag ${RUN_TAG}: the certification projects are run-owned`);
    }
  }
  return env;
}

export function runTitle(label: string): string {
  return `${RUN_TAG}-${label}`;
}

/**
 * Re-check the target before EVERY unsafe request, not once at sign-in.
 *
 * Every write needs the CSRF header, so routing the header through the guard
 * means no write in these specs can skip it - the same rule `fixtures.ts`
 * applies ("checked on every write rather than once at import").
 */
export function guardedSession(session: FixtureSession): FixtureSession {
  return {
    ...session,
    headers(extra: Record<string, string> = {}) {
      assertTargetIsNotProduction();
      return session.headers(extra);
    },
  };
}

/** A register-entry body that names the fixture organisation and carries the run tag. */
export function entryBody(projectId: string, label: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  const title = runTitle(label);
  const organization_id = fixtureOrganizationId();
  assertWriteIsTenantSafe({ organization_id, name: title });
  return { organization_id, project_id: projectId, title, start_date: "2026-02-10T00:00:00", ...extra };
}

async function ok(response: { ok(): boolean; status(): number; text(): Promise<string> }, what: string) {
  if (!response.ok()) {
    // Status and server detail only: staging fixtures carry no personal data.
    throw new Error(`${what} failed with ${response.status()}: ${(await response.text()).slice(0, 300)}`);
  }
}

// --------------------------------------------------------------------------- #
// Relationship targets
// --------------------------------------------------------------------------- #

export interface Target {
  id: string;
  label: string;
}

export async function ensureProgrammeMilestone(session: FixtureSession, projectId: string, label: string): Promise<Target> {
  const ref = runTitle(label);
  const organization_id = fixtureOrganizationId();
  assertWriteIsTenantSafe({ organization_id, name: ref });
  const listed = await session.request.get("/api/programme-milestones", { params: { project_id: projectId, limit: 500 } });
  await ok(listed, "GET /api/programme-milestones");
  const hit = ((await listed.json()) as any[]).find((row) => row?.milestone_ref === ref);
  if (hit) return { id: String(hit._id ?? hit.id), label: ref };
  const created = await session.request.post("/api/programme-milestones", {
    headers: session.headers(),
    data: { organization_id, project_id: projectId, milestone_ref: ref, title: `${ref} activity`, planned_date: "2026-03-01T00:00:00" },
  });
  await ok(created, "POST /api/programme-milestones");
  const body = await created.json();
  return { id: String(body._id ?? body.id), label: ref };
}

export async function ensureKeyDate(session: FixtureSession, projectId: string, label: string): Promise<Target> {
  const title = runTitle(label);
  const organization_id = fixtureOrganizationId();
  assertWriteIsTenantSafe({ organization_id, name: title });
  const listed = await session.request.get("/api/key-dates", { params: { project_id: projectId } });
  await ok(listed, "GET /api/key-dates");
  const rows: any[] = await listed.json().then((body) => (Array.isArray(body) ? body : body.items ?? body.milestones ?? []));
  const hit = rows.find((row) => row?.title === title);
  if (hit) return { id: String(hit._id ?? hit.id), label: hit.milestone_ref || title };
  const created = await session.request.post("/api/key-dates", {
    headers: session.headers(),
    data: { organization_id, project_id: projectId, title, contractual_week_number: 12 },
  });
  await ok(created, "POST /api/key-dates");
  const body = await created.json();
  return { id: String(body._id ?? body.id), label: body.milestone_ref || title };
}

/**
 * An EOT submission in a run-owned project. Freezes that project's baseline
 * first (irreversible - which is why the project must be run-owned).
 */
export async function ensureEotSubmission(session: FixtureSession, projectId: string, label: string): Promise<Target> {
  const reference = runTitle(label);
  const organization_id = fixtureOrganizationId();
  assertWriteIsTenantSafe({ organization_id, name: reference });
  const workflow = async () => {
    const response = await session.request.get("/api/key-dates/workflow", { params: { project_id: projectId, contract_id: "primary" } });
    await ok(response, "GET /api/key-dates/workflow");
    return response.json();
  };
  const hit = ((await workflow()).submissions ?? []).find((row: any) => row?.eot_reference === reference);
  if (hit) return { id: String(hit.id ?? hit._id), label: String(hit.revision_label) };

  const freeze = await session.request.post("/api/key-dates/baseline/freeze", {
    headers: session.headers(),
    data: { organization_id, project_id: projectId, contract_id: "primary", confirmation: true },
  });
  // Already frozen by an earlier attempt of this run is the only tolerated refusal.
  if (!freeze.ok() && !/already|frozen/i.test(await freeze.text())) {
    await ok(freeze, "POST /api/key-dates/baseline/freeze");
  }
  const created = await session.request.post("/api/key-dates/eot-submissions", {
    headers: session.headers(),
    data: { organization_id, project_id: projectId, contract_id: "primary", eot_reference: reference, status: "draft", items: [] },
  });
  await ok(created, "POST /api/key-dates/eot-submissions");
  const body = await created.json();
  return { id: String(body.id ?? body._id), label: String(body.revision_label) };
}

/**
 * A run-owned document in a named project (the shared `ensureDisposableDocument`
 * always uploads into `E2E_STAGING_PROJECT_ID`). Used for the cross-project refusal.
 */
export async function ensureDocumentInProject(session: FixtureSession, projectId: string, label: string): Promise<string> {
  const letterNo = runTitle(label);
  const organization_id = fixtureOrganizationId();
  assertWriteIsTenantSafe({ organization_id, name: letterNo });
  const listed = await session.request.get("/api/documents", { params: { search: letterNo, limit: 50 } });
  await ok(listed, "GET /api/documents");
  const rows: any[] = await listed.json().then((body) => body.documents ?? body.items ?? []);
  const hit = rows.find((row) => row?.letterNo === letterNo || row?.letter_no === letterNo);
  if (hit) return String(hit._id ?? hit.id);
  const created = await session.request.post("/api/documents", {
    headers: session.headers(),
    multipart: {
      file: { name: `${letterNo}.txt`, mimeType: "text/plain", buffer: Buffer.from(`Hindrance certification fixture.\nRun: ${RUN_ID}\n`, "utf-8") },
      organization_id,
      project_id: projectId,
      uploadType: "incoming",
      letterNo,
      date: new Date().toISOString().slice(0, 10),
      subject: `Hindrance certification ${RUN_ID}`,
      ocrEnabled: "0",
      compressionEnabled: "0",
    },
  });
  await ok(created, "POST /api/documents");
  const body = await created.json();
  return String(body._id ?? body.id);
}

// --------------------------------------------------------------------------- #
// Register entries
// --------------------------------------------------------------------------- #

export interface RegisterEntry {
  id: string;
  ref: string;
  project_id: string;
}

export async function createEntry(
  session: FixtureSession,
  projectId: string,
  label: string,
  eventType: "hindrance" | "constraint" | "delay_event" = "hindrance",
): Promise<RegisterEntry> {
  const created = await session.request.post("/api/hindrances", {
    headers: session.headers(),
    data: entryBody(projectId, label, { event_type: eventType }),
  });
  await ok(created, "POST /api/hindrances");
  const body = await created.json();
  return { id: String(body.id ?? body._id), ref: String(body.hindrance_ref), project_id: projectId };
}

async function listRunOwned(session: FixtureSession, projectId: string): Promise<any[]> {
  const rows: any[] = [];
  for (let skip = 0; skip < 10_000; skip += 200) {
    const response = await session.request.get("/api/hindrances", {
      params: { project_id: projectId, include_archived: "true", q: RUN_TAG, skip, limit: 200 },
    });
    await ok(response, "GET /api/hindrances");
    const body = await response.json();
    const items: any[] = body.items ?? [];
    rows.push(...items.filter((row) => isRunOwned(row?.title)));
    if (items.length < 200) break;
  }
  return rows;
}

export interface ResidueReport {
  run_id: string;
  archived_now: string[];
  already_archived: string[];
  links_removed: string[];
  document_links_removed: string[];
  failures: string[];
  residual_register_ids: string[];
}

/**
 * The residue policy: archive every run-owned entry, soft-remove every active
 * relationship link on it, delete nothing, and record what is left.
 */
export async function archiveRunOwned(session: FixtureSession, projectIds: string[]): Promise<ResidueReport> {
  assertTargetIsNotProduction();
  const report: ResidueReport = {
    run_id: RUN_ID,
    archived_now: [],
    already_archived: [],
    links_removed: [],
    document_links_removed: [],
    failures: [],
    residual_register_ids: [],
  };
  for (const projectId of projectIds) {
    for (const row of await listRunOwned(session, projectId)) {
      const id = String(row.id ?? row._id);
      report.residual_register_ids.push(id);
      const links = await session.request.get(`/api/hindrances/${id}/links`);
      if (links.ok()) {
        for (const link of (await links.json()).links ?? []) {
          if (link.removed_at) continue;
          const linkId = String(link.id ?? link._id);
          const removed = await session.request.post(`/api/hindrances/${id}/links/${linkId}/remove`, {
            headers: session.headers(),
            data: { reason: `certification teardown ${RUN_ID}` },
          });
          if (removed.ok()) report.links_removed.push(linkId);
          else report.failures.push(`link ${linkId}: ${removed.status()}`);
        }
      } else {
        report.failures.push(`links of ${id}: ${links.status()}`);
      }
      const documentLinks = await session.request.get(`/api/entities/delay_event/${id}/document-links`);
      if (documentLinks.ok()) {
        for (const link of (await documentLinks.json()).links ?? []) {
          if (link.removed_at) continue;
          const linkId = String(link.id ?? link._id);
          const removed = await session.request.post(`/api/document-links/${linkId}:remove`, {
            headers: session.headers(),
            data: { reason: `certification teardown ${RUN_ID}`, expected_revision: Number(link.revision ?? link._revision ?? 1) },
          });
          if (removed.ok()) report.document_links_removed.push(linkId);
          else report.failures.push(`document link ${linkId}: ${removed.status()}`);
        }
      } else {
        report.failures.push(`document links of ${id}: ${documentLinks.status()}`);
      }
      if (row.archived_at) {
        report.already_archived.push(id);
        continue;
      }
      const archived = await session.request.post(`/api/hindrances/${id}/archive`, {
        headers: session.headers(),
        data: { reason: `certification teardown ${RUN_ID}` },
      });
      if (archived.ok()) report.archived_now.push(id);
      else report.failures.push(`archive ${id}: ${archived.status()}`);
    }
  }
  return report;
}

/** Evidence goes to `E2E_EVIDENCE_DIR` when the runbook sets it; never into the repo. */
export function writeEvidence(name: string, body: unknown): void {
  const dir = (process.env.E2E_EVIDENCE_DIR ?? "").trim();
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `${RUN_ID}-${name}.json`), JSON.stringify(body, null, 2));
}

export function expectNoFailures(report: ResidueReport): void {
  expect(report.failures, "residue teardown left failures").toEqual([]);
}
