import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../app/static/js/dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        qadzone: path.resolve(__dirname, "src/entries/qadzone.jsx"),
        apex:    path.resolve(__dirname, "src/entries/apex.jsx"),
      },
      output: {
        entryFileNames: "[name].bundle.js",
        chunkFileNames: "[name].[hash].js",
        assetFileNames: "[name].[hash][extname]",
      },
    },
  },
  server: {
    // Dev proxy — Vite on 5173, FastAPI on 8000
    proxy: {
      "/agents": "http://localhost:8000",
      "/static":  "http://localhost:8000",
      "/login":   "http://localhost:8000",
    },
  },
});
