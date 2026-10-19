// 新聞服務對外 API v1（SPEC-017 §10）：GET /functions/v1/api/v1/items（T-401 起多 translation、title_zh、summary_zh；T-402 起多 mentions）、
// /v1/graph?id=（T-404：一則新聞的關係圖）、/v1/stock-news?id=tw:2382 與 /v1/stock-news-counts?ids=…（反向看圖：某檔連得到哪些新聞）、/v1/health。
// 只給伺服器呼叫（網站的 Pages Functions、Python），帶 x-news-key（NEWS_API_KEY）；瀏覽器不直接打這裡。
import config from '../../../config/news.json' with { type: 'json' }
import { API_VERSION, health, parseItemsQuery } from '../../../src/api.ts'
import type { EntityType } from '../../../src/graph.ts'
import { buildNodes, itemMentions, type EdgeRow, type EntityRow, type GNode, type ItemMention } from '../../../src/graphapi.ts'
import { validateConfig } from '../../../src/ingest.ts'
import { countsFor, linksTo, parseStockIds, prepare, type ItemMentions, type Prepared } from '../../../src/stocknews.ts'
import { authorized, db, json } from '../_shared/io.ts'

const cfg = validateConfig(config)
const meta = new Map(cfg.sources.map((s) => [s.id, s]))
const iso = (d: Date | null): string | null => (d ? d.toISOString() : null)

const GRAPH_TABLES_TTL_MS = 10 * 60 * 1000
let graphCache: { at: number; entities: Map<string, EntityRow>; edges: EdgeRow[]; prep: Prepared } | null = null
async function graphTables(sql: ReturnType<typeof db>): Promise<{ entities: Map<string, EntityRow>; edges: EdgeRow[]; prep: Prepared }> {
  if (graphCache && Date.now() - graphCache.at < GRAPH_TABLES_TTL_MS) return graphCache
  const [ents, edges] = await Promise.all([
    sql<EntityRow[]>`select id, type, label, sub from news.entities where valid_to is null`,
    sql<EdgeRow[]>`select src, dst, type, status, label, rank from news.graph_edges where valid_to is null`,
  ])
  const entities = new Map(ents.map((e) => [e.id, e]))
  graphCache = { at: Date.now(), entities, edges, prep: prepare(edges, entities, cfg.graph) }
  return graphCache
}

