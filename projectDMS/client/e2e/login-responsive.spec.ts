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

  const heroDescription = page.getByText(
    "Claims, correspondence and evidence—connected in one defensible workspace."
  );
  if (viewport.width < 640) {
    await expect(heroDescription).toBeHidden();
  } else {
    await expect(heroDescription).toBeVisible();
  }

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

  const headingAlignment = await page
    .getByTestId("login-card-heading")
    .evaluate((element) => {
      const rects = Array.from(element.children).map((child) =>
        child.getBoundingClientRect()
      );

      return {
        leftEdges: rects.map((rect) => rect.left),
        tops: rects.map((rect) => rect.top),
        bottoms: rects.map((rect) => rect.bottom),
      };
    });
  expect(
    Math.max(...headingAlignment.leftEdges) -
      Math.min(...headingAlignment.leftEdges)
  ).toBeLessThanOrEqual(1);
  expect(headingAlignment.bottoms[0]).toBeLessThanOrEqual(
    headingAlignment.tops[1]
  );
  expect(headingAlignment.bottoms[1]).toBeLessThanOrEqual(
    headingAlignment.tops[2]
  );

  const submitBox = await page
    .getByRole("button", { name: "Sign in" })
    .boundingBox();
  expect(submitBox).not.toBeNull();
  expect(submitBox!.y + submitBox!.height).toBeLessThanOrEqual(viewport.height);

  const submitBackground = await page
    .getByRole("button", { name: "Sign in" })
    .evaluate((element) => getComputedStyle(element).backgroundColor);
  expect(submitBackground).toBe("rgb(13, 27, 46)");

  const cardBackground = await page
    .getByTestId("login-card")
    .evaluate((element) => getComputedStyle(element).backgroundColor);
  expect(cardBackground).toBe("rgb(247, 248, 250)");
}

test("login layout fits a 390px mobile viewport", async ({ page }) => {
  await expectResponsiveLogin(page, { width: 390, height: 844 });
});

test("primary login action remains above the fold on a short phone", async ({
  page,
}) => {
  await expectResponsiveLogin(page, { width: 390, height: 667 });
});

test("tablet layout keeps the hero and login card visually grouped", async ({
  page,
}) => {
  await expectResponsiveLogin(page, { width: 768, height: 1024 });

  const heroBox = await page.getByTestId("login-hero").boundingBox();
  const cardBox = await page.getByTestId("login-card").boundingBox();
  expect(heroBox).not.toBeNull();
  expect(cardBox).not.toBeNull();
  expect(cardBox!.y - (heroBox!.y + heroBox!.height)).toBeLessThanOrEqual(64);
});

test("login layout fits a desktop viewport", async ({ page }) => {
  await expectResponsiveLogin(page, { width: 1280, height: 800 });
});
