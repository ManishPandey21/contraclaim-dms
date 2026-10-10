import { describe, expect, it } from "vitest";
import {
  PAGE_RESUME_STAGE,
  isTerminalProcessingStatus,
} from "@/utils/processingStatus";

// The pollers on DocumentViewerPage and UploadPage stop exactly when this
// returns true. Before it existed they stopped only on completed / failed /
// dead_lettered, so a document the backend had settled as needing review,
// stored without extraction, or partially processed was polled every 4 s for
// as long as the page stayed open.

describe("isTerminalProcessingStatus", () => {
  it.each([
    "completed",
    "failed",
    "dead_lettered",
    "human_review_required",
    "stored_only",
    "skipped",
    "not_queued",
  ])("%s stops polling", (status) => {
    expect(isTerminalProcessingStatus(status)).toBe(true);
  });

  it.each(["queued", "processing", "retrying", "metadata_extracted"])(
    "%s keeps polling",
    (status) => {
      expect(isTerminalProcessingStatus(status)).toBe(false);
    }
  );

  it("partially_processed stops polling when it is a final verdict", () => {
    expect(isTerminalProcessingStatus("partially_processed", "indexing_incomplete")).toBe(true);
    // A document with no job (no stage) has nothing left to resume it.
    expect(isTerminalProcessingStatus("partially_processed")).toBe(true);
  });

  it("partially_processed keeps polling while its pages wait to be retried", () => {
    expect(isTerminalProcessingStatus("partially_processed", PAGE_RESUME_STAGE)).toBe(false);
  });

  it("is case-insensitive and treats a missing status as not yet settled", () => {
    expect(isTerminalProcessingStatus("Human_Review_Required")).toBe(true);
    expect(isTerminalProcessingStatus(undefined)).toBe(false);
    expect(isTerminalProcessingStatus(null)).toBe(false);
    expect(isTerminalProcessingStatus("")).toBe(false);
  });
});
