import React from "react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ErrorBoundary } from "../ErrorBoundary";

const Boom = () => {
  throw new Error("page exploded");
};
const Ok = () => <div>healthy page</div>;

describe("ErrorBoundary (section-level containment)", () => {
  beforeEach(() => {
    // The boundary's componentDidCatch fires a best-effort logError beacon;
    // stub fetch so no real request is attempted during the test.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}")));
    // React logs caught errors to console.error — silence the expected noise.
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders children when there is no error", () => {
    render(
      <ErrorBoundary>
        <Ok />
      </ErrorBoundary>
    );
    expect(screen.getByText("healthy page")).toBeInTheDocument();
  });

  it("catches a child render crash and shows the fallback", () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>
    );
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
    expect(screen.getByText("page exploded")).toBeInTheDocument();
  });

  it("resets when the key changes, mirroring navigation to another route", () => {
    // Matches MainLayout's <ErrorBoundary key={location.pathname}>: a crashed
    // page must not keep showing the fallback after the user navigates away.
    const { rerender } = render(
      <ErrorBoundary key="/crashing-route">
        <Boom />
      </ErrorBoundary>
    );
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();

    rerender(
      <ErrorBoundary key="/healthy-route">
        <Ok />
      </ErrorBoundary>
    );
    expect(screen.getByText("healthy page")).toBeInTheDocument();
    expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument();
  });
});
