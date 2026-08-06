import { describe, expect, it } from "vitest";

import { isAllowedVideoUrl, toEmbed, toIsoDuration } from "../video-embed";

describe("toEmbed — allowed providers", () => {
  it.each([
    ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"],
    ["https://youtube.com/watch?v=dQw4w9WgXcQ&t=30", "dQw4w9WgXcQ"],
    ["https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"],
    ["https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"],
    ["https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"],
  ])("accepts YouTube URL %s", (url, id) => {
    const embed = toEmbed(url);
    expect(embed).toMatchObject({ provider: "youtube", videoId: id });
    expect(embed?.embedUrl).toBe(
      `https://www.youtube-nocookie.com/embed/${id}?rel=0`,
    );
  });

  it.each([
    ["https://vimeo.com/123456789", "123456789"],
    ["https://player.vimeo.com/video/123456789", "123456789"],
  ])("accepts Vimeo URL %s", (url, id) => {
    const embed = toEmbed(url);
    expect(embed).toMatchObject({ provider: "vimeo", videoId: id });
    expect(embed?.embedUrl).toBe(`https://player.vimeo.com/video/${id}?dnt=1`);
  });

  it("rebuilds the embed URL from the id, discarding attacker-controlled query and fragment", () => {
    const embed = toEmbed(
      "https://www.youtube.com/watch?v=dQw4w9WgXcQ&autoplay=1&evil=%22onload%3Dalert(1)#frag",
    );
    expect(embed?.embedUrl).toBe(
      "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0",
    );
    expect(embed?.embedUrl).not.toContain("autoplay");
    expect(embed?.embedUrl).not.toContain("evil");
  });

  it("never sets an autoplay parameter", () => {
    expect(toEmbed("https://vimeo.com/123456789")?.embedUrl).not.toContain("autoplay");
  });
});

describe("toEmbed — rejected input", () => {
  it.each([
    ["a non-allowlisted host", "https://evil.example.com/embed/abc"],
    ["a lookalike host", "https://youtube.com.evil.example.com/watch?v=dQw4w9WgXcQ"],
    ["plain http", "http://www.youtube.com/watch?v=dQw4w9WgXcQ"],
    ["a javascript: URL", "javascript:alert(1)"],
    ["a data: URL", "data:text/html,<script>alert(1)</script>"],
    ["embedded credentials", "https://user:pass@www.youtube.com/watch?v=dQw4w9WgXcQ"],
    ["a malformed YouTube id", "https://www.youtube.com/watch?v=tooshort"],
    ["a YouTube URL with no id", "https://www.youtube.com/watch"],
    ["a non-numeric Vimeo id", "https://vimeo.com/not-a-number"],
    ["an unparseable string", "definitely not a url"],
    ["an empty string", ""],
  ])("rejects %s", (_label, url) => {
    expect(toEmbed(url)).toBeNull();
    expect(isAllowedVideoUrl(url)).toBe(false);
  });
});

describe("toIsoDuration", () => {
  it.each([
    ["12:45", "PT12M45S"],
    ["1:02:30", "PT1H2M30S"],
    ["0:30", "PT30S"],
  ])("converts %s", (input, expected) => {
    expect(toIsoDuration(input)).toBe(expected);
  });

  it.each(["", "abc", "90", "1:2:3:4", "12:99"])("rejects %s", (input) => {
    expect(toIsoDuration(input)).toBeNull();
  });
});
