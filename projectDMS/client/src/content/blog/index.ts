import { parseMarkdown } from "./markdown";
import { estimateReadingMinutes } from "./reading-time";
import { toEmbed } from "./video-embed";
import { isBlogCategoryId } from "./categories";
import type {
  BlogArticle,
  BlogCategoryId,
  BlogContentType,
  BlogVideo,
  ResolvedArticle,
  ResolvedEntry,
  ResolvedVideo,
} from "./types";

import { whyConstructionClaimsFail } from "./articles/why-construction-claims-fail-before-submission";
import { sevenRecordsEveryEotClaimNeeds } from "./articles/seven-records-every-eot-claim-needs";
import { excelRegistersVersusConnectedRecords } from "./articles/excel-registers-versus-connected-contractual-records";
import { howToBuildDefensibleProjectChronology } from "./articles/how-to-build-defensible-project-chronology";
import { whatAiShouldAndShouldNotDo } from "./articles/what-ai-should-and-should-not-do-contractual-drafting";
import { blogVideos } from "./videos";

/**
 * Blog registry.
 *
 * Source content is declared here; everything the pages consume is derived
 * once at module load: markdown is parsed to a block tree, reading time is
 * computed, and video URLs are validated against the provider allowlist.
 *
 * Adding content means adding a module and listing it below. No page component
 * changes.
 */

/** Every authored article, published or not. */
export const allArticles: BlogArticle[] = [
  whyConstructionClaimsFail,
  sevenRecordsEveryEotClaimNeeds,
  excelRegistersVersusConnectedRecords,
  howToBuildDefensibleProjectChronology,
  whatAiShouldAndShouldNotDo,
];

/** Every authored video, published or not. */
export const allVideos: BlogVideo[] = blogVideos;

export const BLOG_BASE_PATH = "/blog";

/** Newest first; editorial order breaks ties within a publication date. */
function byPublishedDesc(
  a: { publishedAt: string; order: number },
  b: { publishedAt: string; order: number },
): number {
  if (a.publishedAt !== b.publishedAt) {
    return a.publishedAt < b.publishedAt ? 1 : -1;
  }
  return a.order - b.order;
}

function resolveArticle(article: BlogArticle): ResolvedArticle {
  const blocks = parseMarkdown(article.body);
  return { ...article, blocks, readingTime: estimateReadingMinutes(blocks) };
}

function resolveVideo(video: BlogVideo): ResolvedVideo | null {
  const embed = toEmbed(video.videoUrl);
  // A video whose URL fails provider validation is not publishable. Dropping
  // it here means an untrusted URL can never reach the player.
  if (!embed) return null;
  return {
    ...video,
    embed,
    transcriptBlocks: video.transcript ? parseMarkdown(video.transcript) : [],
  };
}

export const publishedArticles: ResolvedArticle[] = allArticles
  .filter((article) => article.status === "published")
  .map(resolveArticle)
  .sort(byPublishedDesc);

export const publishedVideos: ResolvedVideo[] = allVideos
  .filter((video) => video.status === "published")
  .map(resolveVideo)
  .filter((video): video is ResolvedVideo => video !== null)
  .sort(byPublishedDesc);

/** Videos that were authored as published but rejected by URL validation. */
export const rejectedVideos: BlogVideo[] = allVideos.filter(
  (video) => video.status === "published" && toEmbed(video.videoUrl) === null,
);

export function getEntries(type: BlogContentType): ResolvedEntry[] {
  return type === "videos" ? publishedVideos : publishedArticles;
}

export function getArticleBySlug(slug: string): ResolvedArticle | undefined {
  return publishedArticles.find((article) => article.slug === slug);
}

export function getVideoBySlug(slug: string): ResolvedVideo | undefined {
  return publishedVideos.find((video) => video.slug === slug);
}

export function getEntryPath(entry: { type: string; slug: string }): string {
  return entry.type === "video"
    ? `${BLOG_BASE_PATH}/videos/${entry.slug}`
    : `${BLOG_BASE_PATH}/articles/${entry.slug}`;
}

