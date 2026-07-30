import { expect, test, type Page } from "@playwright/test";

async function expectResponsiveLogin(
  page: Page,
  viewport: { width: number; height: number }
) {
  await page.setViewportSize(viewport);
  await page.goto("/login");

  await expect(
    page.getByRole("heading", {
      name: "Welcome back to your contract record.",
    })
  ).toBeVisible();
  await expect(page.getByLabel("Work email")).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();

  const backgroundImage = await page
    .getByTestId("login-background")
    .evaluate((element) => getComputedStyle(element).backgroundImage);
  expect(backgroundImage).toContain("linear-gradient");
  expect(backgroundImage).not.toContain("url(");
  expect(backgroundImage).not.toContain("contract-intelligence-hero.jpg");

  const pageWidth = await page.getByTestId("login-page").evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(pageWidth.scrollWidth).toBeLessThanOrEqual(pageWidth.clientWidth);

  const cardBox = await page.getByTestId("login-card").boundingBox();
  expect(cardBox).not.toBeNull();
  expect(cardBox!.x).toBeGreaterThanOrEqual(0);
  expect(cardBox!.x + cardBox!.width).toBeLessThanOrEqual(viewport.width);

  const submitBox = await page
    .getByRole("button", { name: "Sign in" })
    .boundingBox();
  expect(submitBox).not.toBeNull();
  expect(submitBox!.y + submitBox!.height).toBeLessThanOrEqual(viewport.height);
}

test("login layout fits a 390px mobile viewport", async ({ page }) => {
  await expectResponsiveLogin(page, { width: 390, height: 844 });
});

test("login layout fits a desktop viewport", async ({ page }) => {
  await expectResponsiveLogin(page, { width: 1280, height: 800 });
});
