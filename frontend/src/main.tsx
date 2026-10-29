import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { ACCESS_LOGIN, USE_ACCESS_CARD } from './cloud/auth'

// 雲端版（Vercel，T-332／D-170）只載入三頁的精簡網站；地端（Docker）維持完整研究平台。
// 動態載入：兩邊的程式碼各自打包，雲端版不會帶進研究平台的頁面。
// /login（T-432／T-439）：登入／註冊頁，雲端版自己畫（Cloudflare Pages 找不到靜態檔時給 index.html）。
// T-459：USE_ACCESS_CARD＝true 時 /login 直接轉 Cloudflare 的登入卡（舊的白色登入畫面），不畫深色頁。
const root = createRoot(document.getElementById('root')!)
const cloud = import.meta.env.VITE_APP_MODE === 'cloud'
const onLogin = cloud && window.location.pathname === '/login'
if (onLogin && USE_ACCESS_CARD) {
  window.location.replace(ACCESS_LOGIN)
} else {
  const load = cloud ? (onLogin ? import('./cloud/LoginPage') : import('./cloud/CloudApp')) : import('./App.tsx')
  load.then(({ default: Root }) =>
    root.render(
      <StrictMode>
        <Root />
      </StrictMode>,
    ),
  )
}
