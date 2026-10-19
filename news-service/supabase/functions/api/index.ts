// 新聞服務對外 API v1（SPEC-017 §10）：GET /functions/v1/api/v1/items、/v1/health。
// 只給伺服器呼叫（網站的 Pages Functions、Python），帶 x-news-key（NEWS_API_KEY）；瀏覽器不直接打這裡。
import config from '../../../config/news.json' with { type: 'json' }
import { API_VERSION, health, parseItemsQuery } from '../../../src/api.ts'
import { validateConfig } from '../../../src/ingest.ts'
import { authorized, db, json } from '../_shared/io.ts'

const cfg = validateConfig(config)
const meta = new Map(cfg.sources.map((s) => [s.id, s]))
const iso = (d: Date | null): string | null => (d ? d.toISOString() : null)

Deno.serve(async (req) => {
  if (req.method !== 'GET') return json({ detail: 'method not allowed' }, 405)
  if (!authorized(req, 'NEWS_API_KEY')) return json({ detail: 'forbidden' }, 403)
  const url = new URL(req.url)
  // Supabase 轉進來的路徑是 /api/v1/...（函式名稱在前）
  const path = url.pathname.replace(/^\/api/, '')
  const sql = db()
  try {
    if (path === '/v1/health') {
      // 只看近 7 天：超過 7 天沒成功的來源 last_ok_at 為 null（壞著）
      const rows = await sql<{ source: string; last_run_at: Date | null; last_ok_at: Date | null; last_error: string | null }[]>`
        select source, max(started_at) as last_run_at, max(started_at) filter (where ok) as last_ok_at,
               (array_agg(error order by started_at desc))[1] as last_error
        from news.ingest_runs where started_at > now() - interval '7 days' group by source`
      const runs = rows.map((r) => ({ source: r.source, last_run_at: iso(r.last_run_at), last_ok_at: iso(r.last_ok_at), last_error: r.last_error }))
      return json(health(cfg, runs, new Date()))
    }
    if (path === '/v1/items') {
      let q
      try {
        q = parseItemsQuery(url.searchParams, cfg)
      } catch (e) {
        return json({ detail: e instanceof Error ? e.message : String(e) }, 400)
      }
      const kindSources = q.kind ? cfg.sources.filter((s) => s.kind === q.kind).map((s) => s.id) : null
      // 標題與摘要顯示最新一版（來源後來改過的話）；first_seen_at 永遠是第一次抓到的時間
      const rows = await sql<{ id: string; source: string; lang: string; url: string; title: string; summary: string | null; published_at: Date | null; first_seen_at: Date; versions: number }[]>`
        select i.id, i.source, i.lang, i.url,
               coalesce(v.title_orig, i.title_orig) as title, coalesce(v.summary_orig, i.summary_orig) as summary,
               i.published_at, i.first_seen_at, coalesce(v.n, 0)::int as versions
        from news.raw_items i
        left join lateral (
          select title_orig, summary_orig, count(*) over () as n from news.raw_item_versions
          where item_id = i.id order by seen_at desc limit 1
        ) v on true
        where true
          ${q.since ? sql`and i.first_seen_at >= ${q.since}` : sql``}
          ${q.until ? sql`and i.first_seen_at < ${q.until}` : sql``}
          ${q.source ? sql`and i.source = ${q.source}` : sql``}
          ${kindSources ? sql`and i.source = any(${kindSources})` : sql``}
        order by i.first_seen_at desc, i.id
        limit ${q.limit}`
      const items = rows.map((r) => ({
        id: r.id,
        source: r.source,
        source_name: meta.get(r.source)?.name ?? r.source,
        kind: meta.get(r.source)?.kind ?? null,
        lang: r.lang,
        title: r.title,
        summary: r.summary,
        url: r.url,
        published_at: iso(r.published_at),
        first_seen_at: iso(r.first_seen_at),
        versions: r.versions,
      }))
      return json({ api_version: API_VERSION, items })
    }
    return json({ detail: 'not found' }, 404)
  } catch (e) {
    console.error('news api 失敗：', e)
    return json({ detail: '新聞服務暫時無法取得資料' }, 503)
  } finally {
    await sql.end({ timeout: 5 })
  }
})
