import { useEffect, useState } from "react";

/** Cards per page above and below the `md` breakpoint. */
export const DESKTOP_PAGE_SIZE = 6;
export const MOBILE_PAGE_SIZE = 4;

/** Tailwind's `md` breakpoint, which is where the card grid goes multi-column. */
const DESKTOP_QUERY = "(min-width: 768px)";

export function pageSizeFor(isDesktop: boolean): number {
  return isDesktop ? DESKTOP_PAGE_SIZE : MOBILE_PAGE_SIZE;
}

/**
 * Cards per page for the current viewport: 6 on desktop, 4 on smaller screens.
 *
 * Falls back to the desktop size when `matchMedia` is unavailable, so a
 * non-browser environment renders the fuller list rather than an arbitrarily
 * truncated one.
 */
export function usePageSize(): number {
  const [isDesktop, setIsDesktop] = useState(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return true;
    }
    return window.matchMedia(DESKTOP_QUERY).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }

    const query = window.matchMedia(DESKTOP_QUERY);
    const onChange = (event: MediaQueryListEvent) => setIsDesktop(event.matches);
    setIsDesktop(query.matches);

    // Safari below 14 only has the deprecated listener API.
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    }
    query.addListener(onChange);
    return () => query.removeListener(onChange);
  }, []);

  return pageSizeFor(isDesktop);
}
