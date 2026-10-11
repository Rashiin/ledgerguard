import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs separately. LEDGERGUARD_API points the proxy at it.
const api = process.env.LEDGERGUARD_API ?? "http://127.0.0.1:8610";

export default defineConfig({
  plugins: [react()],
  server: { port: 5610, strictPort: true, proxy: { "/api": api } },
  build: { outDir: "dist", sourcemap: false },
});
