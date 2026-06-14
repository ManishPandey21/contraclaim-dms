import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";
import { visualizer } from "rollup-plugin-visualizer";

export default defineConfig({
  plugins: [
    react(),
    // Bundle analyzer
    visualizer({
      filename: "dist/bundle-analysis.html",
      open: true,
      gzipSize: true,
      brotliSize: true,
    }),
  ],
  resolve: {
    alias: {
      "@": resolve(__dirname, "./src"),
    },
  },
  build: {
    // Optimize bundle size
    rollupOptions: {
      output: {
        manualChunks: {
          // Vendor chunks
          "react-vendor": ["react", "react-dom", "react-router-dom"],
          "ui-vendor": ["lucide-react", "clsx"],
          "utils-vendor": ["axios", "date-fns"],

          // Feature-based chunks
          "document-viewer": [
            "./src/components/document-viewer/DocumentViewer",
            "./src/components/document-viewer/DocumentHeader",
            "./src/components/document-viewer/EnclosuresPanel",
            "./src/components/document-viewer/ReferencesPanel",
            "./src/components/document-viewer/MetadataEditor",
          ],
          "letter-workflow": [
            "./src/components/letter-workflow/LetterWorkflowHeader",
            "./src/components/letter-workflow/RequestInputForm",
            "./src/components/letter-workflow/LetterDraftEditor",
            "./src/components/letter-workflow/AIAssistant",
          ],
          "search-components": [
            "./src/components/search/AdvancedSearchFilters",
            "./src/services/search-api",
            "./src/pages/EnhancedDocumentsPage",
          ],
          "dashboard-components": [
            "./src/pages/DashboardPage",
            "./src/components/documents/BulkOperations",
          ],
        },
      },
    },
    // Enable minification
    minify: "terser",
    terserOptions: {
      compress: {
        drop_console: true,
        drop_debugger: true,
      },
    },
    // Optimize chunk size
    chunkSizeWarningLimit: 1000,
    // Enable source maps for production debugging
    sourcemap: true,
  },
  // Optimize dev server
  server: {
    hmr: {
      overlay: false,
    },
  },
  // Enable CSS code splitting
  css: {
    devSourcemap: true,
  },
  // Optimize dependencies
  optimizeDeps: {
    include: [
      "react",
      "react-dom",
      "react-router-dom",
      "axios",
      "lucide-react",
      "clsx",
    ],
    exclude: [
      // Exclude large libraries that should be loaded on demand
      "@pdf-lib/fontkit",
      "pdfjs-dist",
    ],
  },
});
