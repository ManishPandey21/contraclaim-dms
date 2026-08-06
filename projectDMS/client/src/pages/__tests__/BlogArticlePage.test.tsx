import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import BlogArticlePage from "../BlogArticlePage";
import { getArticleBySlug } from "@/content/blog";

const SLUG = "how-to-build-defensible-project-chronology";

function renderArticle(slug = SLUG) {
  return render(
    <MemoryRouter initialEntries={[`/blog/articles/${slug}`]}>
      <Routes>
        <Route path="/blog/articles/:slug" element={<BlogArticlePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("BlogArticlePage routing", () => {
  it("renders the article matching the slug", () => {
    renderArticle();
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "How to Build a Defensible Project Chronology",
      }),
    ).toBeInTheDocument();
  });

  it("shows a not-found state for an unknown slug instead of crashing", () => {
    renderArticle("no-such-article");
    expect(
      screen.getByRole("heading", { level: 1, name: /could not be found/i }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /back to the blog/i }).length).toBeGreaterThan(0);
  });

  it("keeps a not-found slug out of the search index", async () => {
    renderArticle("no-such-article");
    await waitFor(() => {
      expect(
        document.head.querySelector('meta[name="robots"]')?.getAttribute("content"),
      ).toBe("noindex, nofollow");
    });
  });
});

describe("BlogArticlePage content", () => {
  it("shows author, publication date, reading time and category", () => {
    const { container } = renderArticle();
    // Scoped to the article masthead: the same fields repeat on the related
    // cards further down the page.
    const masthead = container.querySelector("article > header");
    expect(masthead).not.toBeNull();
    const head = within(masthead as HTMLElement);

    expect(head.getByText("ContraClaim Editorial")).toBeInTheDocument();
    expect(head.getByText("6 August 2026")).toBeInTheDocument();
    expect(head.getByText(/min read/i)).toBeInTheDocument();
    expect(head.getByText("Claims Evidence")).toBeInTheDocument();
  });

  it("renders the body with a proper heading hierarchy below the single h1", () => {
    renderArticle();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(
      screen.getByRole("heading", { level: 2, name: /why ordinary timelines break down/i }),
    ).toBeInTheDocument();
  });

  it("renders lists, tables and blockquotes from the source", () => {
    renderArticle();
    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: "Field" })).toBeInTheDocument();
    expect(within(table).getByRole("cell", { name: /stable reference used across/i })).toBeInTheDocument();
    expect(screen.getByText(/what sequence of design submissions/i)).toBeInTheDocument();
    expect(screen.getAllByRole("list").length).toBeGreaterThan(0);
  });

  it("preserves the legal disclaimer", () => {
    renderArticle();
    expect(
      screen.getByText(/A chronology is not, by itself, proof of contractual entitlement/i),
    ).toBeInTheDocument();
  });

  it("renders source links as external links with safe rel attributes", () => {
    renderArticle();
    const link = screen.getByRole("link", { name: /Society of Construction Law/i });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link.getAttribute("href")).toMatch(/^https:\/\//);
  });

  it("never renders markup that came from content", () => {
    const { container } = renderArticle();
    // The parser produces elements, not HTML. Nothing in the article body can
    // introduce a script or an inline event handler.
    expect(container.querySelector("script")).toBeNull();
    expect(container.innerHTML).not.toContain("onerror=");
    expect(container.innerHTML).not.toContain("javascript:");
  });
});

