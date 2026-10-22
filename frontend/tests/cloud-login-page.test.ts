import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { viaMainLogin } from '../functions/_lib/auth.ts'
import { EMAIL_LOGIN_URL, GOOGLE_LOGIN_URL, LOGIN_PAGE } from '../src/cloud/auth.ts'

// T-432：Cloudflare Access 的登入卡片固定是白的（只能改背景、logo、上下文字），
// 所以「登入／註冊」改成先到本站的深色頁 /login，再分 Google、Email 兩條路進 Access。
const read = (p: string): string => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
const login = read('public/login.html')

test('T-432 AC1 登入頁：純靜態深色頁，不載腳本、不呼叫 API', () => {
  assert.doesNotMatch(login, /<script/i)
  assert.doesNotMatch(login, /fetch\(/)
  assert.match(login, /<html lang="zh-Hant">/)
  assert.match(login, /body \{[^}]*background: #0e0f14/, '頁面底色要跟網站一樣深')
  assert.doesNotMatch(login, /background:\s*(#fff\b|#ffffff\b|white\b)/i, '不准有白底區塊')
})

test('T-432 AC2 登入頁：Google 與 Email 兩條路各自連到 Access 保護的入口，並附隱私權政策', () => {
  const hrefs = [...login.matchAll(/href="([^"]+)"/g)].map((m) => m[1])
  assert.ok(hrefs.includes(GOOGLE_LOGIN_URL), 'Google 按鈕')
  assert.ok(hrefs.includes(EMAIL_LOGIN_URL), 'Email 驗證碼按鈕')
  assert.ok(hrefs.includes('/privacy') && hrefs.includes('/about') && hrefs.includes('/'))
  // 頁面只連到這兩個 /api 入口，不碰任何資料 API
  assert.deepEqual(
    hrefs.filter((h) => h.startsWith('/api/')).sort(),
    [EMAIL_LOGIN_URL, GOOGLE_LOGIN_URL].sort(),
  )
  assert.match(login, /第一次登入就自動完成註冊/)
  // D-208：每日名單與績效只給 admin，登入頁不能拿它們招攬一般使用者
  assert.doesNotMatch(login, /每日名單|名單績效|潛伏飆股|大盤精選/)
  assert.match(login, /不是投資建議/)
})

test('T-432 AC3 入口路徑：Google 在 /api/login/ 底下（Access 保護、可單獨設一個應用），登入卡與頂欄都先到 /login', () => {
  assert.equal(LOGIN_PAGE, '/login')
  assert.ok(GOOGLE_LOGIN_URL.startsWith('/api/login/'), 'Google 入口要比 /api 更深一層，才能在 Cloudflare 另設只開 Google 的應用')
  assert.equal(EMAIL_LOGIN_URL, '/api/login')
  const app = read('src/cloud/CloudApp.tsx')
  assert.doesNotMatch(app, /href=\{LOGIN_URL\}/, '網站裡的登入按鈕不再直接進 Cloudflare 的登入頁')
  assert.equal((app.match(/href=\{LOGIN_PAGE\}/g) ?? []).length, 2, '頂欄「登入」與登入卡「登入／註冊」都到 /login')
})

test('T-432 AC4 /api/login/google：登入完先轉 /api/login 換主應用的通行證，不准共用暫存', () => {
  const res = viaMainLogin(new Request('https://twquant.pages.dev/api/login/google'))
  assert.equal(res.status, 302)
  assert.equal(res.headers.get('location'), 'https://twquant.pages.dev/api/login')
  assert.equal(res.headers.get('cache-control'), 'no-store')
  const fn = read('functions/api/login/google.ts')
  assert.match(fn, /viaMainLogin/)
  assert.ok(!fn.includes('_lib/cache'))
})
