import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  allArticles,
  allVideos,
  getAdjacentArticles,
  getArticleBySlug,
  getEntries,
  getEntryPath,
  getRelatedArticles,
  getUsedCategories,
  publishedArticles,
  publishedVideos,
  rejectedVideos,
  validateContent,
} from "..";
import { isBlogCategoryId } from "../categories";
import { SITE_ORIGIN } from "@/config/site";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");

describe("content invariants", () => {
  it("passes every declared content rule", () => {
    expect(validateContent()).toEqual([]);
  });

  it("publishes the five articles of the construction-claims series", () => {
    expect(publishedArticles).toHaveLength(5);
    expect(publishedArticles.map((a) => a.slug).sort()).toEqual(
      [
        "excel-registers-versus-connected-contractual-records",
        "how-to-build-defensible-project-chronology",
        "seven-records-every-eot-claim-needs",
        "what-ai-should-and-should-not-do-contractual-drafting",
        "why-construction-claims-fail-before-submission",
      ].sort(),
    );
  });

  it("derives a positive reading time and parsed body for every article", () => {
    for (const article of publishedArticles) {
      expect(article.readingTime).toBeGreaterThan(0);
      expect(article.blocks.length).toBeGreaterThan(5);
    }
  });

  it("uses a byline and a known category on every entry", () => {
    for (const entry of [...allArticles, ...allVideos]) {
      expect(entry.author).toBe("ContraClaim Editorial");
      expect(isBlogCategoryId(entry.category)).toBe(true);
    }
  });

  it("does not publish any video yet and rejected none silently", () => {
    // No video URLs have been supplied for this series. If this fails, videos
    // were added — extend the video page tests rather than relaxing this.
    expect(publishedVideos).toEqual([]);
    expect(rejectedVideos).toEqual([]);
  });

  it("keeps every article body free of the separate LinkedIn draft", () => {
    for (const article of allArticles) {
      expect(article.body).not.toContain("LinkedIn post");
      expect(article.body).not.toContain("#ConstructionClaims");
    }
  });

  it("retains the legal disclaimer on every article", () => {
    for (const article of allArticles) {
      expect(article.body).toMatch(/\*This article provides general/);
    }
  });
});

describe("registry lookups", () => {
  it("resolves an article by slug and returns undefined for an unknown one", () => {
    expect(getArticleBySlug("seven-records-every-eot-claim-needs")?.order).toBe(2);
    expect(getArticleBySlug("no-such-article")).toBeUndefined();
  });

  it("builds the right path per entry type", () => {
    expect(getEntryPath({ type: "article", slug: "a" })).toBe("/blog/articles/a");
    expect(getEntryPath({ type: "video", slug: "b" })).toBe("/blog/videos/b");
  });

  it("returns entries for the requested tab", () => {
    expect(getEntries("articles")).toHaveLength(5);
    expect(getEntries("videos")).toHaveLength(0);
  });

  it("lists only categories that have published content", () => {
    const used = getUsedCategories("articles");
    expect(used.length).toBeGreaterThan(0);
    expect(used.every(isBlogCategoryId)).toBe(true);
    expect(getUsedCategories("videos")).toEqual([]);
  });

  it("walks previous/next in editorial order and stops at the ends", () => {
    const first = getAdjacentArticles("why-construction-claims-fail-before-submission");
    expect(first.previous).toBeUndefined();
    expect(first.next?.slug).toBe("seven-records-every-eot-claim-needs");

    const last = getAdjacentArticles(
      "what-ai-should-and-should-not-do-contractual-drafting",
    );
    expect(last.next).toBeUndefined();
    expect(last.previous?.slug).toBe("how-to-build-defensible-project-chronology");

    expect(getAdjacentArticles("unknown")).toEqual({});
  });

  it("returns related articles that honour the editorial link plan", () => {
    const related = getRelatedArticles("why-construction-claims-fail-before-submission", 3);
    expect(related).toHaveLength(3);
    expect(related.map((a) => a.slug)).not.toContain(
      "why-construction-claims-fail-before-submission",
    );
    expect(related[0].slug).toBe("seven-records-every-eot-claim-needs");
  });

  it("tops up related articles when the explicit plan is shorter than the limit", () => {
    const related = getRelatedArticles("seven-records-every-eot-claim-needs", 3);
    expect(related).toHaveLength(3);
    expect(new Set(related.map((a) => a.slug)).size).toBe(3);
  });

  it("returns nothing related for an unknown slug", () => {
    expect(getRelatedArticles("unknown")).toEqual([]);
  });
});

describe("sitemap and robots", () => {
  const sitemap = readFileSync(resolve(projectRoot, "public/sitemap.xml"), "utf8");
  const robots = readFileSync(resolve(projectRoot, "public/robots.txt"), "utf8");

  it("lists every published entry", () => {
    for (const entry of [...publishedArticles, ...publishedVideos]) {
      expect(sitemap).toContain(`${SITE_ORIGIN}${getEntryPath(entry)}`);
    }
  });

  it("lists the blog index and the landing page", () => {
    expect(sitemap).toContain(`<loc>${SITE_ORIGIN}/</loc>`);
    expect(sitemap).toContain(`<loc>${SITE_ORIGIN}/blog</loc>`);
  });

  it("has no stale blog URL for content that is not published", () => {
    const listed = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)]
      .map((match) => match[1])
      .filter((url) => url.includes("/blog/"));
    const expected = [...publishedArticles, ...publishedVideos].map(
      (entry) => `${SITE_ORIGIN}${getEntryPath(entry)}`,
    );
    expect(listed.sort()).toEqual(expected.sort());
  });

  it("allows the blog and points at the sitemap", () => {
    expect(robots).toContain("Allow: /blog");
    expect(robots).toContain(`Sitemap: ${SITE_ORIGIN}/sitemap.xml`);
  });

  it("keeps authenticated application routes out of the index", () => {
    for (const path of ["/login", "/documents", "/contracts", "/claims", "/api/"]) {
      expect(robots).toContain(`Disallow: ${path}`);
    }
  });
});
