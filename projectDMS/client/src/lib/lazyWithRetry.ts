import { ComponentType, lazy } from "react";

// Reload at most once per short window so a genuinely broken deploy surfaces to
// the error boundary instead of reload-looping forever.
const RELOAD_TIMESTAMP_KEY = "chunkReloadAt";
const RELOAD_WINDOW_MS = 10_000;

/**
 * True when `error` is a failed dynamic `import()` of a route chunk — the
 * signature of a stale deploy: the browser tab is still running an old
 * index.html that references chunk hashes the server no longer serves, so the
 * fetch 404s. Covers the Chrome/Edge, Firefox, and Safari phrasings.
 */
export function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? "");
  return (
    /failed to fetch dynamically imported module/i.test(message) ||
    /error loading dynamically imported module/i.test(message) ||
    /importing a module script failed/i.test(message) ||
    /chunkloaderror/i.test(message)
  );
}

function reloadOncePerWindow(): boolean {
  try {
    const last = Number(
      window.sessionStorage.getItem(RELOAD_TIMESTAMP_KEY) || 0
    );
    if (Number.isFinite(last) && Date.now() - last < RELOAD_WINDOW_MS) {
      // Already reloaded moments ago and the chunk still failed — the deploy is
      // genuinely broken, so stop reloading and let the boundary render.
      return false;
    }
    window.sessionStorage.setItem(RELOAD_TIMESTAMP_KEY, String(Date.now()));
  } catch {
    // sessionStorage unavailable (private mode) — still attempt one reload.
  }
  window.location.reload();
  return true;
}

/**
 * Drop-in replacement for React.lazy that recovers from stale-deploy chunk
 * load failures. On a dynamic-import fetch error it triggers a single full
 * page reload to pull the fresh index.html + current chunk hashes. If the
 * import still fails after that (or the error is not a chunk-load error) it
 * rethrows so the nearest error boundary can render.
 */
export function lazyWithRetry<T extends ComponentType<any>>(
  factory: () => Promise<{ default: T }>
) {
  return lazy(() =>
    factory().catch((error: unknown) => {
      if (isChunkLoadError(error) && reloadOncePerWindow()) {
        // Reload is in flight; return a promise that never settles so Suspense
        // keeps showing its fallback instead of flashing the error boundary
        // before the navigation takes effect.
        return new Promise<{ default: T }>(() => {});
      }
      throw error;
    })
  );
}
