// 追蹤股要知道「這是誰」：驗登入權杖，從裡面取 email。
// T-439／D-210 起正式的登入是 Supabase Auth（Authorization: Bearer <Supabase 簽的 JWT>，userEmail）；
// 過渡期 Cloudflare Access 簽的權杖（Cf-Access-Jwt-Assertion，accessEmail）照收，使用者關掉 /api 的 Access 應用後就只剩 Supabase。
// 不直接信 cf-access-authenticated-user-email 之類的標頭：任何人都能註冊登入，追蹤股又是每個帳號自己的資料，
// 身分必須來自簽過名、驗得過的權杖。

/** Zero Trust 組織網址（簽發者）與「twquant AI 選股」這個 Access 應用的 AUD 標籤。
 *  重建 Access 應用時 AUD 會變：GET /accounts/{帳號}/access/apps 看 aud 欄位。 */
export const ACCESS_TEAM = 'https://twquant-pages.cloudflareaccess.com'
export const ACCESS_AUD = '99bda6fd78e225bb77b58a4e8441b243bed324f00fc113ad1082c03c72c9d518'
export const JWT_HEADER = 'cf-access-jwt-assertion'

type Jwk = { kid: string; kty: string; n: string; e: string }
type Claims = { aud?: string | string[]; iss?: string; exp?: number; nbf?: number; email?: unknown }

// 公鑰快取（同一個 Worker 執行個體內）；Cloudflare 會輪換金鑰，遇到不認得的 kid 就重抓一次
const keyCache = new Map<string, CryptoKey>()

const b64url = (s: string): Uint8Array => {
  const b = atob(s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (s.length % 4)) % 4))
  return Uint8Array.from(b, (c) => c.charCodeAt(0))
}
const decodeJson = <T>(s: string): T | null => {
  try {
    return JSON.parse(new TextDecoder().decode(b64url(s))) as T
  } catch {
    return null
  }
}

async function loadKeys(team: string, fetchImpl: typeof fetch): Promise<void> {
  const res = await fetchImpl(`${team}/cdn-cgi/access/certs`)
  if (!res.ok) throw new Error(`取不到 Access 公鑰（HTTP ${res.status}）`)
  const body = (await res.json()) as { keys?: Jwk[] }
  for (const k of body.keys ?? []) {
    if (k.kty !== 'RSA') continue
    const key = await crypto.subtle.importKey(
      'jwk',
      { kty: k.kty, n: k.n, e: k.e },
      { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
      false,
      ['verify'],
    )
    keyCache.set(`${team}#${k.kid}`, key)
  }
}

/** 登入者的 email（小寫）；沒有權杖、簽章不對、過期、不是這個網站的權杖 → null。公鑰抓不到才丟錯（伺服器問題）。 */
export async function accessEmail(
  request: Request,
  opts: { team?: string; aud?: string; fetchImpl?: typeof fetch; now?: number } = {},
): Promise<string | null> {
  const { team = ACCESS_TEAM, aud = ACCESS_AUD, fetchImpl = fetch, now = Date.now() } = opts
  const token = request.headers.get(JWT_HEADER)
  const parts = token?.split('.') ?? []
  if (parts.length !== 3) return null
  const header = decodeJson<{ alg?: string; kid?: string }>(parts[0])
  const claims = decodeJson<Claims>(parts[1])
  if (!header || !claims || header.alg !== 'RS256' || !header.kid) return null

  const id = `${team}#${header.kid}`
  if (!keyCache.has(id)) await loadKeys(team, fetchImpl)
  const key = keyCache.get(id)
  if (!key) return null
  let sig: Uint8Array
  try {
    sig = b64url(parts[2])
  } catch {
    return null
  }
  const signed = new TextEncoder().encode(`${parts[0]}.${parts[1]}`)
  if (!(await crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, sig, signed))) return null

  const secs = now / 1000
  const auds = Array.isArray(claims.aud) ? claims.aud : [claims.aud]
  if (!auds.includes(aud) || claims.iss !== team) return null
  if (typeof claims.exp !== 'number' || claims.exp <= secs) return null
  if (typeof claims.nbf === 'number' && claims.nbf > secs) return null
  return typeof claims.email === 'string' && claims.email.includes('@') ? claims.email.toLowerCase() : null
}

