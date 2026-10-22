import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Pagination for the blog listing.
 *
 * Buttons rather than links: page state lives in the query string and is
 * applied through the router, and a disabled anchor is not a real control.
 * Previous/Next are genuinely `disabled` at the bounds, so neither pointer nor
 * keyboard can navigate past the available pages.
 */
export function BlogPagination({
  page,
  pageCount,
  onPageChange,
  label = "Blog pages",
}: {
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
  label?: string;
}) {
  if (pageCount <= 1) return null;

  const pages = Array.from({ length: pageCount }, (_, index) => index + 1);
  const atStart = page <= 1;
  const atEnd = page >= pageCount;

  const controlClass =
    "inline-flex h-10 items-center gap-1.5 rounded-full border border-ink/15 px-4 text-sm font-semibold text-ink transition hover:border-brand hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-ink/15 disabled:hover:text-ink";

  return (
    <nav aria-label={label} className="mt-12 flex flex-col items-center gap-4">
      <p className="text-sm text-ink/55" aria-live="polite">
        Page {page} of {pageCount}
      </p>

      <div className="flex flex-wrap items-center justify-center gap-2">
        <button
          type="button"
          className={controlClass}
          onClick={() => onPageChange(page - 1)}
          disabled={atStart}
          aria-label="Go to previous page"
        >
          <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          Previous
        </button>

        <ol className="flex items-center gap-1.5">
          {pages.map((value) => {
            const current = value === page;
            return (
              <li key={value}>
                <button
                  type="button"
                  onClick={() => onPageChange(value)}
                  aria-label={`Go to page ${value}`}
                  aria-current={current ? "page" : undefined}
                  className={cn(
                    "inline-flex h-10 w-10 items-center justify-center rounded-full text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2",
                    current
                      ? "bg-brand text-white"
                      : "border border-ink/15 text-ink hover:border-brand hover:text-brand",
                  )}
                >
                  {value}
                </button>
              </li>
            );
          })}
        </ol>

        <button
          type="button"
          className={controlClass}
          onClick={() => onPageChange(page + 1)}
          disabled={atEnd}
          aria-label="Go to next page"
        >
          Next
          <ChevronRight className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </nav>
  );
}

export default BlogPagination;
