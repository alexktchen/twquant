import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'
import { afterLogin, viaMainLogin } from '../functions/_lib/auth.ts'
import { ACCESS_LOGIN, ACCESS_LOGOUT, GOOGLE_LOGIN, LOGIN_PAGE, USE_ACCESS_CARD } from '../src/cloud/auth.ts'
import { gotrueMessage } from '../src/cloud/session.ts'

// T-432：深色登入／註冊頁 /login（React，LoginPage.tsx）。Email 驗證碼走 Supabase Auth（T-439）；
// Google 走 Cloudflare Access 的 Google 一鍵登入（T-457／D-210 修訂：Supabase 被限流、Google provider 沒開）。
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
  assert.match(page, /href=\{GOOGLE_LOGIN\}/)
  assert.doesNotMatch(page, /signInWithGoogle\(/, 'Google 不再經過 Supabase')
  assert.match(page, /sendEmailCode\(/)
  assert.match(page, /verifyEmailCode\(/)
  assert.match(page, /href="\/privacy"/)
  assert.match(page, /第一次登入就自動完成註冊/)
  assert.match(page, /不是投資建議/)
  // D-208：每日名單與績效只給 admin，登入頁不能拿它們招攬一般使用者
  assert.doesNotMatch(page, /每日名單|名單績效|潛伏飆股|大盤精選/)
  assert.match(page, /新聞搜尋/, '新聞已公開，登入後多的是搜尋')
})

test('T-432 AC3 入口：頂欄「登入」與登入卡「登入／註冊」都到 /login；沒有 /api/logout', () => {
  assert.equal(LOGIN_PAGE, '/login')
  const app = read('src/cloud/CloudApp.tsx')
  assert.equal((app.match(/href=\{LOGIN_PAGE\}/g) ?? []).length, 2)
  for (const p of ['functions/api/logout.ts', 'functions/_lib/logout.ts']) {
    assert.ok(!existsSync(new URL(`../${p}`, import.meta.url)), `${p} 要拿掉`)
  }
  assert.ok(!app.includes('/api/login'), '網站其他地方的登入都先到 /login')
})

test('T-457 AC1 Google 一鍵登入：/api/login/google 先轉 /api/login 換主應用通行證，/api/login 再回首頁', () => {
  assert.equal(GOOGLE_LOGIN, '/api/login/google')
  for (const p of ['functions/api/login.ts', 'functions/api/login/google.ts']) {
    assert.ok(existsSync(new URL(`../${p}`, import.meta.url)), `${p} 要在`)
  }
  const g = viaMainLogin(new Request('https://twquant.pages.dev/api/login/google'))
  assert.equal(g.status, 302)
  assert.equal(g.headers.get('location'), 'https://twquant.pages.dev/api/login')
  const l = afterLogin(new Request('https://twquant.pages.dev/api/login'))
  assert.equal(l.status, 302)
  assert.equal(l.headers.get('location'), 'https://twquant.pages.dev/')
  assert.equal(l.headers.get('cache-control'), 'no-store')
})

test('T-457 AC2 用 Cloudflare 登入的人登出要請 Cloudflare 清通行證；Supabase 的照舊回首頁', () => {
  const app = read('src/cloud/CloudApp.tsx')
  assert.equal(ACCESS_LOGOUT, '/cdn-cgi/access/logout')
  assert.match(app, /const viaAccess = !hasSession\(\)/)
  assert.match(app, /viaAccess \? ACCESS_LOGOUT : '\/'/)
})

test('T-457 AC3 Supabase 限流（402 exceed_egress_quota）改成中文，並指向 Google 登入', () => {
  const msg = gotrueMessage({
    message: 'Service for this project is restricted due to the following violations: exceed_egress_quota. The project owner must upgrade their plan or remove spend caps to restore service.',
  })
  assert.match(msg, /Email 登入暫停中/)
  assert.match(msg, /Google/)
  assert.doesNotMatch(msg, /restricted/)
})

test('T-432 AC4 已登入的人開 /login 直接回首頁；登入還沒設定時說清楚', () => {
  assert.match(page, /hasSession\(\)/)
  assert.match(page, /authEnabled\(\)/)
  assert.match(page, /登入還沒設定/)
  // 沒設定 Supabase 時 Google 照樣能用（T-457）
  assert.ok(page.indexOf('href={GOOGLE_LOGIN}') < page.indexOf('!ready ?'), 'Google 按鈕不要被 Supabase 設定擋住')
})

test('T-459 AC1 舊的登入畫面（使用者 2026-10-29：「我要舊的登入畫面」→ Cloudflare 的白色登入卡）：/login 直接轉 /api/login，不畫深色頁、不經 Supabase', () => {
  assert.equal(USE_ACCESS_CARD, true)
  assert.equal(ACCESS_LOGIN, '/api/login')
  const main = read('src/main.tsx')
  assert.match(main, /if \(onLogin && USE_ACCESS_CARD\) \{\s*window\.location\.replace\(ACCESS_LOGIN\)/)
  // 轉過去之前不載入任何頁面（深色頁的程式留著，開關改回 false 就恢復）
  assert.ok(main.indexOf('window.location.replace(ACCESS_LOGIN)') < main.indexOf("import('./cloud/LoginPage')"))
  // /api/login 受 Cloudflare Access 保護：登入完回首頁（同 T-457 AC1）
  const l = afterLogin(new Request('https://twquant.pages.dev/api/login'))
  assert.equal(l.headers.get('location'), 'https://twquant.pages.dev/')
  // 網站上的登入入口照舊都到 /login，再由這裡轉
  assert.equal(LOGIN_PAGE, '/login')
})
