// 雲端版（T-332）的 API：每日名單、日期清單、個股 K 線與被推薦的日子。
import { apiUrl } from '../api'
import { LoginRequired, needsLogin } from './auth'
import type { Bar, PicksDay } from './format'

const OFFLINE = '連線中斷，請重新整理頁面'

// redirect: 'manual'：沒登入時 Access 會把 /api 導去登入頁，攔下來改丟 LoginRequired，畫面切成預覽模式。
async function get<T>(path: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(apiUrl(path), { headers: { accept: 'application/json' }, redirect: 'manual' })
  } catch {
    throw new Error(OFFLINE)
  }
  if (needsLogin(res)) throw new LoginRequired()
  if (!(res.headers.get('content-type') ?? '').includes('json')) throw new Error(`${res.status} ${res.statusText}`)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

/** 登出後要去的網址：伺服器撤銷登入成功就回首頁（沒登入的預覽畫面），否則退回 Cloudflare 的登出頁，確保一定登得出去。 */
export async function logoutTarget(): Promise<string> {
  const res = await fetch('/api/logout', { method: 'POST' }).catch(() => null)
  return res?.ok ? '/' : '/cdn-cgi/access/logout'
}

/** 目前登入的 email；沒登入時丟 LoginRequired。 */
export const fetchMe = () => get<{ email: string }>('/api/me')

export const fetchPicks = (date?: string) =>
  get<PicksDay>(date ? `/api/picks?date=${encodeURIComponent(date)}` : '/api/picks')

export const fetchPickDates = () => get<{ dates: string[] }>('/api/picks/dates').then((r) => r.dates)

/** 有名單的交易日（公開，不含任何股票），讓沒登入的人也能切到歷史名單選日期。 */
export const fetchPublicDates = () => get<{ dates: string[] }>('/pub/dates').then((r) => r.dates)

export const fetchBars = (sid: string, days = 500) =>
  get<{ rows: Bar[] }>(`/api/stocks/${encodeURIComponent(sid)}/ohlc?days=${days}`).then((r) => r.rows)

export const fetchStockPicks = (sid: string) =>
  get<{ days: Record<string, string[]> }>(`/api/stocks/${encodeURIComponent(sid)}/picks`).then((r) => r.days)
