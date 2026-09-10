/**
 * Run-owned staging fixtures for the Gate 3 bullets that need seeded state.
 *
 * Bullets 2, 3 and 5 cannot be measured against a deployment without objects to
 * measure them on, and the evidence matrix records exactly that as the gap:
 * bullet 2 "needs an org-admin account and a disposable role, so the run leaves
 * no permanent grant behind", bullet 3 "needs two accounts in different scopes
 * and a disposable document, plus a teardown that removes it - the drill must
 * not accumulate staging documents".
 *
 * Four properties, and each one is a rule here rather than a habit:
 *
 * **Run-owned.** Every object this module creates carries `RUN_TAG` in its name.
 * Nothing is deleted that does not. A cleanup that decides membership by "looks
 * like a test object" eventually deletes something that only looked like one -
 * and on a same-host staging stack the thing next door is production's data.
 *
 * **Idempotent.** Creating twice with the same tag returns the existing object
 * rather than a second one, so a re-run after a crash does not accumulate state
 * and does not fail on a uniqueness constraint. R-A8Q watched the app's own
 * seeder refuse twice for exactly that reason (`uq_permissions_name`).
 *
 * **Deterministically cleanable.** `cleanupRunOwned` enumerates by tag and
 * deletes by id, reports what it removed and what it could not, and is safe to
 * call when there is nothing to remove. Cleanup runs in `afterAll` AND can be
 * invoked standalone against a prior `E2E_RUN_ID`, because the run that needs
 * cleaning is the one that died before its `afterAll`.
 *
 * **Tenant-safe.** Every write is refused unless it names `E2E_STAGING_ORG_ID`,
 * and the whole module refuses to run at all when `E2E_BASE_URL` addresses a
 * host listed in `E2E_PRODUCTION_HOSTS`. That is the Gate 2 lesson from R-A6
 * transplanted: a suite whose default target is the wrong engine will one day be
 * pointed at it.
 *
 * NOT YET EXECUTED AGAINST A DEPLOYMENT. R-A8R is an offline convergence phase
 * with no staging window; this module and the specs that use it are the
 * executable artefacts bullets 2 and 3 have never had, and no bullet is ticked.
 */
import type { APIRequestContext, PlaywrightWorkerArgs } from "@playwright/test";

import { BASE_URL, requireStagingEnvironment } from "./staging-target";

const CSRF_HEADER = "x-csrf-token";
const CSRF_COOKIE = "cc_csrf_token";

/** Identifies everything one run created. Supply it to clean up an earlier run. */
export const RUN_ID = (process.env.E2E_RUN_ID ?? "").trim() || defaultRunId();

/** The substring that makes an object run-owned. Nothing without it is touched. */
export const RUN_TAG = `g3-${RUN_ID}`;

function defaultRunId(): string {
  // Time-ordered and unique, so two runs on the same stack never collide and a
  // human reading the staging database can tell when an orphan was created.
  const stamp = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);
  const suffix = Math.random().toString(36).slice(2, 8);
  return `${stamp}-${suffix}`;
}

export function isRunOwned(name: unknown): boolean {
  return typeof name === "string" && name.includes(RUN_TAG);
}

/**
 * The marker `.env.staging.example` ships instead of the real hostnames, kept
 * identical to `scripts/lib/edge_target.sh`'s so the two controls cannot drift.
 */
const EDGE_HOST_PLACEHOLDER = "replace-with";

/** Hostnames that are production. A fixture run may never address one. */
function productionHosts(): string[] {
  return (process.env.E2E_PRODUCTION_HOSTS ?? "")
    .split(",")
    .map((host) => host.trim().toLowerCase())
    .filter(Boolean);
}

/**
 * Refuse to write anything if the target could be production.
 *
 * Deliberately checked on every write rather than once at import: a suite that
 * proved its target at module load and then had `E2E_BASE_URL` changed under it
 * is exactly the sort of thing that only happens on the day it matters.
 */
