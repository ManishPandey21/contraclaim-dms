import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
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
