import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Search, X } from "lucide-react";

import Seo from "@/components/seo/Seo";
import BlogCard from "@/components/blog/BlogCard";
import BlogCta from "@/components/blog/BlogCta";
import BlogLayout from "@/components/blog/BlogLayout";
import BlogPagination from "@/components/blog/BlogPagination";
import { usePageSize } from "@/components/blog/usePageSize";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { getEntries, getUsedCategories } from "@/content/blog";
import { getCategoryLabel, isBlogCategoryId } from "@/content/blog/categories";
import type {
  BlogCategoryId,
  BlogContentType,
  ResolvedEntry,
} from "@/content/blog/types";
import { SITE_NAME, SITE_ORIGIN, absoluteUrl } from "@/config/site";
import { cn } from "@/lib/utils";

const PAGE_TITLE = "Construction Contract and Claims Insights";
const PAGE_INTRO =
  "Practical guidance on contract administration, extension-of-time evidence, project records and the responsible use of AI in contractual work — written for construction and infrastructure teams working under FIDIC-style and similar contracts.";

const TABS: { value: BlogContentType; label: string }[] = [
  { value: "articles", label: "Articles" },
  { value: "videos", label: "Videos" },
];

function parseType(value: string | null): BlogContentType {
  return value === "videos" ? "videos" : "articles";
}

/** Case-insensitive match across title, excerpt, tags and category. */
function matchesQuery(entry: ResolvedEntry, query: string): boolean {
  if (!query) return true;
  const needle = query.toLowerCase();
  return [
    entry.title,
    entry.excerpt,
    getCategoryLabel(entry.category),
    ...entry.tags,
  ].some((field) => field.toLowerCase().includes(needle));
}

