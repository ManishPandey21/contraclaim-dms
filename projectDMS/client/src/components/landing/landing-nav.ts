/**
 * Public-site navigation model.
 *
 * Shared by the landing page and the blog so a link is declared once and shows
 * up in the desktop nav, the mobile menu and the footer together.
 */

export interface LandingNavItem {
  label: string;
  /** Landing-page section id, without the `#`. */
  section?: string;
  /** Router path for real routes. */
  path?: string;
  /** Show in the footer link row. */
  inFooter?: boolean;
  /** Show in the primary (header + mobile) nav. */
  inPrimaryNav?: boolean;
}

export const LANDING_NAV_ITEMS: LandingNavItem[] = [
  { label: "Platform", section: "platform", inPrimaryNav: true, inFooter: true },
  { label: "Workflow", section: "how", inPrimaryNav: true },
  { label: "How it works", section: "how", inFooter: true },
  { label: "Modules", section: "modules", inFooter: true },
  { label: "Governance", section: "governance", inPrimaryNav: true },
  { label: "FAQ", section: "faq", inPrimaryNav: true, inFooter: true },
  { label: "Blog", path: "/blog", inPrimaryNav: true, inFooter: true },
  { label: "Contact", section: "contact", inFooter: true },
];

export const PRIMARY_NAV_ITEMS = LANDING_NAV_ITEMS.filter(
  (item) => item.inPrimaryNav,
);

export const FOOTER_NAV_ITEMS = LANDING_NAV_ITEMS.filter(
  (item) => item.inFooter,
);

/**
 * Resolve a section link for the current route.
 *
 * On the landing page a bare `#anchor` scrolls in place. Anywhere else — the
 * blog, for instance — the same link has to route home first, otherwise it is
 * a dead link.
 */
export function sectionHref(section: string, pathname: string): string {
  return pathname === "/" ? `#${section}` : `/#${section}`;
}

export function navHref(item: LandingNavItem, pathname: string): string {
  if (item.path) return item.path;
  return sectionHref(item.section ?? "", pathname);
}

/** True when `pathname` is inside the section a route-based nav item points at. */
export function isNavItemActive(
  item: LandingNavItem,
  pathname: string,
): boolean {
  if (!item.path) return false;
  return pathname === item.path || pathname.startsWith(`${item.path}/`);
}
