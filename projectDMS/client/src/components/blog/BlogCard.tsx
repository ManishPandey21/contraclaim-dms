import { Link } from "react-router-dom";
import { ArrowRight, Clock3, PlayCircle } from "lucide-react";

import { getCategoryLabel } from "@/content/blog/categories";
import { getEntryPath } from "@/content/blog";
import type { ResolvedEntry } from "@/content/blog/types";

import BlogCover from "./BlogCover";
import { formatBlogDate, formatReadingTime } from "./format";

/**
 * Card for one blog entry. Handles both articles and videos so the two tabs
 * stay visually consistent and future entry types have one place to extend.
 *
 * `eager` is passed for the first row of cards; everything below the fold is
 * lazily loaded.
 */
export function BlogCard({
  entry,
  eager = false,
}: {
  entry: ResolvedEntry;
  eager?: boolean;
}) {
  const path = getEntryPath(entry);
  const isVideo = entry.type === "video";
  const categoryLabel = getCategoryLabel(entry.category);
  const imageUrl = isVideo
    ? entry.thumbnailUrl ?? entry.featuredImageUrl
    : entry.featuredImageUrl;
  const imageAlt = isVideo
    ? entry.thumbnailAlt ?? entry.featuredImageAlt
    : entry.featuredImageAlt;

  return (
    <article className="group relative flex h-full flex-col overflow-hidden rounded-2xl border border-ink/10 bg-white shadow-sm transition duration-300 hover:-translate-y-1 hover:border-brand/30 hover:shadow-[0_30px_70px_-40px_rgba(13,27,46,.4)] motion-reduce:transform-none motion-reduce:transition-none">
      <div className="relative aspect-[16/9] w-full overflow-hidden bg-ink">
        {imageUrl ? (
          <img
            src={imageUrl}
            alt={imageAlt ?? ""}
            loading={eager ? "eager" : "lazy"}
            decoding="async"
            className="h-full w-full object-cover"
          />
        ) : (
          <BlogCover
            category={entry.category}
            title={entry.title}
            className="h-full w-full object-cover"
          />
        )}
        {isVideo && (
          <>
            <span
              className="absolute inset-0 grid place-items-center bg-ink/25"
              aria-hidden="true"
            >
              <PlayCircle className="h-14 w-14 text-white/90" />
            </span>
            {entry.videoDuration && (
              <span className="absolute bottom-3 right-3 rounded-md bg-ink/85 px-2 py-1 text-xs font-semibold text-white">
                {entry.videoDuration}
              </span>
            )}
          </>
        )}
      </div>

      <div className="flex flex-1 flex-col p-6">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs font-semibold">
          <span className="rounded-full bg-brand-soft px-2.5 py-1 text-brand">
            {categoryLabel}
          </span>
          <time dateTime={entry.publishedAt} className="text-ink/50">
            {formatBlogDate(entry.publishedAt)}
          </time>
          {!isVideo && (
            <span className="inline-flex items-center gap-1 text-ink/50">
              <Clock3 className="h-3.5 w-3.5" aria-hidden="true" />
              {formatReadingTime(entry.readingTime)}
            </span>
          )}
        </div>

        <h3 className="mt-4 font-serif text-xl font-semibold leading-snug text-ink">
          <Link
            to={path}
            className="rounded-sm after:absolute after:inset-0 after:content-[''] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 group-hover:text-brand"
          >
            {entry.title}
          </Link>
        </h3>

        <p className="mt-3 line-clamp-4 text-sm leading-6 text-ink/60">
          {entry.excerpt}
        </p>

        <div className="mt-5 flex items-center justify-between pt-1 text-sm font-semibold text-ink/60">
          <span>{entry.author}</span>
          <span className="inline-flex items-center gap-1.5 text-brand">
            {isVideo ? "Watch video" : "Read article"}
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </span>
        </div>
      </div>
    </article>
  );
}

export default BlogCard;
