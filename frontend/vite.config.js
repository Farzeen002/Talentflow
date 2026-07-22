import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3000,
    watch: {
      // Reliable HMR when source is bind-mounted into Docker (esp. on Windows)
      usePolling: true,
    },
    // Optional local proxy if a page still uses /gcs-proxy/... instead of the
    // signed GCS URL. Without this, those paths fall through to the SPA and the
    // dashboard renders inside the resume iframe.
    proxy: {
      "/gcs-proxy": {
        target: "https://storage.googleapis.com",
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace(/^\/gcs-proxy/, ""),
      },
    },
  },
});