export function assertTargetIsNotProduction(): void {
  if (!BASE_URL) {
    throw new Error("E2E_BASE_URL is not set; the fixtures have no target");
  }
  let host: string;
  try {
    host = new URL(BASE_URL).hostname.toLowerCase();
  } catch {
    throw new Error(`E2E_BASE_URL is not a URL: ${BASE_URL}`);
  }
  // An empty denylist refuses nothing. `E2E_PRODUCTION_HOSTS` has no default and
  // appears in no `requireStagingEnvironment(...)` list, so with the variable
  // unset this function ran, matched nothing and returned - and the fixtures
  // would have created, and then DELETED, run-owned rows against whatever
  // `E2E_BASE_URL` named. A control that is present, green and inert is worse
  // than an absent one; the same rule `edge_target.sh` applies to its
  // `REPLACE-WITH` placeholder.
  const declared = productionHosts();
  if (declared.length === 0) {
    throw new Error(
      "E2E_PRODUCTION_HOSTS is unset or empty, so the production refusal below " +
        "would match no host and could never fire. Set it to the production " +
        `hostnames before pointing the fixtures at ${host}.`
    );
  }
  if (declared.some((candidate) => candidate.includes(EDGE_HOST_PLACEHOLDER))) {
    throw new Error(
      `E2E_PRODUCTION_HOSTS is still the ${EDGE_HOST_PLACEHOLDER}... placeholder ` +
        "from .env.staging.example. It would match no host while reading as declared."
    );
  }
  if (declared.includes(host)) {
    throw new Error(
      `refusing to seed fixtures against ${host}: it is listed in E2E_PRODUCTION_HOSTS. ` +
        "A fixture run pointed at production writes to production and files the result " +
        "as staging evidence."
    );
  }
}

/** The organisation every fixture object must belong to. */
export function fixtureOrganizationId(): string {
  const value = (process.env.E2E_STAGING_ORG_ID ?? "").trim();
  if (!value) {
    throw new Error(
      "E2E_STAGING_ORG_ID is not set. Every fixture write names its organisation " +
        "explicitly; a fixture that inherits the caller's active scope writes " +
        "wherever the caller happened to be."
    );
  }
  return value;
}

export function fixtureProjectId(): string {
  const value = (process.env.E2E_STAGING_PROJECT_ID ?? "").trim();
  if (!value) {
    throw new Error("E2E_STAGING_PROJECT_ID is not set");
  }
  return value;
}

/**
 * Refuse a write whose payload leaves the fixture tenant.
 *
 * The gate is the membership answer; this is the write-side equivalent, and it
 * is here rather than in each spec so a new spec cannot forget it.
 */
export function assertWriteIsTenantSafe(payload: {
  organization_id?: string;
  organizationId?: string;
  name?: string;
}): void {
  assertTargetIsNotProduction();
  const expected = fixtureOrganizationId();
  const actual = payload.organization_id ?? payload.organizationId;
  if (actual !== undefined && actual !== expected) {
    throw new Error(
      `refusing a fixture write into organisation ${actual}: fixtures may only write ` +
        `into E2E_STAGING_ORG_ID (${expected})`
    );
  }
  if (payload.name !== undefined && !isRunOwned(payload.name)) {
    throw new Error(
      `refusing to create "${payload.name}": a fixture object must carry ` +
        `the run tag ${RUN_TAG}, or cleanup cannot tell it apart from real data`
    );
  }
}

/**
 * A standalone request context that reaches the same stack the tests do.
 *
 * `playwright.request.newContext()` inherits **nothing** from
 * `playwright.config.ts` - not `baseURL`, not `ignoreHTTPSErrors`. A context
 * built without them turns every relative path in this module into an invalid
 * URL, and a staging TLS terminator with a self-signed certificate into a
 * connection error. The teardown would then fail for a reason that has nothing
 * to do with whether the state was removed.
 *
 * It is a function here rather than two identical option bags in two specs, so a
 * third spec cannot be written without them.
 */
export async function newFixtureContext(
  playwright: PlaywrightWorkerArgs["playwright"]
): Promise<APIRequestContext> {
  assertTargetIsNotProduction();
  return playwright.request.newContext({
    baseURL: BASE_URL,
    ignoreHTTPSErrors: process.env.E2E_IGNORE_HTTPS_ERRORS === "1",
  });
}

// --------------------------------------------------------------------------- #
// An authenticated API session
// --------------------------------------------------------------------------- #

export interface FixtureSession {
  request: APIRequestContext;
  csrf: string;
  /** A short-lived token for a step-up-guarded action, minted on demand. */
  stepUp(action: string): Promise<string>;
  headers(extra?: Record<string, string>): Record<string, string>;
}

/**
 * Sign in and return a session that can perform unsafe requests.
 *
 * The CSRF value is read back from the cookie jar rather than reused from the
 * pre-login token: `POST /api/login` issues a NEW `cc_csrf_token`, and the
 * pre-login value stops matching the moment the session exists. The bullet 1
 * spec learned this against the deployment; repeating the mistake here would
 * produce a 403 that reads like an authorisation defect.
 */
