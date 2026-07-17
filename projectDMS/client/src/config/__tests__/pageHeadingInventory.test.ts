import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const srcRoot = resolve(here, "../..");
const routesSource = readFileSync(resolve(srcRoot, "routes.tsx"), "utf8");

const PUBLIC_SHELL_PAGES = new Set(["LandingPage", "LoginPage", "NotFound"]);
const HEADING_DELEGATES: Record<string, string> = {
  DocumentViewerPage: "components/document-viewer/DocumentHeader.tsx",
  LetterWorkflowPage: "components/letter-workflow/LetterWorkflowHeader.tsx",
};

const pageNames = Array.from(
  routesSource.matchAll(/import\("\.\/pages\/(\w+)"\)/g),
  (match) => match[1],
).filter((pageName) => !PUBLIC_SHELL_PAGES.has(pageName));

const readPageSource = (pageName: string) => {
  const pageSource = readFileSync(
    resolve(srcRoot, `pages/${pageName}.tsx`),
    "utf8",
  );
  const delegate = HEADING_DELEGATES[pageName];
  return delegate
    ? `${pageSource}\n${readFileSync(resolve(srcRoot, delegate), "utf8")}`
    : pageSource;
};

describe("page heading inventory", () => {
  it("gives every routed application page a semantic main heading", () => {
    const missingHeadings = pageNames.filter((pageName) => {
      const source = readPageSource(pageName);
      return !/<h1\b/.test(source) && !/<PageHeader\b/.test(source);
    });

    expect(
      missingHeadings,
      `Pages without an h1 or PageHeader: ${missingHeadings.join(", ")}`,
    ).toEqual([]);
  });

  it("keeps the requested canonical headings in the main page components", () => {
    expect(readPageSource("Dashboard")).toContain("Document Dashboard");
    expect(readPageSource("RegisterPage")).toContain(">Registration</h1>");
    expect(readPageSource("Overview")).toContain(">Overview</h1>");
  });

  it("keeps page headings out of the shared navbar", () => {
    const navbar = readFileSync(
      resolve(srcRoot, "components/layout/Navbar.tsx"),
      "utf8",
    );

    expect(navbar).not.toMatch(/<h1\b/);
    expect(navbar).not.toContain("Document Management System");
  });
});
