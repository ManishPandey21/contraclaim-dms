import { expect, test, type Page } from "@playwright/test";

const DESKTOP = { width: 1280, height: 900 };
const MOBILE = { width: 390, height: 844 };

const FIRST_ARTICLE_SLUG = "why-construction-claims-fail-before-submission";

function articleCards(page: Page) {
  return page.locator("article").filter({ has: page.getByRole("link") });
}

test.describe("public blog", () => {
  test("is reachable from the landing page header without signing in", async ({
    page,
  }) => {
    await page.setViewportSize(DESKTOP);
    await page.goto("/");

    const blogLink = page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("link", { name: "Blog" });
    await expect(blogLink).toBeVisible();
    await blogLink.click();

    await expect(page).toHaveURL(/\/blog$/);
    await expect(
      page.getByRole("heading", {
        level: 1,
        name: "Construction Contract and Claims Insights",
      }),
    ).toBeVisible();
    // No session was established, and none is demanded.
    await expect(page.getByLabel("Password", { exact: true })).toHaveCount(0);
  });

  test("is reachable from the mobile menu", async ({ page }) => {
    await page.setViewportSize(MOBILE);
    await page.goto("/");

    await page.getByRole("button", { name: "Open menu" }).click();
    await page
      .getByRole("navigation", { name: "Mobile" })
      .getByRole("link", { name: "Blog" })
      .click();

    await expect(page).toHaveURL(/\/blog$/);
    await expect(
      page.getByRole("heading", { level: 1, name: /Construction Contract/ }),
    ).toBeVisible();
  });

  test("switches between the Articles and Videos tabs", async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await page.goto("/blog");

    await expect(page.getByRole("tab", { name: "Articles" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(articleCards(page)).toHaveCount(5);

    await page.getByRole("tab", { name: "Videos" }).click();

    await expect(page).toHaveURL(/type=videos/);
    await expect(page.getByRole("tab", { name: "Videos" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page.getByText("No videos published yet")).toBeVisible();
  });

  test("paginates within the required range on a small screen", async ({ page }) => {
    await page.setViewportSize(MOBILE);
    await page.goto("/blog");

    // `count()` does not auto-wait, so settle on the rendered grid first.
    await articleCards(page).first().waitFor();
    const perPage = await articleCards(page).count();
    expect(perPage).toBeGreaterThanOrEqual(4);
    expect(perPage).toBeLessThanOrEqual(6);

    await expect(page.getByRole("button", { name: "Go to previous page" })).toBeDisabled();
    await page.getByRole("button", { name: "Go to next page" }).click();

    await expect(page).toHaveURL(/page=2/);
    await expect(page.getByRole("button", { name: "Go to next page" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Go to previous page" })).toBeEnabled();
  });

  test("keeps the tab selection while paginating", async ({ page }) => {
    await page.setViewportSize(MOBILE);
    await page.goto("/blog?type=articles");

    await page.getByRole("button", { name: "Go to next page" }).click();

    await expect(page).toHaveURL(/page=2/);
    await expect(page.getByRole("tab", { name: "Articles" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  test("filters by topic and by search term", async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await page.goto("/blog");

    await page.getByRole("button", { name: "Extension of Time" }).click();
    await expect(page).toHaveURL(/category=extension-of-time/);
    await expect(articleCards(page)).toHaveCount(1);

    await page.getByRole("button", { name: "All topics" }).click();
    await page.getByRole("searchbox", { name: /Search articles/i }).fill("chronology");
    await expect(page).toHaveURL(/q=chronology/);
    await expect(articleCards(page)).toHaveCount(1);
  });

  test("opens an article at its own shareable URL", async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await page.goto(`/blog/articles/${FIRST_ARTICLE_SLUG}`);

    await expect(
      page.getByRole("heading", {
        level: 1,
        name: "Why Construction Claims Fail Before They Are Submitted",
      }),
    ).toBeVisible();
    await expect(page).toHaveTitle(/Why Construction Claims Fail Before Submission/);

    const canonical = page.locator('link[rel="canonical"]');
    await expect(canonical).toHaveAttribute(
      "href",
      `https://web.contraclaim.com/blog/articles/${FIRST_ARTICLE_SLUG}`,
    );
  });

  test("renders article body structure and a next-article link", async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await page.goto(`/blog/articles/${FIRST_ARTICLE_SLUG}`);

    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    await expect(
      page.getByRole("heading", { level: 2, name: "A claim is a chain, not a narrative" }),
    ).toBeVisible();
    await expect(
      page.getByRole("navigation", { name: "Article navigation" }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to the blog" })).toBeVisible();
  });

  test("shows a blog-shaped not-found page for an unknown slug", async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await page.goto("/blog/articles/does-not-exist");

    await expect(
      page.getByRole("heading", { level: 1, name: /could not be found/i }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to the blog" })).toBeVisible();
  });

  test("does not scroll the body sideways on a phone", async ({ page }) => {
    await page.setViewportSize(MOBILE);
    await page.goto("/blog/articles/excel-registers-versus-connected-contractual-records");
    await page.getByRole("heading", { level: 1 }).first().waitFor();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    // The wide comparison table scrolls inside its own container.
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test("exposes robots.txt and a sitemap covering the blog", async ({ request }) => {
    const robots = await request.get("/robots.txt");
    expect(robots.ok()).toBeTruthy();
    expect(await robots.text()).toContain("Allow: /blog");

    const sitemap = await request.get("/sitemap.xml");
    expect(sitemap.ok()).toBeTruthy();
    expect(await sitemap.text()).toContain(`/blog/articles/${FIRST_ARTICLE_SLUG}`);
  });
});
