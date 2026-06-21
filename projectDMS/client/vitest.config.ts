import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";
import { resolve } from "path";

export default defineConfig({
  plugins: [react(), tsconfigPaths()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/tests/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // Running the whole suite in parallel froze the run (workers deadlocked / the
    // main process GC-thrashed to an OOM). The freeze is parallelism-specific:
    // many heavy full-page jsdom renders run at once, some leave open handles
    // (WebSocket/timers), and the main process buffers large console/DOM output —
    // together they exhaust memory and hang. The same files pass reliably when
    // files run one at a time, so execute serially in a single forked process.
    // Slower but deterministic — the right trade for a CI gate. testTimeout is
    // raised because a few integration renders legitimately take >5s.
    pool: "forks",
    fileParallelism: false,
    testTimeout: 20000,
    // Drop captured app console output. The serial worker accumulates heap across
    // the whole run; a big avoidable consumer is buffered console noise (React
    // act() warnings, router future-flag warnings, app error logs). Suppressing
    // it cuts worker memory so stability doesn't hinge on a large heap. Test
    // failures still print their own assertion diffs; this only drops console.*
    // emitted by application code.
    onConsoleLog: () => false,
    coverage: {
      reporter: ["text", "json", "html"],
      exclude: ["node_modules/", "src/tests/setup.ts"],
    },
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "./src"),
    },
  },
});
