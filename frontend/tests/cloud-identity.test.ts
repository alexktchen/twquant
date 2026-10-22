import assert from 'node:assert/strict'
import test from 'node:test'
import { me } from '../functions/_lib/auth.ts'
import { ACCESS_AUD, ACCESS_TEAM, JWT_HEADER, supabaseEmail, userEmail } from '../functions/_lib/identity.ts'

// T-439／D-210：登入換成 Supabase Auth。API 從 Authorization: Bearer 驗 Supabase 簽的權杖；
// 過渡期（Cloudflare 還沒關掉 /api 的 Access）Access 的權杖照收。
const NOW = 1_760_000_000_000
const SUPABASE_URL = 'https://abc.supabase.co'
const enc = (s: string) => new TextEncoder().encode(s)
const b64u = (bytes: Uint8Array | ArrayBuffer) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
const part = (o: unknown) => b64u(enc(JSON.stringify(o)))

/** 一把 ES256 金鑰（Supabase 新專案的簽章方式）與它的 JWKS */
async function es256(kid = 'ec-1') {
  const pair = (await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify'])) as CryptoKeyPair
  const jwk = await crypto.subtle.exportKey('jwk', pair.publicKey)
  const jwks = (async () => new Response(JSON.stringify({ keys: [{ kid, kty: 'EC', crv: 'P-256', alg: 'ES256', use: 'sig', x: jwk.x, y: jwk.y }] }))) as unknown as typeof fetch
  const sign = async (claims: Record<string, unknown>, header: Record<string, unknown> = { alg: 'ES256', kid, typ: 'JWT' }) => {
    const body = `${part(header)}.${part(claims)}`
    const sig = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, pair.privateKey, enc(body))
    return `${body}.${b64u(sig)}`
  }
  return { jwks, sign }
}

