// 這個網站的門（autotrade A-018／D-230）：Cloudflare Access 擋在最前面，沒登入連頁面都看不到。
// 這裡是第二道：每一支端點自己驗 Access 簽的權杖（Cf-Access-Jwt-Assertion），再看 email 在不在管理員名單上。
// 不直接信 cf-access-authenticated-user-email 之類的標頭——身分必須來自簽過名、驗得過的權杖。
//
// 三個設定都放 Pages 的環境變數，不寫進程式（少一個就整個網站關著，不是開著）：
//   ACCESS_TEAM      Zero Trust 組織網址，例 https://<組織>.cloudflareaccess.com
//   ACCESS_AUD       這個網站那個 Access 應用的 AUD 標籤（建好應用之後在它的頁面上看得到）
//   AUTOTRADE_ADMINS 可以用這個網站的人的 email，逗號分隔

export type AccessEnv = { ACCESS_TEAM?: string; ACCESS_AUD?: string; AUTOTRADE_ADMINS?: string }
export type AccessOpts = { fetchImpl?: typeof fetch; now?: number }

export const JWT_HEADER = 'cf-access-jwt-assertion'
export const NOT_SET_UP = '這個網站的登入還沒設定好（ACCESS_TEAM、ACCESS_AUD、AUTOTRADE_ADMINS 三個都要有）'

type Jwk = { kid: string; kty: string; n: string; e: string }
type Claims = { aud?: string | string[]; iss?: string; exp?: number; nbf?: number; email?: unknown }

// 公鑰快取（同一個執行個體內）；Cloudflare 會輪換金鑰，遇到不認得的 kid 就重抓一次
const keyCache = new Map<string, CryptoKey>()

const fail = (status: number, detail: string) =>
  new Response(JSON.stringify({ detail }), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, no-store' },
  })

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
    const key = await crypto.subtle.importKey('jwk', { kty: k.kty, n: k.n, e: k.e }, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify'])
    keyCache.set(`${team}#${k.kid}`, key)
  }
}

/** 管理員名單（小寫）。 */
export const admins = (env: AccessEnv): string[] =>
  (env.AUTOTRADE_ADMINS ?? '')
    .split(/[\s,]+/)
    .map((e) => e.trim().toLowerCase())
    .filter((e) => e.includes('@'))

/** 登入者的 email（小寫）；沒有權杖、簽章不對、過期、不是這個網站的權杖 → null。公鑰抓不到才丟錯（伺服器問題）。 */
export async function accessEmail(request: Request, team: string, aud: string, opts: AccessOpts = {}): Promise<string | null> {
  const { fetchImpl = fetch, now = Date.now() } = opts
  const parts = request.headers.get(JWT_HEADER)?.split('.') ?? []
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

/**
 * 每一支端點的第一步：放行就回登入者的 email，不放行就回要給瀏覽器的回應。
 * 設定少一個 → 503（整個網站關著）；權杖驗不過 → 401；不在名單上 → 403。一定在碰資料庫之前叫。
 */
export async function admit(request: Request, env: AccessEnv, opts: AccessOpts = {}): Promise<string | Response> {
  const team = env.ACCESS_TEAM?.replace(/\/+$/, '')
  const aud = env.ACCESS_AUD?.trim()
  const allowed = admins(env)
  if (!team || !aud || allowed.length === 0) return fail(503, NOT_SET_UP)
  const email = await accessEmail(request, team, aud, opts)
  if (email === null) return fail(401, '登入身分驗證失敗，請重新整理頁面再登入一次')
  return allowed.includes(email) ? email : fail(403, '沒有權限')
}
