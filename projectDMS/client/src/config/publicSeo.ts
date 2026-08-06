import {
  getArticleBySlug,
  getEntryPath,
  getVideoBySlug,
  publishedArticles,
  publishedVideos,
} from "@/content/blog";
import { getCategoryLabel } from "@/content/blog/categories";

import {
  DEFAULT_SOCIAL_IMAGE,
  SITE_NAME,
  SITE_ORIGIN,
  SITE_PUBLISHER,
  absoluteUrl,
} from "./site";

export const HOME_TITLE =
  "Construction Claims Management Software | ContraClaim";
export const HOME_DESCRIPTION =
  "Manage construction contracts, claims, correspondence, variations, project records and AI-assisted drafting in one secure, connected ContraClaim DMS platform.";

export const BLOG_TITLE =
  "Construction Contract and Claims Insights | ContraClaim";
export const BLOG_DESCRIPTION =
  "Practical guidance on construction contract administration, extension-of-time evidence, project records, chronology building and responsible AI drafting.";

export const LANDING_FAQS = [
  {
    q: "What is construction claims management software?",
    a: "Construction claims management software keeps the contract, notices, correspondence, evidence, deadlines and claim records connected so teams can trace each position back to the contemporaneous project record.",
  },
  {
    q: "How does ContraClaim connect contractual records?",
    a: "ContraClaim links contracts, clauses, correspondence, exhibits, claims and project records within the correct organisation and project scope. Search and contract Q&A cite the source document, clause and page.",
  },
  {
    q: "Can ContraClaim support extension-of-time claims?",
    a: "Yes. Teams can maintain EOT claim records, notices, programme and delay evidence, response deadlines and contractual time-bars alongside the documents that support the entitlement.",
  },
  {
    q: "Can ContraClaim manage variations and contractual correspondence?",
    a: "Yes. Variation and other claim records sit alongside a governed contractual correspondence workflow covering input, strategy, drafting, review, approval and completion.",
  },
  {
    q: "Does ContraClaim use AI for contractual drafting?",
    a: "ContraClaim can assist with source-grounded drafting and contract retrieval. AI-generated content remains subject to human review and approval before it is treated as outgoing contractual correspondence.",
  },
  {
    q: "Does ContraClaim replace professional contractual review?",
    a: "No. ContraClaim organises the record and assists retrieval and drafting; it does not replace the judgement of the authorised contract, legal or commercial professionals responsible for the final position.",
  },
  {
    q: "How is organisation and project data protected?",
    a: "Data access is scoped by organisation and project, enforced through role-based permissions and controlled downloads. Uploaded files are virus-scanned and access activity is recorded for traceability.",
  },
  {
    q: "Can teams control access by organisation and project?",
    a: "Yes. Users, roles, documents, folders, downloads and project workspaces are permission-aware, with organisation and project scope applied to the records a user can retrieve or manage.",
  },
] as const;

export type PublicOgType = "website" | "article" | "video.other";

export interface PublicPageSeo {
  path: string;
  title: string;
  description: string;
  ogType: PublicOgType;
  image: { url: string; alt: string };
  jsonLd: Record<string, unknown>[];
  lastmod?: string;
  publishedTime?: string;
  modifiedTime?: string;
}

const publisher = {
  "@type": "Organization",
  name: SITE_PUBLISHER,
  url: SITE_ORIGIN,
  logo: {
    "@type": "ImageObject",
    url: absoluteUrl("/contraclaim2.png"),
  },
};

function breadcrumb(items: { name: string; path: string }[]) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map((item, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: item.name,
      item: absoluteUrl(item.path),
    })),
  };
}

export const HOME_SEO: PublicPageSeo = {
  path: "/",
  title: HOME_TITLE,
  description: HOME_DESCRIPTION,
  ogType: "website",
  image: {
    url: DEFAULT_SOCIAL_IMAGE,
    alt: "ContraClaim construction contract and claims management workspace",
  },
  jsonLd: [
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      name: SITE_PUBLISHER,
      url: SITE_ORIGIN,
      logo: absoluteUrl("/contraclaim2.png"),
    },
    {
      "@context": "https://schema.org",
      "@type": "WebSite",
      name: SITE_NAME,
      url: SITE_ORIGIN,
      description: HOME_DESCRIPTION,
      publisher,
    },
    {
      "@context": "https://schema.org",
      "@type": "SoftwareApplication",
      name: SITE_NAME,
      applicationCategory: "BusinessApplication",
      operatingSystem: "Web",
      url: SITE_ORIGIN,
      description: HOME_DESCRIPTION,
      publisher,
      featureList: [
        "Contract and clause retrieval",
        "Claims and variation records",
        "Extension-of-time record management",
        "Project chronology",
        "Governed contractual drafting",
        "Role-based organisation and project access",
      ],
    },
    {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: LANDING_FAQS.map((item) => ({
        "@type": "Question",
        name: item.q,
        acceptedAnswer: { "@type": "Answer", text: item.a },
      })),
    },
  ],
};

