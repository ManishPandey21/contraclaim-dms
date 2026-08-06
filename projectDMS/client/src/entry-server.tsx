import { renderToString } from "react-dom/server";
import { Route, Router, Routes } from "react-router-dom";

import { getIndexablePublicPages } from "@/config/publicSeo";
import LandingPage from "@/pages/LandingPage";
import BlogArticlePage from "@/pages/BlogArticlePage";
import BlogNotFound from "@/pages/BlogNotFound";
import BlogPage from "@/pages/BlogPage";
import BlogVideoPage from "@/pages/BlogVideoPage";

const staticNavigator = {
  createHref(to) {
    if (typeof to === "string") return to;
    return `${to.pathname ?? ""}${to.search ?? ""}${to.hash ?? ""}`;
  },
  go() {},
  push() {},
  replace() {},
} satisfies Parameters<typeof Router>[0]["navigator"];

/** Render only approved, bundled public content. Private application routes
 * are intentionally absent so prerendering can never serialize tenant data. */
export function renderPublicPage(pathname: string): string {
  return renderToString(
    <Router location={pathname} navigator={staticNavigator} static>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/blog" element={<BlogPage />} />
        <Route path="/blog/articles/:slug" element={<BlogArticlePage />} />
        <Route path="/blog/videos/:slug" element={<BlogVideoPage />} />
        <Route path="/blog/*" element={<BlogNotFound />} />
      </Routes>
    </Router>,
  );
}

export { getIndexablePublicPages };
