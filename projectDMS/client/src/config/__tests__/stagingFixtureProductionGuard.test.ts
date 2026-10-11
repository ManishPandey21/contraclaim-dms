/**
 * F-A8T2-4 - the fixtures' production refusal was a denylist that is EMPTY by
 * default.
 *
 * `E2E_PRODUCTION_HOSTS` has no default and appears in no
 * `requireStagingEnvironment(...)` list, so with the variable unset
 * `assertTargetIsNotProduction()` ran, matched nothing and returned. Pointed at
 * a production `E2E_BASE_URL` the fixtures would have created run-owned rows
 * there - and `cleanupRunOwned` would then have issued DELETEs against them.
 *
 * The test that stood for this guard asserted only that the string
 * `E2E_PRODUCTION_HOSTS` appears in the file and that the function body
 * contains `throw new Error`. Both are true whether or not the list can ever
 * match, which is why this one calls the function instead of reading it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const PRODUCTION_URL = "https://contraclaim.com";
const STAGING_URL = "https://staging.internal.invalid:18443";

async function loadFixtures() {
  vi.resetModules();
  return import("../../../e2e/staging/fixtures");
}

describe("assertTargetIsNotProduction", () => {
  const saved = { ...process.env };

  beforeEach(() => {
    process.env.E2E_RUN_ID = "a8t2guard";
    // R-A8U added the allowlist. Every case below that expects a *denylist*
    // refusal declares the staging host too, so the refusal it asserts is the
    // one it names and not the new one firing first.
    process.env.E2E_STAGING_HOST = "staging.internal.invalid";
  });

  afterEach(() => {
    process.env = { ...saved };
    vi.resetModules();
  });

  it("refuses when the denylist is unset, because it could never fire", async () => {
    process.env.E2E_BASE_URL = PRODUCTION_URL;
    delete process.env.E2E_PRODUCTION_HOSTS;

    const { assertTargetIsNotProduction } = await loadFixtures();

    expect(() => assertTargetIsNotProduction()).toThrowError(
      /E2E_PRODUCTION_HOSTS is unset or empty/
    );
  });

  it("refuses an empty denylist just as it refuses an unset one", async () => {
    process.env.E2E_BASE_URL = PRODUCTION_URL;
    process.env.E2E_PRODUCTION_HOSTS = " , ,  ";

    const { assertTargetIsNotProduction } = await loadFixtures();

    expect(() => assertTargetIsNotProduction()).toThrowError(
      /E2E_PRODUCTION_HOSTS is unset or empty/
    );
  });

  it("refuses the shipped placeholder, which reads as declared and matches nothing", async () => {
    process.env.E2E_BASE_URL = PRODUCTION_URL;
    process.env.E2E_PRODUCTION_HOSTS = "REPLACE-WITH-PRODUCTION-HOST";

    const { assertTargetIsNotProduction } = await loadFixtures();

    expect(() => assertTargetIsNotProduction()).toThrowError(/placeholder/);
  });

  it("refuses a declared production host", async () => {
    process.env.E2E_BASE_URL = PRODUCTION_URL;
    process.env.E2E_PRODUCTION_HOSTS = "contraclaim.com,www.contraclaim.com";

    const { assertTargetIsNotProduction } = await loadFixtures();

    expect(() => assertTargetIsNotProduction()).toThrowError(/refusing to seed fixtures/);
  });

  it("allows a staging target once the denylist is really declared", async () => {
    process.env.E2E_BASE_URL = STAGING_URL;
    process.env.E2E_PRODUCTION_HOSTS = "contraclaim.com,www.contraclaim.com";

    const { assertTargetIsNotProduction } = await loadFixtures();

    expect(() => assertTargetIsNotProduction()).not.toThrow();
  });
});

/**
 * R-A8U - the denylist only refuses the hostnames somebody thought of.
 *
 * `staging_teardown_guard.py:52` states the rule for exactly this situation:
 * "An allowlist of one, not a denylist". The file that issues the DELETEs was
 * the one using the denylist. Measured against
 * `E2E_PRODUCTION_HOSTS=contraclaim.com`, every hostname below PASSED, and each
 * of them reaches the same production site.
 */
