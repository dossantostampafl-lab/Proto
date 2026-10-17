import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  // The live 3D floor (three.js) is a deliberate on-demand chunk; scripts/bundle-budget-check.mjs
  // holds it to its own budget.
  build: { chunkSizeWarningLimit: 600 },
});
