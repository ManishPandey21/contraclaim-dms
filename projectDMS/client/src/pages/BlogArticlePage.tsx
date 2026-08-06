import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, Clock3 } from "lucide-react";

import Seo from "@/components/seo/Seo";
import BlogCard from "@/components/blog/BlogCard";
import BlogCover from "@/components/blog/BlogCover";
import BlogCta from "@/components/blog/BlogCta";
import BlogLayout from "@/components/blog/BlogLayout";
import MarkdownContent from "@/components/blog/MarkdownContent";
import ShareLinks from "@/components/blog/ShareLinks";
import { formatBlogDate, formatReadingTime } from "@/components/blog/format";
import { getAdjacentArticles, getArticleBySlug, getRelatedArticles } from "@/content/blog";
import { getCategoryLabel } from "@/content/blog/categories";
import { absoluteUrl } from "@/config/site";
import { getPublicPageSeo } from "@/config/publicSeo";

import BlogNotFound from "./BlogNotFound";

export function BlogArticlePage() {
  const { slug = "" } = useParams<{ slug: string }>();
  const article = getArticleBySlug(slug);

  if (!article) {
    return (
      <BlogNotFound
        heading="That article could not be found"
        body="The article you are looking for may have been moved or renamed. Everything published so far is listed on the blog."
      />
    );
  }

  const canonicalPath = `/blog/articles/${article.slug}`;
  const pageSeo = getPublicPageSeo(canonicalPath)!;
  const shareUrl = absoluteUrl(canonicalPath);
  const { previous, next } = getAdjacentArticles(article.slug);
  const related = getRelatedArticles(article.slug, 3);

  return (
    <BlogLayout>
      <Seo
        title={pageSeo.title}
        description={pageSeo.description}
        ogDescription={article.ogDescription}
        canonicalPath={pageSeo.path}
        ogType={pageSeo.ogType}
        image={pageSeo.image}
        jsonLd={pageSeo.jsonLd}
        publishedTime={pageSeo.publishedTime}
        modifiedTime={pageSeo.modifiedTime}
      />

      <article>
        <header className="border-b border-ink/10 bg-white">
          <div className="container max-w-4xl py-12 md:py-16">
            <nav aria-label="Breadcrumb" className="mb-5 text-sm text-ink/55">
              <ol className="flex flex-wrap items-center gap-2">
                <li><Link to="/" className="hover:text-brand">Home</Link></li>
                <li aria-hidden="true">/</li>
                <li><Link to="/blog" className="hover:text-brand">Blog</Link></li>
                <li aria-hidden="true">/</li>
                <li aria-current="page" className="max-w-full truncate text-ink/75">{article.title}</li>
              </ol>
            </nav>

            <Link
              to="/blog"
              className="inline-flex items-center gap-1.5 rounded-md text-sm font-semibold text-brand transition hover:text-[#1157a8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2"
            >
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              Back to the blog
            </Link>

            <p className="mt-8 text-sm font-bold uppercase tracking-[0.14em] text-brand">
              {getCategoryLabel(article.category)}
            </p>
            <h1 className="mt-4 font-serif text-3xl font-semibold leading-[1.12] tracking-[-0.02em] text-ink md:text-5xl">
              {article.title}
            </h1>
            <p className="mt-5 text-lg leading-8 text-ink/60">{article.excerpt}</p>

            <div className="mt-7 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm font-semibold text-ink/55">
              <span>{article.author}</span>
              <time dateTime={article.publishedAt}>
                {formatBlogDate(article.publishedAt)}
              </time>
              <span className="inline-flex items-center gap-1.5">
                <Clock3 className="h-4 w-4" aria-hidden="true" />
                {formatReadingTime(article.readingTime)}
              </span>
            </div>

            <ShareLinks className="mt-7" url={shareUrl} title={article.title} />
          </div>
        </header>

        <div className="container max-w-4xl py-10 md:py-14">
          <figure className="overflow-hidden rounded-2xl border border-ink/10 bg-ink">
            <div className="aspect-[16/9] w-full">
              {article.featuredImageUrl ? (
                <img
                  src={article.featuredImageUrl}
                  alt={article.featuredImageAlt ?? ""}
                  loading="eager"
                  decoding="async"
                  className="h-full w-full object-cover"
                />
              ) : (
                <BlogCover
                  category={article.category}
                  title={article.title}
                  className="h-full w-full object-cover"
                />
              )}
            </div>
          </figure>

          <MarkdownContent blocks={article.blocks} className="mt-10" />

          {article.tags.length > 0 && (
            <div className="mt-12 flex flex-wrap items-center gap-2 border-t border-ink/10 pt-8">
              <span className="text-sm font-semibold text-ink/55">Topics</span>
              {article.tags.map((tag) => (
                <span
                  key={tag}
                  className="rounded-full bg-brand-soft px-3 py-1 text-xs font-semibold text-brand"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}

          <ShareLinks className="mt-8" url={shareUrl} title={article.title} />

          {(previous || next) && (
            <nav
              aria-label="Article navigation"
              className="mt-12 grid gap-4 border-t border-ink/10 pt-8 sm:grid-cols-2"
            >
              {previous ? (
                <Link
                  to={`/blog/articles/${previous.slug}`}
                  rel="prev"
                  className="group rounded-xl border border-ink/10 bg-white p-5 transition hover:border-brand/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2"
                >
                  <span className="inline-flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.12em] text-ink/45">
                    <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
                    Previous
                  </span>
                  <span className="mt-2 block font-serif text-lg font-semibold text-ink group-hover:text-brand">
                    {previous.title}
                  </span>
                </Link>
              ) : (
                <span />
              )}
              {next && (
                <Link
                  to={`/blog/articles/${next.slug}`}
                  rel="next"
                  className="group rounded-xl border border-ink/10 bg-white p-5 text-right transition hover:border-brand/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 sm:col-start-2"
                >
                  <span className="inline-flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.12em] text-ink/45">
                    Next
                    <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                  </span>
                  <span className="mt-2 block font-serif text-lg font-semibold text-ink group-hover:text-brand">
                    {next.title}
                  </span>
                </Link>
              )}
            </nav>
          )}

          <BlogCta />
        </div>

        {related.length > 0 && (
          <section
            aria-labelledby="related-articles"
            className="border-t border-ink/10 bg-white py-14 md:py-20"
          >
            <div className="container">
              <h2
                id="related-articles"
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
      </article>
    </BlogLayout>
  );
}

export default BlogArticlePage;
