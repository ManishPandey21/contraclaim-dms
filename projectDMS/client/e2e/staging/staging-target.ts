/**
 * The same three rules the backend's Gate-2 harness uses, for Gate 3.
 *
 * `backend/rbac_backend/tests/staging_gate.py` exists because a live suite that
 * skips when its environment is missing is right for a developer and fatal for a
 * gate: the run reports green having measured nothing. Gate 3 has the identical
 * shape and had none of the protection - R-A8I ran 33 of 33 Playwright tests
 * against staging and earned no checkbox, and nothing in the suite could have
 * told the difference between "passed against staging" and "passed against a dev
 * server".
 *
 * So: one switch, absence is a failure under it, and nothing is read off the
 * disk. No credential is ever written into a trace, a title or an error message.
 */
import { test } from "@playwright/test";

/** Points the suite at a deployed stack. Absent means an ordinary local run. */
export const BASE_URL = process.env.E2E_BASE_URL?.trim() ?? "";

/** The one switch. Set it when the run is being offered as Gate 3 evidence. */
export const STRICT = ["1", "true", "yes", "on"].includes(
  (process.env.CONTRACLAIM_STAGING_E2E ?? "").trim().toLowerCase()
);

export const targetsDeployedStack = Boolean(BASE_URL);

function read(name: string): string {
  return (process.env[name] ?? "").trim();
}

/**
 * Gate a staging-only spec on its environment.
 *
 * Outside strict mode a missing variable skips, which is correct for a developer
 * running the suite locally. Inside it, the same condition fails: a skipped
 * required spec is a Gate 3 bullet that was not measured, reported as though it
 * had been.
 */
export function requireStagingEnvironment(...names: string[]): Record<string, string> {
  const missing = names.filter((name) => !read(name));

  if (!targetsDeployedStack) {
    const message = "Set E2E_BASE_URL to run the staging Gate 3 suite against a deployment.";
    if (STRICT) {
      throw new Error(message);
    }
    test.skip(true, message);
    return {};
  }

  if (missing.length > 0) {
    const message = `Missing required staging environment variables: ${missing.join(", ")}`;
    if (STRICT) {
      // Deliberately an error and not `test.skip`: under the switch this must be
      // a red run, and the variable NAMES are safe to print - the values are not
      // and never appear here.
      throw new Error(
        `${message}. CONTRACLAIM_STAGING_E2E is set, so this run is Gate 3 evidence ` +
          "and a required value may not be skipped over."
      );
    }
    test.skip(true, message);
    return {};
  }

  return Object.fromEntries(names.map((name) => [name, read(name)]));
}

/** Credentials for the account the authenticated bullets are measured with. */
export const CREDENTIAL_VARS = ["E2E_STAGING_EMAIL", "E2E_STAGING_PASSWORD"] as const;
