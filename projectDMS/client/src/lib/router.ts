export type NavigationOptions = {
  replace?: boolean;
  state?: any;
};

type NavigatorFn = (path: string, options?: NavigationOptions) => void;

let navigatorFn: NavigatorFn | null = null;

/**
 * Registers the SPA navigator obtained from React Router's useNavigate.
 * Called once at app bootstrap.
 */
export function setNavigator(fn: NavigatorFn) {
  navigatorFn = fn;
}

/**
 * Navigate within the SPA if navigator is ready; otherwise hard-redirect.
 */
export function navigateTo(path: string, options?: NavigationOptions) {
  if (navigatorFn) {
    navigatorFn(path, options);
  } else {
    // Fallback: ensure app resets state even if router not initialized yet
    if (options?.replace) {
      window.location.replace(path);
    } else {
      window.location.assign(path);
    }
  }
}
