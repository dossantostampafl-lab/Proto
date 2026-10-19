import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  // The live 3D floor is loaded on demand in three chunks: Floor3D (the scene code), Floor3DThree
  // (three.js itself) and Floor3DAssets (the glTF loader). None is preloaded;
  // scripts/bundle-budget-check.mjs holds each to its own budget.
  build: {
    chunkSizeWarningLimit: 600,
    rolldownOptions: {
      output: {
        advancedChunks: { groups: [{ name: "Floor3DThree", test: /node_modules[\\/]three[\\/]build[\\/]/ }] },
      },
    },
  },
});
