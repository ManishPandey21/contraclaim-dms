import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

/**
 * The blog must be reachable with no session, and adding it must not weaken
 * the guard on the authenticated application. Both halves are asserted here
 * against the real route table.
 */

vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    isAuthenticated: false,
    isAuthLoading: false,
    user: null,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

vi.mock("@/hooks/useRBAC", () => ({
  default: () => ({ roles: [], can: () => false, loading: false, error: null }),
}));

vi.mock("@/hooks/useEntitlements", () => ({
  default: () => ({ loading: false, error: null, entitlements: null, features: [] }),
}));

vi.mock("@/services/security-terms-api", () => ({
  getSecurityTermsStatus: vi.fn().mockResolvedValue({ requires_acceptance: false }),
}));

const { default: AppRoutes } = await import("../../routes");

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

describe("public access to the blog", () => {
  it("renders /blog for a signed-out visitor", async () => {
    renderAt("/blog");
    expect(
      await screen.findByRole(
        "heading",
        { level: 1, name: /construction contract and claims insights/i },
        { timeout: 8000 },
      ),
    ).toBeInTheDocument();
  });

  it("renders an article page for a signed-out visitor", async () => {
    renderAt("/blog/articles/seven-records-every-eot-claim-needs");
    expect(
      await screen.findByRole(
        "heading",
        { level: 1, name: /seven records every eot claim needs/i },
        { timeout: 8000 },
      ),
    ).toBeInTheDocument();
  });

  it("serves the blog not-found page for an unknown blog path", async () => {
    renderAt("/blog/nonsense/path");
    expect(
      await screen.findByRole(
        "heading",
        { level: 1, name: /could not be found/i },
        { timeout: 8000 },
      ),
    ).toBeInTheDocument();
  });

  it("never shows a sign-in prompt on the blog", async () => {
    renderAt("/blog");
    await screen.findByRole(
      "heading",
      { level: 1, name: /construction contract and claims insights/i },
      { timeout: 8000 },
    );
    expect(screen.queryByLabelText(/password/i)).toBeNull();
  });

  it("still gates an authenticated route for the same signed-out visitor", async () => {
    renderAt("/documents");
    // The guard renders the login surface in place rather than the page.
    expect(
      screen.queryByRole("heading", { level: 1, name: /construction contract/i }),
    ).toBeNull();
    const loginSurface = await screen.findAllByText(/sign in|password|email/i, undefined, {
      timeout: 8000,
    });
    expect(loginSurface.length).toBeGreaterThan(0);
  });
});

describe("blog content carries no private data", () => {
  it("references no organisation, project, user or claim identifiers", async () => {
    const { publishedArticles, publishedVideos } = await import("@/content/blog");
    const corpus = [...publishedArticles, ...publishedVideos]
      .map((entry) =>
        [entry.title, entry.excerpt, "body" in entry ? entry.body : ""].join("\n"),
      )
      .join("\n");

    // Mongo ObjectIds, JWTs, bearer tokens and API keys must never appear in
    // editorial content shipped to the public bundle.
    expect(corpus).not.toMatch(/\b[0-9a-f]{24}\b/i);
    expect(corpus).not.toMatch(/eyJ[A-Za-z0-9_-]{10,}\./);
    expect(corpus).not.toMatch(/\b(bearer|authorization|api[_-]?key|secret)\b\s*[:=]/i);
    expect(corpus).not.toMatch(/api\.contraclaim\.com/i);
  });
});
