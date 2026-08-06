import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { ArrowRight, Menu } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

import {
  PRIMARY_NAV_ITEMS,
  isNavItemActive,
  navHref,
  sectionHref,
} from "./landing-nav";

/**
 * Public-site header, shared by the landing page and the blog.
 *
 * Extracted from LandingPage so navigation has one definition. It also adds
 * the mobile menu the landing page previously lacked — below `md` the nav was
 * simply hidden with no alternative.
 */
export function LandingHeader() {
  const { pathname } = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const contactHref = sectionHref("contact", pathname);

  return (
    <header className="sticky top-0 z-50 border-b border-ink/10 bg-paper/90 backdrop-blur-xl">
      <div className="container flex h-[4.5rem] items-center justify-between gap-4 md:gap-6">
        <Link
          to="/"
          className="flex items-center gap-3 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2"
          aria-label="ContraClaim DMS home"
        >
          <img
            src="/contraclaim2.png"
            alt="ContraClaim DMS"
            className="h-9 w-auto"
            width={1077}
            height={231}
            decoding="async"
          />
        </Link>

        <nav
          aria-label="Primary"
          className="hidden items-center gap-4 text-[13px] font-semibold text-ink/70 md:flex xl:gap-7 xl:text-sm"
        >
          {PRIMARY_NAV_ITEMS.map((item) => {
            const active = isNavItemActive(item, pathname);
            const className = cn(
              "rounded-md transition hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2",
              active && "text-brand underline decoration-2 underline-offset-8",
            );

            return item.path ? (
              <Link
                key={item.label}
                to={item.path}
                className={className}
                aria-current={active ? "page" : undefined}
              >
                {item.label}
              </Link>
            ) : (
              <a key={item.label} href={navHref(item, pathname)} className={className}>
                {item.label}
              </a>
            );
          })}
        </nav>

        <div className="flex items-center gap-2 sm:gap-3">
          <Button
            asChild
            variant="ghost"
            className="hidden text-ink hover:bg-ink/5 hover:text-brand sm:inline-flex"
          >
            <Link to="/login">Sign in</Link>
          </Button>
          <Button
            asChild
            className="hidden gap-2 rounded-full bg-brand text-white shadow-[0_8px_20px_-6px_rgba(20,102,196,.5)] hover:bg-[#1157a8] sm:inline-flex"
          >
            <a href={contactHref}>
              Request a demo <ArrowRight className="h-4 w-4" />
            </a>
          </Button>

          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="text-ink hover:bg-ink/5 hover:text-brand md:hidden"
                aria-label="Open menu"
              >
                <Menu className="h-5 w-5" />
              </Button>
            </SheetTrigger>
            <SheetContent
              side="right"
              className="w-[85vw] max-w-sm bg-paper font-franklin text-ink"
            >
              <SheetTitle className="text-left font-serif text-lg font-semibold text-ink">
                Menu
              </SheetTitle>
              <nav aria-label="Mobile" className="mt-6 flex flex-col gap-1">
                {PRIMARY_NAV_ITEMS.map((item) => {
                  const active = isNavItemActive(item, pathname);
                  const className = cn(
                    "rounded-lg px-3 py-2.5 text-base font-semibold text-ink/80 transition hover:bg-brand-soft hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
                    active && "bg-brand-soft text-brand",
                  );

                  return item.path ? (
                    <SheetClose asChild key={item.label}>
                      <Link
                        to={item.path}
                        className={className}
                        aria-current={active ? "page" : undefined}
                      >
                        {item.label}
                      </Link>
                    </SheetClose>
                  ) : (
                    <SheetClose asChild key={item.label}>
                      <a href={navHref(item, pathname)} className={className}>
                        {item.label}
                      </a>
                    </SheetClose>
                  );
                })}
              </nav>

              <div className="mt-6 flex flex-col gap-3 border-t border-ink/10 pt-6">
                <SheetClose asChild>
                  <Button
                    asChild
                    className="gap-2 rounded-full bg-brand text-white hover:bg-[#1157a8]"
                  >
                    <a href={contactHref}>
                      Request a demo <ArrowRight className="h-4 w-4" />
                    </a>
                  </Button>
                </SheetClose>
                <SheetClose asChild>
                  <Button
                    asChild
                    variant="outline"
                    className="rounded-full border-ink/15 text-ink"
                  >
                    <Link to="/login">Sign in</Link>
                  </Button>
                </SheetClose>
              </div>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
  );
}

export default LandingHeader;
