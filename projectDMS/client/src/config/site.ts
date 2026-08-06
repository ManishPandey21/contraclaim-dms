/**
 * Public site identity used for canonical URLs, Open Graph tags, sitemap
 * entries and structured data.
 *
 * Matches the canonical origin already declared in `index.html`. Changing it
 * here changes every generated blog URL, so keep `public/sitemap.xml` in step
 * (the registry test asserts the sitemap covers every published slug).
 */
export const SITE_ORIGIN = "https://web.contraclaim.com";

export const SITE_NAME = "ContraClaim DMS";

export const SITE_PUBLISHER = "ContraClaim";

/** Fallback social preview image, relative to the site root. */
export const DEFAULT_SOCIAL_IMAGE = "/og-image.jpg";

/** Resolve a site-relative path to an absolute URL. Absolute input passes through. */
export function absoluteUrl(pathOrUrl: string): string {
  if (/^https?:\/\//i.test(pathOrUrl)) return pathOrUrl;
  return `${SITE_ORIGIN}${pathOrUrl.startsWith("/") ? "" : "/"}${pathOrUrl}`;
}
