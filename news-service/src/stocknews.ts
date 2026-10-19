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
  for (const k of ['stock_max_degree', 'stock_days', 'stock_limit', 'counts_days', 'counts_max_ids'] as const)
    if (!Number.isInteger(c![k]) || (c![k] as number) < 1) fail(k)
  if (c!.stock_max_degree! > 2) fail('stock_max_degree（最多 2）')
  if (typeof c!.hub_share !== 'number' || c!.hub_share <= 0 || c!.hub_share > 1) fail('hub_share')
  return c as StockNewsConfig
}
