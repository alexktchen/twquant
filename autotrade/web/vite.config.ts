import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 程式交易的遙控網站（autotrade A-018）。/api 由 functions/ 底下的 Pages Functions 回答；
// 本機只跑 vite 時沒有後端（頁面會顯示連不上），要連真的後端用 `npx wrangler pages dev`。
export default defineConfig({ plugins: [react()] })
