import type { ReactNode } from "react";

import LandingFooter from "@/components/landing/LandingFooter";
import LandingHeader from "@/components/landing/LandingHeader";

/**
 * Public shell for every blog page: the same header, footer, typography and
 * brand tokens as the landing page, so the blog reads as part of the site
 * rather than a bolt-on.
 */
export function BlogLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen overflow-x-hidden bg-paper font-franklin text-ink antialiased">
      <LandingHeader />
      <a href="#blog-main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-md focus:bg-white focus:px-4 focus:py-2 focus:text-ink focus:shadow-lg">
        Skip to main content
      </a>
      <main id="blog-main">{children}</main>
      <LandingFooter />
    </div>
  );
}

export default BlogLayout;
