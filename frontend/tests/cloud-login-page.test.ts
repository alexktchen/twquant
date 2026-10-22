import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'
import { LOGIN_PAGE } from '../src/cloud/auth.ts'

// T-432：深色登入／註冊頁 /login。T-435 起它是網站自己的頁（React，LoginPage.tsx），登入走 Supabase Auth，
// 不再經過 Cloudflare Access 的白色卡片。
const read = (p: string): string => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
const page = read('src/cloud/LoginPage.tsx')
const css = read('src/cloud/login.css')

test('T-432 AC1 登入頁：/login 由網站自己畫（深色），不再有靜態的 login.html', () => {
  assert.ok(!existsSync(new URL('../public/login.html', import.meta.url)), 'public/login.html 要拿掉（Cloudflare Pages 會優先給靜態檔）')
  assert.match(read('src/main.tsx'), /pathname === '\/login'/)
  assert.match(read('src/main.tsx'), /import\('\.\/cloud\/LoginPage'\)/)
  assert.match(css, /\.lg-page \{[^}]*background: #0e0f14/, '頁面底色要跟網站一樣深')
  assert.doesNotMatch(css, /background:\s*(#fff\b|#ffffff\b|white\b)/i, '不准有白底區塊')
})

test('T-432 AC2 登入頁：Google 與 Email 驗證碼兩條路、隱私權政策連結、第一次登入即註冊；不拿 admin 才有的功能招攬', () => {
  assert.match(page, /signInWithGoogle\(/)
  assert.match(page, /sendEmailCode\(/)
  assert.match(page, /verifyEmailCode\(/)
  assert.match(page, /href="\/privacy"/)
  assert.match(page, /第一次登入就自動完成註冊/)
  assert.match(page, /不是投資建議/)
  // D-208：每日名單與績效只給 admin，登入頁不能拿它們招攬一般使用者
  assert.doesNotMatch(page, /每日名單|名單績效|潛伏飆股|大盤精選/)
  assert.match(page, /新聞搜尋/, '新聞已公開，登入後多的是搜尋')
})

test('T-432 AC3 入口：頂欄「登入」與登入卡「登入／註冊」都到 /login；沒有任何 /api/login、/api/logout', () => {
  assert.equal(LOGIN_PAGE, '/login')
  const app = read('src/cloud/CloudApp.tsx')
  assert.equal((app.match(/href=\{LOGIN_PAGE\}/g) ?? []).length, 2)
  for (const p of ['functions/api/login.ts', 'functions/api/login/google.ts', 'functions/api/logout.ts', 'functions/_lib/logout.ts']) {
    assert.ok(!existsSync(new URL(`../${p}`, import.meta.url)), `${p} 要拿掉`)
  }
  assert.ok(!app.includes('/api/login') && !app.includes('/cdn-cgi/access'), '網站不再連到 Cloudflare Access 的登入／登出')
})

test('T-432 AC4 已登入的人開 /login 直接回首頁；登入還沒設定時說清楚', () => {
  assert.match(page, /hasSession\(\)/)
  assert.match(page, /authEnabled\(\)/)
  assert.match(page, /登入還沒設定/)
})
