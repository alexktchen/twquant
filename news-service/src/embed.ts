// 新聞向量（T-410／SPEC-018 §2、§3.1）：ingest 第五步。每則新聞用 Workers AI 的 bge-m3 算 1024 維向量，
// 給網站的「相似新聞」「語意搜尋」，以及同事件合併（clusters：只新併舊，不回頭重分）。
// 英文新聞等翻譯完（ok 或 failed）才算：2026-10-20 實測原文 vs 自己的譯文 cosine 中位數 0.805，未達事前門檻 0.85
// （docs/reports/news-vectors-t410-2026-10-20.md），所以有譯文用譯文、翻譯沒過用原文；向量算了就不重算。
// 純函式；呼叫模型與資料庫由呼叫端注入（supabase/functions/_shared/io.ts）。

export type EmbedConfig = {
  model: string
  dim: number
  /** 一次送幾則給模型 */
  batch: number
  /** 每次執行最多算幾則（剩下的下次） */
  per_run_limit: number
  /** 一則最多取幾個字（標題＋摘要） */
  max_chars: number
  /** 模型回應沒附 Neurons 時，用輸入 token 估 */
  input_neurons_per_1m: number
  /** 雲端只留近幾天的向量：更舊的刪掉、也不再補算（免費資料庫 500 MB；原文、提及、事件群都留） */
  retention_days: number
  cluster: {
    /** 跟最像的較舊一則 cosine ≥ 這個 → 同一事件 */
    min_score: number
    /** 只往回找幾小時內的 */
    window_hours: number
    /** 這些來源不合併（聯準會新聞稿是同一個範本換名字，分數高但不是同一件事） */
    exclude_sources: string[]
  }
  /** 事件追蹤（T-420）：新開的事件群跟近 window_days 天最像的舊群中心 cosine ≥ min_score → 串成同一條線 */
  thread: { min_score: number; window_days: number }
  similar: { limit: number; min_score: number }
  search: { limit: number; max_query_chars: number; min_score: number }
}

export function validateEmbedConfig(x: unknown): EmbedConfig {
  const c = x as Partial<EmbedConfig> | null
  const fail = (why: string): never => {
    throw new Error(`news 設定錯誤：embed.${why}`)
  }
  if (!c || typeof c !== 'object') fail('（整段缺少）')
  if (typeof c!.model !== 'string' || !c!.model.startsWith('@cf/')) fail('model')
  for (const k of ['dim', 'batch', 'per_run_limit', 'max_chars', 'retention_days'] as const) if (!Number.isInteger(c![k]) || (c![k] as number) < 1) fail(k)
  if (typeof c!.input_neurons_per_1m !== 'number' || c!.input_neurons_per_1m <= 0) fail('input_neurons_per_1m')
  const score = (v: unknown) => typeof v === 'number' && v > 0 && v < 1
  const cl = c!.cluster
  if (!cl || !score(cl.min_score) || !Number.isInteger(cl.window_hours) || cl.window_hours < 1 || !Array.isArray(cl.exclude_sources)) fail('cluster')
  const th = c!.thread
  if (!th || !score(th.min_score) || !Number.isInteger(th.window_days) || th.window_days < 1) fail('thread')
  const si = c!.similar
  if (!si || !Number.isInteger(si.limit) || si.limit < 1 || !score(si.min_score)) fail('similar')
  const se = c!.search
  if (!se || !Number.isInteger(se.limit) || se.limit < 1 || !Number.isInteger(se.max_query_chars) || se.max_query_chars < 1 || !score(se.min_score)) fail('search')
  return c as EmbedConfig
}

/** 要算向量的一則：標題摘要（目前這一版）、譯文（英文且翻譯 ok 才有） */
export type EmbedSource = {
  item_id: string
  source: string
  first_seen_at: string
  title: string
  summary: string | null
  title_zh: string | null
  summary_zh: string | null
}

/** 算向量用的文字：有譯文用譯文，否則原文；標題＋換行＋摘要，截到 max_chars。 */
export function embedText(s: EmbedSource, maxChars: number): string {
  const zh = s.title_zh !== null && s.title_zh !== ''
  const title = zh ? s.title_zh! : s.title
  const summary = (zh ? s.summary_zh : s.summary) ?? ''
  return [...`${title}\n${summary}`.trim()].slice(0, maxChars).join('')
}

