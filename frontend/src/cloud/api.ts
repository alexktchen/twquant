// 雲端版（T-332）的 API：每日名單、日期清單、個股 K 線與被推薦的日子。
import { apiUrl } from '../api'
import { LoginRequired, needsLogin } from './auth'
import type { Bar, DateCounts, PicksDay } from './format'
import type { SearchHit, StockScores, WatchList } from './scores'

const OFFLINE = '連線中斷，請重新整理頁面'

// redirect: 'manual'：沒登入時 Access 會把 /api 導去登入頁，攔下來改丟 LoginRequired，畫面切成預覽模式。
async function get<T>(path: string, method = 'GET'): Promise<T> {
  let res: Response
  try {
    res = await fetch(apiUrl(path), { method, headers: { accept: 'application/json' }, redirect: 'manual', cache: 'no-store' })
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

/** 有名單的日期（新到舊），附每天每條線的檔數（舊後端沒有 counts/since → null）。 */
export const fetchPickDates = () =>
  get<{ dates: string[]; counts?: DateCounts['counts']; since?: DateCounts['since'] }>('/api/picks/dates').then((r) => ({
    dates: r.dates,
    counts: r.counts && r.since ? { counts: r.counts, since: r.since } : null,
  }))

/** 有名單的交易日（公開，不含任何股票），讓沒登入的人也能切到歷史名單選日期。 */
export const fetchPublicDates = () => get<{ dates: string[] }>('/pub/dates').then((r) => r.dates)

export const fetchBars = (sid: string, days = 500) =>
  get<{ rows: Bar[] }>(`/api/stocks/${encodeURIComponent(sid)}/ohlc?days=${days}`).then((r) => r.rows)

export const fetchStockPicks = (sid: string) =>
  get<{ days: Record<string, string[]> }>(`/api/stocks/${encodeURIComponent(sid)}/picks`).then((r) => r.days)

/** 三個模型最近幾天的分數與名次，加上最新一天在哪條線的名單上。 */
export const fetchScores = (sid: string) => get<StockScores>(`/api/stocks/${encodeURIComponent(sid)}/scores`)

/** 代號開頭或名稱含這幾個字的股票（最多 10 檔）。 */
export const searchStocks = (q: string) =>
  get<{ items: SearchHit[] }>(`/api/stocks/search?q=${encodeURIComponent(q)}`).then((r) => r.items)

/** 登入者的追蹤股（新加的在前），附最新收盤與三個模型的分數。 */
export const fetchWatchlist = () => get<WatchList>('/api/watchlist')

/** 加入（on）或移除追蹤股；兩個都冪等。 */
export const setTracked = (sid: string, on: boolean) =>
  get<{ ok: boolean }>(`/api/watchlist/${encodeURIComponent(sid)}`, on ? 'PUT' : 'DELETE')
