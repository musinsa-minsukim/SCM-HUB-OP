import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
    proxy: { "/api": { target: "http://localhost:8010", changeOrigin: true } },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules")) {
            if (id.includes("ag-grid")) return "aggrid";
            if (id.includes("react")) return "react";
            return "vendor";
          }
        },
      },
    },
  },
});
