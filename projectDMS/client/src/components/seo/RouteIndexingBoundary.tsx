import { useEffect } from "react";
import { useLocation } from "react-router-dom";

import { isIndexablePublicPath } from "@/config/publicRoutes";

/**
 * Fail closed whenever client-side navigation crosses the public/private edge.
 *
 * The built application shell is noindex by default and public pages opt in.
 * This boundary keeps that rule true after SPA navigation too, including when
 * a visitor moves from a prerendered article into login or an authenticated
 * DMS route without a full page load.
 */
export function RouteIndexingBoundary() {
  const { pathname } = useLocation();

  useEffect(() => {
    let robots = document.head.querySelector<HTMLMetaElement>(
      'meta[name="robots"]',
    );
    if (!robots) {
      robots = document.createElement("meta");
      robots.name = "robots";
      document.head.appendChild(robots);
    }
    const isCanonicalProductionHost =
      window.location.hostname === "web.contraclaim.com";
    robots.content = isCanonicalProductionHost && isIndexablePublicPath(pathname)
      ? "index, follow"
      : "noindex, nofollow";
  }, [pathname]);

  return null;
}

export default RouteIndexingBoundary;
