// 瀏覽器端的 Supabase 登入（T-439／D-210）——純函式的部分，供 node 單元測試；碰網路與 localStorage 的在 session-client.ts。
// 不帶 supabase-js：只用到 GoTrue 的四支 REST（authorize、otp、verify、token），自己寫比整包小得多、也看得清楚在做什麼。

export const SESSION_KEY = 'twquant.session'
/** PKCE 的 code_verifier 暫放 sessionStorage（登入回來要用，同一個瀏覽器才有） */
export const VERIFIER_KEY = 'twquant.pkce'
/** 到期前幾秒就換新權杖 */
export const REFRESH_SKEW_S = 60

export type Session = { access_token: string; refresh_token: string; expires_at: number; email: string }

const isStr = (v: unknown): v is string => typeof v === 'string' && v !== ''

/** localStorage 裡存的登入狀態；格式不對 → null。 */
export function parseSession(raw: string | null): Session | null {
  if (raw === null) return null
  let o: unknown
  try {
    o = JSON.parse(raw)
  } catch {
    return null
  }
  if (!o || typeof o !== 'object') return null
  const s = o as Record<string, unknown>
  if (!isStr(s.access_token) || !isStr(s.refresh_token) || typeof s.expires_at !== 'number' || !isStr(s.email)) return null
  return { access_token: s.access_token, refresh_token: s.refresh_token, expires_at: s.expires_at, email: s.email }
}

/** /auth/v1/token 與 /auth/v1/verify 回的內容 → 要存的登入狀態；缺欄位 → null。 */
export function sessionFrom(body: unknown, nowS: number): Session | null {
  if (!body || typeof body !== 'object') return null
  const b = body as Record<string, unknown>
  const user = b.user as Record<string, unknown> | undefined
  if (!isStr(b.access_token) || !isStr(b.refresh_token) || !user || !isStr(user.email)) return null
  const expiresAt = typeof b.expires_at === 'number' ? b.expires_at : typeof b.expires_in === 'number' ? nowS + b.expires_in : null
  if (expiresAt === null) return null
  return { access_token: b.access_token, refresh_token: b.refresh_token, expires_at: expiresAt, email: user.email.toLowerCase() }
}

/** 該不該先換新權杖（到期前 REFRESH_SKEW_S 秒起算）。 */
export const needsRefresh = (s: Session, nowS: number, skewS = REFRESH_SKEW_S): boolean => s.expires_at - skewS <= nowS

export function base64url(bytes: Uint8Array | ArrayBuffer): string {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** PKCE：code_challenge＝base64url(sha256(code_verifier))（RFC 7636）。 */
export async function pkceChallenge(verifier: string): Promise<string> {
  return base64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)))
}

/** 隨機的 code_verifier（43 字，base64url 字元）。 */
export function randomVerifier(): string {
  return base64url(crypto.getRandomValues(new Uint8Array(32)))
}

/** Google 登入的網址（Supabase 轉去 Google，回來帶 ?code=）。 */
export function authorizeUrl(base: string, redirectTo: string, challenge: string): string {
  const u = new URL(`${base.replace(/\/+$/, '')}/auth/v1/authorize`)
  u.searchParams.set('provider', 'google')
  u.searchParams.set('redirect_to', redirectTo)
  u.searchParams.set('code_challenge', challenge)
  u.searchParams.set('code_challenge_method', 's256')
  return u.toString()
}

/** 登入回來網址上的 ?code=；沒有 → null。 */
export function loginCodeFromUrl(href: string): string | null {
  try {
    return new URL(href).searchParams.get('code')
  } catch {
    return null
  }
}

/** 用過的 ?code= 從網址拿掉（重新整理不要再換一次）；其他參數與 hash 保留。 */
export function withoutCode(href: string): string {
  const u = new URL(href)
  u.searchParams.delete('code')
  return u.pathname + u.search + u.hash
}

export const emailLooksValid = (s: string): boolean => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s.trim())

/** GoTrue 的錯誤改成中文；不認得的照原文。 */
export function gotrueMessage(body: unknown): string {
  if (!body || typeof body !== 'object') return '登入服務暫時無法使用'
  const b = body as Record<string, unknown>
  const code = typeof b.error_code === 'string' ? b.error_code : typeof b.error === 'string' ? b.error : ''
  const text = [b.msg, b.message, b.error_description].find(isStr) ?? ''
  if (code === 'over_email_send_rate_limit' || /rate limit/i.test(text)) return '寄信次數到上限，請幾分鐘後再試'
  if (code === 'otp_expired' || /expired or is invalid/i.test(text)) return '驗證碼錯誤或已過期，請重寄一次'
  if (code === 'invalid_grant' || /refresh token/i.test(text)) return '登入已失效，請重新登入'
  if (code === 'signup_disabled' || /signups not allowed/i.test(text)) return '目前不開放新帳號'
  if (code === 'validation_failed' || /validate email/i.test(text)) return 'Email 格式不對'
  return text || code || '登入服務暫時無法使用'
}
