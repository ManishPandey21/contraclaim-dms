# ContraClaim public-site SEO audit and implementation report

Date: 2026-08-06
Preferred production origin: `https://web.contraclaim.com`
Framework: React 18, React Router, Vite, TypeScript

## Executive summary

The public surface currently consists of the landing page, the blog index, and five published articles. There are no separate public pricing, about, legal, policy, contact, feature, or published video pages in the active router. Product capabilities and the demonstration form are sections of the landing page. Login and all DMS routes remain outside the indexable allow-list.

The principal technical defect was that every URL received the same client-only HTML shell. Critical content, page-specific metadata, canonicals, and structured data were therefore unavailable in the initial response. The production build now statically prerenders exactly seven approved public URLs, while a separate fail-closed application shell serves authenticated and unknown routes. The public route allow-list, metadata registry, sitemap, and published blog registry are tested together to prevent drift.

This work does not claim a numeric Lighthouse improvement or completed visual QA. The in-app browser could not access the local validation server because its admin-enforced localhost policy could not be verified. Build, source, HTTP, metadata, sitemap, link, heading, and security-boundary checks completed; desktop/mobile screenshots, interaction-level accessibility testing, and Lighthouse remain explicit production-gate items.

## Page-by-page audit

| Page/Route | Issue | SEO Impact | Priority | Recommended Fix | Implementation Status |
|---|---|---:|---|---|---|
| All public routes | Initial HTML was a shared empty SPA shell; crawlers depended on JavaScript | Critical content and metadata could be delayed or missed | Critical | Prerender approved public routes and hydrate the static markup | Implemented and build-verified |
| All non-production hosts | The same build could be indexed on development or staging | Duplicate/non-authoritative indexing | Critical | Default to `noindex`; opt in only for the exact production host and exact public path | Implemented; HTTP header and runtime boundary verified |
| Private DMS routes | Broad SPA fallback could return a generic 200 without an explicit indexing boundary | Soft-404 and accidental indexing risk | Critical | Serve a noindex app shell, keep authentication/authorization intact, and exclude routes from sitemap/robots | Implemented and HTTP-verified |
| `/` | Generic metadata and vague hero language; important product themes and trust limitations were incomplete | Weak commercial relevance and conversion clarity | High | Unique metadata, one descriptive H1, specific product copy, trust/security content, visible FAQ and internal links | Implemented |
| `/` | FAQ content and genuine product answers had no structured representation | Reduced eligibility for enhanced understanding | Medium | Render factual FAQs visibly and emit matching `FAQPage` data | Implemented; eight visible questions |
| `/blog` | Shared homepage metadata; content absent from initial HTML | Duplicate metadata and weaker discovery | High | Unique Blog metadata, prerendered entries, `Blog` and breadcrumb data | Implemented |
| Five `/blog/articles/:slug` pages | Article metadata was client-only and structured data/breadcrumb handling was duplicated | Weaker article discovery and inconsistency risk | High | Centralize unique metadata, `BlogPosting`, dates, canonical, social cards, breadcrumbs and related links | Implemented for all five published articles |
| `/blog/videos/:slug` | No published video exists and no factual video URL/duration is available | Fabricated schema would be misleading | Low | Keep unpublished; generate `VideoObject` only after required factual data exists | Correctly excluded; not applicable to current sitemap |
| Unknown public routes | Generic SPA fallback could behave as a soft 404 | Crawl-budget and index-quality risk | High | Return HTTP 404 with the noindex app shell | Implemented and HTTP-verified |
| Trailing-slash public variants | Duplicate URL variants were not normalized by the SPA server | Duplicate indexing risk | Medium | Permanent single-hop redirect to the canonical no-trailing-slash URL | Implemented with HTTP 308 |
| Public metadata | Canonical, Open Graph, X/Twitter, and schema values were template-global | Duplicate or incorrect search/social representation | High | Generate a single page-specific absolute canonical and complete social metadata | Implemented and rendered-source verified |
| `robots.txt` | Private route inventory was incomplete | Crawlers could spend time on worthless private routes | High | Disallow the actual authenticated route prefixes and reference the canonical sitemap | Implemented; authorization remains the real security control |
| `sitemap.xml` | Static maintenance could drift and contained strategy values not tied to content facts | Missing/stale URLs and misleading signals | High | Generate it during the production build from published content and real modification dates | Implemented; seven URLs, no fabricated frequency/priority |
| Public navigation | Product sections and articles were not all connected with descriptive anchors | Weaker topic relationships and orphan risk | Medium | Link capability sections, insights, related articles, breadcrumbs, blog and demo section | Implemented; local link check passed |
| Public images | Brand images lacked reserved dimensions/lazy-loading choices | Layout-shift and image understanding risk | Medium | Add intrinsic dimensions, descriptive/empty alt as appropriate, eager only above the fold, lazy below | Implemented for reviewed landing/blog assets |
| Fonts and scripts | CSS `@import` delayed font discovery; an unrelated third-party script was present | Render delay and unnecessary third-party work | Medium | Use head preconnect/stylesheet discovery and remove the unused script | Implemented |
| Accessibility | No site-wide skip link and article breadcrumb was not visible | Keyboard/navigation and content-understanding cost | High | Add skip links, semantic landmarks, visible breadcrumbs, labels and logical headings | Implemented statically; browser interaction audit remains blocked |
| Analytics/search verification | No public analytics or webmaster verification implementation was found | Conversions and indexing cannot be measured after release | Medium | Configure only with owner-approved IDs, consent policy, event taxonomy and Search Console access | Production action required |
| Desktop/mobile visual QA | Managed browser policy denied localhost access | Layout and interaction acceptance lacks screenshot evidence | High | Re-run desktop/mobile navigation, accessibility and Lighthouse checks in an allowed browser environment | Blocked by browser policy |

