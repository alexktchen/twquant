// 新聞服務對外 API v1 的純函式（SPEC-017 §10）：查詢參數驗證、健康狀態判讀。SQL 與 HTTP 在 supabase/functions/api。
// 欄位只增不刪；要刪欄或改意義就開 /v2。
import type { NewsConfig } from './ingest.ts'

export const API_VERSION = 1
/** 新聞流一次最多幾則 */
export const ITEMS_MAX = 200
export const ITEMS_DEFAULT = 100

export type ItemsQuery = {
  /** 新聞類型（T-421）：config/news_types.json 的類別之一；null＝全部 */
  type: string | null
  since: string | null
  /** 含（≤）：同一次進料的一批同一個時間，翻頁時不能跳過；重複的由網站去掉（每來源每次最多 per_run_limit 則 < 一頁） */
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
export function parseItemsQuery(params: URLSearchParams, cfg: NewsConfig, typeClasses: readonly string[] = []): ItemsQuery {
  const kind = params.get('kind')
  const type = params.get('type')
  if (type !== null && !typeClasses.includes(type)) throw new Error(`沒有這個新聞類型：${type}`)
  if (kind !== null && kind !== 'intl' && kind !== 'tw') throw new Error(`kind 只能是 intl 或 tw：${kind}`)
  const source = params.get('source')
  if (source !== null && !cfg.sources.some((s) => s.id === source)) throw new Error(`沒有這個來源：${source}`)
  const rawLimit = params.get('limit')
  const limit = rawLimit === null ? ITEMS_DEFAULT : Number(rawLimit)
  if (!Number.isInteger(limit) || limit < 1 || limit > ITEMS_MAX) throw new Error(`limit 要是 1～${ITEMS_MAX}：${rawLimit}`)
  return { type, since: isoOrNull(params.get('since'), 'since'), until: isoOrNull(params.get('until'), 'until'), kind, source, limit }
}

/** 新聞庫容量（Supabase 免費方案）：健康檢查回報用量，超過 warn_share 網站狀態列變黃 */
export type StorageConfig = { limit_mb: number; warn_share: number }
export function validateStorageConfig(x: unknown): StorageConfig {
  const c = x as Partial<StorageConfig> | null
  if (!c || typeof c.limit_mb !== 'number' || c.limit_mb <= 0 || typeof c.warn_share !== 'number' || c.warn_share <= 0 || c.warn_share >= 1)
    throw new Error('news 設定錯誤：storage（limit_mb > 0、0 < warn_share < 1）')
  return c as StorageConfig
}
export type Storage = { used_mb: number; limit_mb: number; warn: boolean }
/** 資料庫用量（位元組）→ 健康檢查的容量欄。 */
export const storageOf = (cfg: StorageConfig, bytes: number): Storage => {
  const used = Math.round((bytes / 1024 / 1024) * 10) / 10
  return { used_mb: used, limit_mb: cfg.limit_mb, warn: used >= cfg.limit_mb * cfg.warn_share }
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
  /** 新聞庫用量（2026-10-20 起；呼叫端沒給 → 沒有這欄） */
  storage?: Storage
}

/** 各來源最後一次執行與成功的時間 → 健康狀態。設定裡有、資料庫還沒有紀錄的來源也列出來（從沒跑過）。 */
export function health(cfg: NewsConfig, runs: SourceRun[], now: Date, dbBytes?: number): Health {
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
  const out: Health = { api_version: API_VERSION, now: now.toISOString(), last_ok_at: lastOk, stale, sources }
  if (dbBytes !== undefined) out.storage = storageOf(cfg.storage, dbBytes)
  return out
}
