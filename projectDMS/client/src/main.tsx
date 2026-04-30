import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./index.css";

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

createRoot(document.getElementById("root")!).render(<App />);