export async function signIn(
  request: APIRequestContext,
  emailVar: string,
  passwordVar: string
): Promise<FixtureSession> {
  assertTargetIsNotProduction();
  const env = requireStagingEnvironment(emailVar, passwordVar);

  const seed = await request.get("/api/csrf-token");
  if (!seed.ok()) {
    throw new Error(`GET /api/csrf-token failed with ${seed.status()}`);
  }
  const seedToken = (await seed.json()).csrf_token as string;

  const login = await request.post("/api/login", {
    headers: { [CSRF_HEADER]: seedToken },
    data: { email: env[emailVar], password: env[passwordVar] },
  });
  if (!login.ok()) {
    // The status only. A body can carry the address that failed to sign in.
    throw new Error(`sign-in as ${emailVar} failed with ${login.status()}`);
  }

  const cookies = (await request.storageState()).cookies;
  const csrf = cookies.find((cookie) => cookie.name === CSRF_COOKIE)?.value;
  if (!csrf) {
    throw new Error("sign-in issued no CSRF cookie");
  }

  const session: FixtureSession = {
    request,
    csrf,
    headers: (extra = {}) => ({ [CSRF_HEADER]: csrf, ...extra }),
    async stepUp(action: string) {
      const response = await request.post("/api/step-up", {
        headers: { [CSRF_HEADER]: csrf },
        data: { password: env[passwordVar], action },
      });
      if (!response.ok()) {
        throw new Error(`POST /api/step-up for ${action} failed with ${response.status()}`);
      }
      return (await response.json()).step_up_token as string;
    },
  };
  return session;
}

// --------------------------------------------------------------------------- #
// Roles - bullet 2
// --------------------------------------------------------------------------- #

export interface FixtureRole {
  id: string;
  name: string;
}

export function fixtureRoleName(label: string): string {
  return `${RUN_TAG}-${label}`;
}

async function findRoleByName(session: FixtureSession, name: string): Promise<FixtureRole | null> {
  const response = await session.request.get("/api/roles");
  if (!response.ok()) {
    throw new Error(`GET /api/roles failed with ${response.status()}`);
  }
  const body = await response.json();
  const roles: any[] = Array.isArray(body) ? body : (body.roles ?? []);
  const match = roles.find((role) => role?.name === name);
  if (!match) {
    return null;
  }
  return { id: String(match._id ?? match.id), name: String(match.name) };
}

/**
 * A disposable organisation-scoped role, created once per run tag.
 *
 * Idempotent by name: a re-run after a crash finds the role it already made
 * instead of failing on the uniqueness constraint or creating a second one.
 */
export async function ensureDisposableRole(
  session: FixtureSession,
  label: string,
  permissions: string[] = []
): Promise<FixtureRole> {
  const name = fixtureRoleName(label);
  const organizationId = fixtureOrganizationId();
  assertWriteIsTenantSafe({ organization_id: organizationId, name });

  const existing = await findRoleByName(session, name);
  if (existing) {
    return existing;
  }

  const stepUpToken = await session.stepUp("platform.role.manage");
  const response = await session.request.post("/api/roles", {
    headers: session.headers({ "x-step-up-token": stepUpToken }),
    data: {
      name,
      description: `Gate 3 fixture, run ${RUN_ID}. Safe to delete.`,
      scope: "organization",
      organization_id: organizationId,
      permissions,
    },
  });
  if (!response.ok()) {
    throw new Error(`POST /api/roles failed with ${response.status()}`);
  }
  const created = await response.json();
  return { id: String(created._id ?? created.id), name };
}

export async function setRolePermissions(
  session: FixtureSession,
  role: FixtureRole,
  permissions: string[]
): Promise<void> {
  assertWriteIsTenantSafe({ name: role.name });
  const stepUpToken = await session.stepUp("platform.role.manage");
  const response = await session.request.put(`/api/roles/${role.id}`, {
    headers: session.headers({ "x-step-up-token": stepUpToken }),
    data: { permissions },
  });
  if (!response.ok()) {
    throw new Error(`PUT /api/roles/${role.id} failed with ${response.status()}`);
  }
}

