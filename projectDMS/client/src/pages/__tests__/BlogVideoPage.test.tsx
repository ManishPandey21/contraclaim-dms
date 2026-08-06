import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { BlogVideo, ResolvedVideo } from "@/content/blog/types";
import { parseMarkdown } from "@/content/blog/markdown";
import { toEmbed } from "@/content/blog/video-embed";

/**
 * No videos are published yet, so the page is exercised against fixtures. The
 * fixtures go through the same `toEmbed` validation the registry uses, which
 * is the property that actually matters: an entry only becomes renderable if
 * its URL passed the provider allowlist.
 */

const baseVideo: BlogVideo = {
  id: "video-fixture",
  type: "video",
  title: "Walkthrough: the seven EOT record groups",
  slug: "seven-eot-record-groups-walkthrough",
  excerpt: "A short walkthrough of the record groups behind an EOT claim.",
  description: "Longer description of the walkthrough shown on the video page.",
  videoUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  videoDuration: "12:45",
  isoDuration: "PT12M45S",
  category: "extension-of-time",
  tags: ["EOT"],
  author: "ContraClaim Editorial",
  publishedAt: "2026-08-06",
  status: "published",
  order: 1,
  seoTitle: "Walkthrough: the seven EOT record groups | ContraClaim",
  metaDescription: "A short walkthrough of the record groups behind an EOT claim.",
  transcript: "## Summary\n\nThe seven groups are introduced in order.",
};

function resolve(video: BlogVideo): ResolvedVideo | null {
  const embed = toEmbed(video.videoUrl);
  if (!embed) return null;
  return {
    ...video,
    embed,
    transcriptBlocks: video.transcript ? parseMarkdown(video.transcript) : [],
  };
}

const videoStore = { current: resolve(baseVideo) };

vi.mock("@/content/blog", async () => {
  const actual = await vi.importActual<typeof import("@/content/blog")>("@/content/blog");
  return {
    ...actual,
    publishedVideos: [],
    getVideoBySlug: (slug: string) =>
      videoStore.current && videoStore.current.slug === slug
        ? videoStore.current
        : undefined,
  };
});

function renderVideo(slug = baseVideo.slug) {
  return render(
    <MemoryRouter initialEntries={[`/blog/videos/${slug}`]}>
      <Routes>
        <Route path="/blog/videos/:slug" element={<BlogVideoPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const { default: BlogVideoPage } = await import("../BlogVideoPage");

describe("BlogVideoPage playback", () => {
  it("renders a responsive, titled player for a validated video", () => {
    renderVideo();
    const frame = screen.getByTitle(/seven EOT record groups — video player/i);
    expect(frame).toBeInTheDocument();
    expect(frame.getAttribute("src")).toBe(
      "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0",
    );
    expect(frame).toHaveAttribute("loading", "lazy");
    expect(frame).toHaveAttribute("referrerpolicy", "strict-origin-when-cross-origin");
    expect(frame.parentElement?.className).toContain("aspect-video");
  });

  it("never autoplays", () => {
    renderVideo();
    const frame = screen.getByTitle(/video player/i);
    expect(frame.getAttribute("src")).not.toContain("autoplay");
    expect(frame.getAttribute("allow")).not.toContain("autoplay");
  });

  it("only ever loads an allowlisted provider origin", () => {
    renderVideo();
    const src = screen.getByTitle(/video player/i).getAttribute("src") ?? "";
    expect(new URL(src).hostname).toBe("www.youtube-nocookie.com");
  });

  it("shows title, description, duration, date, category and provider link", () => {
    renderVideo();
    expect(
      screen.getByRole("heading", { level: 1, name: /seven EOT record groups/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/longer description of the walkthrough/i)).toBeInTheDocument();
    expect(screen.getByText("12:45")).toBeInTheDocument();
    // Related article cards repeat the date and category further down the page.
    expect(screen.getAllByText("6 August 2026").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Extension of Time").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /open on youtube/i })).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    );
  });

  it("renders the transcript when one is supplied", () => {
    renderVideo();
    expect(screen.getByRole("heading", { name: "Transcript" })).toBeInTheDocument();
    expect(screen.getByText(/the seven groups are introduced in order/i)).toBeInTheDocument();
  });

  it("emits VideoObject structured data", async () => {
    renderVideo();
    await waitFor(() => {
      expect(document.head.querySelector('script[type="application/ld+json"]')).not.toBeNull();
    });
    const data = JSON.parse(
      document.head.querySelector('script[type="application/ld+json"]')?.textContent ?? "{}",
    );
    expect(data["@type"]).toBe("VideoObject");
    expect(data.embedUrl).toBe("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0");
    expect(data.duration).toBe("PT12M45S");
  });

  it("shows a not-found state for an unknown slug", () => {
    renderVideo("no-such-video");
    expect(
      screen.getByRole("heading", { level: 1, name: /could not be found/i }),
    ).toBeInTheDocument();
    expect(screen.queryByTitle(/video player/i)).toBeNull();
  });
});

describe("unsafe video URLs", () => {
  it.each([
    "https://evil.example.com/embed/abc",
    "javascript:alert(1)",
    "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com.evil.example.com/watch?v=dQw4w9WgXcQ",
  ])("cannot be published, so no iframe is ever rendered for %s", (videoUrl) => {
    // Validation happens before the entry becomes renderable, exactly as the
    // registry does it, so an unsafe URL cannot reach the player.
    expect(resolve({ ...baseVideo, videoUrl })).toBeNull();

    videoStore.current = resolve({ ...baseVideo, videoUrl });
    renderVideo();
    expect(screen.queryByTitle(/video player/i)).toBeNull();
    expect(document.querySelector("iframe")).toBeNull();
    expect(
      screen.getByRole("heading", { level: 1, name: /could not be found/i }),
    ).toBeInTheDocument();

    videoStore.current = resolve(baseVideo);
  });
});