describe("BlogArticlePage SEO", () => {
  it("sets the authored title, description and canonical URL", async () => {
    const article = getArticleBySlug(SLUG);
    renderArticle();

    await waitFor(() => expect(document.title).toBe(article?.seoTitle));
    expect(
      document.head.querySelector('meta[name="description"]')?.getAttribute("content"),
    ).toBe(article?.metaDescription);
    expect(
      document.head.querySelector('link[rel="canonical"]')?.getAttribute("href"),
    ).toBe(`https://web.contraclaim.com/blog/articles/${SLUG}`);
  });

  it("sets Open Graph and Twitter tags for sharing", async () => {
    renderArticle();
    await waitFor(() => {
      expect(
        document.head.querySelector('meta[property="og:type"]')?.getAttribute("content"),
      ).toBe("article");
    });
    expect(
      document.head.querySelector('meta[property="og:url"]')?.getAttribute("content"),
    ).toBe(`https://web.contraclaim.com/blog/articles/${SLUG}`);
    expect(
      document.head.querySelector('meta[name="twitter:card"]')?.getAttribute("content"),
    ).toBe("summary_large_image");
    expect(
      document.head.querySelector('meta[property="og:image"]')?.getAttribute("content"),
    ).toMatch(/^https:\/\/web\.contraclaim\.com\//);
  });

  it("emits BlogPosting structured data", async () => {
    renderArticle();
    await waitFor(() => {
      expect(document.head.querySelector('script[type="application/ld+json"]')).not.toBeNull();
    });
    const data = JSON.parse(
      document.head.querySelector('script[type="application/ld+json"]')?.textContent ?? "{}",
    );
    expect(data["@type"]).toBe("BlogPosting");
    expect(data.headline).toBe("How to Build a Defensible Project Chronology");
    expect(data.datePublished).toBe("2026-08-06");
  });

  it("suppresses the site-level SoftwareApplication schema while an article is shown", async () => {
    // index.html ships a SoftwareApplication block describing the product. On
    // an article page that would have a blog post declare itself a software
    // application, so it is detached for the duration and restored after.
    const siteSchema = document.createElement("script");
    siteSchema.type = "application/ld+json";
    siteSchema.textContent = JSON.stringify({ "@type": "SoftwareApplication" });
    document.head.appendChild(siteSchema);

    const { unmount } = renderArticle();

    await waitFor(() => {
      const types = [
        ...document.head.querySelectorAll('script[type="application/ld+json"]'),
      ].map((node) => JSON.parse(node.textContent ?? "{}")["@type"]);
      expect(types).toEqual(["BlogPosting", "BreadcrumbList"]);
    });

    unmount();

    const restored = [
      ...document.head.querySelectorAll('script[type="application/ld+json"]'),
    ].map((node) => JSON.parse(node.textContent ?? "{}")["@type"]);
    expect(restored).toEqual(["SoftwareApplication"]);

    siteSchema.remove();
  });

  it("restores the previous document metadata on unmount", async () => {
    document.title = "Original title";
    const { unmount } = renderArticle();
    await waitFor(() => expect(document.title).not.toBe("Original title"));

    unmount();

    expect(document.title).toBe("Original title");
    expect(document.head.querySelector('script[type="application/ld+json"]')).toBeNull();
  });
});

describe("BlogArticlePage navigation", () => {
  it("links back to the blog index", () => {
    renderArticle();
    expect(screen.getByRole("link", { name: /back to the blog/i })).toHaveAttribute(
      "href",
      "/blog",
    );
  });

  it("offers previous and next articles in editorial order", () => {
    renderArticle();
    const nav = screen.getByRole("navigation", { name: /article navigation/i });
    expect(
      within(nav).getByRole("link", { name: /excel registers versus connected/i }),
    ).toHaveAttribute("rel", "prev");
    expect(
      within(nav).getByRole("link", { name: /what ai should/i }),
    ).toHaveAttribute("rel", "next");
  });

  it("omits the previous link on the first article", () => {
    renderArticle("why-construction-claims-fail-before-submission");
    const nav = screen.getByRole("navigation", { name: /article navigation/i });
    expect(within(nav).queryByText(/^previous$/i)).toBeNull();
  });

  it("shows related articles", () => {
    renderArticle();
    const section = screen.getByRole("region", { name: /related reading/i });
    expect(within(section).getAllByRole("article").length).toBeGreaterThan(0);
  });

  it("offers sharing options", () => {
    renderArticle();
    const shareLink = screen.getAllByRole("link", { name: /share on linkedin/i })[0];
    expect(shareLink.getAttribute("href")).toContain(
      encodeURIComponent(`https://web.contraclaim.com/blog/articles/${SLUG}`),
    );
    expect(screen.getAllByRole("button", { name: /copy link/i }).length).toBeGreaterThan(0);
  });

  it("shows a call to action without interrupting the article body", () => {
    renderArticle();
    expect(
      screen.getByRole("link", { name: /request a demonstration/i }),
    ).toHaveAttribute("href", "/#contact");
  });
});