export async function readRolePermissions(
  session: FixtureSession,
  role: FixtureRole
): Promise<string[]> {
  const response = await session.request.get(`/api/roles/${role.id}/permissions`);
  if (!response.ok()) {
    throw new Error(`GET /api/roles/${role.id}/permissions failed with ${response.status()}`);
  }
  const body = await response.json();
  const rows: any[] = Array.isArray(body) ? body : (body.permissions ?? []);
  return rows.map((row) => String(row?.name ?? row)).sort();
}

// --------------------------------------------------------------------------- #
// Documents - bullet 3
// --------------------------------------------------------------------------- #

export interface FixtureDocument {
  id: string;
  letterNo: string;
}

export function fixtureLetterNo(label: string): string {
  return `${RUN_TAG}-${label}`;
}

async function findDocumentByLetterNo(
  session: FixtureSession,
  letterNo: string
): Promise<FixtureDocument | null> {
  const response = await session.request.get("/api/documents", {
    params: { search: letterNo, limit: 50 },
  });
  if (!response.ok()) {
    throw new Error(`GET /api/documents failed with ${response.status()}`);
  }
  const body = await response.json();
  const rows: any[] = body.documents ?? body.items ?? [];
  const match = rows.find(
    (row) => row?.letterNo === letterNo || row?.letter_no === letterNo
  );
  return match ? { id: String(match._id ?? match.id), letterNo } : null;
}

/**
 * A disposable document, uploaded once per run tag.
 *
 * The payload is generated here rather than read from a checked-in file: a
 * fixture file in the repository is a fixture that will one day be replaced by
 * a real contract someone had handy. OCR is off - this bullet is about upload,
 * view, download and refusal, and ingestion is bullet 4's subject.
 */
export async function ensureDisposableDocument(
  session: FixtureSession,
  label: string
): Promise<FixtureDocument> {
  const letterNo = fixtureLetterNo(label);
  const organizationId = fixtureOrganizationId();
  const projectId = fixtureProjectId();
  assertWriteIsTenantSafe({ organization_id: organizationId, name: letterNo });

  const existing = await findDocumentByLetterNo(session, letterNo);
  if (existing) {
    return existing;
  }

  const body = Buffer.from(
    `Gate 3 fixture document.\nRun: ${RUN_ID}\nLetter: ${letterNo}\n`,
    "utf-8"
  );
  const response = await session.request.post("/api/documents", {
    headers: session.headers(),
    multipart: {
      file: { name: `${letterNo}.txt`, mimeType: "text/plain", buffer: body },
      organization_id: organizationId,
      project_id: projectId,
      uploadType: "incoming",
      letterNo,
      date: new Date().toISOString().slice(0, 10),
      subject: `Gate 3 fixture ${RUN_ID}`,
      ocrEnabled: "0",
      compressionEnabled: "0",
    },
  });
  if (!response.ok()) {
    throw new Error(`POST /api/documents failed with ${response.status()}`);
  }
  const created = await response.json();
  return { id: String(created._id ?? created.id), letterNo };
}

// --------------------------------------------------------------------------- #
// Arbitration drafts - bullet 7
// --------------------------------------------------------------------------- #

export interface FixtureArbitrationDraft {
  id: string;
  title: string;
}

export function fixtureDraftTitle(label: string): string {
  return `${RUN_TAG}-${label}`;
}

async function findDraftByTitle(
  session: FixtureSession,
  title: string
): Promise<FixtureArbitrationDraft | null> {
  const response = await session.request.get("/api/arbitration/drafts", {
    params: { q: title, limit: 50 },
  });
  if (!response.ok()) {
    throw new Error(`GET /api/arbitration/drafts failed with ${response.status()}`);
  }
  const body = await response.json();
  const rows: any[] = Array.isArray(body) ? body : (body.drafts ?? body.items ?? []);
  const match = rows.find((row) => row?.title === title);
  return match ? { id: String(match._id ?? match.id), title } : null;
}

/**
 * A disposable arbitration draft, created once per run tag.
 *
 * `manual_facts` and `relief_sought` are supplied so the deterministic generator
 * has real source material and the generated draft is not an empty shell -
 * bullet 7 asks that the chain *completes*, not that the endpoints answer 200.
 *
 * No model is involved. `ArbitrationDraftingService._resolve_generator` reads
 * `ARBITRATION_DRAFT_MODE`, whose default is `deterministic`, so a staging run
 * costs no tokens and is reproducible. That is also why this bullet does not
 * depend on the LangGraph rollout: the drafting surface is served by
 * `arbitration_v2` whatever `ARBITRATION_ENGINE_ROLLOUT_MODE` says.
 */