export const BLOG_SEO: PublicPageSeo = {
  path: "/blog",
  title: BLOG_TITLE,
  description: BLOG_DESCRIPTION,
  ogType: "website",
  image: {
    url: DEFAULT_SOCIAL_IMAGE,
    alt: "ContraClaim construction contract and claims insights",
  },
  jsonLd: [
    {
      "@context": "https://schema.org",
      "@type": "Blog",
      name: BLOG_TITLE,
      description: BLOG_DESCRIPTION,
      url: absoluteUrl("/blog"),
      publisher,
      blogPost: publishedArticles.map((article) => ({
        "@type": "BlogPosting",
        headline: article.title,
        url: absoluteUrl(getEntryPath(article)),
        datePublished: article.publishedAt,
        dateModified: article.updatedAt ?? article.publishedAt,
        author: { "@type": "Organization", name: article.author },
      })),
    },
    breadcrumb([
      { name: "Home", path: "/" },
      { name: "Blog", path: "/blog" },
    ]),
  ],
};

function articleSeo(slug: string): PublicPageSeo | undefined {
  const article = getArticleBySlug(slug);
  if (!article) return undefined;
  const path = getEntryPath(article);
  const modified = article.updatedAt ?? article.publishedAt;

  return {
    path,
    title: article.seoTitle,
    description: article.metaDescription,
    ogType: "article",
    image: {
      url: article.featuredImageUrl ?? DEFAULT_SOCIAL_IMAGE,
      alt:
        article.featuredImageAlt ??
        `ContraClaim insight: ${article.title}`,
    },
    lastmod: modified,
    publishedTime: article.publishedAt,
    modifiedTime: modified,
    jsonLd: [
      {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        headline: article.title,
        description: article.metaDescription,
        datePublished: article.publishedAt,
        dateModified: modified,
        author: { "@type": "Organization", name: article.author },
        publisher,
        mainEntityOfPage: { "@type": "WebPage", "@id": absoluteUrl(path) },
        image: absoluteUrl(article.featuredImageUrl ?? DEFAULT_SOCIAL_IMAGE),
        keywords: article.tags.join(", "),
        articleSection: getCategoryLabel(article.category),
      },
      breadcrumb([
        { name: "Home", path: "/" },
        { name: "Blog", path: "/blog" },
        { name: article.title, path },
      ]),
    ],
  };
}

function videoSeo(slug: string): PublicPageSeo | undefined {
  const video = getVideoBySlug(slug);
  if (!video) return undefined;
  const path = getEntryPath(video);
  const modified = video.updatedAt ?? video.publishedAt;

  return {
    path,
    title: video.seoTitle,
    description: video.metaDescription,
    ogType: "video.other",
    image: {
      url: video.thumbnailUrl ?? video.featuredImageUrl ?? DEFAULT_SOCIAL_IMAGE,
      alt: video.thumbnailAlt ?? video.featuredImageAlt ?? video.title,
    },
    lastmod: modified,
    publishedTime: video.publishedAt,
    modifiedTime: modified,
    jsonLd: [
      {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        name: video.title,
        description: video.metaDescription,
        uploadDate: video.publishedAt,
        thumbnailUrl: absoluteUrl(
          video.thumbnailUrl ?? video.featuredImageUrl ?? DEFAULT_SOCIAL_IMAGE,
        ),
        ...(video.isoDuration ? { duration: video.isoDuration } : {}),
        embedUrl: video.embed.embedUrl,
        contentUrl: video.embed.watchUrl,
        publisher,
      },
      breadcrumb([
        { name: "Home", path: "/" },
        { name: "Blog", path: "/blog" },
        { name: video.title, path },
      ]),
    ],
  };
}

function normalizedPath(pathname: string): string {
  const path = pathname.split(/[?#]/, 1)[0] || "/";
  return path.length > 1 ? path.replace(/\/+$/, "") : path;
}

export function getPublicPageSeo(pathname: string): PublicPageSeo | undefined {
  const path = normalizedPath(pathname);
  if (path === "/") return HOME_SEO;
  if (path === "/blog") return BLOG_SEO;

  const articleMatch = path.match(/^\/blog\/articles\/([^/]+)$/);
  if (articleMatch) return articleSeo(articleMatch[1]);
  const videoMatch = path.match(/^\/blog\/videos\/([^/]+)$/);
  if (videoMatch) return videoSeo(videoMatch[1]);
  return undefined;
}

export function getIndexablePublicPages(): PublicPageSeo[] {
  return [
    HOME_SEO,
    BLOG_SEO,
    ...publishedArticles.flatMap((article) => {
      const page = articleSeo(article.slug);
      return page ? [page] : [];
    }),
    ...publishedVideos.flatMap((video) => {
      const page = videoSeo(video.slug);
      return page ? [page] : [];
    }),
  ];
}

export function isIndexablePublicPath(pathname: string): boolean {
  return Boolean(getPublicPageSeo(pathname));
}
