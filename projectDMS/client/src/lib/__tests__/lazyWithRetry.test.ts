import { describe, it, expect } from "vitest";
import { isChunkLoadError } from "../lazyWithRetry";

describe("isChunkLoadError", () => {
  it("matches the real chunk-load failures across browsers", () => {
    // Chrome/Edge — the exact string from the reported production incident.
    expect(
      isChunkLoadError(
        new Error(
          "Failed to fetch dynamically imported module: https://contraclaim.com/assets/ContractsUploadPage-BhXaqC2i.js"
        )
      )
    ).toBe(true);
    // Firefox
    expect(
      isChunkLoadError(new Error("error loading dynamically imported module"))
    ).toBe(true);
    // Safari
    expect(
      isChunkLoadError(new Error("Importing a module script failed."))
    ).toBe(true);
    // Bundler-style
    expect(isChunkLoadError(new Error("ChunkLoadError: loading chunk 5 failed")))
      .toBe(true);
  });

  it("does not treat ordinary runtime errors as chunk failures", () => {
    expect(
      isChunkLoadError(new Error("Cannot read properties of undefined"))
    ).toBe(false);
    expect(isChunkLoadError(new TypeError("x is not a function"))).toBe(false);
    expect(isChunkLoadError(null)).toBe(false);
    expect(isChunkLoadError(undefined)).toBe(false);
    expect(isChunkLoadError("some string")).toBe(false);
  });
});
