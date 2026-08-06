/**
 * Video provider allowlist and embed-URL construction.
 *
 * Nothing reaches an iframe unless it parses as an https URL on an allowlisted
 * host and yields a well-formed video id. `toEmbed` never passes any part of
 * the input URL through to the embed — it rebuilds the embed URL from the
 * extracted id, so query strings, fragments, credentials and path traversal in
 * the source URL cannot influence what the iframe loads.
 */

export type VideoProvider = "youtube" | "vimeo";

export interface VideoEmbed {
  provider: VideoProvider;
  videoId: string;
  /** URL loaded in the iframe. Built from the id, never from raw input. */
  embedUrl: string;
  /** Canonical public watch page, used for schema.org and "open on" links. */
  watchUrl: string;
}

const YOUTUBE_HOSTS = new Set([
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "youtu.be",
  "www.youtu.be",
  "youtube-nocookie.com",
  "www.youtube-nocookie.com",
]);

const VIMEO_HOSTS = new Set([
  "vimeo.com",
  "www.vimeo.com",
  "player.vimeo.com",
]);

const YOUTUBE_ID_RE = /^[A-Za-z0-9_-]{11}$/;
const VIMEO_ID_RE = /^\d{6,12}$/;

export const ALLOWED_VIDEO_HOSTS: string[] = [
  ...YOUTUBE_HOSTS,
  ...VIMEO_HOSTS,
].sort();

function extractYouTubeId(url: URL): string | null {
  const segments = url.pathname.split("/").filter(Boolean);

  if (url.hostname === "youtu.be" || url.hostname === "www.youtu.be") {
    return segments[0] ?? null;
  }
  if (segments[0] === "embed" || segments[0] === "shorts" || segments[0] === "v") {
    return segments[1] ?? null;
  }
  if (segments[0] === "watch") {
    return url.searchParams.get("v");
  }
  return url.searchParams.get("v");
}

function extractVimeoId(url: URL): string | null {
  const segments = url.pathname.split("/").filter(Boolean);
  if (segments[0] === "video") return segments[1] ?? null;
  return segments[0] ?? null;
}

/**
 * Validate a video URL and build its embed. Returns null for anything that is
 * not an https URL on an allowlisted host with a well-formed video id.
 */
export function toEmbed(rawUrl: string): VideoEmbed | null {
  if (typeof rawUrl !== "string" || !rawUrl.trim()) return null;

  let url: URL;
  try {
    url = new URL(rawUrl.trim());
  } catch {
    return null;
  }

  // https only: an http embed would be blocked as mixed content anyway, and
  // other schemes (javascript:, data:) must never reach an iframe src.
  if (url.protocol !== "https:") return null;
  // Embedded credentials are never legitimate here.
  if (url.username || url.password) return null;

  const host = url.hostname.toLowerCase();

  if (YOUTUBE_HOSTS.has(host)) {
    const videoId = extractYouTubeId(url);
    if (!videoId || !YOUTUBE_ID_RE.test(videoId)) return null;
    return {
      provider: "youtube",
      videoId,
      // youtube-nocookie defers cookie setting until playback starts.
      // No autoplay parameter is set, by design.
      embedUrl: `https://www.youtube-nocookie.com/embed/${videoId}?rel=0`,
      watchUrl: `https://www.youtube.com/watch?v=${videoId}`,
    };
  }

  if (VIMEO_HOSTS.has(host)) {
    const videoId = extractVimeoId(url);
    if (!videoId || !VIMEO_ID_RE.test(videoId)) return null;
    return {
      provider: "vimeo",
      videoId,
      embedUrl: `https://player.vimeo.com/video/${videoId}?dnt=1`,
      watchUrl: `https://vimeo.com/${videoId}`,
    };
  }

  return null;
}

export function isAllowedVideoUrl(rawUrl: string): boolean {
  return toEmbed(rawUrl) !== null;
}

/**
 * Convert a display duration ("12:45", "1:02:30") to an ISO-8601 duration for
 * VideoObject schema. Returns null when the input is not a clock duration.
 */
export function toIsoDuration(display: string): string | null {
  const parts = display.trim().split(":");
  if (parts.length < 2 || parts.length > 3) return null;
  if (!parts.every((part) => /^\d{1,2}$/.test(part))) return null;

  const [hours, minutes, seconds] =
    parts.length === 3
      ? parts.map(Number)
      : [0, Number(parts[0]), Number(parts[1])];

  if (minutes > 59 || seconds > 59) return null;

  const time = [
    hours ? `${hours}H` : "",
    minutes ? `${minutes}M` : "",
    seconds ? `${seconds}S` : "",
  ].join("");

  return time ? `PT${time}` : null;
}
