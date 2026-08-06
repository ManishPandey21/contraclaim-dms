import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BlogPage from "../BlogPage";
import { publishedArticles } from "@/content/blog";
import {
  DESKTOP_PAGE_SIZE,
  MOBILE_PAGE_SIZE,
  pageSizeFor,
} from "@/components/blog/usePageSize";

/**
 * The shared test setup mocks `matchMedia` to always report `matches: false`,
 * so these renders take the small-screen path — four cards per page. The
 * desktop size is covered explicitly where it matters.
 */

function LocationProbe() {
  const location = useLocation();
  return (
    <div data-testid="location">{`${location.pathname}${location.search}`}</div>
  );
}

function renderBlog(initialEntry = "/blog") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/blog"
          element={
            <>
              <BlogPage />
              <LocationProbe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

const currentUrl = () => screen.getByTestId("location").textContent ?? "";
const articleCards = () =>
  screen.queryAllByRole("article").filter((node) => within(node).queryByRole("link"));

beforeEach(() => {
  vi.clearAllMocks();
});

describe("BlogPage rendering", () => {
  it("renders the blog heading and introduction", () => {
    renderBlog();
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: /construction contract and claims insights/i,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/practical guidance on contract administration/i)).toBeInTheDocument();
  });

  it("renders the first page of articles", () => {
    renderBlog();
    expect(articleCards()).toHaveLength(MOBILE_PAGE_SIZE);
    expect(
      screen.getByRole("link", {
        name: /why construction claims fail before they are submitted/i,
      }),
    ).toHaveAttribute("href", "/blog/articles/why-construction-claims-fail-before-submission");
  });

  it("shows the entry count, category, date, reading time and author on cards", () => {
    renderBlog();
    const card = articleCards()[0];
    expect(within(card).getByText(/min read/i)).toBeInTheDocument();
    expect(within(card).getByText("ContraClaim Editorial")).toBeInTheDocument();
    expect(within(card).getByText("6 August 2026")).toBeInTheDocument();
    expect(within(card).getByText(/read article/i)).toBeInTheDocument();
  });

  it("sets SEO metadata for the blog index", async () => {
    renderBlog();
    await waitFor(() => {
      expect(document.title).toMatch(/Construction Contract and Claims Insights/);
    });
    expect(
      document.head.querySelector('link[rel="canonical"]')?.getAttribute("href"),
    ).toBe("https://web.contraclaim.com/blog");
    expect(
      document.head.querySelector('meta[property="og:type"]')?.getAttribute("content"),
    ).toBe("website");
    const jsonLd = document.head.querySelector('script[type="application/ld+json"]');
    expect(JSON.parse(jsonLd?.textContent ?? "{}")["@type"]).toBe("Blog");
  });
});

