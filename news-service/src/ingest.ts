// 新聞服務的進料（T-400／SPEC-017 §3）：每個來源抓一次 → 解析 → 新的寫一列、內容變了加一版、每次都記一列執行紀錄。
// 資料只增不改（D-200 §四）：first_seen_at 是我們第一次抓到的時間（研究用的可得時間），之後不會再改。
// 所有 IO（抓網頁、時鐘、資料庫）由呼叫端注入：Edge Function 給真的、測試給假的。一個來源壞掉不擋其他來源。
import { parseCnyes, parseFeed, type FeedItem } from './feed.ts'
import type { EdgeType, GraphConfig } from './graph.ts'
import { validateMentionConfig, type MentionConfig } from './mention.ts'
import { validateTranslateConfig, type TranslateConfig } from './translate.ts'

export type Source = {
  id: string
  name: string
  /** intl＝國際（要翻譯）；tw＝台灣 */
  kind: 'intl' | 'tw'
  lang: 'en' | 'zh'
  format: 'rss' | 'cnyes'
  url: string
  /** 來源條款網址與我們最後檢查的日期（SPEC-017 §3） */
  terms: string
  checked: string
}

export type NewsConfig = {
  user_agent: string
  /** 每次每個來源最多收幾則（來源本身由新到舊排） */
  per_run_limit: number
  /** 超過幾小時沒抓到任何來源就算停擺（/v1/health） */
  stale_hours: number
  sources: Source[]
  translate: TranslateConfig
  /** T-402：別名比對（一度） */
  mention: MentionConfig
  /** T-404：關係圖（propagate 的參數＋同族群線、/v1/items 每則幾個實體） */
  graph: NewsGraphConfig
}

export type NewsGraphConfig = GraphConfig & {
  /** 同族群線：每檔連到族群裡成交金額前幾名 */
  peers_per_group: number
  /** /v1/items 每則最多回幾個一度實體（台股優先） */
  mentions_per_item: number
}

const EDGE_TYPES: EdgeType[] = ['supplier', 'belongs', 'member', 'peer', 'theme']

function validateGraphConfig(x: unknown): NewsGraphConfig {
  const c = x as Partial<NewsGraphConfig> | null
  const fail = (why: string): never => {
    throw new Error(`news 設定錯誤：graph.${why}`)
  }
  if (!c || typeof c !== 'object') fail('（整段缺少）')
  const w = c!.weights as Record<string, unknown> | undefined
  if (!w || typeof w !== 'object') fail('weights')
  for (const t of EDGE_TYPES) if (typeof w![t] !== 'number' || (w![t] as number) <= 0) fail(`weights.${t}`)
  if (typeof c!.fanout_power !== 'number' || c!.fanout_power < 0) fail('fanout_power')
  for (const k of ['max_degree', 'max_links', 'peers_per_group', 'mentions_per_item'] as const) if (!Number.isInteger(c![k]) || (c![k] as number) < 1) fail(k)
  return c as NewsGraphConfig
}

const ID_RE = /^[a-z0-9-]{2,40}$/