/** Categories that actually have published content, in taxonomy order. */
export function getUsedCategories(type: BlogContentType): BlogCategoryId[] {
  const used = new Set(getEntries(type).map((entry) => entry.category));
  return [...used];
}

/**
 * Adjacent articles in reading order (oldest first), for previous/next
 * navigation on an article page.
 */
export function getAdjacentArticles(slug: string): {
  previous?: ResolvedArticle;
  next?: ResolvedArticle;
} {
  const ordered = [...publishedArticles].sort((a, b) => a.order - b.order);
  const index = ordered.findIndex((article) => article.slug === slug);
  if (index === -1) return {};
  return { previous: ordered[index - 1], next: ordered[index + 1] };
}

/**
 * Related articles: explicit editorial cross-links first, then same-category
 * articles, then the most recent remaining ones.
 */
export function getRelatedArticles(
  slug: string,
  limit = 3,
): ResolvedArticle[] {
  const source = getArticleBySlug(slug);
  if (!source) return [];

  const bySlug = new Map(publishedArticles.map((a) => [a.slug, a]));
  const picked: ResolvedArticle[] = [];
  const seen = new Set<string>([slug]);

  const take = (article: ResolvedArticle | undefined) => {
    if (!article || seen.has(article.slug) || picked.length >= limit) return;
    seen.add(article.slug);
    picked.push(article);
  };

  source.relatedSlugs?.forEach((related) => take(bySlug.get(related)));
  publishedArticles
    .filter((article) => article.category === source.category)
    .forEach(take);
  publishedArticles.forEach(take);

  return picked;
}

/**
 * Development-time content invariants. Called from the registry test so a
 * malformed entry fails the suite instead of shipping.
 */
export function validateContent(): string[] {
  const problems: string[] = [];
  const slugs = new Set<string>();
  const ids = new Set<string>();
  const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const datePattern = /^\d{4}-\d{2}-\d{2}$/;

  const entries = [...allArticles, ...allVideos];
  const publishedSlugs = new Set(
    entries.filter((e) => e.status === "published").map((e) => e.slug),
  );

  for (const entry of entries) {
    const where = `${entry.type} "${entry.slug}"`;
    if (slugs.has(entry.slug)) problems.push(`duplicate slug: ${entry.slug}`);
    slugs.add(entry.slug);
    if (ids.has(entry.id)) problems.push(`duplicate id: ${entry.id}`);
    ids.add(entry.id);

    if (!slugPattern.test(entry.slug)) problems.push(`${where}: slug is not URL-friendly`);
    if (!datePattern.test(entry.publishedAt)) problems.push(`${where}: publishedAt must be YYYY-MM-DD`);
    if (!isBlogCategoryId(entry.category)) problems.push(`${where}: unknown category "${entry.category}"`);
    if (!entry.title.trim()) problems.push(`${where}: missing title`);
    if (!entry.excerpt.trim()) problems.push(`${where}: missing excerpt`);
    if (!entry.author.trim()) problems.push(`${where}: missing author`);
    if (!entry.seoTitle.trim()) problems.push(`${where}: missing seoTitle`);
    if (!entry.metaDescription.trim()) problems.push(`${where}: missing metaDescription`);
    if (entry.metaDescription.length > 200) problems.push(`${where}: metaDescription is over 200 characters`);

    for (const related of entry.relatedSlugs ?? []) {
      if (related === entry.slug) problems.push(`${where}: relatedSlugs references itself`);
      else if (!publishedSlugs.has(related)) problems.push(`${where}: relatedSlugs references unpublished "${related}"`);
    }

    if (entry.type === "article" && !entry.body.trim()) {
      problems.push(`${where}: empty body`);
    }
    if (entry.type === "video" && entry.status === "published" && !toEmbed(entry.videoUrl)) {
      problems.push(`${where}: videoUrl is not an allowed provider URL`);
    }
  }

  return problems;
}

export * from "./types";
export { BLOG_CATEGORIES, getCategory, getCategoryLabel, isBlogCategoryId } from "./categories";
export { toEmbed, isAllowedVideoUrl, ALLOWED_VIDEO_HOSTS } from "./video-embed";
