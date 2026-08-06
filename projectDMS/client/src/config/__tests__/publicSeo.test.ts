import { describe, expect, it } from "vitest";

import {
  HOME_SEO,
  LANDING_FAQS,
  getIndexablePublicPages,
  getPublicPageSeo,
} from "../publicSeo";
import { isIndexablePublicPath } from "../publicRoutes";
import { SITE_ORIGIN } from "../site";

describe("public SEO contract", () => {
  const pages = getIndexablePublicPages();

  it("publishes one unique metadata set for every indexable page", () => {
    expect(pages).toHaveLength(7);
    expect(new Set(pages.map((page) => page.path)).size).toBe(pages.length);
    expect(new Set(pages.map((page) => page.title)).size).toBe(pages.length);
    expect(new Set(pages.map((page) => page.description)).size).toBe(pages.length);

    for (const page of pages) {
      expect(page.title.length).toBeGreaterThanOrEqual(30);
      expect(page.title.length).toBeLessThanOrEqual(65);
      expect(page.description.length).toBeGreaterThanOrEqual(120);
      expect(page.description.length).toBeLessThanOrEqual(165);
      expect(page.image.url).toMatch(/^\//);
      expect(page.image.alt.length).toBeGreaterThan(10);
    }
  });

  it("keeps the landing page FAQ content aligned with its structured data", () => {
    const faq = HOME_SEO.jsonLd.find((item) => item["@type"] === "FAQPage");
    expect(faq).toBeDefined();
    expect(faq?.mainEntity).toHaveLength(LANDING_FAQS.length);
    expect(LANDING_FAQS).toHaveLength(8);
  });

  it("emits BlogPosting and breadcrumb data for each article", () => {
    const articles = pages.filter((page) => page.path.startsWith("/blog/articles/"));
    expect(articles).toHaveLength(5);

    for (const article of articles) {
      expect(article.jsonLd.map((item) => item["@type"])).toEqual([
        "BlogPosting",
        "BreadcrumbList",
      ]);
      const posting = article.jsonLd[0];
      expect(posting.mainEntityOfPage).toEqual({
        "@type": "WebPage",
        "@id": `${SITE_ORIGIN}${article.path}`,
      });
      expect(article.publishedTime).toMatch(/^\d{4}-\d{2}-\d{2}/);
      expect(article.modifiedTime).toMatch(/^\d{4}-\d{2}-\d{2}/);
    }
  });

  it("fails closed for every route outside the exact public allow-list", () => {
    for (const path of ["/login", "/dashboard", "/blog/unknown", "/documents/1"]) {
      expect(isIndexablePublicPath(path)).toBe(false);
      expect(getPublicPageSeo(path)).toBeUndefined();
    }
  });
});
