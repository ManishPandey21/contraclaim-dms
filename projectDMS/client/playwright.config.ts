import { defineConfig, devices } from "@playwright/test";

/**
 * Gate 3 is scored only against a deployed staging stack, and until now this
 * config made that impossible: `baseURL` was the literal dev server and
 * `webServer` always started Vite. R-A8I ran 33 of 33 Playwright tests green
 * against staging and earned no checkbox, because reaching staging at all
 * required a temporary config file written for the run and deleted afterwards -
 * evidence produced by an artefact that is not in the repository is not evidence.
 *
 * So the target is an input. `E2E_BASE_URL` points the suite at a deployed stack
 * and, when it is set, no dev server is started: a `webServer` alongside an
 * external target is how a run silently measures localhost while reporting a
 * staging URL.
 *
 * The mocked suites are excluded in that mode rather than left to pass. They
 * intercept `**\/api/**`, so against staging they would assert on their own
 * fixtures while appearing to exercise the deployment - which is exactly the
 * shape of false evidence Gate 3 is trying to avoid.
 */
const stagingBaseUrl = process.env.E2E_BASE_URL?.trim();
const targetsDeployedStack = Boolean(stagingBaseUrl);

/** Suites whose network is mocked. They prove component behaviour, never a deployment. */
const MOCKED_SUITES = [
  "**/contract-workflows.spec.ts",
  "**/contract-master.spec.ts",
  "**/security-terms-acceptance.spec.ts",
  "**/correspondence-linking.spec.ts",
];

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: {
    timeout: 10_000,
  },
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  // A deployed-stack run is certification evidence. One worker: the staging specs
  // clean up by run tag from more than one describe block, and parallel workers
  // would delete each other's run-owned objects mid-test (R-A9A review). No
  // retries: a pass on the second attempt is not evidence, and a retried serial
  // group re-creates a role whose soft-deleted name still answers 409.
  workers: targetsDeployedStack ? 1 : undefined,
  retries: targetsDeployedStack ? 0 : process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["html", { open: "never" }], ["list"]] : "list",
  testIgnore: targetsDeployedStack ? MOCKED_SUITES : [],
  use: {
    baseURL: stagingBaseUrl ?? "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    ignoreHTTPSErrors: process.env.E2E_IGNORE_HTTPS_ERRORS === "1",
  },
  webServer: targetsDeployedStack
    ? undefined
    : {
        command: "npm run dev -- --host 127.0.0.1",
        url: "http://127.0.0.1:5173",
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