/** configs/news.yaml 的 JSON 鏡像 → NewsConfig；少欄位、型別不對、代號重複 → 丟錯。 */
export function validateConfig(x: unknown): NewsConfig {
  const c = x as Partial<NewsConfig> | null
  const fail = (why: string): never => {
    throw new Error(`news 設定錯誤：${why}`)
  }
  if (!c || typeof c.user_agent !== 'string' || !c.user_agent) fail('user_agent')
  if (!Number.isInteger(c?.per_run_limit) || (c?.per_run_limit ?? 0) < 1) fail('per_run_limit')
  if (typeof c?.stale_hours !== 'number' || c.stale_hours <= 0) fail('stale_hours')
  if (!Array.isArray(c?.sources) || c.sources.length === 0) fail('sources')
  const seen = new Set<string>()
  for (const s of c!.sources!) {
    if (!ID_RE.test(s.id) || seen.has(s.id)) fail(`來源代號 ${s.id}`)
    seen.add(s.id)
    if (!['intl', 'tw'].includes(s.kind) || !['en', 'zh'].includes(s.lang) || !['rss', 'cnyes'].includes(s.format)) fail(`來源 ${s.id} 的 kind／lang／format`)
    if (!/^https:\/\//.test(s.url) || !/^https:\/\//.test(s.terms) || !/^\d{4}-\d{2}-\d{2}$/.test(s.checked) || !s.name) fail(`來源 ${s.id} 的網址、條款或檢查日期`)
  }
  validateTranslateConfig(c!.translate)
  validateMentionConfig(c!.mention)
  validateGraphConfig(c!.graph)
  return c as NewsConfig
}

export type FetchResult = { status: number; body: string | null; etag: string | null; lastModified: string | null }
export type SourceState = { etag: string | null; lastModified: string | null }
export type ItemRow = {
  id: string
  source: string
  guid: string
  url: string
  title_orig: string
  summary_orig: string | null
  lang: 'en' | 'zh'
  published_at: string | null
  first_seen_at: string
  content_sha: string
}
export type VersionRow = { item_id: string; title_orig: string; summary_orig: string | null; content_sha: string; seen_at: string }
export type RunRow = {
  run_id: string
  source: string
  started_at: string
  ok: boolean
  http_status: number | null
  fetched: number
  new_items: number
  new_versions: number
  error: string | null
}

export interface NewsStore {
  /** 上次的 ETag／Last-Modified（條件式請求用）；沒抓過 → null */
  state(source: string): Promise<SourceState | null>
  saveState(source: string, st: SourceState): Promise<void>
  /** 這些 id 目前最新內容的雜湊（含版本表）；沒有的不在 Map 裡 */
  latestSha(ids: string[]): Promise<Map<string, string>>
  insert(items: ItemRow[], versions: VersionRow[]): Promise<void>
  recordRun(run: RunRow): Promise<void>
}

export type Deps = {
  now: () => Date
  fetch: (url: string, state: SourceState | null, userAgent: string) => Promise<FetchResult>
  store: NewsStore
  runId: string
}

export async function sha256Hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s))
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/** 一則的代號：來源＋guid 的 sha256 前 16 碼（同一則永遠同一個代號）。 */
export const itemId = async (source: string, guid: string): Promise<string> => (await sha256Hex(`${source}\n${guid}`)).slice(0, 16)
/** 內容雜湊：標題＋摘要；變了就加一版。 */
export const contentSha = (title: string, summary: string | null): Promise<string> => sha256Hex(`${title}\n${summary ?? ''}`)

function parse(src: Source, body: string): FeedItem[] {
  if (src.format === 'cnyes') {
    let json: unknown
    try {
      json = JSON.parse(body)
    } catch {
      throw new Error('鉅亨網回應不是 JSON')
    }
    return parseCnyes(json)
  }
  return parseFeed(body)
}

/** 一個來源抓一次。任何錯誤都記進執行紀錄（ok=false、error），不往外丟。 */
export async function ingestSource(src: Source, cfg: NewsConfig, deps: Deps): Promise<RunRow> {
  const started = deps.now().toISOString()
  const run: RunRow = { run_id: deps.runId, source: src.id, started_at: started, ok: false, http_status: null, fetched: 0, new_items: 0, new_versions: 0, error: null }
  try {
    const prev = await deps.store.state(src.id)
    const res = await deps.fetch(src.url, prev, cfg.user_agent)
    run.http_status = res.status
    if (res.status === 304) {
      run.ok = true
      return run
    }
    if (res.status < 200 || res.status >= 300 || res.body === null) throw new Error(`HTTP ${res.status}`)

    const items = parse(src, res.body).slice(0, cfg.per_run_limit)
    run.fetched = items.length
    const seen = new Set<string>()
    const rows: { id: string; sha: string; it: FeedItem }[] = []
    for (const it of items) {
      const id = await itemId(src.id, it.guid)
      if (seen.has(id)) continue // 同一份來源裡重複的那則只算一次
      seen.add(id)
      rows.push({ id, sha: await contentSha(it.title, it.summary), it })
    }
    const known = await deps.store.latestSha(rows.map((r) => r.id))
    const seenAt = deps.now().toISOString()
    const fresh: ItemRow[] = []
    const versions: VersionRow[] = []
    for (const { id, sha, it } of rows) {
      const old = known.get(id)
      if (old === undefined) {
        fresh.push({ id, source: src.id, guid: it.guid, url: it.url, title_orig: it.title, summary_orig: it.summary, lang: src.lang, published_at: it.publishedAt, first_seen_at: seenAt, content_sha: sha })
      } else if (old !== sha) {
        versions.push({ item_id: id, title_orig: it.title, summary_orig: it.summary, content_sha: sha, seen_at: seenAt })
      }
    }
    await deps.store.insert(fresh, versions)
    await deps.store.saveState(src.id, { etag: res.etag, lastModified: res.lastModified })
    run.new_items = fresh.length
    run.new_versions = versions.length
    run.ok = true
  } catch (e) {
    run.error = e instanceof Error ? e.message : String(e)
  } finally {
    await deps.store.recordRun(run)
  }
  return run
}

/** 全部來源各抓一次（同時抓，各自獨立）。回傳每個來源的執行紀錄。 */
export async function runIngest(cfg: NewsConfig, deps: Deps): Promise<RunRow[]> {
  return Promise.all(cfg.sources.map((s) => ingestSource(s, cfg, deps)))
}
