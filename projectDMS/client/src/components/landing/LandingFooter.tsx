import { Link, useLocation } from "react-router-dom";

import { FOOTER_NAV_ITEMS, isNavItemActive, navHref } from "./landing-nav";

/**
 * Public-site footer, shared by the landing page and the blog.
 */
export function LandingFooter() {
  const { pathname } = useLocation();

  return (
    <footer className="border-t border-ink/10 bg-paper">
      <div className="container flex flex-col gap-4 py-8 text-sm text-ink/55 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-3">
          <img
            src="/page.png"
            alt=""
            className="h-8 w-8 rounded-md object-cover"
            aria-hidden="true"
          />
          <span className="font-bold text-ink">ContraClaim DMS</span>
          <span className="text-ink/40">© 2026</span>
        </div>
        <nav
          aria-label="Footer"
          className="flex flex-wrap gap-x-5 gap-y-2 font-semibold"
        >
          {FOOTER_NAV_ITEMS.map((item) => {
            const active = isNavItemActive(item, pathname);

            return item.path ? (
              <Link
                key={item.label}
                to={item.path}
                className={active ? "text-brand" : "hover:text-brand"}
                aria-current={active ? "page" : undefined}
              >
                {item.label}
              </Link>
            ) : (
              <a
                key={item.label}
                href={navHref(item, pathname)}
                className="hover:text-brand"
              >
                {item.label}
              </a>
            );
          })}
          <Link to="/login" className="hover:text-brand">
            Sign in
          </Link>
        </nav>
      </div>
    </footer>
  );
}

export default LandingFooter;
