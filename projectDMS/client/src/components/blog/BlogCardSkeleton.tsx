/**
 * Placeholder shown while the lazily-loaded blog chunk resolves. Mirrors the
 * card grid so the layout does not jump when real content arrives.
 */
export function BlogCardSkeleton() {
  return (
    <div
      className="overflow-hidden rounded-2xl border border-ink/10 bg-white shadow-sm"
      aria-hidden="true"
    >
      <div className="aspect-[16/9] w-full animate-pulse bg-ink/10 motion-reduce:animate-none" />
      <div className="space-y-3 p-6">
        <div className="h-4 w-28 animate-pulse rounded bg-ink/10 motion-reduce:animate-none" />
        <div className="h-6 w-full animate-pulse rounded bg-ink/10 motion-reduce:animate-none" />
        <div className="h-6 w-3/4 animate-pulse rounded bg-ink/10 motion-reduce:animate-none" />
        <div className="h-4 w-full animate-pulse rounded bg-ink/[0.07] motion-reduce:animate-none" />
        <div className="h-4 w-5/6 animate-pulse rounded bg-ink/[0.07] motion-reduce:animate-none" />
      </div>
    </div>
  );
}

export function BlogListSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div
      className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3"
      role="status"
      aria-label="Loading content"
    >
      {Array.from({ length: count }, (_, index) => (
        <BlogCardSkeleton key={index} />
      ))}
    </div>
  );
}

export default BlogListSkeleton;