export async function ensureDisposableArbitrationDraft(
  session: FixtureSession,
  label: string
): Promise<FixtureArbitrationDraft> {
  const title = fixtureDraftTitle(label);
  const organizationId = fixtureOrganizationId();
  const projectId = fixtureProjectId();
  assertWriteIsTenantSafe({ organization_id: organizationId, name: title });

  const existing = await findDraftByTitle(session, title);
  if (existing) {
    return existing;
  }

  const response = await session.request.post("/api/arbitration/drafts", {
    headers: session.headers(),
    data: {
      organization_id: organizationId,
      project_id: projectId,
      draft_type: "statement_of_claim",
      party_role: "claimant",
      dispute_type: "eot_delay",
      title,
      relief_sought: `Extension of time of 28 days. Fixture ${RUN_ID}.`,
      manual_facts:
        "The Employer instructed a change to the piling sequence on 3 March. " +
        "The Contractor gave notice within 14 days and maintained records " +
        `throughout. Fixture ${RUN_ID}.`,
      governing_law: "Laws of India",
      arbitration_clause: "Clause 20.6",
      currency: "INR",
      include_register_sources: false,
    },
  });
  if (!response.ok()) {
    throw new Error(`POST /api/arbitration/drafts failed with ${response.status()}`);
  }
  const created = await response.json();
  return { id: String(created._id ?? created.id), title };
}

// --------------------------------------------------------------------------- #
// Teardown
// --------------------------------------------------------------------------- #

export interface CleanupReport {
  runTag: string;
  deleted: string[];
  failed: { subject: string; status: number }[];
}

/**
 * Remove everything this run tag owns. Safe to call twice, and safe to call
 * when the run created nothing.
 *
 * Enumerates by tag and deletes by id. A 404 counts as removed - the object is
 * gone, which is the property being asserted - and any other failure is
 * reported rather than swallowed, because a teardown that reports success while
 * leaving state behind is how staging accumulates it.
 */
export async function cleanupRunOwned(session: FixtureSession): Promise<CleanupReport> {
  assertTargetIsNotProduction();
  const report: CleanupReport = { runTag: RUN_TAG, deleted: [], failed: [] };

  const documents = await session.request.get("/api/documents", {
    params: { search: RUN_TAG, limit: 200 },
  });
  if (documents.ok()) {
    const body = await documents.json();
    const rows: any[] = body.documents ?? body.items ?? [];
    for (const row of rows) {
      const letterNo = row?.letterNo ?? row?.letter_no;
      if (!isRunOwned(letterNo)) {
        continue;
      }
      const id = String(row._id ?? row.id);
      const response = await session.request.delete(`/api/documents/${id}`, {
        headers: session.headers(),
      });
      if (response.ok() || response.status() === 404) {
        report.deleted.push(`document:${letterNo}`);
      } else {
        report.failed.push({ subject: `document:${letterNo}`, status: response.status() });
      }
    }
  }

  const drafts = await session.request.get("/api/arbitration/drafts", {
    params: { q: RUN_TAG, limit: 200 },
  });
  if (drafts.ok()) {
    const body = await drafts.json();
    const rows: any[] = Array.isArray(body) ? body : (body.drafts ?? body.items ?? []);
    for (const row of rows) {
      if (!isRunOwned(row?.title)) {
        continue;
      }
      const id = String(row._id ?? row.id);
      const response = await session.request.delete(`/api/arbitration/drafts/${id}`, {
        headers: session.headers(),
      });
      if (response.ok() || response.status() === 404) {
        report.deleted.push(`arbitration_draft:${row.title}`);
      } else {
        report.failed.push({ subject: `arbitration_draft:${row.title}`, status: response.status() });
      }
    }
  }

  const roles = await session.request.get("/api/roles");
  if (roles.ok()) {
    const body = await roles.json();
    const rows: any[] = Array.isArray(body) ? body : (body.roles ?? []);
    for (const row of rows) {
      if (!isRunOwned(row?.name)) {
        continue;
      }
      const id = String(row._id ?? row.id);
      const stepUpToken = await session.stepUp("platform.role.manage");
      const response = await session.request.delete(`/api/roles/${id}`, {
        headers: session.headers({ "x-step-up-token": stepUpToken }),
      });
      if (response.ok() || response.status() === 404) {
        report.deleted.push(`role:${row.name}`);
      } else {
        report.failed.push({ subject: `role:${row.name}`, status: response.status() });
      }
    }
  }

  return report;
}
