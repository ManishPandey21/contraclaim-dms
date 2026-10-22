import type { MarkdownBlock } from "./markdown";
import type { VideoEmbed } from "./video-embed";

/**
 * Content model for the public blog.
 *
 * Content lives in typed modules under `content/blog/`, never inside page
 * components, so a new article or video is one new file plus one registry
 * entry. Article bodies are markdown strings parsed into a typed block AST
 * and rendered as React elements — the blog never renders raw HTML.
 */

export type BlogStatus = "draft" | "published";

export type BlogCategoryId =
  | "contract-administration"
  | "extension-of-time"
  | "claims-evidence"
  | "project-records"
  | "construction-technology"
  | "responsible-ai";

export interface BlogCategory {
  id: BlogCategoryId;
  label: string;
  /** Short line used on category chips and filter tooltips. */
  description: string;
}

interface BlogEntryBase {
  id: string;
  title: string;
  slug: string;
  excerpt: string;
  category: BlogCategoryId;
  tags: string[];
  author: string;
  /** ISO-8601 date (YYYY-MM-DD). */
  publishedAt: string;
  updatedAt?: string;
  status: BlogStatus;
  /**
   * Editorial sequence from the series plan. Used only as a deterministic
   * tiebreaker when several entries share a publication date.
   */
  order: number;
  seoTitle: string;
  metaDescription: string;
  /** Falls back to `metaDescription` when omitted. */
  ogDescription?: string;
  /**
   * Site-relative path to an owned image. Entries without one fall back to
   * the branded `BlogCover` graphic.
   */
  featuredImageUrl?: string;
  featuredImageAlt?: string;
  /**
   * Explicit editorial cross-links, most relevant first. The related-content
   * list starts from these and tops up by category and recency, so a partial
   * list is fine.
   */
  relatedSlugs?: string[];
}

export interface BlogArticle extends BlogEntryBase {
  type: "article";
  /** Markdown body. Parsed at registry build time; never rendered as HTML. */
  body: string;
}

export interface BlogVideo extends BlogEntryBase {
  type: "video";
  /** Watch URL. Validated against the provider allowlist before publication. */
  videoUrl: string;
  description: string;
  /** Human-readable runtime, e.g. "12:45". */
  videoDuration?: string;
  /** ISO-8601 duration for VideoObject schema, e.g. "PT12M45S". */
  isoDuration?: string;
  thumbnailUrl?: string;
  thumbnailAlt?: string;
  /** Optional markdown transcript or extended summary. */
  transcript?: string;
}

export type BlogEntry = BlogArticle | BlogVideo;

/** An article after parsing and derivation. This is what pages consume. */
export interface ResolvedArticle extends BlogArticle {
  blocks: MarkdownBlock[];
  /** Estimated reading time in whole minutes, minimum 1. */
  readingTime: number;
}

/** A video after embed validation. This is what pages consume. */
export interface ResolvedVideo extends BlogVideo {
  embed: VideoEmbed;
  transcriptBlocks: MarkdownBlock[];
}

export type ResolvedEntry = ResolvedArticle | ResolvedVideo;

export type BlogContentType = "articles" | "videos";
