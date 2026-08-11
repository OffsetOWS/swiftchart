import baseConfig from "../../frontend/vite.config.js";

export default {
  ...baseConfig,
  server: {
    ...(baseConfig.server || {}),
    proxy: {
      "/api": {
        target: "http://13.140.179.82",
        changeOrigin: true,
      },
      "/health": {
        target: "http://13.140.179.82",
        changeOrigin: true,
      },
    },
  },
};
