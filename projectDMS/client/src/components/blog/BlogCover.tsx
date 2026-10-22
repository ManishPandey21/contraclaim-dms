import { getCategoryLabel } from "@/content/blog/categories";
import type { BlogCategoryId } from "@/content/blog/types";

/**
 * Branded cover graphic used when an entry has no featured image.
 *
 * Inline SVG rather than a raster file: it costs no extra request, scales
 * cleanly, and keeps the blog free of third-party stock imagery. The pattern
 * is derived from the category so the same article always looks the same.
 */

const PALETTES: Record<BlogCategoryId, { from: string; to: string }> = {
  "contract-administration": { from: "#0d1b2e", to: "#1466c4" },
  "extension-of-time": { from: "#10243d", to: "#2a7fd4" },
  "claims-evidence": { from: "#0d1b2e", to: "#2b6cb0" },
  "project-records": { from: "#132b45", to: "#1f7ac0" },
  "construction-technology": { from: "#0f2038", to: "#1466c4" },
  "responsible-ai": { from: "#0d1b2e", to: "#3a80cc" },
};

export function BlogCover({
  category,
  title,
  className,
}: {
  category: BlogCategoryId;
  title: string;
  className?: string;
}) {
  const palette = PALETTES[category] ?? PALETTES["contract-administration"];
  const gradientId = `blog-cover-${category}`;
  const gridId = `blog-cover-grid-${category}`;
  const label = getCategoryLabel(category);

  return (
    <svg
      viewBox="0 0 640 360"
      className={className}
      role="img"
      aria-label={`${label} — ${title}`}
      preserveAspectRatio="xMidYMid slice"
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor={palette.from} />
          <stop offset="100%" stopColor={palette.to} />
        </linearGradient>
        <pattern
          id={gridId}
          width="40"
          height="40"
          patternUnits="userSpaceOnUse"
        >
          <path
            d="M40 0H0V40"
            fill="none"
            stroke="#ffffff"
            strokeOpacity="0.09"
            strokeWidth="1"
          />
        </pattern>
      </defs>

      <rect width="640" height="360" fill={`url(#${gradientId})`} />
      <rect width="640" height="360" fill={`url(#${gridId})`} />
      <circle cx="560" cy="70" r="130" fill="#ffffff" fillOpacity="0.06" />

      {/* Stylised stack of contractual records. */}
      <g transform="translate(56 104)" fill="#ffffff">
        <rect width="132" height="168" rx="8" fillOpacity="0.16" />
        <rect x="16" y="-14" width="132" height="168" rx="8" fillOpacity="0.24" />
        <g fillOpacity="0.55" transform="translate(36 14)">
          <rect width="92" height="7" rx="3.5" />
          <rect y="22" width="72" height="7" rx="3.5" />
          <rect y="44" width="84" height="7" rx="3.5" />
          <rect y="66" width="58" height="7" rx="3.5" />
          <rect y="88" width="80" height="7" rx="3.5" />
        </g>
      </g>

      <text
        x="230"
        y="168"
        fill="#ffffff"
        fillOpacity="0.62"
        fontFamily="'Libre Franklin', system-ui, sans-serif"
        fontSize="17"
        fontWeight="700"
        letterSpacing="2.4"
      >
        {label.toUpperCase()}
      </text>
      <text
        x="230"
        y="212"
        fill="#ffffff"
        fontFamily="'Source Serif 4', Georgia, serif"
        fontSize="30"
        fontWeight="600"
      >
        ContraClaim
      </text>
      <text
        x="230"
        y="244"
        fill="#ffffff"
        fillOpacity="0.7"
        fontFamily="'Libre Franklin', system-ui, sans-serif"
        fontSize="16"
      >
        Contract &amp; Claims Insights
      </text>
    </svg>
  );
}

export default BlogCover;