/** 近 days 天的新聞各提到哪些實體（目前的比對規則）。 */
async function recentMentions(sql: ReturnType<typeof db>, days: number): Promise<ItemMentions[]> {
  const rows = await sql<{ item_id: string; entity_id: string }[]>`
    select m.item_id, m.entity_id from news.mentions m join news.raw_items i on i.id = m.item_id
    where i.first_seen_at > now() - make_interval(days => ${days}) and m.rule_version = ${cfg.mention.rule_version}`
  const by = new Map<string, string[]>()
  for (const r of rows) by.set(r.item_id, [...(by.get(r.item_id) ?? []), r.entity_id])
  return [...by].map(([item_id, entities]) => ({ item_id, entities: entities.sort() }))
}

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
    if (path === '/v1/stock-news' || path === '/v1/stock-news-counts') {
      const sn = cfg.stock_news
      let ids: string[]
      try {
        ids = path === '/v1/stock-news' ? parseStockIds(url.searchParams.get('id'), 1) : parseStockIds(url.searchParams.get('ids'), sn.counts_max_ids)
      } catch (e) {
        return json({ detail: e instanceof Error ? e.message : String(e) }, 400)
      }
      const days = path === '/v1/stock-news' ? sn.stock_days : sn.counts_days
      const t = await graphTables(sql)
      const recent = await recentMentions(sql, days)
      if (path === '/v1/stock-news-counts') {
        return json({ days, counts: countsFor(ids, recent, t.prep, t.entities, cfg.graph, sn.stock_max_degree, sn.hub_share) })
      }
      const links = linksTo(ids[0], recent, t.prep, t.entities, cfg.graph, sn.stock_max_degree, sn.hub_share)
      const metaRows =
        links.length === 0
          ? []
          : await sql<{ id: string; source: string; url: string; first_seen_at: Date; title: string; title_zh: string | null }[]>`
              select i.id, i.source, i.url, i.first_seen_at, coalesce(v.title_orig, i.title_orig) as title, t.title_zh
              from news.raw_items i
              left join lateral (
                select title_orig, content_sha from news.raw_item_versions where item_id = i.id order by seen_at desc limit 1
              ) v on true
              left join news.translations t on t.item_id = i.id and t.content_sha = coalesce(v.content_sha, i.content_sha) and t.status = 'ok'
              where i.id = any(${links.map((l) => l.item_id)})`
      const byId = new Map(metaRows.map((r) => [r.id, r]))
      // 直接提到的在前；同一種裡新的在前
      const items = links
        .filter((l) => byId.has(l.item_id))
        .map((l) => {
          const r = byId.get(l.item_id)!
          return {
            id: r.id,
            source_name: meta.get(r.source)?.name ?? r.source,
            url: r.url,
            first_seen_at: iso(r.first_seen_at),
            title: r.title,
            title_zh: r.title_zh,
            degree: l.degree,
            score: Number(l.score.toFixed(4)),
            draft: l.draft,
            path: l.path,
          }
        })
        .sort((a, b) => a.degree - b.degree || (b.first_seen_at ?? '').localeCompare(a.first_seen_at ?? ''))
        .slice(0, sn.stock_limit)
      return json({ id: ids[0], days, items })
    }
    if (path === '/v1/graph') {
      const id = url.searchParams.get('id')
      if (!id) return json({ detail: '缺 id' }, 400)
      // 顯示最新一版的標題；關係圖用「目前這一版內容」比對到的實體（一度）
      const [it] = await sql<{ id: string; source: string; url: string; first_seen_at: Date; title: string; title_zh: string | null; sha: string }[]>`
        select i.id, i.source, i.url, i.first_seen_at, coalesce(v.title_orig, i.title_orig) as title, t.title_zh,
               coalesce(v.content_sha, i.content_sha) as sha
        from news.raw_items i
        left join lateral (
          select title_orig, content_sha from news.raw_item_versions
          where item_id = i.id order by seen_at desc limit 1
        ) v on true
        left join news.translations t on t.item_id = i.id and t.content_sha = coalesce(v.content_sha, i.content_sha)
        where i.id = ${id}`
      if (!it) return json({ detail: '沒有這則新聞' }, 404)
      const mentioned = await sql<{ entity_id: string }[]>`
        select entity_id from news.mentions where item_id = ${it.id} and content_sha = ${it.sha}
          and rule_version = ${cfg.mention.rule_version} order by entity_id`
      let nodes: GNode[] = []
      if (mentioned.length > 0) {
        // 整張有效的實體表與線表（幾千列）：同族群線要知道整個族群的名次，只抓鄰居算不出來。
        // 兩張表一天才同步一次（scripts/news_sync_entities.py），同一個函式執行個體暫存 GRAPH_TABLES_TTL_MS（2026-10-20 實測每次重讀要 2.4～3.4 秒）
        const t = await graphTables(sql)
        nodes = buildNodes(
          mentioned.map((m) => m.entity_id),
          t.edges,
          t.entities,
          cfg.graph,
        )
      }
      return json({
        item: {
          id: it.id,
          source_name: meta.get(it.source)?.name ?? it.source,
          url: it.url,
          first_seen_at: iso(it.first_seen_at),
          title: it.title,
          title_zh: it.title_zh,
        },
        rule_version: cfg.mention.rule_version,
        nodes,
      })
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
      const rows = await sql<{
        id: string
        source: string
        lang: string
        url: string
        title: string
        summary: string | null
        published_at: Date | null
        first_seen_at: Date
        versions: number
        title_zh: string | null
        summary_zh: string | null
        tr_status: string | null
        sha: string
      }[]>`
        select i.id, i.source, i.lang, i.url,
               coalesce(v.title_orig, i.title_orig) as title, coalesce(v.summary_orig, i.summary_orig) as summary,
               i.published_at, i.first_seen_at, coalesce(v.n, 0)::int as versions,
               t.title_zh, t.summary_zh, t.status as tr_status, coalesce(v.content_sha, i.content_sha) as sha
        from news.raw_items i
        left join lateral (
          select title_orig, summary_orig, content_sha, count(*) over () as n from news.raw_item_versions
          where item_id = i.id order by seen_at desc limit 1
        ) v on true
        -- 目前這一版內容的翻譯（T-401）；還沒翻 → null
        left join news.translations t on t.item_id = i.id and t.content_sha = coalesce(v.content_sha, i.content_sha)
        where true
          ${q.since ? sql`and i.first_seen_at >= ${q.since}` : sql``}
          ${q.until ? sql`and i.first_seen_at <= ${q.until}` : sql``}
          ${q.source ? sql`and i.source = ${q.source}` : sql``}
          ${kindSources ? sql`and i.source = any(${kindSources})` : sql``}
        order by i.first_seen_at desc, i.id
        limit ${q.limit}`
      // 每則目前這一版內容比對到的實體（T-402，一度）；實體已停用（valid_to 有值）的不列
      const ids = rows.map((r) => r.id)
      const shaOf = new Map(rows.map((r) => [r.id, r.sha]))
      const mrows =
        ids.length === 0
          ? []
          : await sql<{ item_id: string; content_sha: string; id: string; type: EntityType; label: string }[]>`
              select m.item_id, m.content_sha, e.id, e.type, e.label
              from news.mentions m join news.entities e on e.id = m.entity_id and e.valid_to is null
              where m.item_id = any(${ids}) and m.rule_version = ${cfg.mention.rule_version}`
      const mentionsOf = new Map<string, ItemMention[]>()
      for (const m of mrows) {
        if (shaOf.get(m.item_id) !== m.content_sha) continue
        mentionsOf.set(m.item_id, [...(mentionsOf.get(m.item_id) ?? []), { id: m.id, type: m.type, label: m.label }])
      }
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
        // 英文才有：ok＝有譯文、failed＝沒過檢查（網站顯示原文標「未譯」）、pending＝還沒翻；中文新聞 null
        translation: r.lang === 'en' ? (r.tr_status ?? 'pending') : null,
        title_zh: r.title_zh,
        summary_zh: r.summary_zh,
        // 台股在前、再國外、再族群／主題，最多 graph.mentions_per_item 個；沒有 → []
        mentions: itemMentions(mentionsOf.get(r.id) ?? [], cfg.graph.mentions_per_item),
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
