import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Closing call to action.
 *
 * Deliberately restrained and placed after the content rather than inside it,
 * so reading is never interrupted. The wording stays within what the product
 * actually does — connecting records and surfacing gaps — and claims nothing
 * about determining entitlement.
 */
export function BlogCta({
  heading = "See the record behind every decision",
  body = "ContraClaim DMS connects contractual correspondence, notices, programme references and claim issues into one source-linked workspace, so gaps surface while they can still be corrected.",
}: {
  heading?: string;
  body?: string;
}) {
  return (
    <aside className="mt-16 overflow-hidden rounded-2xl border border-brand/15 bg-ink px-7 py-9 text-white md:px-10">
      <h2 className="font-serif text-2xl font-semibold leading-tight text-white md:text-3xl">
        {heading}
      </h2>
      <p className="mt-4 max-w-2xl text-base leading-7 text-white/70">{body}</p>
      <div className="mt-7 flex flex-col gap-3 sm:flex-row">
        <Button
          asChild
          size="lg"
          className="gap-2 rounded-full bg-brand text-white shadow-[0_8px_20px_-6px_rgba(20,102,196,.5)] hover:bg-[#1157a8]"
        >
          <Link to="/#contact">
            Request a demonstration <ArrowRight className="h-5 w-5" />
          </Link>
        </Button>
        <Button
          asChild
          size="lg"
          variant="outline"
          className="rounded-full border-white/25 bg-transparent text-white hover:bg-white hover:text-ink"
        >
          <Link to="/#platform">Explore ContraClaim DMS</Link>
        </Button>
      </div>
    </aside>
  );
}

export default BlogCta;
