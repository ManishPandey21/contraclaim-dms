import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// R-A8T — the staging Gate 3 suites must SKIP on an ordinary run, not fail.
//
// `npm run test:e2e` is a `frontend-checks` step, and `playwright.config.ts`
// collects `./e2e` whole, so the staging specs are collected on every CI run
// with no `E2E_BASE_URL`. Their per-test `requireStagingEnvironment()` skips
// correctly - but `test.afterAll` runs even when every test in the group
// skipped, and those teardowns build a fixture context, which by design refuses
// to act without a target and throws.
//
// Measured before the fix: `10 failed, 4 skipped, 33 passed`. After:
// `0 failed, 14 skipped, 33 passed`. The first CI run this release branch ever
// gets is the evidence Gate 1 bullet 6 is waiting on, and it would have been
// red on this - in a suite nobody had executed locally.
//
// So: any suite-level hook in the staging specs must ask whether it has work
// before it touches a fixture.

const here = dirname(fileURLToPath(import.meta.url));
const STAGING_DIR = resolve(here, "../../../e2e/staging");

const GUARD = "stagingTeardownHasWork";
const HOOKS = ["test.afterAll", "test.beforeAll"];

function specFiles(): string[] {
  return readdirSync(STAGING_DIR).filter((name) => name.endsWith(".spec.ts"));
}

describe("staging e2e suites skip cleanly without a deployment", () => {
  it("finds the staging specs at all", () => {
    // Anti-vacuity: a guard pointed at an empty directory passes forever.
    expect(specFiles().length).toBeGreaterThanOrEqual(4);
  });

  it("exports the predicate the teardowns ask", () => {
    const source = readFileSync(resolve(STAGING_DIR, "staging-target.ts"), "utf8");
    expect(source).toContain(`export function ${GUARD}(`);
  });

  it.each(specFiles())("%s guards every suite-level hook", (name) => {
    const source = readFileSync(resolve(STAGING_DIR, name), "utf8");
    const hooks = HOOKS.filter((hook) => source.includes(hook));
    if (hooks.length === 0) {
      // A spec with no suite-level hook cannot have this defect.
      return;
    }
    expect(
      source.includes(GUARD),
      `${name} declares ${hooks.join(" and ")} but never calls ${GUARD}(). ` +
        "A suite-level hook runs even when every test in the group skipped, and " +
        "the staging fixtures throw without E2E_BASE_URL - so this turns a clean " +
        "skip into a failed frontend-checks run.",
    ).toBe(true);
  });
});