// ── Supabase Auth（T-439）────────────────────────────────────────────────────────────────
/** Pages 的環境變數：SUPABASE_URL＝專案網址（https://<ref>.supabase.co）；SUPABASE_JWT_SECRET 只有舊專案（HS256 共用祕密）才要設，
 *  新專案用非對稱金鑰（ES256／RS256），公鑰從 /auth/v1/.well-known/jwks.json 拿。 */
export type IdentityEnv = { SUPABASE_URL?: string; SUPABASE_JWT_SECRET?: string }
export type UserOpts = { team?: string; aud?: string; fetchImpl?: typeof fetch; now?: number }

type SbClaims = Claims & { role?: unknown }

async function loadJwks(url: string, fetchImpl: typeof fetch): Promise<void> {
  const res = await fetchImpl(url)
  if (!res.ok) throw new Error(`取不到 Supabase JWKS（HTTP ${res.status}）`)
  const body = (await res.json()) as { keys?: Record<string, string>[] }
  for (const k of body.keys ?? []) {
    if (!k.kid) continue
    let key: CryptoKey
    if (k.kty === 'EC' && k.crv === 'P-256') {
      key = await crypto.subtle.importKey('jwk', { kty: 'EC', crv: 'P-256', x: k.x, y: k.y }, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify'])
    } else if (k.kty === 'RSA') {
      key = await crypto.subtle.importKey('jwk', { kty: 'RSA', n: k.n, e: k.e }, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify'])
    } else continue
    keyCache.set(`${url}#${k.kid}`, key)
  }
}

/** Supabase 登入者的 email（小寫）；沒有 Bearer、簽章不對、不是這個專案、不是登入者（anon 金鑰也是 JWT）、過期 → null。
 *  沒設 SUPABASE_URL 整條路關著。JWKS 抓不到才丟錯（伺服器問題）。 */
export async function supabaseEmail(request: Request, env: IdentityEnv, opts: { fetchImpl?: typeof fetch; now?: number } = {}): Promise<string | null> {
  const { fetchImpl = fetch, now = Date.now() } = opts
  const base = env.SUPABASE_URL?.replace(/\/+$/, '')
  if (!base) return null
  const auth = request.headers.get('authorization') ?? ''
  if (!/^Bearer /i.test(auth)) return null
  const parts = auth.slice(7).trim().split('.')
  if (parts.length !== 3) return null
  const header = decodeJson<{ alg?: string; kid?: string }>(parts[0])
  const claims = decodeJson<SbClaims>(parts[1])
  if (!header || !claims) return null
  let sig: Uint8Array
  try {
    sig = b64url(parts[2])
  } catch {
    return null
  }
  const signed = new TextEncoder().encode(`${parts[0]}.${parts[1]}`)
  let ok = false
  if (header.alg === 'HS256') {
    if (!env.SUPABASE_JWT_SECRET) return null
    const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(env.SUPABASE_JWT_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['verify'])
    ok = await crypto.subtle.verify('HMAC', key, sig, signed)
  } else if (header.alg === 'ES256' || header.alg === 'RS256') {
    if (!header.kid) return null
    const jwks = `${base}/auth/v1/.well-known/jwks.json`
    const id = `${jwks}#${header.kid}`
    if (!keyCache.has(id)) await loadJwks(jwks, fetchImpl)
    const key = keyCache.get(id)
    if (!key) return null
    ok =
      header.alg === 'ES256'
        ? await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, key, sig, signed)
        : await crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, sig, signed)
  } else {
    return null
  }
  if (!ok) return null
  const secs = now / 1000
  const auds = Array.isArray(claims.aud) ? claims.aud : [claims.aud]
  if (!auds.includes('authenticated') || claims.iss !== `${base}/auth/v1` || claims.role !== 'authenticated') return null
  if (typeof claims.exp !== 'number' || claims.exp <= secs) return null
  if (typeof claims.nbf === 'number' && claims.nbf > secs) return null
  return typeof claims.email === 'string' && claims.email.includes('@') ? claims.email.toLowerCase() : null
}

/** 登入者的 email：先看 Supabase 的權杖，沒有再看 Cloudflare Access 的（過渡期）；都沒有 → null。所有要身分的端點用這個。 */
export async function userEmail(request: Request, env: IdentityEnv, opts: UserOpts = {}): Promise<string | null> {
  return (await supabaseEmail(request, env, opts)) ?? accessEmail(request, opts)
}
