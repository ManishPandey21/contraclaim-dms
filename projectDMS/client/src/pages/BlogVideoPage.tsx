import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Clock3, ExternalLink } from "lucide-react";

import Seo from "@/components/seo/Seo";
import BlogCard from "@/components/blog/BlogCard";
import BlogCta from "@/components/blog/BlogCta";
import BlogLayout from "@/components/blog/BlogLayout";
import MarkdownContent from "@/components/blog/MarkdownContent";
import ShareLinks from "@/components/blog/ShareLinks";
import { formatBlogDate } from "@/components/blog/format";
import { getVideoBySlug, publishedArticles, publishedVideos } from "@/content/blog";
import { getCategoryLabel } from "@/content/blog/categories";
import { DEFAULT_SOCIAL_IMAGE, SITE_ORIGIN, absoluteUrl } from "@/config/site";

import BlogNotFound from "./BlogNotFound";

export function BlogVideoPage() {
  const { slug = "" } = useParams<{ slug: string }>();
  // Only videos whose URL passed the provider allowlist are published, so
  // reaching this page at all means the embed is already validated.
  const video = getVideoBySlug(slug);

  const jsonLd = useMemo(() => {
    if (!video) return undefined;
    return {
      "@context": "https://schema.org",
      "@type": "VideoObject",
      name: video.title,
      description: video.metaDescription,
      uploadDate: video.publishedAt,
      thumbnailUrl: absoluteUrl(
        video.thumbnailUrl ?? video.featuredImageUrl ?? DEFAULT_SOCIAL_IMAGE,
      ),
      ...(video.isoDuration ? { duration: video.isoDuration } : {}),
      embedUrl: video.embed.embedUrl,
      contentUrl: video.embed.watchUrl,
      publisher: {
        "@type": "Organization",
        name: "ContraClaim",
        url: SITE_ORIGIN,
      },
    };
  }, [video]);

  if (!video) {
    return (
      <BlogNotFound
        heading="That video could not be found"
        body="The video you are looking for may have been moved or renamed. Everything published so far is listed on the blog."
      />
    );
  }

  const canonicalPath = `/blog/videos/${video.slug}`;
  const shareUrl = absoluteUrl(canonicalPath);
  const relatedVideos = publishedVideos.filter((entry) => entry.slug !== video.slug);
  const relatedArticles = publishedArticles.filter(
    (entry) => entry.category === video.category,
  );
  const related = [...relatedVideos, ...relatedArticles].slice(0, 3);

  return (
    <BlogLayout>
      <Seo
        title={video.seoTitle}
        description={video.metaDescription}
        ogDescription={video.ogDescription}
        canonicalPath={canonicalPath}
        ogType="video.other"
        image={
          video.thumbnailUrl
            ? { url: video.thumbnailUrl, alt: video.thumbnailAlt }
            : undefined
        }
        jsonLd={jsonLd}
      />

      <div className="container max-w-4xl py-12 md:py-16">
        <Link
          to="/blog?type=videos"
          className="inline-flex items-center gap-1.5 rounded-md text-sm font-semibold text-brand transition hover:text-[#1157a8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back to the blog
        </Link>

        {/* Fixed 16:9 box: the player scales with the viewport and never
            overflows the page on a phone. No autoplay, by design. */}
        <div className="mt-8 overflow-hidden rounded-2xl border border-ink/10 bg-ink">
          <div className="aspect-video w-full">
            <iframe
              src={video.embed.embedUrl}
              title={`${video.title} — video player`}
              className="h-full w-full"
              loading="lazy"
              referrerPolicy="strict-origin-when-cross-origin"
              allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
              allowFullScreen
            />
          </div>
        </div>

        <p className="mt-5 text-sm font-bold uppercase tracking-[0.14em] text-brand">
          {getCategoryLabel(video.category)}
        </p>
        <h1 className="mt-4 font-serif text-3xl font-semibold leading-[1.12] tracking-[-0.02em] text-ink md:text-4xl">
          {video.title}
        </h1>

        <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm font-semibold text-ink/55">
          <span>{video.author}</span>
          <time dateTime={video.publishedAt}>{formatBlogDate(video.publishedAt)}</time>
          {video.videoDuration && (
            <span className="inline-flex items-center gap-1.5">
              <Clock3 className="h-4 w-4" aria-hidden="true" />
              {video.videoDuration}
            </span>
          )}
          <a
            href={video.embed.watchUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-brand hover:text-[#1157a8]"
          >
            Open on {video.embed.provider === "youtube" ? "YouTube" : "Vimeo"}
            <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
          </a>
        </div>

        <p className="mt-6 text-lg leading-8 text-ink/70">{video.description}</p>

        <ShareLinks className="mt-8" url={shareUrl} title={video.title} />

        {video.transcriptBlocks.length > 0 && (
          <section aria-labelledby="video-transcript" className="mt-12 border-t border-ink/10 pt-8">
            <h2
              id="video-transcript"
              className="font-serif text-2xl font-semibold text-ink"
            >
              Transcript
            </h2>
            <MarkdownContent blocks={video.transcriptBlocks} className="mt-2" />
          </section>
        )}

        <BlogCta />
      </div>

      {related.length > 0 && (
        <section
          aria-labelledby="related-content"
          className="border-t border-ink/10 bg-white py-14 md:py-20"
        >
          <div className="container">
            <h2
              id="related-content"
              className="font-serif text-2xl font-semibold text-ink md:text-3xl"
            >
              Related reading
            </h2>
            <div className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {related.map((entry) => (
                <BlogCard key={entry.id} entry={entry} />
              ))}
            </div>
          </div>
        </section>
      )}
    </BlogLayout>
  );
}

export default BlogVideoPage;