## Implemented architecture

- `src/config/publicSeo.ts` is the approved metadata and schema registry.
- `src/config/publicRoutes.ts` separates anonymous routing from the exact URLs permitted to be indexed.
- `src/entry-server.tsx` imports only public components and bundled editorial content; no authenticated route is eligible for prerendering.
- `scripts/prerender-public.mjs` creates one HTML document per approved public URL, the noindex application shell, generated sitemap, and route manifest.
- `scripts/serve-dist.mjs` returns public pages with 200, normalizes public trailing slashes with 308, returns real 404s for unknown clean URLs, serves private deep links through the noindex app shell, applies host-level `X-Robots-Tag` protection, compression, caching, and security headers.
- The Docker client image uses the repository-owned static server rather than the former generic SPA server.

## Metadata and structured data

All seven indexable pages have unique titles (49-64 characters) and descriptions (140-160 characters), one self-referencing canonical on `https://web.contraclaim.com`, complete Open Graph and X/Twitter cards, and absolute social image URLs.

Implemented schema types:

- Landing page: `Organization`, `WebSite`, `SoftwareApplication`, `FAQPage`.
- Blog index: `Blog`, `BreadcrumbList`.
- Articles: `BlogPosting`, `BreadcrumbList`, with author, published/modified dates, publisher, main entity, image, keywords and article section.
- Video support: `VideoObject` is generated only for a published video with validated registry data; none is published today.

JSON is serialized with `<` escaped before insertion into a script element. Metadata derives only from the static approved public-content registry.

## Homepage content and internal linking

The homepage now states what ContraClaim is, who it serves, and how it differs from generic document storage. It covers contractual correspondence, claims and variations, EOT records, chronology, contract/clause retrieval, connected records, governed drafting, audit trails, role-based access, organisation/project isolation, document security, and required human review of AI output.

Descriptive links connect the hero and capability overview to landing-page sections, the blog index, three featured articles, the demonstration form, and sign-in. Each article contains a visible Home/Blog/article breadcrumb, related reading, and a product CTA.

## Crawl and URL strategy

Canonical form uses lowercase paths without a trailing slash, except `/`. Query-driven blog filters remain non-canonical UI state and canonicalize to `/blog`. The sitemap contains only `/`, `/blog`, and the five published articles. Unknown clean paths return 404; known private deep links return the noindex application shell so application routing still works. Development and staging hosts receive `X-Robots-Tag: noindex, nofollow`, and client routing removes indexing permission unless the hostname is exactly `web.contraclaim.com`.

## Validation evidence

