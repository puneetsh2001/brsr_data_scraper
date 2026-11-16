import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The production build is served by FastAPI at /app/ (see app/api/main.py).
// `npm run dev` proxies /api to a locally running API (uvicorn on :8000).
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.js"],
    globals: true,
  },
});
