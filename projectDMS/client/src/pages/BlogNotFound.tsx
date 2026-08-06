import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import Seo from "@/components/seo/Seo";
import BlogLayout from "@/components/blog/BlogLayout";
import { Button } from "@/components/ui/button";
import { SITE_NAME } from "@/config/site";

/**
 * Shown when a blog slug does not resolve.
 *
 * Rendered inside the blog shell rather than falling through to the generic
 * 404 so the reader keeps the navigation and a direct way back to the index.
 * Marked `noindex` so a mistyped URL never enters the search index.
 */
export function BlogNotFound({
  heading = "That page could not be found",
  body = "The page you are looking for may have been moved or renamed. Everything published so far is listed on the blog.",
}: {
  heading?: string;
  body?: string;
}) {
  return (
    <BlogLayout>
      <Seo
        title={`Not found | ${SITE_NAME}`}
        description={body}
        canonicalPath="/blog"
        noIndex
      />
      <div className="container max-w-2xl py-24 text-center md:py-32">
        <p className="text-sm font-bold uppercase tracking-[0.14em] text-brand">
          404
        </p>
        <h1 className="mt-4 font-serif text-3xl font-semibold text-ink md:text-4xl">
          {heading}
        </h1>
        <p className="mt-5 text-lg leading-8 text-ink/60">{body}</p>
        <Button
          asChild
          size="lg"
          className="mt-8 gap-2 rounded-full bg-brand text-white hover:bg-[#1157a8]"
        >
          <Link to="/blog">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            Back to the blog
          </Link>
        </Button>
      </div>
    </BlogLayout>
  );
}

export default BlogNotFound;
