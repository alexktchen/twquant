// 新聞服務對外 API v1（SPEC-017 §10）：GET /functions/v1/api/v1/items（T-401 起多 translation、title_zh、summary_zh；T-402 起多 mentions）、
// /v1/graph?id=（T-404：一則新聞的關係圖）、/v1/similar?id=（T-410：相似新聞，同事件在前）、/v1/search?q=（T-410：語意搜尋）、GET／POST /v1/review（T-403：核可頁，只有擁有者）、/v1/digest（T-417：熱門主題、多家同報、股票熱度三張卡）、/v1/galaxy（T-418：全景星雲圖，最新一筆快照）、/v1/stock-news-days?id=（T-419：個股頁 K 線上的新聞標記）、/v1/thread?id=（T-420：這件事的來龍去脈）、/v1/summary?id=（D-207：點開才翻摘要）、/v1/stock-news?id=tw:2382 與 /v1/stock-news-counts?ids=…（反向看圖：某檔連得到哪些新聞）、/v1/evidence?ids=…&entities=…（T-437：證據句）、/v1/health。
// 只給伺服器呼叫（網站的 Pages Functions、Python），帶 x-news-key（NEWS_API_KEY）；瀏覽器不直接打這裡。
import config from '../../../config/news.json' with { type: 'json' }
import typeWeights from '../../../config/news_types.json' with { type: 'json' }
import { API_VERSION, health, parseItemsQuery } from '../../../src/api.ts'
import type { EntityType } from '../../../src/graph.ts'
import { buildNodes, itemMentions, type EdgeRow, type EntityRow, type GNode, type ItemMention } from '../../../src/graphapi.ts'
import { validateConfig } from '../../../src/ingest.ts'
import { parseEvidenceQuery, pickEvidence, type EvidenceRow } from '../../../src/evidence.ts'
import { mergeSimilar, parseSearchQuery } from '../../../src/embed.ts'
import { baseDaysOf, hotStocks, multiOutlet, topTopics, type CoRow, type EvRow, type HotRow, type TopicRow } from '../../../src/digest.ts'
import { REVIEW_TYPES, edgeKey, groupDrafts, isOwner, parseReviewBody, planReview, type DecisionIn, type DraftEdge, type EntityInfo } from '../../../src/review.ts'
import { countsFor, coverageDaysOf, linksTo, newsDays, parseStockIds, prepare, quietGap, type DayRow, type ItemMentions, type Prepared } from '../../../src/stocknews.ts'
import { summaryFor } from '../../../src/translate.ts'
import { authorized, db, embedQuery, json, summaryDeps } from '../_shared/io.ts'

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

/** 直接提到這檔的新聞，近 days 天（台北日期）。 */
async function dayRows(sql: ReturnType<typeof db>, id: string, days: number): Promise<DayRow[]> {
  return sql<DayRow[]>`
    select (i.first_seen_at at time zone 'Asia/Taipei')::date::text as day, i.id, i.source,
           coalesce(v.title_orig, i.title_orig) as title, tr.title_zh, i.url, i.first_seen_at::text as first_seen_at
    from news.mentions m join news.raw_items i on i.id = m.item_id
    left join lateral (select title_orig, content_sha from news.raw_item_versions where item_id = i.id order by seen_at desc limit 1) v on true
    left join news.translations tr on tr.item_id = i.id and tr.content_sha = coalesce(v.content_sha, i.content_sha) and tr.status = 'ok'
    where m.entity_id = ${id} and m.rule_version = ${cfg.mention.rule_version}
      and i.first_seen_at > now() - make_interval(days => ${days})`
}

