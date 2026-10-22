import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `base` is set for GitHub Pages sub-path hosting later (`/lexicon/`); defaults to `/` for
// the desktop (Tauri) build and custom-domain hosting. See prompt.md Phase 5.
export default defineConfig({
  plugins: [react()],
  base: process.env.VITE_BASE ?? "/",
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  server: {
    port: 5173,
    strictPort: true,
  },
});
