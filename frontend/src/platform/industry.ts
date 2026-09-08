/** Stock rows grouped under one industry label, kept as strings so "0050" stays "0050". */
export type IndustryStock = { stock_id: string; name: string | null }

type MetaLike = Record<string, { name: string | null; industry: string | null; market: string | null }>

/**
 * List the stocks currently classified under one industry.
 *
 * stock_meta only carries the current classification, so callers must label the result as
 * current rather than claiming a point-in-time industry for a historical date (SPEC-014 §6).
 */
export function stocksInIndustry(meta: MetaLike, industry: string): IndustryStock[] {
  return Object.entries(meta)
    .filter(([, entry]) => entry.industry === industry)
    .map(([stock_id, entry]) => ({ stock_id, name: entry.name }))
    .sort((left, right) => (left.stock_id < right.stock_id ? -1 : 1))
}

/** How many of an industry's flow rows the current classification can actually name. */
export function coverageNote(reported: number, named: number): string {
  if (named === 0) return `API 回報 ${reported} 檔；目前分類表沒有可對應的股票，無法列出成分`
  if (named < reported) return `API 回報 ${reported} 檔，目前分類表可對應 ${named} 檔（其餘未在分類表中）`
  return `API 回報 ${reported} 檔，目前分類表可對應 ${named} 檔`
}