/** 久違的消息（T-419）：這檔最近一次被提到之前隔了多久；要我們收新聞夠久才說得出口。 */
async function quietOf(sql: ReturnType<typeof db>, id: string) {
  const sn = cfg.stock_news
  const [days, first] = await Promise.all([
    sql<{ day: string }[]>`
      select distinct (i.first_seen_at at time zone 'Asia/Taipei')::date::text as day
      from news.mentions m join news.raw_items i on i.id = m.item_id
      where m.entity_id = ${id} and m.rule_version = ${cfg.mention.rule_version} and i.first_seen_at > now() - make_interval(days => ${sn.timeline_days})`,
    sql<{ t: Date | null }[]>`select min(first_seen_at) as t from news.raw_items`,
  ])
  return quietGap(days.map((d) => d.day), coverageDaysOf(iso(first[0]?.t ?? null), new Date()), sn.timeline_days, sn.quiet_min_days)
}

type Scored = { item_id: string; score: number; same_event?: boolean }
/** 一批新聞的顯示欄位（最新一版標題、翻譯 ok 的譯文），依 rows 的順序、附上分數；找不到的略過。 */
async function withMeta(sql: ReturnType<typeof db>, rows: Scored[]) {
  if (rows.length === 0) return []
  const meta_ = await sql<{ id: string; source: string; url: string; first_seen_at: Date; title: string; title_zh: string | null }[]>`
    select i.id, i.source, i.url, i.first_seen_at, coalesce(v.title_orig, i.title_orig) as title, t.title_zh
    from news.raw_items i
    left join lateral (
      select title_orig, content_sha from news.raw_item_versions where item_id = i.id order by seen_at desc limit 1
    ) v on true
    left join news.translations t on t.item_id = i.id and t.content_sha = coalesce(v.content_sha, i.content_sha) and t.status = 'ok'
    where i.id = any(${rows.map((r) => r.item_id)})`
  const byId = new Map(meta_.map((r) => [r.id, r]))
  return rows
    .filter((r) => byId.has(r.item_id))
    .map((r) => {
      const m = byId.get(r.item_id)!
      return {
        id: m.id,
        source_name: meta.get(m.source)?.name ?? m.source,
        kind: meta.get(m.source)?.kind ?? null,
        url: m.url,
        first_seen_at: iso(m.first_seen_at),
        title: m.title,
        title_zh: m.title_zh,
        score: Number(r.score.toFixed(4)),
        same_event: r.same_event ?? false,
      }
    })
}

