# Public Blog Section — Design

**Date:** 2026-08-06
**Status:** Approved
**Scope:** Add a production-ready public Blog to the ContraClaim DMS landing website and publish five supplied articles.

## 1. Existing implementation reviewed

| Area | Finding |
|---|---|
| Framework | Vite 6 + React 18 SPA, TypeScript, client-rendered (no SSR) |
| Routing | `react-router-dom` v6, all routes declared in `client/src/routes.tsx`, lazy-loaded through `lazyWithRetry` |
| Public surface | `/` (landing) and `/login` only; every other route is wrapped in `ProtectedRoute` |
| Styling | Tailwind + shadcn/ui. Brand tokens `ink #0d1b2e`, `brand #1466c4`, `brand-soft #eaf2fc`, `brand-tint`, `paper #fbfbf9`; fonts `font-franklin` (Libre Franklin) and `font-serif` (Source Serif 4) |
| Landing page | `client/src/LandingPage.tsx` (838 lines) with header and footer inline; navigation is `#anchor` links; **no mobile navigation exists** |
| SEO | Static `<head>` in `client/index.html` only. No head-management library, no `robots.txt`, no sitemap |
| Content | No CMS, no blog content, no markdown renderer dependency |
| Tests | Vitest + Testing Library (`src/**/*.{test,spec}.{ts,tsx}`, serial forks, jsdom); Playwright for e2e |

## 2. Approach

Content is authored as **markdown inside typed TypeScript modules** and rendered through a small in-repo markdown-subset parser that emits a typed block AST, which a React component renders as elements.

Rejected alternatives:

- `react-markdown` + `DOMPurify` — adds runtime dependencies and makes correct sanitiser configuration a permanent obligation.
- HTML strings in a content store — needs a sanitiser *and* introduces a `dangerouslySetInnerHTML` sink.

The chosen approach means **no `dangerouslySetInnerHTML` anywhere in the blog**. Script injection from content is impossible by construction rather than by configuration. No new dependencies are added.

## 3. Content architecture

```
client/src/content/blog/
  types.ts              BlogArticle | BlogVideo | BlogEntry, BlogCategory union
  markdown.ts           markdown subset -> block AST (pure function, unit-tested)
  reading-time.ts       word count -> minutes
  video-embed.ts        provider allowlist + canonical embed URL builder
  categories.ts         taxonomy (id, label, slug)
  articles/*.ts         one module per article: typed metadata + markdown body
  videos/index.ts       published video set (empty at launch)
  index.ts              registry: slug validation, status filter, ordering, lookups
```

Adding a future article is one new file in `articles/` plus one line in the registry array. Page components never change.

**Markdown subset supported:** headings `##`/`###`/`####`, paragraphs, unordered and ordered lists, tables, blockquotes, horizontal rules; inline `**bold**`, `*italic*`, `` `code` ``, `[text](url)`.

**Link safety:** hrefs are scheme-checked. Relative (`/`, `#`) and `http(s)` pass; anything else (`javascript:`, `data:`, …) renders as plain text.

## 4. Routes

| Route | Access | Notes |
|---|---|---|
| `/blog` | public | query state `?type=articles\|videos&page=N&category=<slug>&q=<text>` |
| `/blog/articles/:slug` | public | article detail |
| `/blog/videos/:slug` | public | video detail |

All three sit outside `ProtectedRoute` and are lazy-loaded into their own chunks. Authenticated DMS routes are untouched.

## 5. Shared landing shell

`LandingHeader` and `LandingFooter` are extracted from `LandingPage.tsx` into `client/src/components/landing/` and shared with the blog, so navigation has a single source of truth.

- A **mobile navigation** is added (shadcn `Sheet`); none existed before. It carries the section anchors, Blog, and Sign in.
- Section anchors resolve to `/#platform` when the current route is not the landing page, so they are not dead links from `/blog`.
- The Blog link carries `aria-current="page"` on any `/blog*` route.
- The footer gains a Blog link.

## 6. Behaviour

**Pagination.** 6 items per page at ≥768px, 4 below, resolved through `matchMedia`. Page is clamped when the page size changes. Invalid or out-of-range `page` values clamp and rewrite the URL with `replace`. Changing tab, category or search resets to page 1 and preserves the remaining parameters. After a page change focus moves to the results heading (`tabIndex={-1}`) and the heading is scrolled into view — smoothly unless `prefers-reduced-motion: reduce`.

