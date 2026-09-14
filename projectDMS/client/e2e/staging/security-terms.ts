/**
 * The mandatory Security Terms screen, as a user meets it.
 *
 * `ProtectedRoute` sends an authenticated user who has not accepted the active
 * terms version to `/security-terms` before any protected page renders. Every
 * browser test that opens a protected page has to get past it the way a person
 * does - tick the consent box, press "Accept and Continue" - or it measures the
 * terms screen instead of the page it names.
 *
 * This module holds no target or credential logic, so the same acceptance code
 * the staging specs run can also be exercised against the page itself in a
 * mocked suite (`e2e/security-terms-acceptance.spec.ts`). That suite is component
 * evidence only; it proves the locator and the flow, never a deployment.
 */
import { expect } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";

export const SECURITY_TERMS_PATH = "/security-terms";
export const SECURITY_TERMS_HEADING = "Security, Privacy & Anti-Piracy Terms";
export const SECURITY_TERMS_CONSENT =
  "I have read and agree to the active Security, Privacy & Anti-Piracy Terms.";

/**
 * The terms page's title: the one level-1 heading with that exact name.
 *
 * F-A8Z-B1. `SecurityTermsPage` renders the title as the page's `h1`, and the
 * active version's own `title` as an `h2` inside the "Scrollable security terms"
 * region. The deployment's active version is titled with the same text, so a
 * heading locator by name alone matched both and strict mode refused it. The
 * level is the page's semantic contract - one document title - and does not
 * depend on element order, so `first()`/`nth()` would be the wrong fix: they
 * would pick whichever element happens to come first, including the body's copy
 * if the layout ever changed.
 */
export function securityTermsHeading(page: Page): Locator {
  return page.getByRole("heading", { level: 1, name: SECURITY_TERMS_HEADING, exact: true });
}

/**
 * Wait for either the protected page or the terms screen; if the terms screen
 * appeared, accept through the page and return to `path`.
 *
 * R-A8W Stage B run 2 got past the screen by calling
 * `POST /api/security-terms/accept` from the harness, which proves the endpoint
 * and not the page. Here the consent box is ticked, "Accept and Continue" is
 * pressed, and the acceptance is waited for on the wire.
 */
export async function acceptSecurityTermsIfPresented(
  page: Page,
  path: string,
  ready: Locator
): Promise<void> {
  const terms = securityTermsHeading(page);
  await Promise.race([
    ready.waitFor({ state: "visible", timeout: 30_000 }),
    terms.waitFor({ state: "visible", timeout: 30_000 }),
  ]);
  if (await terms.isVisible()) {
    await page.getByLabel(SECURITY_TERMS_CONSENT).check();
    const [accepted] = await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes("/api/security-terms/accept") && response.request().method() === "POST"
      ),
      page.getByRole("button", { name: "Accept and Continue" }).click(),
    ]);
    expect(accepted.ok(), `accepting the security terms failed with ${accepted.status()}`).toBe(true);
    await page.waitForURL((url) => url.pathname !== SECURITY_TERMS_PATH);
    await page.goto(path);
  }
  await expect(ready).toBeVisible({ timeout: 30_000 });
}
