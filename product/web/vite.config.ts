import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget =
    env.PRODUCT_API_URL ||
    process.env.PRODUCT_API_URL ||
    "http://localhost:8000";
  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: {
        "/api": {
          target: apiTarget,
          rewrite: (p: string) => p.replace("/api", ""),
        },
      },
    },
    preview: {
      proxy: {
        "/api": {
          target: apiTarget,
          rewrite: (p: string) => p.replace("/api", ""),
        },
      },
    },
  };
});