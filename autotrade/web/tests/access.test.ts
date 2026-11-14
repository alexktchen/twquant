// autotrade A-018（D-230）：這個網站自己的門——Cloudflare Access 簽的登入，加上管理員名單。
// 設定少一個整個網站就關著；權杖一定要驗過（簽章、是不是這個網站的、有沒有過期）才信裡面的 email。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { JWT_HEADER, NOT_SET_UP, accessEmail, admins, admit, type AccessEnv } from '../functions/_lib/access.ts'

const TEAM = 'https://example-team.cloudflareaccess.com'
const AUD = 'aud-of-this-site'
const NOW = 1_800_000_000_000
const ENV: AccessEnv = { ACCESS_TEAM: `${TEAM}/`, ACCESS_AUD: AUD, AUTOTRADE_ADMINS: 'Boss@Example.com, second@example.com' }

const b64 = (bytes: Uint8Array | string): string =>
  Buffer.from(bytes as Uint8Array)
    .toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')

const pair = await crypto.subtle.generateKey({ name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' }, true, ['sign', 'verify'])
const jwk = await crypto.subtle.exportKey('jwk', pair.publicKey)
const other = await crypto.subtle.generateKey({ name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' }, true, ['sign', 'verify'])

let fetched = 0
const certs = (async (url: string | URL | Request) => {
  fetched += 1
  assert.equal(String(url), `${TEAM}/cdn-cgi/access/certs`)
  return new Response(JSON.stringify({ keys: [{ kid: 'k1', kty: 'RSA', n: jwk.n, e: jwk.e }] }), { headers: { 'content-type': 'application/json' } })
}) as typeof fetch

async function token(claims: Record<string, unknown>, key: CryptoKey = pair.privateKey, alg = 'RS256'): Promise<string> {
  const head = b64(JSON.stringify({ alg, kid: 'k1' }))
  const body = b64(JSON.stringify(claims))
  const sig = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key, new TextEncoder().encode(`${head}.${body}`))
  return `${head}.${body}.${b64(new Uint8Array(sig))}`
}
const good = (more: Record<string, unknown> = {}) => ({ aud: [AUD], iss: TEAM, exp: NOW / 1000 + 600, nbf: NOW / 1000 - 60, email: 'Boss@Example.com', ...more })
const ask = (jwt?: string) => new Request('https://site.example/api/me', { headers: jwt ? { [JWT_HEADER]: jwt } : {} })
const opts = { fetchImpl: certs, now: NOW }
const status = async (r: string | Response) => (r instanceof Response ? [r.status, ((await r.json()) as { detail: string }).detail] : r)

test('A-018 AC2 放行：權杖驗過、email 在名單上，回小寫的 email', async () => {
  assert.equal(await admit(ask(await token(good())), ENV, opts), 'boss@example.com')
  assert.equal(await admit(ask(await token(good({ email: 'second@example.com', aud: AUD }))), ENV, opts), 'second@example.com')
  assert.equal(fetched, 1) // 公鑰抓一次就留著
  assert.deepEqual(admins(ENV), ['boss@example.com', 'second@example.com'])
})

test('A-018 AC2 設定少一個，整個網站關著（不是開著）', async () => {
  for (const missing of ['ACCESS_TEAM', 'ACCESS_AUD', 'AUTOTRADE_ADMINS'] as const) {
    const env = { ...ENV, [missing]: missing === 'AUTOTRADE_ADMINS' ? ' , ' : undefined }
    assert.deepEqual(await status(await admit(ask(await token(good())), env, opts)), [503, NOT_SET_UP], missing)
  }
  assert.deepEqual(await status(await admit(ask(await token(good())), {}, opts)), [503, NOT_SET_UP])
})

test('A-018 AC2 驗不過的權杖一律 401：沒有、亂寫、別人簽的、別的網站的、過期的、還沒生效的', async () => {
  const cases: [string, string | undefined][] = [
    ['沒有權杖', undefined],
    ['不是三段', 'abc.def'],
    ['別的金鑰簽的', await token(good(), other.privateKey)],
    ['別的網站的權杖', await token(good({ aud: ['another-site'] }))],
    ['別的組織簽的', await token(good({ iss: 'https://evil.cloudflareaccess.com' }))],
    ['過期了', await token(good({ exp: NOW / 1000 - 1 }))],
    ['還沒生效', await token(good({ nbf: NOW / 1000 + 60 }))],
    ['沒有 email', await token(good({ email: undefined }))],
    ['演算法不對', await token(good(), pair.privateKey, 'none')],
  ]
  for (const [why, jwt] of cases) {
    const got = await status(await admit(ask(jwt), ENV, opts))
    assert.equal(Array.isArray(got) ? got[0] : got, 401, why)
  }
  // 內容被改過（把 email 換成名單上的人）：簽章對不上
  const [head, , sig] = (await token(good({ email: 'stranger@example.com' }))).split('.')
  const forged = `${head}.${b64(JSON.stringify(good()))}.${sig}`
  assert.equal(await accessEmail(ask(forged), TEAM, AUD, opts), null)
})

test('A-018 AC2 登入了但不在名單上：403；只信權杖，不信自己帶的標頭', async () => {
  assert.deepEqual(await status(await admit(ask(await token(good({ email: 'stranger@example.com' }))), ENV, opts)), [403, '沒有權限'])
  const spoof = new Request('https://site.example/api/me', { headers: { 'cf-access-authenticated-user-email': 'boss@example.com' } })
  assert.equal(((await admit(spoof, ENV, opts)) as Response).status, 401)
})

test('A-018 AC2 每一支端點都先過這道門；名單與應用代號不寫在程式裡', () => {
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')
  for (const file of ['me.ts', 'autotrade/index.ts', 'autotrade/stop.ts', 'autotrade/command.ts']) {
    const src = read(`../functions/api/${file}`)
    assert.match(src, /await admit\((ctx\.)?request, (ctx\.)?env\)/, file)
    assert.match(src, /who instanceof Response/, file)
  }
  const lib = read('../functions/_lib/access.ts')
  assert.doesNotMatch(lib, /[\w.+-]+@[\w-]+\.(com|tw|net|org)/) // 沒有把任何人的 email 寫進程式
  const toml = read('../wrangler.toml')
  assert.match(toml, /ACCESS_TEAM = "https:\/\/[\w-]+\.cloudflareaccess\.com"/)
  assert.doesNotMatch(toml, /^\s*(ACCESS_AUD|AUTOTRADE_ADMINS)\s*=/m) // 這兩個是祕密，不進版控
})
