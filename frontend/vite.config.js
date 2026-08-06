import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import mdx from "@mdx-js/rollup";

const releaseSha = process.env.SWIFTCHART_RELEASE_SHA
  || process.env.VERCEL_GIT_COMMIT_SHA
  || "development";

function releaseManifest() {
  return {
    name: "swiftchart-release-manifest",
    apply: "build",
    generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "release.json",
        source: `${JSON.stringify({ release: releaseSha })}\n`,
      });
    },
  };
}

export default defineConfig({
  plugins: [mdx(), react(), releaseManifest()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("@supabase")) return "supabase";
          if (id.includes("lightweight-charts")) return "charts";
          if (id.includes("node_modules")) return "vendor";
        },
      },
    },
  },
});
