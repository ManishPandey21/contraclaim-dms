import type { ComponentType, ReactNode } from "react";
import { renderToString } from "react-dom/server";
import { Route, Routes } from "react-router-dom";

import { getIndexablePublicPages } from "@/config/publicSeo";
import LandingPage from "@/pages/LandingPage";
import BlogArticlePage from "@/pages/BlogArticlePage";
import BlogNotFound from "@/pages/BlogNotFound";
import BlogPage from "@/pages/BlogPage";
import BlogVideoPage from "@/pages/BlogVideoPage";

type StaticRouterComponent = ComponentType<{
  location: string;
  children?: ReactNode;
}>;

// React Router 6 exposed StaticRouter through react-router-dom/server; v7
// exports it from react-router. Resolve the build-only renderer lazily so the
// prerender pipeline remains compatible while the two repository histories
// converge on the upgraded dependency.
const legacyServerModule = "react-router-dom/server";
const StaticRouter = await import(/* @vite-ignore */ legacyServerModule)
  .then((module) => module.StaticRouter as StaticRouterComponent)
  .catch(async () => {
    const module = await import("react-router");
    return (module as unknown as { StaticRouter: StaticRouterComponent }).StaticRouter;
  });

/** Render only approved, bundled public content. Private application routes
 * are intentionally absent so prerendering can never serialize tenant data. */
export function renderPublicPage(pathname: string): string {
  return renderToString(
    <StaticRouter location={pathname}>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/blog" element={<BlogPage />} />
        <Route path="/blog/articles/:slug" element={<BlogArticlePage />} />
        <Route path="/blog/videos/:slug" element={<BlogVideoPage />} />
        <Route path="/blog/*" element={<BlogNotFound />} />
      </Routes>
    </StaticRouter>,
  );
}

export { getIndexablePublicPages };