describe("BlogPage tabs", () => {
  it("exposes correct tab roles and selection state", () => {
    renderBlog();
    const articlesTab = screen.getByRole("tab", { name: "Articles" });
    const videosTab = screen.getByRole("tab", { name: "Videos" });

    expect(articlesTab).toHaveAttribute("aria-selected", "true");
    expect(videosTab).toHaveAttribute("aria-selected", "false");
    expect(articlesTab).toHaveAttribute("aria-controls");
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "aria-labelledby",
      articlesTab.id,
    );
  });

  it("switches to Videos and reflects the tab in the URL", async () => {
    const user = userEvent.setup();
    renderBlog();

    await user.click(screen.getByRole("tab", { name: "Videos" }));

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Videos" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });
    expect(currentUrl()).toContain("type=videos");
  });

  it("is operable by keyboard", async () => {
    const user = userEvent.setup();
    renderBlog();

    screen.getByRole("tab", { name: "Articles" }).focus();
    await user.keyboard("{ArrowRight}");

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Videos" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });
  });

  it("opens on the Videos tab when the URL says so", () => {
    renderBlog("/blog?type=videos");
    expect(screen.getByRole("tab", { name: "Videos" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("shows the empty state for videos, since none are published", () => {
    renderBlog("/blog?type=videos");
    expect(screen.getByText(/no videos published yet/i)).toBeInTheDocument();
    expect(articleCards()).toHaveLength(0);
  });
});

describe("BlogPage pagination", () => {
  it("paginates at the small-screen page size and disables Previous on page 1", () => {
    renderBlog();
    expect(articleCards()).toHaveLength(MOBILE_PAGE_SIZE);
    expect(screen.getByRole("button", { name: /previous page/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /next page/i })).toBeEnabled();
    expect(screen.getByText(/page 1 of 2/i)).toBeInTheDocument();
  });

  it("shows six per page on desktop widths", () => {
    expect(pageSizeFor(true)).toBe(DESKTOP_PAGE_SIZE);
    expect(pageSizeFor(false)).toBe(MOBILE_PAGE_SIZE);
    expect(DESKTOP_PAGE_SIZE).toBeGreaterThanOrEqual(4);
    expect(DESKTOP_PAGE_SIZE).toBeLessThanOrEqual(6);
    expect(MOBILE_PAGE_SIZE).toBeGreaterThanOrEqual(4);
  });

  it("moves to the next page, updates the URL and disables Next at the end", async () => {
    const user = userEvent.setup();
    renderBlog();

    await user.click(screen.getByRole("button", { name: /next page/i }));

    await waitFor(() => expect(currentUrl()).toContain("page=2"));
    expect(articleCards()).toHaveLength(publishedArticles.length - MOBILE_PAGE_SIZE);
    expect(screen.getByRole("button", { name: /next page/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /previous page/i })).toBeEnabled();
  });

  it("moves focus to the results heading after a page change", async () => {
    const user = userEvent.setup();
    renderBlog();

    await user.click(screen.getByRole("button", { name: /next page/i }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 2, name: /articles/i })).toHaveFocus();
    });
  });

  it("marks the current page for assistive technology", () => {
    renderBlog("/blog?page=2");
    expect(screen.getByRole("button", { name: "Go to page 2" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it.each([
    ["a page beyond the last", "/blog?page=99"],
    ["a zero page", "/blog?page=0"],
    ["a negative page", "/blog?page=-3"],
    ["a non-numeric page", "/blog?page=drop-table"],
  ])("clamps %s to a valid page and rewrites the URL", async (_label, entry) => {
    renderBlog(entry);

    await waitFor(() => {
      expect(currentUrl()).not.toContain("page=99");
    });
    expect(articleCards().length).toBeGreaterThan(0);
    expect(screen.getByText(/page [12] of 2/i)).toBeInTheDocument();
  });

  it("resets to page 1 when the tab changes", async () => {
    const user = userEvent.setup();
    renderBlog("/blog?page=2");

    await user.click(screen.getByRole("tab", { name: "Videos" }));

    await waitFor(() => expect(currentUrl()).toContain("type=videos"));
    expect(currentUrl()).not.toContain("page=2");
  });
});

describe("BlogPage search and filters", () => {
  it("filters by search term and preserves it in the URL", async () => {
    const user = userEvent.setup();
    renderBlog();

    await user.type(screen.getByRole("searchbox", { name: /search articles/i }), "chronology");

    await waitFor(() => expect(currentUrl()).toContain("q=chronology"));
    await waitFor(() => expect(articleCards()).toHaveLength(1));
    expect(
      screen.getByRole("link", { name: /how to build a defensible project chronology/i }),
    ).toBeInTheDocument();
  });

  it("filters by category and marks the active chip", async () => {
    const user = userEvent.setup();
    renderBlog();

    await user.click(screen.getByRole("button", { name: "Extension of Time" }));

    await waitFor(() => expect(currentUrl()).toContain("category=extension-of-time"));
    expect(screen.getByRole("button", { name: "Extension of Time" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(articleCards()).toHaveLength(1);
  });

  it("resets pagination when a filter changes", async () => {
    const user = userEvent.setup();
    renderBlog("/blog?page=2");

    await user.click(screen.getByRole("button", { name: "Claims Evidence" }));

    await waitFor(() => expect(currentUrl()).toContain("category=claims-evidence"));
    expect(currentUrl()).not.toContain("page=2");
  });

  it("ignores an unknown category rather than showing nothing", () => {
    renderBlog("/blog?category=../../etc/passwd");
    expect(articleCards()).toHaveLength(MOBILE_PAGE_SIZE);
    expect(screen.getByRole("button", { name: "All topics" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("shows a no-results state with a way to clear filters", async () => {
    const user = userEvent.setup();
    renderBlog("/blog?q=zzzzznomatch");

    expect(screen.getByText(/no articles match your filters/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /clear filters/i }));

    await waitFor(() => expect(articleCards()).toHaveLength(MOBILE_PAGE_SIZE));
  });
});

describe("BlogPage navigation and CTA", () => {
  it("offers the demo and platform calls to action", () => {
    renderBlog();
    expect(screen.getByRole("link", { name: /request a demonstration/i })).toHaveAttribute(
      "href",
      "/#contact",
    );
    expect(screen.getByRole("link", { name: /explore contraclaim dms/i })).toBeInTheDocument();
  });

  it("marks the Blog nav link as the current page", () => {
    renderBlog();
    const blogLinks = screen.getAllByRole("link", { name: "Blog" });
    expect(blogLinks.some((link) => link.getAttribute("aria-current") === "page")).toBe(
      true,
    );
  });
});
