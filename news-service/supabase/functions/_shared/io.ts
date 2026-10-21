// Edge Function 的 IO（Deno）：資料庫連線、抓 RSS、把 ingest.ts 的 NewsStore 介面接到 Postgres。
// 邏輯都在 news-service/src（純函式、有測試）；這裡只有 SQL 與 HTTP。
import postgres from 'npm:postgres@3.4.5'
import type { FetchResult, NewsStore, SourceState } from '../../../src/ingest.ts'
import type { EntityAliases, MentionDeps, PendingItem } from '../../../src/mention.ts'
import type { Pending, TranslateDeps } from '../../../src/translate.ts'
import type { CooccurDeps, CooccurEdge, PairCount } from '../../../src/cooccur.ts'
import type { EntityType } from '../../../src/graph.ts'
import type { TypeDeps } from '../../../src/types.ts'
import { embedText, parseEmbedReply, vecLiteral, type EmbedConfig, type EmbedDeps, type EmbedSource, type NearCluster, type Neighbor } from '../../../src/embed.ts'
import { sha256Hex } from '../../../src/ingest.ts'

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

/** 這個 UTC 日（Workers AI 免費額度的日界）新聞這邊已用的 Neurons：翻譯、向量、搜尋共用（D-200 §五）。 */
export const usedNeurons = (sql: Sql) => async (day: string): Promise<number> => {
  const [r] = await sql<{ n: number }[]>`
    select coalesce(sum(neurons), 0)::float8 as n from news.llm_usage
    where ts >= (${day}::date)::timestamp at time zone 'UTC' and ts < ((${day}::date) + 1)::timestamp at time zone 'UTC'`
  return r.n
}

/** 翻譯要的資料庫與 Workers AI（T-401）。帳號與金鑰是 Edge Function 祕密 CLOUDFLARE_ACCOUNT_ID、CLOUDFLARE_AI_TOKEN。 */
export function translateDeps(sql: Sql, model: string): TranslateDeps {
  const account = Deno.env.get('CLOUDFLARE_ACCOUNT_ID')
  const token = Deno.env.get('CLOUDFLARE_AI_TOKEN')
  if (!account || !token) throw new Error('CLOUDFLARE_ACCOUNT_ID／CLOUDFLARE_AI_TOKEN 沒設定，不能翻譯')
  return {
    now: () => new Date(),
    // 還沒翻過「目前這一版內容」的英文新聞，新的先
    pending: (limit) => sql<Pending[]>`
      select i.id, coalesce(v.title_orig, i.title_orig) as title, coalesce(v.summary_orig, i.summary_orig) as summary,
             coalesce(v.content_sha, i.content_sha) as content_sha
      from news.raw_items i
      left join lateral (
        select title_orig, summary_orig, content_sha from news.raw_item_versions
        where item_id = i.id order by seen_at desc limit 1
      ) v on true
      where i.lang = 'en'
        and not exists (
          select 1 from news.translations t where t.item_id = i.id and t.content_sha = coalesce(v.content_sha, i.content_sha)
        )
      order by i.first_seen_at desc, i.id
      limit ${limit}`,
    usedToday: usedNeurons(sql),
    ai: async (body) => {
      const res = await fetch(`https://api.cloudflare.com/client/v4/accounts/${account}/ai/run/${model}`, {
        method: 'POST',
        headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(60_000),
      })
      // 錯誤時 Cloudflare 也回 JSON（success: false），交給 parseReply 丟出原因
      return res.json()
    },
    save: async (rows, usage) => {
      if (rows.length > 0) {
        const data = rows.map((r) => ({ ...r, checks: sql.json(r.checks) }))
        await sql`insert into news.translations ${sql(data)} on conflict (item_id, content_sha) do nothing`
      }
      if (usage.length > 0) await sql`insert into news.llm_usage ${sql(usage)}`
    },
  }
}

