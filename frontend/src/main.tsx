import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

// 雲端版（Vercel，T-332／D-170）只載入三頁的精簡網站；地端（Docker）維持完整研究平台。
// 動態載入：兩邊的程式碼各自打包，雲端版不會帶進研究平台的頁面。
// /login（T-432／T-435）：登入／註冊頁，雲端版自己畫（Cloudflare Pages 找不到靜態檔時給 index.html）。
const root = createRoot(document.getElementById('root')!)
const cloud = import.meta.env.VITE_APP_MODE === 'cloud'
const load = cloud ? (window.location.pathname === '/login' ? import('./cloud/LoginPage') : import('./cloud/CloudApp')) : import('./App.tsx')
load.then(({ default: Root }) =>
  root.render(
    <StrictMode>
      <Root />
    </StrictMode>,
  ),
)
