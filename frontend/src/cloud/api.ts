// 雲端版（T-332）的 API：每日名單、日期清單、個股 K 線與被推薦的日子。
import { apiUrl } from '../api'
import type { Bar, PicksDay } from './format'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(apiUrl(path), { headers: { accept: 'application/json' } })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

export const fetchPicks = (date?: string) =>
  get<PicksDay>(date ? `/api/picks?date=${encodeURIComponent(date)}` : '/api/picks')

export const fetchPickDates = () => get<{ dates: string[] }>('/api/picks/dates').then((r) => r.dates)

export const fetchBars = (sid: string, days = 500) =>
  get<{ rows: Bar[] }>(`/api/stocks/${encodeURIComponent(sid)}/ohlc?days=${days}`).then((r) => r.rows)

export const fetchStockPicks = (sid: string) =>
  get<{ days: Record<string, string[]> }>(`/api/stocks/${encodeURIComponent(sid)}/picks`).then((r) => r.days)
