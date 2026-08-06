import { useEffect } from "react";

import {
  DEFAULT_SOCIAL_IMAGE,
  SITE_NAME,
  absoluteUrl,
} from "@/config/site";
import { isIndexablePublicPath } from "@/config/publicRoutes";

/**
 * Document-head manager for public pages.
 *
 * The app has no head-management library and does not need one: this sets
 * title, description, canonical, Open Graph, Twitter Card and JSON-LD on
 * mount, and restores whatever was there before on unmount. Tags it created
 * are removed; tags it only edited are reverted to their previous value, so
 * navigating away from the blog leaves the landing page's static metadata
 * intact.
 *
 * Public routes are also emitted as build-time HTML snapshots, so these same
 * values are available before JavaScript runs. This component keeps the head
 * correct after client-side navigation.
 */

const MANAGED_ATTR = "data-seo-managed";

export type SeoImage = { url: string; alt?: string };

export interface SeoProps {
  title: string;
  description: string;
  /** Site-relative path, e.g. "/blog/articles/my-post". */
  canonicalPath?: string;
  ogType?: "website" | "article" | "video.other";
  /** Defaults to `description`. */
  ogDescription?: string;
  image?: SeoImage;
  /** Structured data. Rendered as one or more application/ld+json scripts. */
  jsonLd?: Record<string, unknown> | Record<string, unknown>[];
  noIndex?: boolean;
  publishedTime?: string;
  modifiedTime?: string;
}

type Restore = () => void;

function setMeta(
  selectorAttr: "name" | "property",
  key: string,
  content: string,
): Restore {
  const selector = `meta[${selectorAttr}="${key}"]`;
  const existing = document.head.querySelector<HTMLMetaElement>(selector);

  if (existing) {
    const previous = existing.getAttribute("content");
    existing.setAttribute("content", content);
    return () => {
      if (previous === null) existing.removeAttribute("content");
      else existing.setAttribute("content", previous);
    };
  }

  const created = document.createElement("meta");
  created.setAttribute(selectorAttr, key);
  created.setAttribute("content", content);
  created.setAttribute(MANAGED_ATTR, "true");
  document.head.appendChild(created);
  return () => created.remove();
}

function setCanonical(href: string): Restore {
  const existing = document.head.querySelector<HTMLLinkElement>(
    'link[rel="canonical"]',
  );

  if (existing) {
    const previous = existing.getAttribute("href");
    existing.setAttribute("href", href);
    return () => {
      if (previous === null) existing.removeAttribute("href");
      else existing.setAttribute("href", previous);
    };
  }

  const created = document.createElement("link");
  created.setAttribute("rel", "canonical");
  created.setAttribute("href", href);
  created.setAttribute(MANAGED_ATTR, "true");
  document.head.appendChild(created);
  return () => created.remove();
}

/**
 * Take the site-level structured data in index.html out of play while a
 * managed page is mounted.
 *
 * That block describes ContraClaim as a SoftwareApplication, which is right
 * for the landing page and wrong for an article: leaving it in would have a
 * blog post declare itself a software product. The node is detached and put
 * back in the same position on cleanup.
 */
function suppressStaticJsonLd(): Restore {
  const statics = Array.from(
    document.head.querySelectorAll<HTMLScriptElement>(
      `script[type="application/ld+json"]:not([${MANAGED_ATTR}])`,
    ),
  ).map((node) => ({ node, nextSibling: node.nextSibling }));

  statics.forEach(({ node }) => node.remove());

  return () => {
    statics.forEach(({ node, nextSibling }) => {
      if (nextSibling && nextSibling.parentNode === document.head) {
        document.head.insertBefore(node, nextSibling);
      } else {
        document.head.appendChild(node);
      }
    });
  };
}

function addJsonLd(data: Record<string, unknown>): Restore {
  const script = document.createElement("script");
  script.type = "application/ld+json";
  script.setAttribute(MANAGED_ATTR, "true");
  // JSON.stringify output is assigned as text content, never parsed as HTML.
  script.textContent = JSON.stringify(data);
  document.head.appendChild(script);
  return () => script.remove();
}

export function Seo({
  title,
  description,
  canonicalPath,
  ogType = "website",
  ogDescription,
  image,
  jsonLd,
  noIndex = false,
  publishedTime,
  modifiedTime,
}: SeoProps) {
  const socialDescription = ogDescription ?? description;
  const imageUrl = absoluteUrl(image?.url ?? DEFAULT_SOCIAL_IMAGE);
  const imageAlt = image?.alt ?? SITE_NAME;
  const canonicalUrl = canonicalPath ? absoluteUrl(canonicalPath) : "";
  const jsonLdKey = jsonLd ? JSON.stringify(jsonLd) : "";

  useEffect(() => {
    const previousTitle = document.title;
    document.title = title;
    const mayIndex =
      !noIndex &&
      window.location.hostname === "web.contraclaim.com" &&
      isIndexablePublicPath(window.location.pathname);

    const restores: Restore[] = [
      setMeta("name", "description", description),
      setMeta("name", "robots", mayIndex ? "index, follow" : "noindex, nofollow"),
      setMeta("property", "og:type", ogType),
      setMeta("property", "og:site_name", SITE_NAME),
      setMeta("property", "og:title", title),
      setMeta("property", "og:description", socialDescription),
      setMeta("property", "og:image", imageUrl),
      setMeta("property", "og:image:alt", imageAlt),
      setMeta("name", "twitter:card", "summary_large_image"),
      setMeta("name", "twitter:title", title),
      setMeta("name", "twitter:description", socialDescription),
      setMeta("name", "twitter:image", imageUrl),
      setMeta("name", "twitter:image:alt", imageAlt),
    ];

    if (canonicalUrl) {
      restores.push(setCanonical(canonicalUrl));
      restores.push(setMeta("property", "og:url", canonicalUrl));
    }
    if (publishedTime) {
      restores.push(setMeta("property", "article:published_time", publishedTime));
    }
    if (modifiedTime) {
      restores.push(setMeta("property", "article:modified_time", modifiedTime));
    }
    if (jsonLdKey) {
      restores.push(suppressStaticJsonLd());
      const parsed = JSON.parse(jsonLdKey) as
        | Record<string, unknown>
        | Record<string, unknown>[];
      const documents = Array.isArray(parsed) ? parsed : [parsed];
      documents.forEach((doc) => restores.push(addJsonLd(doc)));
    }

    return () => {
      document.title = previousTitle;
      // Reverse order so edits to shared tags unwind cleanly.
      restores.reverse().forEach((restore) => restore());
    };
  }, [
    title,
    description,
    canonicalUrl,
    ogType,
    socialDescription,
    imageUrl,
    imageAlt,
    noIndex,
    publishedTime,
    modifiedTime,
    jsonLdKey,
  ]);

  return null;
}

export default Seo;
