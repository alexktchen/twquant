import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  authorizeUrl,
  emailLooksValid,
  gotrueMessage,
  loginCodeFromUrl,
  needsRefresh,
  parseSession,
  pkceChallenge,
  randomVerifier,
  sessionFrom,
  withoutCode,
} from '../src/cloud/session.ts'

// T-439：瀏覽器端的 Supabase 登入（自己打 GoTrue 的 REST，不帶 supabase-js）——純函式的部分
const read = (p: string): string => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
const NOW = 1_760_000_000

test('T-439 AC4 登入狀態：從 /token、/verify 的回應取出要存的欄位；壞的存檔不認', () => {
  const body = { access_token: 'a.b.c', refresh_token: 'r1', expires_in: 3600, token_type: 'bearer', user: { id: 'u', email: 'Me@Example.com' } }
  assert.deepEqual(sessionFrom(body, NOW), { access_token: 'a.b.c', refresh_token: 'r1', expires_at: NOW + 3600, email: 'me@example.com' })
  assert.deepEqual(sessionFrom({ ...body, expires_at: NOW + 99 }, NOW)?.expires_at, NOW + 99, '有 expires_at 就用它')
  assert.equal(sessionFrom({ ...body, user: {} }, NOW), null)
  assert.equal(sessionFrom({ ...body, access_token: '' }, NOW), null)
  assert.equal(sessionFrom(null, NOW), null)
  const s = sessionFrom(body, NOW)!
  assert.deepEqual(parseSession(JSON.stringify(s)), s)
  assert.equal(parseSession('{"access_token":1}'), null)
  assert.equal(parseSession('not json'), null)
  assert.equal(parseSession(null), null)
  // 到期前 60 秒就換新
  assert.equal(needsRefresh(s, NOW), false)
  assert.equal(needsRefresh(s, NOW + 3600 - 61), false)
  assert.equal(needsRefresh(s, NOW + 3600 - 60), true)
  assert.equal(needsRefresh(s, NOW + 9999), true)
})

test('T-439 AC5 Google 登入網址（PKCE）：帶 provider、回來的網址、code_challenge；驗證碼照 RFC 7636 算', async () => {
  const u = new URL(authorizeUrl('https://abc.supabase.co/', 'https://twquant.pages.dev/', 'CHAL'))
  assert.equal(u.origin + u.pathname, 'https://abc.supabase.co/auth/v1/authorize')
  assert.equal(u.searchParams.get('provider'), 'google')
  assert.equal(u.searchParams.get('redirect_to'), 'https://twquant.pages.dev/')
  assert.equal(u.searchParams.get('code_challenge'), 'CHAL')
  assert.equal(u.searchParams.get('code_challenge_method'), 's256')
  // RFC 7636 附錄 B 的例子
  assert.equal(await pkceChallenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'), 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM')
  const v = randomVerifier()
  assert.match(v, /^[A-Za-z0-9_-]{43,128}$/)
  assert.notEqual(v, randomVerifier())
  // 回來的網址：?code= 取出、用過拿掉（其他參數與 hash 保留）
  assert.equal(loginCodeFromUrl('https://twquant.pages.dev/?code=abc-123&tab=news#x'), 'abc-123')
  assert.equal(loginCodeFromUrl('https://twquant.pages.dev/?tab=news'), null)
  assert.equal(loginCodeFromUrl('not a url'), null)
  assert.equal(withoutCode('https://twquant.pages.dev/?code=abc&tab=news#x'), '/?tab=news#x')
  assert.equal(emailLooksValid('a@b.co'), true)
  assert.equal(emailLooksValid('a@b'), false)
  assert.equal(emailLooksValid(' '), false)
})

test('T-439 AC6 GoTrue 的錯誤改成中文；不認得的照原文', () => {
  assert.equal(gotrueMessage({ error_code: 'over_email_send_rate_limit', msg: 'x' }), '寄信次數到上限，請幾分鐘後再試')
  assert.equal(gotrueMessage({ msg: 'Token has expired or is invalid' }), '驗證碼錯誤或已過期，請重寄一次')
  assert.equal(gotrueMessage({ error_code: 'otp_expired' }), '驗證碼錯誤或已過期，請重寄一次')
  assert.equal(gotrueMessage({ error: 'invalid_grant', error_description: 'Invalid Refresh Token: Already Used' }), '登入已失效，請重新登入')
  assert.equal(gotrueMessage({ message: 'Something odd' }), 'Something odd')
  assert.equal(gotrueMessage(null), '登入服務暫時無法使用')
})

test('T-439 AC7 網站每個 /api 請求都帶 Bearer 權杖；/pub 不帶；401 當成要重新登入', () => {
  const api = read('src/cloud/api.ts')
  assert.match(api, /const token = path\.startsWith\('\/pub\/'\) \? null : await getToken\(\)/)
  assert.match(api, /authorization: `Bearer \$\{token\}`/)
  assert.ok(!api.includes('logoutTarget'), '登出改在瀏覽器端清掉登入狀態，沒有 /api/logout')
  const client = read('src/cloud/session-client.ts')
  for (const s of ['grant_type=refresh_token', 'grant_type=pkce', '/auth/v1/otp', '/auth/v1/verify', '/auth/v1/logout', 'apikey']) {
    assert.ok(client.includes(s), `session-client.ts 要有 ${s}`)
  }
  assert.match(client, /create_user: true/, '第一次登入就註冊')
})
