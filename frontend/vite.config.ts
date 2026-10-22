import path from "path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";
import { ADSENSE, adsTxtAsset } from "./src/cloud/ads.ts";

// T-433：開了廣告（src/cloud/ads.ts 填了發布商 ID）才在網站根目錄產生 ads.txt
const adsTxt = (): Plugin => ({
  name: "twquant-ads-txt",
  generateBundle() {
    const asset = adsTxtAsset(ADSENSE);
    if (asset) this.emitFile({ type: "asset", ...asset });
  },
});

export default defineConfig({
  plugins: [react(), viteSingleFile(), adsTxt()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // 本機開發：把 /api 反代到 `twquant serve`（預設 :8000），與前端同源免 CORS。
  // 覆寫後端位址：VITE_API_TARGET=http://host:port npx vite dev
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    assetsInlineLimit: 100000000,
    chunkSizeWarningLimit: 100000,
    cssCodeSplit: false,
  },
});
