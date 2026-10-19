// Edge Function 的 IO（Deno）：資料庫連線、抓 RSS、把 ingest.ts 的 NewsStore 介面接到 Postgres。
// 邏輯都在 news-service/src（純函式、有測試）；這裡只有 SQL 與 HTTP。
import postgres from 'npm:postgres@3.4.5'
import type { FetchResult, NewsStore, SourceState } from '../../../src/ingest.ts'

export type Sql = ReturnType<typeof postgres>

/** 新聞專案自己的資料庫（Edge Function 內建環境變數 SUPABASE_DB_URL）。 */
export function db(): Sql {
  const url = Deno.env.get('SUPABASE_DB_URL')
  if (!url) throw new Error('SUPABASE_DB_URL 沒設定')
  return postgres(url, { prepare: false, max: 3, idle_timeout: 5 })
}

/** 抓一個來源：帶上次的 ETag／Last-Modified，20 秒逾時，跟著轉址（Bloomberg 會 301）。 */
export async function fetchFeed(url: string, state: SourceState | null, userAgent: string): Promise<FetchResult> {
  const headers: Record<string, string> = {
    'user-agent': userAgent,
    accept: 'application/rss+xml, application/atom+xml, application/xml, application/json;q=0.9, */*;q=0.8',
  }
  if (state?.etag) headers['if-none-match'] = state.etag
  if (state?.lastModified) headers['if-modified-since'] = state.lastModified
  const res = await fetch(url, { headers, redirect: 'follow', signal: AbortSignal.timeout(20_000) })
  const body = res.status === 304 ? null : await res.text()
  return { status: res.status, body, etag: res.headers.get('etag'), lastModified: res.headers.get('last-modified') }
}

export function pgStore(sql: Sql): NewsStore {
  return {
    async state(source) {
      const [r] = await sql<{ etag: string | null; last_modified: string | null }[]>`
        select etag, last_modified from news.source_state where source = ${source}`
      return r ? { etag: r.etag, lastModified: r.last_modified } : null
    },
    async saveState(source, st) {
      await sql`
        insert into news.source_state (source, etag, last_modified, updated_at)
        values (${source}, ${st.etag}, ${st.lastModified}, now())
        on conflict (source) do update set etag = excluded.etag, last_modified = excluded.last_modified, updated_at = now()`
    },
    async latestSha(ids) {
      if (ids.length === 0) return new Map()
      const rows = await sql<{ id: string; sha: string }[]>`
        select i.id,
               coalesce((select v.content_sha from news.raw_item_versions v where v.item_id = i.id order by v.seen_at desc limit 1),
                        i.content_sha) as sha
        from news.raw_items i where i.id = any(${ids})`
      return new Map(rows.map((r) => [r.id, r.sha]))
    },
    async insert(items, versions) {
      if (items.length === 0 && versions.length === 0) return
      await sql.begin(async (tx) => {
        // 兩次執行同時抓到同一則（理論上不會，排程 30 分鐘一次）→ 先寫的算數，後到的略過，不覆寫
        if (items.length > 0) await tx`insert into news.raw_items ${tx(items)} on conflict (id) do nothing`
        if (versions.length > 0) await tx`insert into news.raw_item_versions ${tx(versions)} on conflict do nothing`
      })
    },
    async recordRun(run) {
      await sql`insert into news.ingest_runs ${sql(run)}`
    },
  }
}

export const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } })

/** 服務金鑰（網站、Python、排程各用自己的那把）；不對就回 403。 */
export function authorized(req: Request, envName: string): boolean {
  const want = Deno.env.get(envName)
  return Boolean(want) && req.headers.get('x-news-key') === want
}
