import { createRoot, hydrateRoot } from "react-dom/client";
import App from "./App.tsx";
import "./index.css";
import { ensureCsrfToken, installCsrfFetchInterceptor } from "./services/http";

if (
  typeof import.meta !== "undefined" &&
  import.meta.env &&
  import.meta.env.MODE === "production"
) {
  const noop = () => {};
  console.log = noop;
  console.info = noop;
  console.debug = noop;
  console.warn = noop;
}

installCsrfFetchInterceptor();
void ensureCsrfToken();

const root = document.getElementById("root")!;
if (root.hasChildNodes()) {
  hydrateRoot(root, <App />);
} else {
  createRoot(root).render(<App />);
}