| Check | Result |
|---|---|
| Clean lockfile install | Passed: 778 packages installed; npm reported 2 moderate and 4 high dependency advisories (not changed automatically) |
| TypeScript | Passed: `npx tsc --noEmit` |
| Focused ESLint | Passed across 19 SEO/public files |
| Public-page tests | Passed: 71/71 across landing metadata, blog index, article, accessibility behavior, route allow-list, published content, sitemap and robots |
| Complete client unit suite | 321 passed, 3 skipped, 1 failed; the sole failure is the pre-existing unrelated RBAC plan-feature parity assertion for `feature.drafting.requests`, which this scoped task did not alter |
| Production build | Passed: 3,924 client modules; SSR bundle; seven pages prerendered |
| Rendered source | Seven pages; each has one H1, one canonical, page-specific metadata, index directive and prerendered body |
| Heading hierarchy | No upward level skips across all seven generated pages |
| Structured data | Every generated JSON-LD document parsed; landing 4 documents, blog/articles 2 each |
| Sitemap | Valid XML; exactly seven URLs; no `changefreq` or `priority` |
| Internal links/images | Seven pages checked; no unresolved internal paths, missing fragments or missing local assets |
| HTTP public pages | 200 for landing, blog and article routes |
| Redirects/404 | `/blog/` returned 308 to `/blog`; unknown article and unknown route returned 404 |
| Private route | `/login` returned the noindex app shell with 200; the same boundary applies to the enumerated DMS prefixes |
| Non-production indexing | Local host returned `X-Robots-Tag: noindex, nofollow`; production Host simulation did not |
| Compression | Landing served gzip; blog served Brotli |
| Browser/mobile/a11y screenshots | Not completed: admin-enforced localhost policy could not be verified by the in-app browser |
| Lighthouse/Core Web Vitals | Not measured; no numeric before/after claim is made |
| E2E interaction suite | Not run because the permitted in-app browser could not navigate to localhost |

Build warnings retained for follow-up: Browserslist data is 22 months old, and the existing auth service has both static and dynamic imports. Neither warning caused a build failure.

## Before/after evidence

| Measure | Before | After |
|---|---:|---:|
| Public URLs with page-specific initial HTML | 0 | 7 |
| Unique generated public title/description pairs | 1 shared template | 7 |
| Public pages with one generated canonical | 1 template-level value | 7 |
| Public pages with prerendered body content | 0 | 7 |
| Generated public schema documents | 1 template document | 16 total across seven pages |
| Unknown clean URL status | Generic SPA fallback behavior | HTTP 404 |
| Public trailing-slash normalization | None in generic SPA server | Single HTTP 308 |
| App shell canonical/schema exposure | Homepage metadata in shared shell | 0 canonical, 0 JSON-LD, `noindex` |
| Quantitative Lighthouse/CWV score | Not recorded | Not recorded; browser policy blocked a comparable run |

## Remaining production actions

1. Re-run desktop (1440px) and mobile viewport browser checks, screenshots, keyboard navigation, accessibility scan, broken-image checks after the managed localhost/browser policy is available or on an approved preview host.
2. Capture comparable mobile and desktop Lighthouse runs, then address any measured LCP, INP, CLS, TBT or unused-code issues. The current build output shows the landing route chunk at about 27.49 kB (7.79 kB gzip), shared CSS at 119.40 kB (20.03 kB gzip), and larger authenticated-only chunks remain code-split.
3. Validate JSON-LD with the production URL in the relevant search-engine rich-result/schema tools after deployment.
4. Submit `sitemap.xml`, inspect coverage, and request indexing using verified Search Console/Bing Webmaster ownership. Do not add placeholder verification tokens.
5. Choose and approve analytics/consent tooling and identifiers. Define privacy-reviewed route-change page views and demo-form/CTA/blog events without sending tenant, project, document, claim, or user data.
6. If separate pricing, about, contact, privacy, terms, security, feature or video pages are later approved, add real content first, then extend the public route and metadata registries. Do not expose the current authenticated `/security-terms` route merely to satisfy a marketing URL checklist.
7. Review the six npm dependency advisories in a separate dependency-remediation change; no automatic or breaking upgrade was included in this scoped SEO implementation.

## Files changed

- Build/server: `client/package.json`, `client/Dockerfile`, `client/scripts/prerender-public.mjs`, `client/scripts/serve-dist.mjs`, `client/src/entry-server.tsx`, `client/src/main.tsx`.
- SEO/indexing: `client/index.html`, `client/src/components/seo/Seo.tsx`, `client/src/components/seo/RouteIndexingBoundary.tsx`, `client/src/config/publicSeo.ts`, `client/src/config/publicRoutes.ts`, `client/public/robots.txt`, `client/public/sitemap.xml`.
- Public content/navigation: `client/src/LandingPage.tsx`, landing header/footer, blog layout/page/article/not-found, login/not-found pages.
- Performance/accessibility: `client/src/index.css`, public image sizing/loading changes, skip links, hydration-safe page-size initialization.
- Tests: `client/src/config/__tests__/publicSeo.test.ts`, `client/src/content/blog/__tests__/registry.test.ts`.