/** Workers AI bge-m3 的回應 → 每則一個向量、這次用了多少 Neurons 與 token。形狀不對 → 丟錯（不寫壞向量）。 */
export function parseEmbedReply(payload: unknown, n: number, cfg: Pick<EmbedConfig, 'dim' | 'input_neurons_per_1m'>): { vecs: number[][]; tokens: number; neurons: number } {
  const p = payload as { success?: boolean; errors?: unknown; result?: { data?: unknown; meta?: Record<string, unknown> } } | null
  if (!p || p.success === false || !p.result) throw new Error(`Workers AI 向量失敗：${JSON.stringify(p?.errors ?? p).slice(0, 300)}`)
  const data = p.result.data
  if (!Array.isArray(data) || data.length !== n) throw new Error(`向量數量不對：要 ${n} 個，回 ${Array.isArray(data) ? data.length : '非陣列'}`)
  for (const v of data) {
    if (!Array.isArray(v) || v.length !== cfg.dim || !v.every((x) => typeof x === 'number' && Number.isFinite(x))) throw new Error(`向量維度或數值不對（要 ${cfg.dim} 維有限數）`)
  }
  const meta = p.result.meta ?? {}
  const tokens = meta.cost_metric_name_1 === 'input_tokens' && typeof meta.cost_metric_value_1 === 'number' ? meta.cost_metric_value_1 : NaN
  if (!Number.isFinite(tokens)) throw new Error('Workers AI 回應沒有輸入 token 數，無法記帳')
  const neurons = typeof meta.neurons === 'number' && Number.isFinite(meta.neurons) ? meta.neurons : (tokens * cfg.input_neurons_per_1m) / 1_000_000
  return { vecs: data as number[][], tokens, neurons }
}

/** pgvector 的文字格式：[0.1,0.2,…]（halfvec 只有 3 位有效數字，存 6 位小數就夠） */
export const vecLiteral = (v: number[]): string => `[${v.map((x) => (Math.abs(x) < 5e-7 ? '0' : x.toFixed(6))).join(',')}]`

/** 最像的較舊一則（窗口內、已有事件群） */
export type Neighbor = { item_id: string; cluster_id: string; score: number }
export type ClusterRow = { item_id: string; cluster_id: string; seed_item_id: string | null; score: number | null }

/**
 * 同事件合併（只新併舊）：最像的較舊一則達門檻 → 併進它的事件群；否則自己開一群（群代號＝自己的 id）。
 * 排除的來源一律自己一群。neighbors 已依分數由高到低。
 */
export function assignCluster(item: Pick<EmbedSource, 'item_id' | 'source'>, neighbors: Neighbor[], cfg: EmbedConfig['cluster']): ClusterRow {
  const best = cfg.exclude_sources.includes(item.source) ? undefined : neighbors.find((n) => n.item_id !== item.item_id)
  if (best && best.score >= cfg.min_score) return { item_id: item.item_id, cluster_id: best.cluster_id, seed_item_id: best.item_id, score: best.score }
  return { item_id: item.item_id, cluster_id: item.item_id, seed_item_id: null, score: null }
}

/** 近期最像的舊事件群（中心向量） */
export type NearCluster = { cluster_id: string; thread_id: string; score: number }
export type ThreadRow = { cluster_id: string; thread_id: string; seed_cluster_id: string | null; score: number | null }

/** 新開的事件群要不要接上舊的線：最像的舊群 ≥ min_score → 接它那條線；否則自己開一條（thread_id＝cluster_id）。 */
export function assignThread(clusterId: string, nearest: NearCluster | null, cfg: EmbedConfig['thread']): ThreadRow {
  if (nearest && nearest.cluster_id !== clusterId && nearest.score >= cfg.min_score)
    return { cluster_id: clusterId, thread_id: nearest.thread_id, seed_cluster_id: nearest.cluster_id, score: nearest.score }
  return { cluster_id: clusterId, thread_id: clusterId, seed_cluster_id: null, score: null }
}

export type EmbeddingRow = { item_id: string; model: string; text_sha: string; vec: string }
export type EmbedUsage = { ts: string; task: string; model_id: string; input_tokens: number; output_tokens: number; neurons: number }

export type EmbedDeps = {
  now: () => Date
  /** 保留期（retention_days 天）內還沒有向量的新聞（英文要翻譯完），舊的先（同事件合併要「新併舊」）；保留期外的不再補算 */
  pending: (limit: number) => Promise<EmbedSource[]>
  /** 這個 UTC 日新聞這邊已用的 Neurons（跟翻譯共用每天的額度） */
  usedToday: (utcDay: string) => Promise<number>
  /** 呼叫 Workers AI，回原始 JSON */
  ai: (texts: string[]) => Promise<unknown>
  sha: (s: string) => Promise<string>
  /** 窗口內、比這則早、已有事件群的最像幾則（依分數由高到低） */
  neighbors: (vec: string, before: string, windowHours: number) => Promise<Neighbor[]>
  /** 寫一則的向量與事件群（同一個交易）；用量另外記 */
  save: (e: EmbeddingRow, c: ClusterRow) => Promise<void>
  saveUsage: (u: EmbedUsage) => Promise<void>
  /** 刪掉超過保留期的向量，回刪了幾筆 */
  prune: (retentionDays: number) => Promise<number>
  /** 事件群中心：成員加入就更新（第一個成員＝自己） */
  upsertCentroid: (clusterId: string, vec: number[], seenAt: string) => Promise<void>
  /** 近 windowDays 天、比 before 早開始、不是自己的最像舊群 */
  nearestCluster: (vec: string, before: string, windowDays: number, excludeCluster: string) => Promise<NearCluster | null>
  saveThread: (t: ThreadRow) => Promise<void>
}