**Tabs.** Radix `Tabs` provide roving-focus keyboard navigation and correct `role`/`aria-selected`/`aria-controls` wiring. The selected tab is reflected in the URL and preserved across pagination.

**Empty and loading states.** Empty state is shown when a tab, filter or search yields nothing. Loading state is the route-level `Suspense` fallback for the lazy blog chunk, rendered as card skeletons.

## 7. SEO

An in-repo `<Seo>` component sets `document.title`, description, canonical, Open Graph, Twitter Card and JSON-LD on mount and restores the previous values on unmount. Only nodes it created are removed. No new dependency.

- `/blog` — `Blog` schema
- `/blog/articles/:slug` — `Article` schema
- `/blog/videos/:slug` — `VideoObject` schema

`client/public/robots.txt` and `client/public/sitemap.xml` are added. A unit test asserts the sitemap contains an entry for every published slug, so it cannot silently drift.

Canonical origin: `https://web.contraclaim.com` (matches the existing canonical in `index.html`).

**Known limitation.** This is a client-rendered SPA. Search engines that execute JavaScript will index the generated metadata, but social scrapers that do not run JavaScript (LinkedIn, X, WhatsApp) will read the static tags in `index.html` for every blog URL. Fixing that requires prerendering or SSR, which is a build-architecture change outside this scope.

## 8. Video security

`toEmbed(url)` parses with `new URL()`, requires `https:`, and accepts only these hosts:

`youtube.com`, `www.youtube.com`, `youtu.be`, `m.youtube.com`, `vimeo.com`, `www.vimeo.com`, `player.vimeo.com`

It returns a canonical embed URL (`https://www.youtube-nocookie.com/embed/<id>` or `https://player.vimeo.com/video/<id>`) or `null`. Entries whose URL does not validate are dropped from the published set and never reach an iframe.

The player iframe carries a `title`, `loading="lazy"`, `referrerpolicy="strict-origin-when-cross-origin"`, `allowfullscreen`, and **no autoplay parameter**, inside a fixed 16:9 box.

## 9. Images

No third-party stock imagery is sourced (copyright). Featured imagery reuses assets already owned in `client/public/`; every other entry uses `BlogCover`, a deterministic branded inline SVG keyed on category. All covers hold a 16:9 aspect ratio with `object-cover` so nothing distorts. Card images below the fold use `loading="lazy"` and `decoding="async"` with descriptive `alt` text.

`Infrastructure.png` (2.4 MB) is deliberately not used as a card image.

## 10. Content decisions

- `00-editorial-plan.md` is an internal editorial plan, not an article — not published.
- The `## LinkedIn post` section of each source file is separate-channel content and is excluded (requirement 8.5, duplicated content / drafting notes).
- Article bodies otherwise preserve the source verbatim, including the "Sources and further reading" list and the legal disclaimer.
- Byline: **ContraClaim Editorial**.
- `publishedAt` is `2026-08-06` for all five — the actual publication date. Editorial sequence from the plan is carried in `order` and used as the sort tiebreaker. Dates can be adjusted later without code changes.
- Excerpts are contiguous verbatim sentences from each article's opening paragraph; `seoTitle` and `metaDescription` are the authored `meta_title` / `meta_description` from source frontmatter.
- No legal authority, case law, statistic, clause or factual claim is added to any article.

**Taxonomy** (from the editorial plan): Contract Administration, Extension of Time, Claims Evidence, Project Records, Construction Technology, Responsible AI.

**Videos:** the full pipeline is built and tested against fixtures, with **zero published entries** at launch — no video URLs were supplied. The Videos tab renders its empty state until real URLs are added.

## 11. Testing

- markdown parser: headings, lists, tables, blockquotes, inline formatting, unsafe-scheme link rejection
- registry: unique slugs, required SEO fields present, reading time computed, valid categories, sitemap covers every published slug
- `BlogPage`: render, tab switching, tab ARIA attributes, pagination bounds, invalid page clamping, category filter, search, empty state, URL parameter round-trip, page reset on filter change
- `BlogArticlePage`: slug routing, unknown slug, SEO metadata, previous/next navigation, related articles, share links, absence of raw HTML injection
- `BlogVideoPage`: valid embed rendered, unsafe URL rejected, no autoplay
- `LandingHeader`: Blog link in desktop and mobile navigation, active state
- public access: `/blog` renders without a session and does not redirect to `/login`
- Playwright e2e: public access, tab switching and pagination at desktop and mobile viewports

Gates: `tsc --noEmit`, `npm run lint`, full `vitest run`, `npm run build`, and browser verification with desktop and mobile screenshots.
