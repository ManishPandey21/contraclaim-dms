/**
 * F-A8Z-B1: the Security Terms screen, reached and accepted through its own UI.
 *
 * R-A8Z Stage B ran the org-admin staging spec against a deployment and it failed
 * before measuring anything: the terms page renders the text "Security, Privacy &
 * Anti-Piracy Terms" twice - the page title (`h1`) and the active version's own
 * title (`h2`) inside the scrollable terms region - so the heading locator the
 * staging harness waited on matched two elements and Playwright strict mode
 * refused it. Serial mode then skipped every Gate 4 test.
 *
 * This suite mocks the network, in the shape the real endpoints return, with an
 * active version whose title is identical to the page title, exactly as the
 * deployment served it. It exercises the SAME acceptance code the staging specs
 * run (`staging/security-terms.ts`). It is component evidence: it proves the
 * locator is unambiguous and the flow goes through the consent box and the
 * button. It earns no gate bullet and is excluded from deployed-stack runs by
 * `playwright.config.ts`.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

import {
  SECURITY_TERMS_CONSENT,
  SECURITY_TERMS_HEADING,
  SECURITY_TERMS_PATH,
  acceptSecurityTermsIfPresented,
  securityTermsHeading,
} from "./staging/security-terms";

const ACTIVE_VERSION = {
  id: "terms-v1",
  version: "1.0",
  // The deployment's active version carries the page title as its own title.
  title: SECURITY_TERMS_HEADING,
  body: "Fixture terms body.",
  effective_date: "2026-01-01",
  is_active: true,
  terms_hash: "fixture-hash",
};

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/** A signed-in user who has not accepted the active terms. */
async function mockUnacceptedUser(page: Page) {
  const state = { accepted: false, acceptPosts: [] as unknown[] };

  // Registered first so the specific routes below take precedence. Anything the
  // page loads after navigation answers empty rather than reaching a backend.
  await page.route("**/api/**", (route) => json(route, {}));
  await page.route("**/api/me", (route) =>
    json(route, {
      id: "user-1",
      email: "terms.user@example.test",
      organization_id: "org-A",
      roles: ["orgadmin"],
      permissions: ["dms.dashboard.view"],
    })
  );
  await page.route("**/api/security-terms/status", (route) =>
    json(route, {
      requires_acceptance: !state.accepted,
      active_version: ACTIVE_VERSION,
      accepted_acceptance: null,
    })
  );
  await page.route("**/api/security-terms/accept", async (route) => {
    if (route.request().method() !== "POST") {
      return route.fallback();
    }
    state.acceptPosts.push(route.request().postDataJSON());
    state.accepted = true;
    return json(route, {
      id: "acceptance-1",
      user_id: "user-1",
      terms_version: ACTIVE_VERSION.version,
      accepted_at: "2026-09-14T00:00:00Z",
      terms_hash: ACTIVE_VERSION.terms_hash,
      acceptance_method: "checkbox_accept_continue",
    });
  });
  return state;
}

test.describe("F-A8Z-B1 - the Security Terms page, located and accepted through its UI", () => {
  test("the page title is one unambiguous heading although the active version repeats its text", async ({
    page,
  }) => {
    await mockUnacceptedUser(page);
    await page.goto(SECURITY_TERMS_PATH);

    // The premise of F-A8Z-B1, kept as an assertion: if the page stops repeating
    // the text, this test must be revisited rather than silently pass.
    await expect(page.getByRole("heading", { name: SECURITY_TERMS_HEADING })).toHaveCount(2);

    // Strict mode: this call throws if the locator resolves to more than one element.
    await securityTermsHeading(page).waitFor({ state: "visible" });
    await expect(securityTermsHeading(page)).toHaveCount(1);
  });

  test("acceptance goes through the consent box and the button, and is observed on the wire", async ({
    page,
  }) => {
    const state = await mockUnacceptedUser(page);
    await page.goto(SECURITY_TERMS_PATH);

    // The page's own gate: the button is inert until the consent box is ticked.
    await expect(page.getByRole("button", { name: "Accept and Continue" })).toBeDisabled();
    await expect(page.getByLabel(SECURITY_TERMS_CONSENT)).not.toBeChecked();

    // After acceptance the same page shows the already-accepted state.
    const ready = page.getByRole("button", { name: "Continue", exact: true });
    await acceptSecurityTermsIfPresented(page, SECURITY_TERMS_PATH, ready);

    expect(state.acceptPosts, "the acceptance was not posted exactly once by the page").toHaveLength(1);
    expect(state.acceptPosts[0]).toMatchObject({ accepted: true, acceptance_method: "checkbox_accept_continue" });
    await expect(page.getByRole("button", { name: "Accept and Continue" })).toHaveCount(0);
  });
});
