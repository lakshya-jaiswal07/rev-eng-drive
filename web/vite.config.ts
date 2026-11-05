import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// Where the backend lives when it isn't this page's own host. Empty means "same
// host" (local dev, the Render deployment). The Vercel build serves only the
// pages: /api is proxied by vercel.json, but Vercel can't proxy websockets, so
// the drive socket goes straight to Render.
const backendUrl = process.env.VITE_BACKEND_URL ?? (process.env.VERCEL ? "https://routevoice.onrender.com" : "");

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __BACKEND_URL__: JSON.stringify(backendUrl.replace(/\/$/, "")),
  },
  resolve: {
    alias: { "~": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5174,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
