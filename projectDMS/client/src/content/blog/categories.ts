import type { BlogCategory, BlogCategoryId } from "./types";

/**
 * Blog taxonomy. Matches the site taxonomy agreed in the construction-claims
 * editorial plan, so categories stay stable as the series grows.
 */
export const BLOG_CATEGORIES: BlogCategory[] = [
  {
    id: "contract-administration",
    label: "Contract Administration",
    description: "Notices, entitlement mapping and day-to-day contractual control.",
  },
  {
    id: "extension-of-time",
    label: "Extension of Time",
    description: "EOT entitlement, programme evidence and delay records.",
  },
  {
    id: "claims-evidence",
    label: "Claims Evidence",
    description: "Building and testing the contemporaneous record behind a claim.",
  },
  {
    id: "project-records",
    label: "Project Records",
    description: "Registers, document control and connected contractual records.",
  },
  {
    id: "construction-technology",
    label: "Construction Technology",
    description: "Systems and tooling for managed project information.",
  },
  {
    id: "responsible-ai",
    label: "Responsible AI",
    description: "Controlled, source-grounded and human-reviewed use of AI.",
  },
];

const CATEGORY_BY_ID = new Map<BlogCategoryId, BlogCategory>(
  BLOG_CATEGORIES.map((category) => [category.id, category]),
);

export function getCategory(id: BlogCategoryId): BlogCategory | undefined {
  return CATEGORY_BY_ID.get(id);
}

export function getCategoryLabel(id: BlogCategoryId): string {
  return CATEGORY_BY_ID.get(id)?.label ?? id;
}

export function isBlogCategoryId(value: string): value is BlogCategoryId {
  return CATEGORY_BY_ID.has(value as BlogCategoryId);
}