Deno.serve(async (req) => {
  const url = new URL(req.url)
  // Supabase 轉進來的路徑是 /api/v1/...（函式名稱在前）
  const path = url.pathname.replace(/^\/api/, '')
  // 只有核可頁收 POST
  if (req.method !== 'GET' && !(req.method === 'POST' && path === '/v1/review')) return json({ detail: 'method not allowed' }, 405)
  if (!authorized(req, 'NEWS_API_KEY')) return json({ detail: 'forbidden' }, 403)
  const sql = db()
  try {
    if (path === '/v1/health') {
      // 只看近 7 天：超過 7 天沒成功的來源 last_ok_at 為 null（壞著）
      const rows = await sql<{ source: string; last_run_at: Date | null; last_ok_at: Date | null; last_error: string | null }[]>`
        select source, max(started_at) as last_run_at, max(started_at) filter (where ok) as last_ok_at,
               (array_agg(error order by started_at desc))[1] as last_error
        from news.ingest_runs where started_at > now() - interval '7 days' group by source`
      const runs = rows.map((r) => ({ source: r.source, last_run_at: iso(r.last_run_at), last_ok_at: iso(r.last_ok_at), last_error: r.last_error }))
      const [db] = await sql<{ b: string }[]>`select pg_database_size(current_database())::text as b`
      return json(health(cfg, runs, new Date(), Number(db.b)))
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
      return json({ id: ids[0], days, items, quiet: await quietOf(sql, ids[0]) })
    }
    if (path === '/v1/stock-news-days') {
      let ids: string[]
      try {
        ids = parseStockIds(url.searchParams.get('id'), 1)
      } catch (e) {
        return json({ detail: e instanceof Error ? e.message : String(e) }, 400)
      }
      const sn = cfg.stock_news
      const rows = await dayRows(sql, ids[0], sn.timeline_days)
      return json({ id: ids[0], timeline_days: sn.timeline_days, days: newsDays(rows, (s) => meta.get(s)?.name ?? s, sn.timeline_per_day) })
    }
    if (path === '/v1/galaxy') {
      // 排程（scripts/news_galaxy.py）算好的整包；原樣送出文字，不在這裡 parse 再 stringify（約 700 KB，省 CPU）
      const [row] = await sql<{ d: string }[]>`select data::text as d from news.galaxy_snapshots order by built_at desc limit 1`
      if (!row) return json({ detail: '全景還沒算好（排程每天跑幾次）' }, 404)
      return new Response(row.d, { status: 200, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } })
    }
    if (path === '/v1/digest') {
      const rv = cfg.mention.rule_version
      const dg = cfg.digest
      // 近 48 小時每則提到哪些實體（同一則不同版本只算一次）
      const recent = (hours: number) => sql`
        select distinct m.item_id, m.entity_id, i.first_seen_at from news.mentions m join news.raw_items i on i.id = m.item_id
        where m.rule_version = ${rv} and i.first_seen_at > now() - make_interval(hours => ${hours})`
      const [topicRows, evRows, first, totalRows] = await Promise.all([
        sql<TopicRow[]>`
          with m as (${recent(48)})
          select e.id, e.label, e.type,
                 count(*) filter (where m.first_seen_at > now() - interval '24 hours')::int as n_now,
                 count(*) filter (where m.first_seen_at <= now() - interval '24 hours')::int as n_prev
          from m join news.entities e on e.id = m.entity_id and e.valid_to is null
          where e.type in ('theme', 'group') group by 1, 2, 3`,
        // 近 24 小時有新進成員的事件群（至少兩則），整群拿出來（含 24 小時前的舊成員）
        sql<(Omit<EvRow, 'first_seen_at'> & { first_seen_at: Date })[]>`
          with live as (
            select c.cluster_id from news.clusters c join news.raw_items i on i.id = c.item_id
            group by 1 having count(*) >= 2 and max(i.first_seen_at) > now() - interval '24 hours'
          )
          select c.cluster_id as ev, i.id as item_id, i.source, coalesce(v.title_orig, i.title_orig) as title, tr.title_zh, i.url, i.first_seen_at,
                 coalesce((select array_agg(distinct e.label order by e.label) from news.mentions m join news.entities e on e.id = m.entity_id
                           where m.item_id = i.id and m.rule_version = ${rv} and e.type = 'tw'), '{}') as stocks
          from live join news.clusters c using (cluster_id) join news.raw_items i on i.id = c.item_id
          left join lateral (select title_orig, content_sha from news.raw_item_versions where item_id = i.id order by seen_at desc limit 1) v on true
          left join news.translations tr on tr.item_id = i.id and tr.content_sha = coalesce(v.content_sha, i.content_sha) and tr.status = 'ok'`,
        sql<{ t: Date | null }[]>`select min(first_seen_at) as t from news.raw_items`,
        sql<{ n: number }[]>`with m as (${recent(24)}) select count(distinct item_id)::int as n from m`,
      ])
      const total = totalRows[0]?.n ?? 0
      const ids = topTopics(topicRows, [], total, dg).items.map((t) => t.id)
      const now = new Date()
      const baseDays = baseDaysOf(iso(first[0]?.t ?? null), now, dg)
      const [coRows, hotRows] = await Promise.all([
        ids.length === 0
          ? Promise.resolve([] as CoRow[])
          : sql<CoRow[]>`
              with m as (${recent(24)})
              select t.entity_id as topic, s.entity_id as stock, e.label, count(*)::int as n
              from m t join m s on s.item_id = t.item_id join news.entities e on e.id = s.entity_id and e.type = 'tw'
              where t.entity_id = any(${ids}) group by 1, 2, 3`,
        sql<HotRow[]>`
          with m as (${recent(24 * (dg.base_days + 1))})
          select e.id, e.label, e.sub,
                 count(*) filter (where m.first_seen_at > now() - interval '24 hours')::int as n_now,
                 count(*) filter (where m.first_seen_at <= now() - interval '24 hours'
                                    and m.first_seen_at > now() - make_interval(hours => ${24 * (baseDays + 1)}))::int as n_base
          from m join news.entities e on e.id = m.entity_id and e.valid_to is null and e.type = 'tw'
          group by 1, 2, 3`,
      ])
      const hot = hotStocks(hotRows, baseDays, dg)
      // 久違的消息（T-419）：熱度榜上的台股，上一次被提到是多久以前
      const quiet = await Promise.all(hot.map((h) => quietOf(sql, h.id)))
      return json({
        api_version: API_VERSION,
        as_of: now.toISOString(),
        base_days: baseDays,
        topics: topTopics(topicRows, coRows, total, dg),
        multi: multiOutlet(evRows.map((r) => ({ ...r, first_seen_at: iso(r.first_seen_at)! })), (s) => meta.get(s)?.name ?? s, dg),
        hot: hot.map((h, i) => ({ ...h, quiet: quiet[i] })),
      })
    }
    if (path === '/v1/review') {
      // 登入者 email 由網站驗過 Cloudflare Access 權杖後放在 x-news-user（只有帶服務金鑰的呼叫端送得進來）；擁有者名單是祕密 NEWS_OWNER_EMAILS
      const user = req.headers.get('x-news-user')
      const owner = isOwner(user, Deno.env.get('NEWS_OWNER_EMAILS'))
      if (req.method === 'GET') {
        if (!owner) return json({ can_review: false, total: 0, groups: [] })
        const [drafts, ents] = await Promise.all([
          sql<DraftEdge[]>`
            select src, dst, type, label, rank, source from news.graph_edges
            where valid_to is null and status = 'draft' and type = any(${[...REVIEW_TYPES]})`,
          sql<EntityInfo[]>`select id, type, label, sub from news.entities where valid_to is null`,
        ])
        return json({ can_review: true, total: drafts.length, groups: groupDrafts(drafts, new Map(ents.map((e) => [e.id, e]))) })
      }
      // 不回 403：網站把 403 當成「要重新登入」（frontend/functions/_lib/news-proxy.ts 也不轉 403）
      if (!owner) return json({ detail: '只有擁有者能核可' }, 400)
      let decisions: DecisionIn[]
      try {
        decisions = parseReviewBody(await req.json())
      } catch (e) {
        return json({ detail: e instanceof Error ? e.message : String(e) }, 400)
      }
      const plan = await sql.begin(async (tx) => {
        // 鎖住要處理的草稿：兩個分頁同時按，後到的那個看到已經不是草稿 → 略過
        const rows = await tx<DraftEdge[]>`
          select src, dst, type, label, rank, source from news.graph_edges
          where valid_to is null and status = 'draft' and (src || '|' || dst || '|' || type) = any(${decisions.map(edgeKey)})
          for update`
        const p = planReview(decisions, new Map(rows.map((r) => [edgeKey(r), r])), user!, new Date())
        const at = p.reviews[0]?.reviewed_at
        for (const c of p.close)
          await tx`update news.graph_edges set valid_to = ${at} where valid_to is null and status = 'draft' and src = ${c.src} and dst = ${c.dst} and type = ${c.type}`
        if (p.insert.length) await tx`insert into news.graph_edges ${tx(p.insert)}`
        if (p.reviews.length) await tx`insert into news.reviews ${tx(p.reviews)}`
        return p
      })
      graphCache = null // 這個執行個體馬上看到新的線；其他執行個體最多 GRAPH_TABLES_TTL_MS 後跟上
      return json({
        approved: plan.insert.length,
        rejected: plan.reviews.length - plan.insert.length,
        skipped: plan.skipped,
      })
    }
    if (path === '/v1/thread') {
      const id = url.searchParams.get('id')
      if (!id) return json({ detail: '缺 id' }, 400)
      const [me] = await sql<{ cluster_id: string; thread_id: string }[]>`
        select c.cluster_id, coalesce(t.thread_id, c.cluster_id) as thread_id from news.clusters c left join news.threads t on t.cluster_id = c.cluster_id where c.item_id = ${id}`
      if (!me) return json({ id, cluster_id: null, steps: [] })
      // 這條線上的每個事件群：最早那則當代表（標題、來源），幾則、哪幾家、起訖時間；依時間排
      const steps = await sql<{ cluster_id: string; n: number; first_seen_at: Date; last_seen_at: Date; item_id: string; title: string; title_zh: string | null; url: string; sources: string[] }[]>`
        with tc as (select cluster_id from news.threads where thread_id = ${me.thread_id} union select ${me.cluster_id}),
        members as (
          select c.cluster_id, i.id, i.source, i.url, i.first_seen_at, coalesce(v.title_orig, i.title_orig) as title, tr.title_zh
          from tc join news.clusters c using (cluster_id) join news.raw_items i on i.id = c.item_id
          left join lateral (select title_orig, content_sha from news.raw_item_versions where item_id = i.id order by seen_at desc limit 1) v on true
          left join news.translations tr on tr.item_id = i.id and tr.content_sha = coalesce(v.content_sha, i.content_sha) and tr.status = 'ok'
        )
        select m.cluster_id, count(*)::int as n, min(m.first_seen_at) as first_seen_at, max(m.first_seen_at) as last_seen_at,
               (array_agg(m.id order by m.first_seen_at, m.id))[1] as item_id,
               (array_agg(m.title order by m.first_seen_at, m.id))[1] as title,
               (array_agg(m.title_zh order by m.first_seen_at, m.id))[1] as title_zh,
               (array_agg(m.url order by m.first_seen_at, m.id))[1] as url,
               array_agg(distinct m.source) as sources
        from members m group by m.cluster_id order by min(m.first_seen_at)`
      return json({
        id,
        cluster_id: me.cluster_id,
        steps: steps.map((s) => ({
          cluster_id: s.cluster_id,
          current: s.cluster_id === me.cluster_id,
          n: s.n,
          first_seen_at: iso(s.first_seen_at),
          last_seen_at: iso(s.last_seen_at),
          item_id: s.item_id,
          title: s.title_zh ?? s.title,
          url: s.url,
          outlets: [...new Set(s.sources.map((x) => meta.get(x)?.name ?? x))].sort(),
        })),
      })
    }
    if (path === '/v1/evidence') {
      // 證據句（T-437）：比對名字時存下的「命中字前後各 20 字」；只有目前比對規則的那一批
      let q: { ids: string[]; entities: string[] }
      try {
        q = parseEvidenceQuery(url.searchParams.get('ids'), url.searchParams.get('entities'), cfg.evidence)
      } catch (e) {
        return json({ detail: e instanceof Error ? e.message : String(e) }, 400)
      }
      const rows = await sql<EvidenceRow[]>`
        select item_id, entity_id, evidence, created_at from news.mentions
        where item_id = any(${q.ids}) and entity_id = any(${q.entities}) and rule_version = ${cfg.mention.rule_version}`
      return json({ api_version: API_VERSION, items: pickEvidence(rows) })
    }
    if (path === '/v1/summary') {
      // 點開才翻摘要（D-207）：翻過就直接給，沒翻過現在翻（一次約 2～5 秒）；額度用完回 budget，網站顯示英文摘要
      const id = url.searchParams.get('id')
      if (!id) return json({ detail: '缺 id' }, 400)
      const r = await summaryFor(cfg.translate, summaryDeps(sql, cfg.translate.model), id)
      if (r === null) return json({ detail: '找不到這則新聞' }, 404)
      return json({ api_version: API_VERSION, id, ...r })
    }
    if (path === '/v1/similar') {
      const id = url.searchParams.get('id')
      if (!id) return json({ detail: '缺 id' }, 400)
      const sc = cfg.embed.similar
      const [me] = await sql<{ cluster_id: string | null }[]>`
        select c.cluster_id from news.embeddings e left join news.clusters c on c.item_id = e.item_id where e.item_id = ${id}`
      // 沒有向量：超過保留期（雲端只留近 retention_days 天）→ expired；否則是還沒算（英文等翻譯、或剛抓到）
      if (!me) {
        const [old] = await sql<{ old: boolean }[]>`
          select first_seen_at < now() - make_interval(days => ${cfg.embed.retention_days}) as old from news.raw_items where id = ${id}`
        return json({ id, ready: false, expired: old?.old ?? false, retention_days: cfg.embed.retention_days, items: [] })
      }
      const q = sql`(select vec from news.embeddings where item_id = ${id})`
      const [same, near] = await Promise.all([
        sql<{ item_id: string; score: number }[]>`
          select c.item_id, (1 - (e.vec <=> ${q}))::float8 as score
          from news.clusters c join news.embeddings e on e.item_id = c.item_id
          where c.cluster_id = ${me.cluster_id} and c.item_id <> ${id}`,
        sql<{ item_id: string; score: number }[]>`
          select item_id, (1 - (vec <=> ${q}))::float8 as score from news.embeddings
          where item_id <> ${id} order by vec <=> ${q} limit ${sc.limit * 2}`,
      ])
      return json({ id, ready: true, items: await withMeta(sql, mergeSimilar(id, same, near, sc)) })
    }
    if (path === '/v1/search') {
      let q: string
      try {
        q = parseSearchQuery(url.searchParams, cfg.embed.search)
      } catch (e) {
        return json({ detail: e instanceof Error ? e.message : String(e) }, 400)
      }
      const vec = await embedQuery(sql, cfg.embed, cfg.translate.daily_neurons, q)
      // 今天新聞這邊的 AI 額度用完（台北早上 8 點重算）：回空的、標出原因
      if (vec === null) return json({ q, budget_stop: true, items: [] })
      const rows = await sql<{ item_id: string; score: number }[]>`
        select item_id, (1 - (vec <=> ${vec}::extensions.halfvec))::float8 as score from news.embeddings
        order by vec <=> ${vec}::extensions.halfvec limit ${cfg.embed.search.limit}`
      return json({ q, budget_stop: false, items: await withMeta(sql, rows.filter((r) => r.score >= cfg.embed.search.min_score)) })
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
        q = parseItemsQuery(url.searchParams, cfg, typeWeights.classes)
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
        type: string | null
      }[]>`
        select i.id, i.source, i.lang, i.url,
               coalesce(v.title_orig, i.title_orig) as title, coalesce(v.summary_orig, i.summary_orig) as summary,
               i.published_at, i.first_seen_at, coalesce(v.n, 0)::int as versions,
               t.title_zh, coalesce(t.summary_zh, st.summary_zh) as summary_zh, t.status as tr_status, coalesce(v.content_sha, i.content_sha) as sha,
               ty.type
        from news.raw_items i
        -- 新聞類型（T-421）：現行探針版本；還沒分類 → null
        left join news.item_types ty on ty.item_id = i.id and ty.version = ${typeWeights.version}
        left join lateral (
          select title_orig, summary_orig, content_sha, count(*) over () as n from news.raw_item_versions
          where item_id = i.id order by seen_at desc limit 1
        ) v on true
        -- 目前這一版內容的翻譯（T-401）；還沒翻 → null
        left join news.translations t on t.item_id = i.id and t.content_sha = coalesce(v.content_sha, i.content_sha)
        -- 點開翻過的摘要（D-207）；排程只翻標題
        left join news.summary_translations st on st.item_id = i.id and st.content_sha = coalesce(v.content_sha, i.content_sha) and st.status = 'ok'
        where true
          ${q.since ? sql`and i.first_seen_at >= ${q.since}` : sql``}
          ${q.until ? sql`and i.first_seen_at <= ${q.until}` : sql``}
          ${q.source ? sql`and i.source = ${q.source}` : sql``}
          ${kindSources ? sql`and i.source = any(${kindSources})` : sql``}
          ${q.type ? sql`and ty.type = ${q.type}` : sql``}
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
        // 新聞類型（T-421）：earnings／business／capital／policy／market／other；還沒分類 null
        type: r.type,
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
