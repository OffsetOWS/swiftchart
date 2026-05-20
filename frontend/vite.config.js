import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import mdx from "@mdx-js/rollup";

export default defineConfig({
  plugins: [mdx(), react()],
  build: {
    modulePreload: {
      resolveDependencies(_url, deps) {
        return deps.filter((dep) => !dep.includes("genlayer-"));
      },
    },
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (
            id.includes("genlayer-js") ||
            id.includes("node_modules/viem") ||
            id.includes("node_modules/ox") ||
            id.includes("node_modules/abitype") ||
            id.includes("node_modules/@noble") ||
            id.includes("node_modules/@scure")
          ) {
            return "genlayer";
          }
          if (id.includes("@supabase")) return "supabase";
          if (id.includes("lightweight-charts")) return "charts";
          if (id.includes("node_modules")) return "vendor";
        },
      },
    },
  },
});
