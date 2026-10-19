// 新聞服務對外 API v1 的純函式（SPEC-017 §10）：查詢參數驗證、健康狀態判讀。SQL 與 HTTP 在 supabase/functions/api。
// 欄位只增不刪；要刪欄或改意義就開 /v2。
import type { NewsConfig } from './ingest.ts'

export const API_VERSION = 1
/** 新聞流一次最多幾則 */
export const ITEMS_MAX = 200
export const ITEMS_DEFAULT = 100

export type ItemsQuery = {
  since: string | null
  until: string | null
  kind: 'intl' | 'tw' | null
  source: string | null
  limit: number
}

const isoOrNull = (v: string | null, name: string): string | null => {
  if (v === null || v === '') return null
  const t = Date.parse(v)
  if (!Number.isFinite(t)) throw new Error(`${name} 不是時間：${v}`)
  return new Date(t).toISOString()
}

/** /v1/items 的查詢參數 → ItemsQuery；不合法 → 丟錯（端點回 400）。 */
export function parseItemsQuery(params: URLSearchParams, cfg: NewsConfig): ItemsQuery {
  const kind = params.get('kind')
  if (kind !== null && kind !== 'intl' && kind !== 'tw') throw new Error(`kind 只能是 intl 或 tw：${kind}`)
  const source = params.get('source')
  if (source !== null && !cfg.sources.some((s) => s.id === source)) throw new Error(`沒有這個來源：${source}`)
  const rawLimit = params.get('limit')
  const limit = rawLimit === null ? ITEMS_DEFAULT : Number(rawLimit)
  if (!Number.isInteger(limit) || limit < 1 || limit > ITEMS_MAX) throw new Error(`limit 要是 1～${ITEMS_MAX}：${rawLimit}`)
  return { since: isoOrNull(params.get('since'), 'since'), until: isoOrNull(params.get('until'), 'until'), kind, source, limit }
}

export type SourceRun = { source: string; last_run_at: string | null; last_ok_at: string | null; last_error: string | null }
export type Health = {
  api_version: number
  now: string
  /** 任一來源最後一次成功的時間；從沒成功 → null */
  last_ok_at: string | null
  /** 超過 stale_hours 小時沒有任何來源成功（或從沒成功） */
  stale: boolean
  sources: (SourceRun & { name: string; failing: boolean })[]
}

/** 各來源最後一次執行與成功的時間 → 健康狀態。設定裡有、資料庫還沒有紀錄的來源也列出來（從沒跑過）。 */
export function health(cfg: NewsConfig, runs: SourceRun[], now: Date): Health {
  const by = new Map(runs.map((r) => [r.source, r]))
  const sources = cfg.sources.map((s) => {
    const r = by.get(s.id) ?? { source: s.id, last_run_at: null, last_ok_at: null, last_error: null }
    // 最後一次執行沒成功（成功時間早於執行時間）＝這個來源目前壞著
    const failing = r.last_run_at !== null && (r.last_ok_at === null || r.last_ok_at < r.last_run_at)
    return { ...r, name: s.name, failing }
  })
  const oks = sources.map((s) => s.last_ok_at).filter((t): t is string => t !== null)
  const lastOk = oks.length > 0 ? oks.reduce((a, b) => (a > b ? a : b)) : null
  const stale = lastOk === null || now.getTime() - Date.parse(lastOk) > cfg.stale_hours * 3600_000
  return { api_version: API_VERSION, now: now.toISOString(), last_ok_at: lastOk, stale, sources }
}
