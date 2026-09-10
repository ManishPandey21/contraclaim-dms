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
