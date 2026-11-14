import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import MainLayout from "../MainLayout";

vi.mock("../Sidebar", () => ({ default: () => <nav>Sidebar</nav> }));
vi.mock("../Navbar", () => ({ default: () => <header>Navbar</header> }));
vi.mock("@/contexts/TenantContext", () => ({
  TenantProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useTenant: () => ({ selectedOrganizationId: "org-1", selectedProjectId: "proj-1" }),
}));

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<MainLayout />}>
          <Route path="/documentviewer/:id" element={<p>viewer page</p>} />
          <Route path="/overview" element={<p>overview page</p>} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
  const main = screen.getByRole("main");
  return { main, shell: main.parentElement!.parentElement!, outlet: main.firstElementChild! };
}

describe("MainLayout height contract", () => {
  it("bounds the shell to the viewport on the Document Viewer route", () => {
    const { main, shell, outlet } = renderAt("/documentviewer/doc-1");

    expect(shell).toHaveClass("h-screen", "overflow-hidden");
    expect(shell).not.toHaveClass("min-h-screen");
    // <main> takes the height left under the Navbar and stays the fallback
    // scroller for short windows; the page shell itself never scrolls.
    expect(main).toHaveClass("min-h-0", "overflow-auto");
    expect(outlet).toHaveClass("h-full", "min-h-0");
  });

  it("keeps the page-scrolling shell on every other route", () => {
    const { main, shell, outlet } = renderAt("/overview");

    expect(shell).toHaveClass("min-h-screen");
    expect(shell).not.toHaveClass("h-screen");
    expect(main).toHaveClass("overflow-auto");
    expect(main).not.toHaveClass("min-h-0");
    expect(outlet).not.toHaveClass("h-full");
  });
});
