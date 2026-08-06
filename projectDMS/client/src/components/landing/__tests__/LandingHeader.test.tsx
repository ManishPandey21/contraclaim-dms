import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import LandingFooter from "../LandingFooter";
import LandingHeader from "../LandingHeader";

function renderHeader(route = "/") {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <LandingHeader />
    </MemoryRouter>,
  );
}

describe("LandingHeader desktop navigation", () => {
  it("shows a Blog link that routes to /blog", () => {
    renderHeader();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Blog" })).toHaveAttribute(
      "href",
      "/blog",
    );
  });

  it("keeps the existing landing sections alongside it", () => {
    renderHeader();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    for (const [label, href] of [
      ["Platform", "#platform"],
      ["Workflow", "#how"],
      ["Governance", "#governance"],
      ["FAQ", "#faq"],
    ] as const) {
      expect(within(nav).getByRole("link", { name: label })).toHaveAttribute("href", href);
    }
  });

  it("marks Blog as the current page on blog routes", () => {
    renderHeader("/blog");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Blog" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("marks Blog current on an article route too", () => {
    renderHeader("/blog/articles/seven-records-every-eot-claim-needs");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Blog" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("does not mark Blog current on the landing page", () => {
    renderHeader();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Blog" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("routes section links home when away from the landing page", () => {
    renderHeader("/blog");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Platform" })).toHaveAttribute(
      "href",
      "/#platform",
    );
    expect(screen.getByRole("link", { name: /request a demo/i })).toHaveAttribute(
      "href",
      "/#contact",
    );
  });
});

describe("LandingHeader mobile navigation", () => {
  it("opens a menu containing the Blog link", async () => {
    const user = userEvent.setup();
    renderHeader();

    await user.click(screen.getByRole("button", { name: /open menu/i }));

    const mobileNav = await screen.findByRole("navigation", { name: "Mobile" });
    expect(within(mobileNav).getByRole("link", { name: "Blog" })).toHaveAttribute(
      "href",
      "/blog",
    );
  });

  it("carries the section links and sign-in in the mobile menu", async () => {
    const user = userEvent.setup();
    renderHeader();

    await user.click(screen.getByRole("button", { name: /open menu/i }));
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByRole("link", { name: "Platform" })).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "FAQ" })).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: /sign in/i })).toHaveAttribute(
      "href",
      "/login",
    );
  });

  it("marks the active route in the mobile menu", async () => {
    const user = userEvent.setup();
    renderHeader("/blog");

    await user.click(screen.getByRole("button", { name: /open menu/i }));
    const mobileNav = await screen.findByRole("navigation", { name: "Mobile" });

    expect(within(mobileNav).getByRole("link", { name: "Blog" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("closes when a link is chosen", async () => {
    const user = userEvent.setup();
    renderHeader();

    await user.click(screen.getByRole("button", { name: /open menu/i }));
    const mobileNav = await screen.findByRole("navigation", { name: "Mobile" });
    await user.click(within(mobileNav).getByRole("link", { name: "Blog" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });
});

describe("LandingFooter", () => {
  function renderFooter() {
    return render(
      <MemoryRouter initialEntries={["/"]}>
        <LandingFooter />
      </MemoryRouter>,
    );
  }

  it("includes a Blog link", () => {
    renderFooter();
    const nav = screen.getByRole("navigation", { name: "Footer" });
    expect(within(nav).getByRole("link", { name: "Blog" })).toHaveAttribute(
      "href",
      "/blog",
    );
  });

  it("keeps the existing footer links", () => {
    renderFooter();
    const nav = screen.getByRole("navigation", { name: "Footer" });
    for (const label of ["Platform", "How it works", "Modules", "FAQ", "Contact", "Sign in"]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
  });
});
