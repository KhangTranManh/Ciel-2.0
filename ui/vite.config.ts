import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Browser-first dev server. When wrapped by Tauri later, Tauri points at this same
// dev server (dev) or the dist/ build (release) — no code changes needed here.
export default defineConfig({
  plugins: [react()],
  // Fixed port so the (future) Tauri config can hardcode the dev URL.
  server: {
    port: 1420,
    strictPort: true,
  },
  clearScreen: false,
});