/** 舊專案的 HS256（共用祕密） */
async function hs256(secret: string) {
  const key = await crypto.subtle.importKey('raw', enc(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'])
  return async (claims: Record<string, unknown>, header: Record<string, unknown> = { alg: 'HS256', typ: 'JWT' }) => {
    const body = `${part(header)}.${part(claims)}`
    return `${body}.${b64u(await crypto.subtle.sign('HMAC', key, enc(body)))}`
  }
}

const good = {
  iss: `${SUPABASE_URL}/auth/v1`,
  aud: 'authenticated',
  role: 'authenticated',
  sub: 'user-1',
  email: 'Someone@Example.com',
  exp: NOW / 1000 + 3600,
  iat: NOW / 1000,
}
const bearer = (token?: string) =>
  new Request('https://twquant.pages.dev/api/me', { headers: token ? { authorization: `Bearer ${token}` } : {} })

test('T-439 AC1 Supabase 權杖（ES256＋JWKS）：簽章、簽發者、對象、角色、期限都對才算登入，email 轉小寫', async () => {
  const k = await es256()
  const env = { SUPABASE_URL }
  const ask = (claims: Record<string, unknown>, header?: Record<string, unknown>) =>
    k.sign(claims, header).then((t) => supabaseEmail(bearer(t), env, { fetchImpl: k.jwks, now: NOW }))
  assert.equal(await ask(good), 'someone@example.com')
  assert.equal(await ask({ ...good, iss: 'https://evil.supabase.co/auth/v1' }), null)
  assert.equal(await ask({ ...good, aud: 'anon' }), null)
  assert.equal(await ask({ ...good, aud: ['authenticated', 'x'] }), 'someone@example.com', 'aud 可以是陣列')
  assert.equal(await ask({ ...good, role: 'anon' }), null, 'anon 金鑰本身也是 JWT，不能當登入')
  assert.equal(await ask({ ...good, exp: NOW / 1000 - 1 }), null)
  assert.equal(await ask({ ...good, nbf: NOW / 1000 + 600 }), null)
  assert.equal(await ask({ ...good, email: undefined }), null)
  assert.equal(await ask(good, { alg: 'none', kid: 'ec-1' }), null)
  // 別人的金鑰簽的
  const other = await es256('ec-1')
  assert.equal(await supabaseEmail(bearer(await other.sign(good)), env, { fetchImpl: k.jwks, now: NOW }), null)
  // 沒有、壞掉的標頭
  assert.equal(await supabaseEmail(bearer(), env, { fetchImpl: k.jwks, now: NOW }), null)
  assert.equal(await supabaseEmail(bearer('a.b'), env, { fetchImpl: k.jwks, now: NOW }), null)
  assert.equal(await supabaseEmail(new Request('https://x/api/me', { headers: { authorization: 'Basic abc' } }), env, { fetchImpl: k.jwks, now: NOW }), null)
  // 沒設 SUPABASE_URL：整條路關著（不會去抓 JWKS）
  const never = (async () => assert.fail('不該抓 JWKS')) as unknown as typeof fetch
  assert.equal(await supabaseEmail(bearer(await k.sign(good)), {}, { fetchImpl: never, now: NOW }), null)
  // JWKS 抓不到是伺服器問題：丟錯（不是默默當成沒登入）
  const down = (async () => new Response('x', { status: 500 })) as unknown as typeof fetch
  await assert.rejects(supabaseEmail(bearer(await (await es256('ec-9')).sign(good)), env, { fetchImpl: down, now: NOW }), /JWKS/)
})

test('T-439 AC2 Supabase 權杖（HS256 共用祕密，舊專案）：有設 SUPABASE_JWT_SECRET 才驗；祕密不對、沒設都不算', async () => {
  const sign = await hs256('s3cret-s3cret-s3cret')
  const never = (async () => assert.fail('HS256 不該抓 JWKS')) as unknown as typeof fetch
  const env = { SUPABASE_URL, SUPABASE_JWT_SECRET: 's3cret-s3cret-s3cret' }
  assert.equal(await supabaseEmail(bearer(await sign(good)), env, { fetchImpl: never, now: NOW }), 'someone@example.com')
  assert.equal(await supabaseEmail(bearer(await sign({ ...good, role: 'anon' })), env, { fetchImpl: never, now: NOW }), null)
  assert.equal(await supabaseEmail(bearer(await (await hs256('wrong'))(good)), env, { fetchImpl: never, now: NOW }), null)
  assert.equal(await supabaseEmail(bearer(await sign(good)), { SUPABASE_URL }, { fetchImpl: never, now: NOW }), null, '沒設祕密不收 HS256')
})

test('T-439 AC3 userEmail：先看 Supabase 權杖，沒有再看 Cloudflare Access 的（過渡期）；兩個都沒有 → null', async () => {
  const k = await es256('ec-3')
  // Access 的 RS256 權杖（沿用 cloud-watchlist.test.ts 的做法）
  const pair = (await crypto.subtle.generateKey({ name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' }, true, ['sign', 'verify'])) as CryptoKeyPair
  const jwk = await crypto.subtle.exportKey('jwk', pair.publicKey)
  const certs = JSON.stringify({ keys: [{ kid: 'cf-1', kty: 'RSA', alg: 'RS256', use: 'sig', e: jwk.e, n: jwk.n }] })
  const accessToken = async () => {
    const body = `${part({ alg: 'RS256', kid: 'cf-1' })}.${part({ aud: [ACCESS_AUD], iss: ACCESS_TEAM, email: 'cf@example.com', exp: NOW / 1000 + 600 })}`
    return `${body}.${b64u(await crypto.subtle.sign('RSASSA-PKCS1-v1_5', pair.privateKey, enc(body)))}`
  }
  // 同一個假 fetch 依網址給 JWKS 或 Access 公鑰
  const fetchImpl = (async (url: string) => {
    if (String(url).includes('/auth/v1/.well-known/jwks.json')) return k.jwks(url)
    if (String(url).includes('/cdn-cgi/access/certs')) return new Response(certs)
    throw new Error(`unexpected ${url}`)
  }) as unknown as typeof fetch
  const env = { SUPABASE_URL }
  const opts = { fetchImpl, now: NOW }
  assert.equal(await userEmail(bearer(await k.sign(good)), env, opts), 'someone@example.com')
  const viaAccess = new Request('https://x/api/me', { headers: { [JWT_HEADER]: await accessToken() } })
  assert.equal(await userEmail(viaAccess, env, opts), 'cf@example.com')
  assert.equal(await userEmail(new Request('https://x/api/me'), env, opts), null)
  // 冒用 email 標頭沒用
  const spoof = new Request('https://x/api/me', { headers: { 'cf-access-authenticated-user-email': 'boss@example.com' } })
  assert.equal(await userEmail(spoof, env, opts), null)
  // /api/me 只回驗得過的身分
  assert.equal((await me(new Request('https://x/api/me'), env, opts)).status, 401)
  const ok = await me(bearer(await k.sign(good)), env, opts)
  assert.equal(ok.status, 200)
  assert.equal(ok.headers.get('cache-control'), 'no-store')
  assert.deepEqual(await ok.json(), { email: 'someone@example.com' })
})
