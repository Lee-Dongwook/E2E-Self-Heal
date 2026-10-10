import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// /api/* goes to the FastAPI dev server
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.PRODUCT_API_URL || process.env.PRODUCT_API_URL || "http://localhost:8000";
  const api = { "/api": { target: apiTarget, rewrite: (path: string) => path.replace(/^\//api/, "") } };
  return {
    plugins: [react(), tailwindcss()],
    server: { proxy: api },
    preview: { proxy: api },
  };
});