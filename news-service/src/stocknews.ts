// 反向看圖（使用者 2026-10-20：「那有反向看圖的地方嗎？像是每日推薦股有關的新聞」）：某一檔股票連得到哪些新聞。
// 跟單則關係圖用同一套線與同一次 propagate（src/graph.ts、graphapi.ts edgesFor），所以這裡說「間接關聯」的新聞，
// 打開那則的心智圖一定看得到這一檔、路徑也一樣。個股頁與名單只到 stock_max_degree（SPEC-017：二度以內）。純函式。
import { adjacency, expandEdges, propagateWith, type Adjacency, type Edge, type Link } from './graph.ts'
import { edgesFor, type EdgeRow, type EntityRow, type GraphApiConfig } from './graphapi.ts'

export type StockNewsConfig = {
  /** 個股頁、名單只算到幾度（SPEC-017 §6：二度以內） */
  stock_max_degree: number
  /** 個股頁列近幾天的新聞 */
  stock_days: number
  /** 個股頁最多列幾則 */
  stock_limit: number
  /** 名單小標算近幾天 */
  counts_days: number
  /** 一次最多問幾檔（名單三條線加起來約 45 檔） */
  counts_max_ids: number
  /** 被超過這個比例的新聞提到的實體不當「間接」的橋（「AI」這種到處都提的主題） */
  hub_share: number
  /** 個股頁 K 線上的新聞標記看近幾天（T-419） */
  timeline_days: number
  /** 每一天最多帶幾則標題 */
  timeline_per_day: number
  /** 「久違的消息」：這檔上一次被提到距今至少幾天（而且我們收新聞至少也這麼多天）才標 */
  quiet_min_days: number
}

/** 一則新聞提到的實體（一度）。 */
export type ItemMentions = { item_id: string; entities: string[] }
export type PathStep = { id: string; label: string; via: string; via_label?: string }
export type StockLink = { item_id: string; degree: number; score: number; draft: boolean; path: PathStep[] }

/** 查很多檔共用的準備：單則關係圖用的線，加上「誰一步走得到我」的反查表。 */
export type Prepared = { edges: Edge[]; adj: Adjacency; into: Map<string, Set<string>> }

export function prepare(rows: EdgeRow[], entities: Map<string, EntityRow>, cfg: GraphApiConfig): Prepared {
  const edges = edgesFor(rows, entities, cfg)
  const into = new Map<string, Set<string>>()
  for (const e of expandEdges(edges)) {
    let s = into.get(e.dst)
    if (!s) into.set(e.dst, (s = new Set()))
    s.add(e.src)
  }
  return { edges, adj: adjacency(edges), into }
}

/**
 * 哪些新聞連得到 stock（degree ≤ maxDegree，最多 2）：先挑候選（直接提到它、或提到一步走得到它的實體），
 * 每則照單則關係圖的算法跑一次 propagate（同樣的 max_degree、max_links），結果裡有它而且度數夠近才算。
 */
export function linksTo(
  stock: string,
  items: ItemMentions[],
  prep: Prepared,
  entities: Map<string, EntityRow>,
  cfg: GraphApiConfig,
  maxDegree: number,
  hubShare = 1,
): StockLink[] {
  return linksFrom(stock, items, prep, entities, cfg, maxDegree, new Map(), hubsOf(items, hubShare))
}

/** 這批新聞裡被超過 share 比例提到的實體（到處都提，當「間接」的橋沒有資訊量）。 */
export function hubsOf(items: ItemMentions[], share: number): Set<string> {
  const n = new Map<string, number>()
  for (const it of items) for (const e of new Set(it.entities)) n.set(e, (n.get(e) ?? 0) + 1)
  return new Set([...n].filter(([, k]) => k > share * items.length).map(([e]) => e))
}

/** 一則新聞的關聯（同單則關係圖）；同一次查詢裡算過的直接拿（名單一次問很多檔，每則只算一次）。 */
function linksOf(it: ItemMentions, prep: Prepared, entities: Map<string, EntityRow>, cfg: GraphApiConfig, memo: Map<string, Map<string, Link>>): Map<string, Link> {
  let m = memo.get(it.item_id)
  if (!m) {
    const known = it.entities.filter((e) => entities.has(e))
    m = new Map(propagateWith(known, prep.adj, cfg).map((l) => [l.entity, l]))
    memo.set(it.item_id, m)
  }
  return m
}

function linksFrom(
  stock: string,
  items: ItemMentions[],
  prep: Prepared,
  entities: Map<string, EntityRow>,
  cfg: GraphApiConfig,
  maxDegree: number,
  memo: Map<string, Map<string, Link>>,
  hubs: Set<string>,
): StockLink[] {
  if (maxDegree < 1 || maxDegree > 2) throw new Error(`stock_max_degree 只能 1 或 2：${maxDegree}`)
  const near = new Set<string>([stock])
  if (maxDegree >= 2) for (const s of prep.into.get(stock) ?? []) near.add(s)
  const out: StockLink[] = []
  for (const it of items) {
    if (!it.entities.some((e) => near.has(e) && entities.has(e))) continue
    const hit = linksOf(it, prep, entities, cfg, memo).get(stock)
    if (!hit || hit.degree > maxDegree) continue
    if (hit.degree > 1 && hubs.has(hit.path[0].entity)) continue // 橋是到處都提的實體
    out.push({
      item_id: it.item_id,
      degree: hit.degree,
      score: hit.score,
      draft: hit.draft,
      path: hit.path.map((s) => ({
        id: s.entity,
        label: entities.get(s.entity)?.label ?? s.entity,
        via: s.via,
        ...(s.label ? { via_label: s.label } : {}),
      })),
    })
  }
  return out.sort((a, b) => a.degree - b.degree || b.score - a.score || a.item_id.localeCompare(b.item_id))
}

