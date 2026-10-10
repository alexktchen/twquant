// 雲端版（T-332）的 API：每日名單、日期清單、個股 K 線與被推薦的日子。
import { apiUrl } from '../api'
import type { Bar, PicksDay } from './format'

// 登入（Cloudflare Access）過期時，API 會被導去登入頁：瀏覽器只回 TypeError 或拿到 HTML。
const RELOGIN = '登入已過期或連線中斷，請重新整理頁面'

async function get<T>(path: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(apiUrl(path), { headers: { accept: 'application/json' } })
  } catch {
    throw new Error(RELOGIN)
  }
  if (!(res.headers.get('content-type') ?? '').includes('json')) throw new Error(RELOGIN)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

/** Cloudflare Access 登入身分；網站沒開 Access（或本機開發）時回 null，不丟錯。 */
/** 登出後要去的網址：伺服器撤銷登入成功就到本站的登出頁，否則退回 Cloudflare 的登出頁，確保一定登得出去。 */
export async function logoutTarget(): Promise<string> {
  const res = await fetch('/api/logout', { method: 'POST' }).catch(() => null)
  return res?.ok ? '/signed-out' : '/cdn-cgi/access/logout'
}

export async function fetchIdentity(): Promise<{ email: string } | null> {
  try {
    const res = await fetch('/cdn-cgi/access/get-identity', { headers: { accept: 'application/json' } })
    if (!res.ok || !(res.headers.get('content-type') ?? '').includes('json')) return null
    const body = (await res.json()) as { email?: unknown }
    return typeof body.email === 'string' ? { email: body.email } : null
  } catch {
    return null
  }
}

export const fetchPicks = (date?: string) =>
  get<PicksDay>(date ? `/api/picks?date=${encodeURIComponent(date)}` : '/api/picks')

export const fetchPickDates = () => get<{ dates: string[] }>('/api/picks/dates').then((r) => r.dates)

export const fetchBars = (sid: string, days = 500) =>
  get<{ rows: Bar[] }>(`/api/stocks/${encodeURIComponent(sid)}/ohlc?days=${days}`).then((r) => r.rows)

export const fetchStockPicks = (sid: string) =>
  get<{ days: Record<string, string[]> }>(`/api/stocks/${encodeURIComponent(sid)}/picks`).then((r) => r.days)
