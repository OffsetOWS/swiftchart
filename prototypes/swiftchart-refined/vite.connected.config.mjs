import baseConfig from "../../frontend/vite.config.js";

const apiTarget = process.env.SWIFTCHART_API_BASE_URL || "http://127.0.0.1:8000";

export default {
  ...baseConfig,
  server: {
    ...(baseConfig.server || {}),
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/health": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
};