/** 名單小標：每一檔直接提到幾則、間接關聯幾則。 */
export function countsFor(
  stocks: string[],
  items: ItemMentions[],
  prep: Prepared,
  entities: Map<string, EntityRow>,
  cfg: GraphApiConfig,
  maxDegree: number,
  hubShare = 1,
): Record<string, { direct: number; indirect: number }> {
  const out: Record<string, { direct: number; indirect: number }> = {}
  const memo = new Map<string, Map<string, Link>>()
  const hubs = hubsOf(items, hubShare)
  for (const s of [...new Set(stocks)].sort()) {
    const links = linksFrom(s, items, prep, entities, cfg, maxDegree, memo, hubs)
    out[s] = { direct: links.filter((l) => l.degree === 1).length, indirect: links.filter((l) => l.degree > 1).length }
  }
  return out
}

/** /v1/stock-news 的 id、/v1/stock-news-counts 的 ids：只收台股（tw:代號）。 */
export const STOCK_ID_RE = /^tw:[0-9A-Z]{4,6}$/

export function parseStockIds(raw: string | null, max: number): string[] {
  if (!raw) throw new Error('缺 ids')
  const ids = [...new Set(raw.split(',').map((s) => s.trim()).filter(Boolean))]
  if (ids.length === 0) throw new Error('缺 ids')
  if (ids.length > max) throw new Error(`ids 最多 ${max} 檔：${ids.length}`)
  const bad = ids.filter((s) => !STOCK_ID_RE.test(s))
  if (bad.length) throw new Error(`ids 要像 tw:2330：${bad.join(',')}`)
  return ids
}

export function validateStockNewsConfig(x: unknown): StockNewsConfig {
  const c = x as Partial<StockNewsConfig> | null
  const fail = (why: string): never => {
    throw new Error(`news 設定錯誤：stock_news.${why}`)
  }
  if (!c || typeof c !== 'object') fail('（整段缺少）')
  for (const k of ['stock_max_degree', 'stock_days', 'stock_limit', 'counts_days', 'counts_max_ids', 'timeline_days', 'timeline_per_day', 'quiet_min_days'] as const)
    if (!Number.isInteger(c![k]) || (c![k] as number) < 1) fail(k)
  if (c!.stock_max_degree! > 2) fail('stock_max_degree（最多 2）')
  if (typeof c!.hub_share !== 'number' || c!.hub_share <= 0 || c!.hub_share > 1) fail('hub_share')
  return c as StockNewsConfig
}

// ── 個股頁新聞時間軸與「久違的消息」（T-419；使用者 2026-10-20 選的 2、4）──────────────────────────
/** 直接提到這檔的一則（day＝台北日期） */
export type DayRow = { day: string; id: string; source: string; title: string; title_zh: string | null; url: string; first_seen_at: string }
export type NewsDay = { day: string; n: number; outlets: string[]; items: { id: string; title: string; src: string; url: string }[] }

/** 每天幾則、哪幾家、前幾則標題（新的先）；日期由舊到新。outletOf 把來源代號換成媒體名。 */
export function newsDays(rows: DayRow[], outletOf: (source: string) => string, perDay: number): NewsDay[] {
  const by = new Map<string, DayRow[]>()
  for (const r of rows) by.set(r.day, [...(by.get(r.day) ?? []), r])
  return [...by]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([day, rs]) => {
      const sorted = [...rs].sort((a, b) => (a.first_seen_at < b.first_seen_at ? 1 : a.first_seen_at > b.first_seen_at ? -1 : a.id.localeCompare(b.id)))
      return {
        day,
        n: rs.length,
        outlets: [...new Set(rs.map((r) => outletOf(r.source)))].sort(),
        items: sorted.slice(0, perDay).map((r) => ({ id: r.id, title: r.title_zh ?? r.title, src: outletOf(r.source), url: r.url })),
      }
    })
}

export type Quiet = { days: number; at_least: boolean }

/**
 * 久違的消息：最近一次被提到之前，隔了多久沒消息。days＝有消息的台北日期（由舊到新，時間軸範圍內）；
 * coverageDays＝我們收新聞到最近那天共幾天；windowDays＝時間軸看幾天。
 * 有前一次 → 隔的天數；時間軸裡沒有前一次 → 至少「收新聞以來」或「時間軸那麼久」（取小的），at_least 標 true。
 * 隔不到 minQuiet 天、或我們收新聞還不到 minQuiet 天（沒資格說「久違」）→ null。
 */
export function quietGap(days: string[], coverageDays: number, windowDays: number, minQuiet: number): Quiet | null {
  if (days.length === 0 || coverageDays < minQuiet) return null
  const sorted = [...days].sort()
  const latest = sorted[sorted.length - 1]
  const prev = sorted.length >= 2 ? sorted[sorted.length - 2] : null
  const gap = prev ? Math.round((Date.parse(latest) - Date.parse(prev)) / 86_400_000) : Math.min(coverageDays, windowDays)
  if (gap < minQuiet) return null
  return { days: gap, at_least: prev === null }
}

/** 收新聞到 at 為止共幾天（第一則的日期到 at，至少 1）。 */
export const coverageDaysOf = (firstSeenAt: string | null, at: Date): number =>
  firstSeenAt ? Math.max(1, Math.floor((at.getTime() - Date.parse(firstSeenAt)) / 86_400_000) + 1) : 0
