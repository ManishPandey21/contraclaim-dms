import type { BlogVideo } from "../types";

/**
 * Published video content.
 *
 * Empty at launch: no video URLs have been supplied for this series yet. The
 * Videos tab renders its empty state until entries are added here, and no
 * placeholder or third-party video is published in the meantime.
 *
 * To add one, append an entry in the shape below. `videoUrl` is validated
 * against the provider allowlist in `../video-embed.ts` (YouTube and Vimeo,
 * https only) when the registry is built; an entry whose URL does not validate
 * is dropped and never reaches an iframe.
 *
 * @example
 * {
 *   id: "video-eot-records-walkthrough",
 *   type: "video",
 *   title: "Walkthrough: the seven EOT record groups",
 *   slug: "seven-eot-record-groups-walkthrough",
 *   excerpt: "A short walkthrough of the seven record groups behind an EOT claim.",
 *   description: "Longer description shown on the video page.",
 *   videoUrl: "https://www.youtube.com/watch?v=XXXXXXXXXXX",
 *   videoDuration: "12:45",
 *   isoDuration: "PT12M45S",
 *   category: "extension-of-time",
 *   tags: ["EOT", "Claims Evidence"],
 *   author: "ContraClaim Editorial",
 *   publishedAt: "2026-08-06",
 *   status: "published",
 *   order: 1,
 *   seoTitle: "Walkthrough: the seven EOT record groups | ContraClaim",
 *   metaDescription: "A short walkthrough of the seven record groups behind an EOT claim.",
 * }
 */
export const blogVideos: BlogVideo[] = [];
