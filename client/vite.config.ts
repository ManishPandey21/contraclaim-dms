import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  build: {
    target: "esnext",
    chunkSizeWarningLimit: 500,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("@react-pdf-viewer") || id.includes("pdfjs-dist")) {
            return "vendor-pdf";
          }
          if (id.includes("@tiptap")) {
            return "vendor-editor";
          }
          if (id.includes("recharts")) {
            return "vendor-charts";
          }
          if (id.includes("@radix-ui")) {
            return "vendor-radix";
          }
          if (
            id.includes("react-dom") ||
            id.includes("react-router-dom") ||
            /node_modules[\\/](react|scheduler)[\\/]/.test(id)
          ) {
            return "vendor-react";
          }
        },
      },
    },
  },
  optimizeDeps: {
    include: [
      "@react-pdf-viewer/core",
      "@react-pdf-viewer/default-layout",
      "@react-pdf-viewer/search",
      "@react-pdf-viewer/zoom",
      "pdfjs-dist",
    ],
    esbuildOptions: {
      // Force proper CJS → ESM named-export interop.
      // Without this, esbuild may produce only a default export wrapper
      // for CommonJS packages like @react-pdf-viewer/core, breaking
      // named imports (Viewer, Worker, SpecialZoomLevel, etc.).
      supported: { "dynamic-import": true },
    },
    // Force Vite to always re-bundle these deps (bypasses hash-based caching)
    force: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        secure: false,
      },
      "/login": {
        // Proxy /login -> backend /api/login (local dev)
        target: "http://localhost:8000/api",
        changeOrigin: true,
        secure: false,
        bypass: (req) => {
          const accept = Array.isArray(req.headers.accept)
            ? req.headers.accept.join(",")
            : req.headers.accept || "";
          if (req.method === "GET" && accept.includes("text/html")) {
            return "/index.html";
          }
        },
      },
      "/refresh": {
        // Proxy /refresh -> backend /api/refresh (local dev)
        target: "http://localhost:8000/api",
        changeOrigin: true,
        secure: false,
      },
      "/logout": {
        // Proxy /logout -> backend /api/logout (local dev)
        target: "http://localhost:8000/api",
        changeOrigin: true,
        secure: false,
      },
    },
  },
  plugins: [react(), mode === "development" && componentTagger()].filter(
    Boolean
  ),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
}));
