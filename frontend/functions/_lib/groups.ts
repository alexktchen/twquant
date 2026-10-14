// 族群標籤（D-177）：每檔股票的族群。排程每週從櫃買中心「產業價值鏈資訊平台」抓、挑好一條存在
// serving.stock_group（twquant stock-groups）；沒有列的股票（平台沒這家、或掛的鏈都不屬於它的官方產業別）用官方產業別。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

/** 資料庫的一列；陣列欄位在 SQL 裡轉成 JSON 文字（連線設定 fetch_types: false，text[] 不會自己變成陣列）。 */
export type GroupRow = { stock_id: string; chain: string; segments: string | null; also: string | null }
/** 網站用的族群：name＝顯示的字；segs＝在這條鏈上掛哪幾段；also＝還掛了哪些鏈；official＝沒有產業鏈、用的是官方產業別。 */
export type Group = { name: string; segs: string[]; also: string[]; official: boolean }

function list(text: string | null): string[] {
  if (!text) return []
  const v: unknown = JSON.parse(text)
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

/** 有產業鏈就用鏈，沒有就退回官方產業別；兩個都沒有 → null（不顯示）。 */
export function groupOf(row: GroupRow | undefined, industry: string | null): Group | null {
  if (row) return { name: row.chain, segs: list(row.segments), also: list(row.also), official: false }
  const ind = industry?.trim()
  return ind ? { name: ind, segs: [], also: [], official: true } : null
}

/** 這幾檔的族群列。表還沒建（新功能上線後第一次排程跑完之前）當成都沒有，網站先顯示官方產業別。 */
export async function readGroups(db: ReturnType<typeof sql>, sids: string[]): Promise<Map<string, GroupRow>> {
  if (sids.length === 0) return new Map()
  const rows = await readRows(
    () => db<GroupRow[]>`
      SELECT stock_id, chain, array_to_json(segments)::text AS segments, array_to_json(also)::text AS also
      FROM serving.stock_group WHERE stock_id = ANY(string_to_array(${sids.join(',')}, ','))`,
  )
  return new Map(rows.map((r) => [r.stock_id, r]))
}
