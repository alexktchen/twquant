// 瀏覽器端的 Supabase 登入（T-439／D-210）——碰網路與 localStorage 的部分。純函式在 session.ts。
// 登入狀態存 localStorage（twquant.session）：access_token 約 1 小時，到期前用 refresh_token 換新；登出就清掉。
import {
  REFRESH_SKEW_S,
  SESSION_KEY,
  VERIFIER_KEY,
  authorizeUrl,
  gotrueMessage,
  loginCodeFromUrl,
  needsRefresh,
  parseSession,
  pkceChallenge,
  randomVerifier,
  sessionFrom,
  withoutCode,
  type Session,
} from './session'

const BASE = (import.meta.env.VITE_SUPABASE_URL ?? '').replace(/\/+$/, '')
const ANON = import.meta.env.VITE_SUPABASE_ANON_KEY ?? ''

/** 建置時有給 Supabase 專案網址與 anon 金鑰才能登入。 */
export const authEnabled = (): boolean => BASE !== '' && ANON !== ''

const nowS = () => Math.floor(Date.now() / 1000)

// localStorage 可能不能用（隱私模式、被擋）：讀不到就當沒登入，寫不進去就這次登入留不住
const read = (): string | null => {
  try {
    return localStorage.getItem(SESSION_KEY)
  } catch {
    return null
  }
}
const write = (s: Session): void => {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(s))
  } catch {
    /* 留不住登入狀態；下次打開要重新登入 */
  }
}

/** 這台瀏覽器存的登入狀態（可能已過期，要用 getToken 拿可用的權杖）。 */
export const loadSession = (): Session | null => parseSession(read())
export const hasSession = (): boolean => loadSession() !== null

export function clearSession(): void {
  try {
    localStorage.removeItem(SESSION_KEY)
  } catch {
    /* 本來就沒有 */
  }
}

/** 打一支 GoTrue 端點；錯誤整理成中文的 Error。204 沒內容回 null。 */
async function gotrue(path: string, body: unknown, token?: string): Promise<unknown> {
  if (!authEnabled()) throw new Error('登入還沒設定')
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { apikey: ANON, 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(body),
    })
  } catch {
    throw new Error('連不上登入服務，請檢查網路')
  }
  const text = await res.text()
  let parsed: unknown = null
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = null
    }
  }
  if (!res.ok) throw new Error(gotrueMessage(parsed))
  return parsed
}

let refreshing: Promise<Session | null> | null = null

async function refresh(s: Session): Promise<Session | null> {
  try {
    const fresh = sessionFrom(await gotrue('/auth/v1/token?grant_type=refresh_token', { refresh_token: s.refresh_token }), nowS())
    if (!fresh) throw new Error('登入服務回的內容不對')
    write(fresh)
    return fresh
  } catch {
    // 換不到新的（refresh token 失效、被撤銷）：清掉，之後的 /api 會回 401、畫面變成沒登入
    clearSession()
    return null
  }
}

/** 可用的 access token；沒登入或換新失敗 → null。快到期（REFRESH_SKEW_S 秒內）先換新，同時間只換一次。 */
export async function getToken(): Promise<string | null> {
  const s = loadSession()
  if (!s) return null
  if (!needsRefresh(s, nowS())) return s.access_token
  refreshing ??= refresh(s).finally(() => {
    refreshing = null
  })
  return (await refreshing)?.access_token ?? null
}

/** Google 登入：存 PKCE verifier、整頁跳去 Supabase → Google；回來帶 ?code=，由 completeOAuth 換成登入狀態。 */
export async function signInWithGoogle(redirectTo = `${window.location.origin}/`): Promise<void> {
  if (!authEnabled()) throw new Error('登入還沒設定')
  const verifier = randomVerifier()
  sessionStorage.setItem(VERIFIER_KEY, verifier)
  window.location.assign(authorizeUrl(BASE, redirectTo, await pkceChallenge(verifier)))
}

/** 網址上有 ?code=（Google 登入或點了信裡的連結回來）就換成登入狀態；換好回 true。沒有 code 回 false。 */
export async function completeOAuth(): Promise<boolean> {
  const code = loginCodeFromUrl(window.location.href)
  if (!code) return false
  window.history.replaceState(null, '', withoutCode(window.location.href))
  const verifier = sessionStorage.getItem(VERIFIER_KEY)
  sessionStorage.removeItem(VERIFIER_KEY)
  if (!verifier) throw new Error('登入流程中斷（要在同一個瀏覽器完成），請再登入一次')
  const s = sessionFrom(await gotrue('/auth/v1/token?grant_type=pkce', { auth_code: code, code_verifier: verifier }), nowS())
  if (!s) throw new Error('登入服務回的內容不對')
  write(s)
  return true
}

/** Email 驗證碼：寄一組驗證碼（信裡也有連結；第一次登入就註冊）。 */
export async function sendEmailCode(email: string): Promise<void> {
  const verifier = randomVerifier()
  sessionStorage.setItem(VERIFIER_KEY, verifier)
  await gotrue('/auth/v1/otp', { email, create_user: true, code_challenge: await pkceChallenge(verifier), code_challenge_method: 's256' })
}

/** Email 驗證碼：填進去就登入。 */
export async function verifyEmailCode(email: string, code: string): Promise<Session> {
  const s = sessionFrom(await gotrue('/auth/v1/verify', { type: 'email', email, token: code.trim() }), nowS())
  if (!s) throw new Error('登入服務回的內容不對')
  write(s)
  return s
}

/** 登出：先清本機，再通知 Supabase 作廢這組權杖（失敗也沒關係，本機已經清了）。 */
export async function signOut(): Promise<void> {
  const s = loadSession()
  clearSession()
  if (s) await gotrue('/auth/v1/logout', {}, s.access_token).catch(() => undefined)
}

export { REFRESH_SKEW_S }