/** 比對新聞提到哪些實體要的資料庫（T-402）。 */
export function mentionDeps(sql: Sql, ruleVersion: string): MentionDeps {
  return {
    now: () => new Date(),
    entities: () => sql<EntityAliases[]>`
      select id, label, aliases from news.entities where valid_to is null order by id`,
    // 「目前這一版內容」還沒比對過的，新的先。英文新聞要等這一版翻過（ok 或 failed 都算翻過）才比：
    // 台股的別名只有中文名，先用英文比完記進 mention_runs 就不會再比譯文了；中文新聞直接比
    pending: (limit) => sql<PendingItem[]>`
      select i.id as item_id, coalesce(v.content_sha, i.content_sha) as content_sha,
             coalesce(v.title_orig, i.title_orig) as title, coalesce(v.summary_orig, i.summary_orig) as summary,
             t.title_zh, t.summary_zh
      from news.raw_items i
      left join lateral (
        select title_orig, summary_orig, content_sha from news.raw_item_versions
        where item_id = i.id order by seen_at desc limit 1
      ) v on true
      left join news.translations t on t.item_id = i.id and t.content_sha = coalesce(v.content_sha, i.content_sha)
      where (i.lang <> 'en' or t.item_id is not null)
        and not exists (
          select 1 from news.mention_runs r where r.item_id = i.id and r.content_sha = coalesce(v.content_sha, i.content_sha)
            and r.rule_version = ${ruleVersion}
        )
      order by i.first_seen_at desc, i.id
      limit ${limit}`,
    save: async (hits, runs, ruleVersion) => {
      if (hits.length === 0 && runs.length === 0) return
      await sql.begin(async (tx) => {
        // 命中與「比對過了」同一個交易寫：不會命中寫了、紀錄沒寫（下次重比），也不會紀錄寫了、命中沒寫（漏掉）
        if (hits.length > 0) {
          const rows = hits.map((h) => ({
            item_id: h.item_id,
            entity_id: h.entity,
            content_sha: h.content_sha,
            method: 'alias',
            evidence: h.evidence,
            rule_version: ruleVersion,
          }))
          await tx`insert into news.mentions ${tx(rows)} on conflict do nothing`
        }
        if (runs.length > 0) {
          const rows = runs.map((r) => ({ item_id: r.item_id, content_sha: r.content_sha, n: r.n, rule_version: ruleVersion }))
          await tx`insert into news.mention_runs ${tx(rows)} on conflict do nothing`
        }
      })
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

/** 從新聞學關係（ingest 第四步，src/cooccur.ts）：同一則新聞一起被提到的配對與次數、現役 cooccur 線的增刪改。 */
export function cooccurDeps(sql: Sql, ruleVersion: string): CooccurDeps {
  return {
    counts: async (windowDays) => {
      const recent = sql`
        select distinct m.item_id, m.entity_id from news.mentions m join news.raw_items i on i.id = m.item_id
        where i.first_seen_at > now() - make_interval(days => ${windowDays}) and m.rule_version = ${ruleVersion}`
      const [pairs, ents, tot] = await Promise.all([
        sql<PairCount[]>`
          with r as (${recent})
          select x.entity_id as a, y.entity_id as b, count(*)::int as n
          from r x join r y on x.item_id = y.item_id and x.entity_id < y.entity_id
          group by 1, 2 having count(*) >= 2`,
        sql<{ id: string; n: number }[]>`with r as (${recent}) select entity_id as id, count(*)::int as n from r group by 1`,
        sql<{ n: number }[]>`with r as (${recent}) select count(distinct item_id)::int as n from r`,
      ])
      return { pairs, mentionsOf: new Map(ents.map((e) => [e.id, e.n])), total: tot[0]?.n ?? 0 }
    },
    types: async () => {
      const rows = await sql<{ id: string; type: EntityType }[]>`select id, type from news.entities where valid_to is null`
      return new Map(rows.map((r) => [r.id, r.type]))
    },
    linked: async () => {
      const rows = await sql<{ k: string }[]>`
        select distinct least(src, dst) || '|' || greatest(src, dst) as k from news.graph_edges
        where valid_to is null and type <> 'cooccur'`
      return new Set(rows.map((r) => r.k))
    },
    existing: () => sql<CooccurEdge[]>`
      select src, dst, coalesce(label, '') as label from news.graph_edges where valid_to is null and type = 'cooccur'`,
    apply: async ({ close, insert, relabel }) => {
      await sql.begin(async (tx) => {
        for (const e of close)
          await tx`update news.graph_edges set valid_to = now() where valid_to is null and type = 'cooccur' and src = ${e.src} and dst = ${e.dst}`
        for (const e of relabel)
          await tx`update news.graph_edges set label = ${e.label} where valid_to is null and type = 'cooccur' and src = ${e.src} and dst = ${e.dst}`
        if (insert.length)
          await tx`insert into news.graph_edges ${tx(insert.map((e) => ({ src: e.src, dst: e.dst, type: 'cooccur', status: 'approved', label: e.label, source: 'news_cooccur' })))}`
      })
    },
  }
}

/** Workers AI 的帳號與金鑰（Edge Function 祕密）；向量、翻譯共用。 */
function workersAi(model: string): (body: unknown) => Promise<unknown> {
  const account = Deno.env.get('CLOUDFLARE_ACCOUNT_ID')
  const token = Deno.env.get('CLOUDFLARE_AI_TOKEN')
  if (!account || !token) throw new Error('CLOUDFLARE_ACCOUNT_ID／CLOUDFLARE_AI_TOKEN 沒設定，不能呼叫 Workers AI')
  return async (body) => {
    const res = await fetch(`https://api.cloudflare.com/client/v4/accounts/${account}/ai/run/${model}`, {
      method: 'POST',
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(60_000),
    })
    return res.json() // 錯誤時也是 JSON（success: false），交給 parseEmbedReply 丟出原因
  }
}

/** 新聞向量（ingest 第五步，src/embed.ts）。 */
export function embedDeps(sql: Sql, cfg: EmbedConfig): EmbedDeps {
  const ai = workersAi(cfg.model)
  return {
    now: () => new Date(),
    // 還沒有向量的；英文要「目前這一版」翻譯完（ok 或 failed）才算，ok 才給譯文；舊的先（同事件只新併舊）
    pending: (limit) => sql<EmbedSource[]>`
      select i.id as item_id, i.source, i.first_seen_at::text as first_seen_at,
             coalesce(v.title_orig, i.title_orig) as title, coalesce(v.summary_orig, i.summary_orig) as summary,
             case when t.status = 'ok' then t.title_zh end as title_zh, case when t.status = 'ok' then t.summary_zh end as summary_zh
      from news.raw_items i
      left join lateral (
        select title_orig, summary_orig, content_sha from news.raw_item_versions
        where item_id = i.id order by seen_at desc limit 1
      ) v on true
      left join news.translations t on t.item_id = i.id and t.content_sha = coalesce(v.content_sha, i.content_sha)
      where (i.lang <> 'en' or t.item_id is not null)
        and i.first_seen_at > now() - make_interval(days => ${cfg.retention_days})
        and not exists (select 1 from news.embeddings e where e.item_id = i.id)
      order by i.first_seen_at, i.id
      limit ${limit}`,
    usedToday: usedNeurons(sql),
    ai: (texts) => ai({ text: texts }),
    sha: sha256Hex,
    neighbors: (vec, before, windowHours) => sql<Neighbor[]>`
      select e.item_id, c.cluster_id, (1 - (e.vec <=> ${vec}::extensions.halfvec))::float8 as score
      from news.embeddings e
      join news.clusters c on c.item_id = e.item_id
      join news.raw_items i on i.id = e.item_id
      where i.first_seen_at <= ${before}::timestamptz and i.first_seen_at > ${before}::timestamptz - make_interval(hours => ${windowHours})
      order by e.vec <=> ${vec}::extensions.halfvec
      limit 5`,
    // 向量與事件群一個語句寫完（原子、一趟來回）：向量已經有了（兩輪同時跑）就兩個都不寫
    save: async (e, c) => {
      await sql`
        with e as (
          insert into news.embeddings (item_id, model, text_sha, vec)
          values (${e.item_id}, ${e.model}, ${e.text_sha}, ${e.vec}::extensions.halfvec)
          on conflict do nothing returning item_id
        )
        insert into news.clusters (item_id, cluster_id, seed_item_id, score)
        select item_id, ${c.cluster_id}, ${c.seed_item_id}, ${c.score}::float8 from e
        on conflict do nothing`
    },
    saveUsage: async (u) => {
      await sql`insert into news.llm_usage ${sql(u)}`
    },
    prune: async (days) => {
      const r = await sql`
        delete from news.embeddings e using news.raw_items i
        where i.id = e.item_id and i.first_seen_at < now() - make_interval(days => ${days})`
      return r.count
    },
    // 群中心＝成員向量的平均：讀出舊的 (n, vec) 在這裡算新平均再寫回（pgvector 沒有乘除純量）
    upsertCentroid: async (clusterId, vec, seenAt) => {
      const [old] = await sql<{ n: number; v: string }[]>`select n, vec::text as v from news.cluster_centroids where cluster_id = ${clusterId}`
      let n = 1
      let mean = vec
      if (old) {
        const prev = JSON.parse(old.v) as number[]
        n = old.n + 1
        mean = prev.map((p, i) => (p * old.n + vec[i]) / n)
      }
      await sql`
        insert into news.cluster_centroids (cluster_id, n, vec, first_seen_at, last_seen_at)
        values (${clusterId}, ${n}, ${vecLiteral(mean)}::extensions.halfvec, ${seenAt}::timestamptz, ${seenAt}::timestamptz)
        on conflict (cluster_id) do update set n = excluded.n, vec = excluded.vec,
          first_seen_at = least(news.cluster_centroids.first_seen_at, excluded.first_seen_at),
          last_seen_at = greatest(news.cluster_centroids.last_seen_at, excluded.last_seen_at)`
    },
    nearestCluster: async (vec, before, windowDays, excludeCluster) => {
      const [row] = await sql<NearCluster[]>`
        select cc.cluster_id, coalesce(t.thread_id, cc.cluster_id) as thread_id, (1 - (cc.vec <=> ${vec}::extensions.halfvec))::float8 as score
        from news.cluster_centroids cc left join news.threads t on t.cluster_id = cc.cluster_id
        where cc.cluster_id <> ${excludeCluster} and cc.first_seen_at < ${before}::timestamptz
          and cc.last_seen_at > ${before}::timestamptz - make_interval(days => ${windowDays})
        order by cc.vec <=> ${vec}::extensions.halfvec limit 1`
      return row ?? null
    },
    saveThread: async (t) => {
      await sql`insert into news.threads ${sql(t)} on conflict (cluster_id) do nothing`
    },
  }
}

/** 搜尋框的問句 → 向量（/v1/search）。額度用完 → null（端點回 503 說明）；用量記 news_search。 */
export async function embedQuery(sql: Sql, cfg: EmbedConfig, dailyNeurons: number, q: string): Promise<string | null> {
  const now = new Date()
  if ((await usedNeurons(sql)(now.toISOString().slice(0, 10))) >= dailyNeurons) return null
  const r = parseEmbedReply(await workersAi(cfg.model)({ text: [q] }), 1, cfg)
  await sql`insert into news.llm_usage ${sql({ ts: now.toISOString(), task: 'news_search', model_id: cfg.model, input_tokens: r.tokens, output_tokens: 0, neurons: r.neurons })}`
  return vecLiteral(r.vecs[0])
}

/** 新聞類型（T-421）：有向量、還沒有這一版類型的新聞 → 分類結果寫 news.item_types。不花 AI 額度。 */
export function typeDeps(sql: Sql, version: string): TypeDeps {
  return {
    pending: async (limit) => {
      const rows = await sql<{ item_id: string; v: string }[]>`
        select e.item_id, e.vec::text as v from news.embeddings e
        left join news.item_types t on t.item_id = e.item_id and t.version = ${version}
        join news.raw_items i on i.id = e.item_id
        where t.item_id is null order by i.first_seen_at desc limit ${limit}`
      return rows.map((r) => ({ item_id: r.item_id, vec: JSON.parse(r.v) as number[] }))
    },
    save: async (rows) => {
      await sql`insert into news.item_types ${sql(rows)} on conflict (item_id, version) do update set type = excluded.type, confidence = excluded.confidence`
    },
  }
}