export type EmbedResult = { embedded: number; clusters_joined: number; threads_joined: number; budget_stop: boolean; pruned: number }

/** 一批一批算；額度（跟翻譯共用的每日 Neurons）用完就停。一則一則依時間先後寫，後面的才找得到前面的當同事件。 */
export async function embedPending(cfg: EmbedConfig, dailyNeurons: number, deps: EmbedDeps): Promise<EmbedResult> {
  const day = deps.now().toISOString().slice(0, 10)
  let used = await deps.usedToday(day)
  // 先刪過期的（不花額度），再算新的
  const pruned = await deps.prune(cfg.retention_days)
  const todo = await deps.pending(cfg.per_run_limit)
  const res: EmbedResult = { embedded: 0, clusters_joined: 0, threads_joined: 0, budget_stop: false, pruned }
  for (let i = 0; i < todo.length; i += cfg.batch) {
    if (used >= dailyNeurons) {
      res.budget_stop = true
      break
    }
    const batch = todo.slice(i, i + cfg.batch)
    const texts = batch.map((s) => embedText(s, cfg.max_chars))
    const r = parseEmbedReply(await deps.ai(texts), batch.length, cfg)
    used += r.neurons
    await deps.saveUsage({ ts: deps.now().toISOString(), task: 'news_embed', model_id: cfg.model, input_tokens: r.tokens, output_tokens: 0, neurons: r.neurons })
    for (let k = 0; k < batch.length; k++) {
      const vec = vecLiteral(r.vecs[k])
      const c = assignCluster(batch[k], await deps.neighbors(vec, batch[k].first_seen_at, cfg.cluster.window_hours), cfg.cluster)
      await deps.save({ item_id: batch[k].item_id, model: cfg.model, text_sha: await deps.sha(texts[k]), vec }, c)
      res.embedded += 1
      await deps.upsertCentroid(c.cluster_id, r.vecs[k], batch[k].first_seen_at)
      if (c.seed_item_id !== null) res.clusters_joined += 1
      else {
        // 新開的事件群：找近期最像的舊群，接上它的線（T-420）
        const t = assignThread(c.cluster_id, await deps.nearestCluster(vec, batch[k].first_seen_at, cfg.thread.window_days, c.cluster_id), cfg.thread)
        await deps.saveThread(t)
        if (t.seed_cluster_id !== null) res.threads_joined += 1
      }
    }
  }
  return res
}

// ── 查詢端（/v1/similar、/v1/search）──
export type SimilarRow = { item_id: string; score: number; same_event: boolean }

/** 相似新聞：同一事件群的在前（依分數），再補向量最近的；去重、去掉自己、低於門檻不列，最多 limit 則。 */
export function mergeSimilar(self: string, sameEvent: { item_id: string; score: number }[], near: { item_id: string; score: number }[], cfg: EmbedConfig['similar']): SimilarRow[] {
  const out: SimilarRow[] = []
  const seen = new Set([self])
  for (const r of [...sameEvent].sort((a, b) => b.score - a.score || a.item_id.localeCompare(b.item_id))) {
    if (seen.has(r.item_id)) continue
    seen.add(r.item_id)
    out.push({ item_id: r.item_id, score: r.score, same_event: true })
  }
  for (const r of [...near].sort((a, b) => b.score - a.score || a.item_id.localeCompare(b.item_id))) {
    if (seen.has(r.item_id) || r.score < cfg.min_score) continue
    seen.add(r.item_id)
    out.push({ item_id: r.item_id, score: r.score, same_event: false })
  }
  return out.slice(0, cfg.limit)
}

/** /v1/search 的 q：去頭尾空白、不可空、不可超過 max_query_chars 字。不合法 → 丟錯（端點回 400）。 */
export function parseSearchQuery(params: URLSearchParams, cfg: EmbedConfig['search']): string {
  const q = (params.get('q') ?? '').trim()
  if (!q) throw new Error('缺 q（要搜尋的字）')
  if ([...q].length > cfg.max_query_chars) throw new Error(`搜尋字太長（最多 ${cfg.max_query_chars} 字）`)
  return q
}