export function BlogPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const pageSize = usePageSize();
  const resultsHeadingRef = useRef<HTMLHeadingElement>(null);
  const shouldFocusResults = useRef(false);

  const type = parseType(searchParams.get("type"));
  const rawCategory = searchParams.get("category");
  const category: BlogCategoryId | null =
    rawCategory && isBlogCategoryId(rawCategory) ? rawCategory : null;
  const query = searchParams.get("q") ?? "";

  const [searchInput, setSearchInput] = useState(query);
  useEffect(() => setSearchInput(query), [query]);

  const allEntries = useMemo(() => getEntries(type), [type]);
  const availableCategories = useMemo(() => getUsedCategories(type), [type]);

  const filtered = useMemo(
    () =>
      allEntries.filter(
        (entry) =>
          (!category || entry.category === category) &&
          matchesQuery(entry, query),
      ),
    [allEntries, category, query],
  );

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));

  // An absent, non-numeric or out-of-range `page` resolves to the nearest
  // valid page rather than rendering an empty grid.
  const rawPage = Number.parseInt(searchParams.get("page") ?? "", 10);
  const page = Number.isFinite(rawPage)
    ? Math.min(Math.max(rawPage, 1), pageCount)
    : 1;

  const updateParams = useCallback(
    (
      changes: Record<string, string | null>,
      options?: { replace?: boolean },
    ) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          Object.entries(changes).forEach(([key, value]) => {
            if (value === null || value === "") next.delete(key);
            else next.set(key, value);
          });
          return next;
        },
        { replace: options?.replace ?? false },
      );
    },
    [setSearchParams],
  );

  // Keep the URL honest: if the requested page was invalid or the page size
  // changed under a resize, rewrite it to the page actually being shown.
  useEffect(() => {
    const current = searchParams.get("page");
    const resolved = page === 1 ? null : String(page);
    if ((current ?? null) !== resolved) {
      updateParams({ page: resolved }, { replace: true });
    }
  }, [page, searchParams, updateParams]);

  // Move focus to the results heading after a page change so keyboard and
  // screen-reader users land on the new content instead of the page top.
  useEffect(() => {
    if (!shouldFocusResults.current) return;
    shouldFocusResults.current = false;

    const heading = resultsHeadingRef.current;
    if (!heading) return;

    heading.focus({ preventScroll: true });

    // Not implemented in every environment (jsdom, for one); focus alone is
    // the accessibility-critical half, so scrolling is best-effort.
    if (typeof heading.scrollIntoView !== "function") return;
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    heading.scrollIntoView({
      behavior: prefersReducedMotion ? "auto" : "smooth",
      block: "start",
    });
  }, [page, type, category, query]);

  const goToPage = (nextPage: number) => {
    shouldFocusResults.current = true;
    updateParams({ page: nextPage === 1 ? null : String(nextPage) });
  };

  const changeType = (nextValue: string) => {
    const nextType = parseType(nextValue);
    if (nextType === type) return;
    // Categories differ per tab; drop one that the new tab cannot show.
    const keepCategory =
      category && getUsedCategories(nextType).includes(category)
        ? category
        : null;
    updateParams({
      type: nextType === "articles" ? null : nextType,
      category: keepCategory,
      page: null,
    });
  };

  const changeCategory = (nextCategory: BlogCategoryId | null) => {
    updateParams({ category: nextCategory, page: null });
  };

  const changeSearch = (value: string) => {
    setSearchInput(value);
    updateParams({ q: value || null, page: null }, { replace: true });
  };

  const hasFilters = Boolean(category) || Boolean(query);
  const start = (page - 1) * pageSize;
  const visible = filtered.slice(start, start + pageSize);
  const typeLabel = type === "videos" ? "Videos" : "Articles";

  const jsonLd = useMemo(
    () => ({
      "@context": "https://schema.org",
      "@type": "Blog",
      name: `${PAGE_TITLE} | ${SITE_NAME}`,
      description: PAGE_INTRO,
      url: `${SITE_ORIGIN}/blog`,
      publisher: { "@type": "Organization", name: "ContraClaim", url: SITE_ORIGIN },
      blogPost: getEntries("articles").map((entry) => ({
        "@type": "BlogPosting",
        headline: entry.title,
        url: absoluteUrl(`/blog/articles/${entry.slug}`),
        datePublished: entry.publishedAt,
        author: { "@type": "Organization", name: entry.author },
      })),
    }),
    [],
  );

  const panel = (
    <>
      <div className="mt-8 flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="relative w-full lg:max-w-sm">
          <Search
            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink/40"
            aria-hidden="true"
          />
          <input
            type="search"
            value={searchInput}
            onChange={(event) => changeSearch(event.target.value)}
            placeholder={`Search ${typeLabel.toLowerCase()}`}
            aria-label={`Search ${typeLabel.toLowerCase()}`}
            className="h-11 w-full rounded-full border border-ink/15 bg-white pl-10 pr-4 text-sm text-ink placeholder:text-ink/40 focus-visible:border-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/30"
          />
        </div>

        {availableCategories.length > 0 && (
          <div
            role="group"
            aria-label="Filter by topic"
            className="flex flex-wrap gap-2"
          >
            <button
              type="button"
              onClick={() => changeCategory(null)}
              aria-pressed={category === null}
              className={cn(
                "rounded-full border px-3.5 py-1.5 text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2",
                category === null
                  ? "border-brand bg-brand text-white"
                  : "border-ink/15 text-ink/70 hover:border-brand hover:text-brand",
              )}
            >
              All topics
            </button>
            {availableCategories.map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => changeCategory(id)}
                aria-pressed={category === id}
                className={cn(
                  "rounded-full border px-3.5 py-1.5 text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2",
                  category === id
                    ? "border-brand bg-brand text-white"
                    : "border-ink/15 text-ink/70 hover:border-brand hover:text-brand",
                )}
              >
                {getCategoryLabel(id)}
              </button>
            ))}
          </div>
        )}
      </div>

      <h2
        ref={resultsHeadingRef}
        tabIndex={-1}
        className="mt-10 scroll-mt-28 font-serif text-2xl font-semibold text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-4"
      >
        {typeLabel}
        <span className="ml-3 align-middle text-sm font-semibold text-ink/45">
          {filtered.length} {filtered.length === 1 ? "entry" : "entries"}
        </span>
      </h2>

      {visible.length > 0 ? (
        <div className="mt-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((entry, index) => (
            <BlogCard key={entry.id} entry={entry} eager={page === 1 && index < 3} />
          ))}
        </div>
      ) : (
        <div className="mt-6 rounded-2xl border border-dashed border-ink/20 bg-white/60 px-6 py-16 text-center">
          <p className="font-serif text-xl font-semibold text-ink">
            {allEntries.length === 0
              ? `No ${typeLabel.toLowerCase()} published yet`
              : `No ${typeLabel.toLowerCase()} match your filters`}
          </p>
          <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-ink/60">
            {allEntries.length === 0
              ? `${typeLabel} will appear here as they are published. In the meantime, the other tab has published content.`
              : "Try a different topic or clear the search to see everything published so far."}
          </p>
          {hasFilters && allEntries.length > 0 && (
            <button
              type="button"
              onClick={() => updateParams({ category: null, q: null, page: null })}
              className="mt-6 inline-flex items-center gap-1.5 rounded-full border border-ink/15 px-4 py-2 text-sm font-semibold text-ink transition hover:border-brand hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2"
            >
              <X className="h-4 w-4" aria-hidden="true" />
              Clear filters
            </button>
          )}
        </div>
      )}

      <BlogPagination
        page={page}
        pageCount={pageCount}
        onPageChange={goToPage}
        label={`${typeLabel} pages`}
      />
    </>
  );

  return (
    <BlogLayout>
      <Seo
        title={`${PAGE_TITLE} | ${SITE_NAME}`}
        description="Practical guidance on construction contract administration, extension-of-time evidence, project records, chronology building and responsible AI in contractual drafting."
        canonicalPath="/blog"
        ogType="website"
        jsonLd={jsonLd}
      />

      <section className="border-b border-ink/10 bg-white">
        <div className="container py-14 md:py-20">
          <p className="text-sm font-bold uppercase tracking-[0.14em] text-brand">
            ContraClaim Insights
          </p>
          <h1 className="mt-4 max-w-4xl font-serif text-4xl font-semibold leading-[1.08] tracking-[-0.02em] text-ink md:text-5xl">
            {PAGE_TITLE}
          </h1>
          <p className="mt-5 max-w-3xl text-lg leading-8 text-ink/60">
            {PAGE_INTRO}
          </p>
        </div>
      </section>

      <section className="container py-12 md:py-16">
        <Tabs value={type} onValueChange={changeType}>
          <TabsList className="grid w-full max-w-sm grid-cols-2">
            {TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>

          {TABS.map((tab) => (
            <TabsContent key={tab.value} value={tab.value} className="mt-0">
              {panel}
            </TabsContent>
          ))}
        </Tabs>

        <BlogCta />
      </section>
    </BlogLayout>
  );
}

export default BlogPage;