describe("assertTargetIsNotProduction - the allowlist of one", () => {
  const saved = { ...process.env };

  beforeEach(() => {
    process.env.E2E_RUN_ID = "a8uguard";
    process.env.E2E_PRODUCTION_HOSTS = "contraclaim.com";
    process.env.E2E_STAGING_HOST = "staging.internal.invalid";
  });

  afterEach(() => {
    process.env = { ...saved };
    vi.resetModules();
  });

  it.each([
    ["a www. alias of the same site", "https://www.contraclaim.com"],
    ["a trailing dot, which DNS treats as the same name", "https://contraclaim.com./"],
    ["the production IP address", "https://203.0.113.10"],
    ["a production host on a non-default port", "https://contraclaim.com:8443"],
    ["an unrelated host nobody listed", "https://somewhere.else.invalid"],
  ])("refuses %s", async (_label, url) => {
    process.env.E2E_BASE_URL = url;

    const { assertTargetIsNotProduction } = await loadFixtures();

    expect(() => assertTargetIsNotProduction()).toThrowError(/refusing to seed fixtures/);
  });

  it("refuses when the staging host is not declared at all", async () => {
    process.env.E2E_BASE_URL = STAGING_URL;
    delete process.env.E2E_STAGING_HOST;

    const { assertTargetIsNotProduction } = await loadFixtures();

    expect(() => assertTargetIsNotProduction()).toThrowError(/E2E_STAGING_HOST is not set/);
  });

  it("refuses the shipped placeholder in the allowlist too", async () => {
    process.env.E2E_BASE_URL = STAGING_URL;
    process.env.E2E_STAGING_HOST = "REPLACE-WITH-STAGING-HOST";

    const { assertTargetIsNotProduction } = await loadFixtures();

    expect(() => assertTargetIsNotProduction()).toThrowError(/placeholder/);
  });

  it("allows the declared staging host", async () => {
    process.env.E2E_BASE_URL = STAGING_URL;

    const { assertTargetIsNotProduction } = await loadFixtures();

    expect(() => assertTargetIsNotProduction()).not.toThrow();
  });

  it("treats a trailing dot on the declared host as the same name", async () => {
    process.env.E2E_BASE_URL = "https://staging.internal.invalid.:18443";

    const { assertTargetIsNotProduction } = await loadFixtures();

    expect(() => assertTargetIsNotProduction()).not.toThrow();
  });
});

/**
 * R-A8U - cleanup deleted by name tag with no tenant check.
 *
 * `assertWriteIsTenantSafe` pins every create to `E2E_STAGING_ORG_ID`; the
 * delete side compared nothing. The session holds `platform.role.manage`
 * step-up, so the listings return rows beyond the fixture organisation, and two
 * runs sharing an operator-supplied `E2E_RUN_ID` in different organisations
 * delete each other's rows.
 */
describe("cleanupRunOwned tenant scoping", () => {
  const saved = { ...process.env };

  beforeEach(() => {
    process.env.E2E_RUN_ID = "a8utenant";
    process.env.E2E_BASE_URL = STAGING_URL;
    process.env.E2E_PRODUCTION_HOSTS = "contraclaim.com";
    process.env.E2E_STAGING_HOST = "staging.internal.invalid";
    process.env.E2E_STAGING_ORG_ID = "org-staging";
  });

  afterEach(() => {
    process.env = { ...saved };
    vi.resetModules();
  });

  it("deletes only rows belonging to the fixture organisation", async () => {
    const fixtures = await loadFixtures();
    const tag = fixtures.RUN_TAG;

    const deleted: string[] = [];
    const session = {
      headers: () => ({}),
      stepUp: async () => "token",
      request: {
        get: async (path: string) => ({
          ok: () => true,
          json: async () => {
            if (path === "/api/documents") {
              return {
                documents: [
                  { _id: "mine", letterNo: `L-${tag}`, organizationId: "org-staging" },
                  { _id: "theirs", letterNo: `L-${tag}`, organizationId: "org-other" },
                  { _id: "orphan", letterNo: `L-${tag}` },
                ],
              };
            }
            return [];
          },
        }),
        delete: async (path: string) => {
          deleted.push(path);
          return { ok: () => true, status: () => 200 };
        },
      },
    } as any;

    const report = await fixtures.cleanupRunOwned(session);

    expect(deleted).toEqual(["/api/documents/mine"]);
    expect(report.deleted).toHaveLength(1);
  });

  it("leaves a row that declares no organisation alone", async () => {
    const fixtures = await loadFixtures();
    const tag = fixtures.RUN_TAG;

    const deleted: string[] = [];
    const session = {
      headers: () => ({}),
      stepUp: async () => "token",
      request: {
        get: async (path: string) => ({
          ok: () => true,
          json: async () =>
            path === "/api/documents"
              ? { documents: [{ _id: "orphan", letterNo: `L-${tag}` }] }
              : [],
        }),
        delete: async (path: string) => {
          deleted.push(path);
          return { ok: () => true, status: () => 200 };
        },
      },
    } as any;

    await fixtures.cleanupRunOwned(session);

    expect(deleted).toEqual([]);
  });
});
